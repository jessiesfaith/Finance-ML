"""
Tests for the NFP CFO decision-intelligence engine.

The pins come from the master build prompt's own worked examples, so
the engine is provably implementing the specified math:
  §22  $200K x 70% = $140K -> expected funding $390K -> gap $110K
  §10  $250,000 x 7% = $17,500 annual interest avoided
  §34  $1M x 7% x 6/12 = $35K bridge interest
  §29  $7.0M - $2.34M = ~$4.66M remaining (historical)
Plus the §61 control battery and provenance/honesty rules.
"""

import pandas as pd
import pytest

from financials.nfp import (
    REPORTS,
    alternatives,
    build_all,
    campaign,
    funding_cliff,
    grants,
    load_settings,
    loc_interest,
    pledges,
    programs,
    scenarios,
    sensitivity,
    solutions,
)


@pytest.fixture(scope="module")
def s():
    return load_settings()


@pytest.fixture(scope="module")
def frames():
    return build_all()


# ---------------- prompt worked examples ----------------

def test_prompt_section22_probability_weighted_gap(s):
    p3 = programs(s).set_index("program_id").loc["P3"]
    assert p3["total_cost"] == 500000
    assert p3["earned_revenue"] == 200000
    assert p3["grants"] == 200000 and p3["grant_renewal_pct"] == 70
    assert p3["expected_funding"] == 390000       # 200 + 140 + 50
    assert p3["probability_weighted_gap"] == 110000


def test_prompt_section10_interest_avoided(s):
    alt5 = alternatives(s).set_index("alternative_id").loc["ALT-5"]
    assert alt5["net_cash_y1"] == 17500           # 250,000 x 7%
    assert alt5["is_recommended"] in (0, 1)       # numeric, not True/False
    assert "17,500" in alt5["note"]


def test_prompt_section34_loc_interest():
    assert loc_interest(1_000_000, 7.0, 6) == pytest.approx(35_000)


def test_prompt_section29_campaign_remaining(s):
    camp = campaign(s)
    remaining = camp[camp["item_id"] == "SUM-1"].iloc[0]
    assert remaining["amount"] == pytest.approx(4_660_000)
    assert "HISTORICAL" in remaining["note"]


def test_invest_alternative_future_value(s):
    alt3 = alternatives(s).set_index("alternative_id").loc["ALT-3"]
    net = (s["expected_investment_return_pct"]
           - s["investment_fees_pct"])            # 5.5%
    fv5 = s["available_capital"] * (1 + net / 100) ** 5
    assert f"{fv5:,.0f}" in alt3["note"]
    # annual mission distribution at the 4% spending rate
    assert alt3["net_cash_y1"] == pytest.approx(
        s["available_capital"] * s["spending_rate_pct"] / 100)


def test_status_quo_months_cash(s):
    alt4 = alternatives(s).set_index("alternative_id").loc["ALT-4"]
    assert alt4["months_cash_after"] == pytest.approx(
        s["unrestricted_liquid_cash"] / s["avg_monthly_operating_expense"],
        abs=0.01)


# ---------------- decision framework ----------------

def test_weights_must_total_100(monkeypatch, tmp_path):
    """The REAL guard in load_settings must fire on a bad settings file
    (the audit caught an earlier version of this test re-implementing
    the check inside pytest.raises instead of exercising it)."""
    import shutil

    import financials.nfp as nfp
    bad_dir = tmp_path / "nfp"
    shutil.copytree(nfp.NFP_DIR, bad_dir)
    f = bad_dir / "nfp_settings.csv"
    text = f.read_text(encoding="utf-8")
    assert "weight_mission,30," in text
    f.write_text(text.replace("weight_mission,30,", "weight_mission,40,"),
                 encoding="utf-8")
    monkeypatch.setattr(nfp, "NFP_DIR", bad_dir)
    with pytest.raises(ValueError, match="100"):
        nfp.load_settings()


def test_npv_function_pinned(s):
    """Audit finding: npv() was unpinned - an off-by-one discounting bug
    passed the whole suite. Pin it at both ends."""
    from financials.nfp import npv
    assert npv(7.0, [-100.0, 107.0]) == pytest.approx(0.0)
    alt5 = alternatives(s).set_index("alternative_id").loc["ALT-5"]
    assert alt5["npv_at_board_rate"] == pytest.approx(
        npv(5.0, [-250000.0] + [17500.0] * 5), abs=1.0)


def test_zero_denominator_guards(s):
    """Audit finding: the guards were dead code under fixture data and
    the matching control was hardcoded PASS. Feed a real zero row."""
    probe = pd.DataFrame([{
        "program_id": "PX", "program_name": "Empty program",
        "owner_role": "", "mission_category": "", "participants": 0,
        "capacity": 0, "earned_revenue": 0, "grants": 0,
        "restricted_contrib": 0, "unrestricted_contrib": 0,
        "sponsorship": 0, "largest_single_funder": 0, "personnel": 0,
        "direct_costs": 0, "allocated_overhead": 0, "capex": 0,
        "grant_renewal_pct": 0, "mission_score": 5, "risk_rating": 3,
        "value_class": "SYNTHETIC"}])
    row = programs(s, inputs=probe).iloc[0]
    for col in ["utilization_pct", "self_sufficiency_pct",
                "cost_per_participant", "funding_per_participant",
                "subsidy_per_participant", "grant_dependency_pct",
                "top_donor_dependency_pct", "restricted_funding_pct"]:
        assert row[col] == 0, col


def test_exactly_one_recommended_alternative(frames):
    alts = frames["nfp_alternatives"]
    assert alts["is_recommended"].sum() == 1
    assert set(alts["recommendation"]) <= {
        "RECOMMEND", "RECOMMEND WITH CONDITIONS", "PILOT", "DEFER",
        "DO NOT RECOMMEND"}
    # raw metrics are never hidden behind the composite
    for col in ["npv_at_board_rate", "five_year_cash",
                "mission_leverage_per_100k", "months_cash_after",
                "risk_rating_1to10"]:
        assert col in alts.columns


def test_new_program_gets_staged_pilot_language(frames):
    alt2 = frames["nfp_alternatives"].set_index("alternative_id").loc["ALT-2"]
    assert "Phase 1 pilot" in alt2["note"]
    assert alt2["recommendation"] == "PILOT"


# ---------------- program portfolio ----------------

def test_classification_matrix(frames):
    prog = frames["nfp_programs"].set_index("program_id")
    assert prog.loc["P1", "classification"] == "GROW"
    assert prog.loc["P7", "classification"] == \
        "MISSION-CRITICAL / INTENTIONALLY SUBSIDIZED"
    assert prog.loc["P7", "risk_flag"] == "FUNDING AT RISK"
    assert prog.loc["P4", "classification"] == \
        "REVIEW STRATEGIC FIT / CROSS-SUBSIDY POTENTIAL"
    assert prog.loc["P8", "classification"] == "REVIEW FOR CONSOLIDATION"
    assert set(prog["verdict"]) <= {"KEEP DOING", "CHANGE", "INVEST",
                                    "WATCH"}


def test_solutions_are_diagnostic_not_generic(frames):
    sols = frames["nfp_solutions"]
    assert len(sols) > 5
    # every suggestion carries its trigger diagnosis and a KPI
    assert (sols["diagnosis"].str.len() > 10).all()
    assert (sols["success_kpi"].str.len() > 0).all()
    # the high-mission deficit rule fires for P7
    assert (sols["subject_id"] == "P7").any()


# ---------------- grants honesty ----------------

def test_research_prospects_never_carry_amounts(frames):
    """Prospects (RESEARCH or UPCOMING) never carry a confirmed amount -
    the Phase-2 research pass added cycles/windows/URLs, not dollars."""
    g = frames["nfp_grants"]
    prospects = g[g["status"].isin(["RESEARCH", "UPCOMING"])]
    assert len(prospects) == 9
    assert (prospects["status"] == "UPCOMING").sum() == 3
    assert (prospects["amount_display"] == "RESEARCH REQUIRED").all()
    assert (prospects["grant_fit"] == "RESEARCH REQUIRED").all()
    # every researched prospect carries provenance for the click-through
    researched = prospects[prospects["date_verified"] != ""]
    assert (researched["url"].str.startswith("http")).all()
    assert (researched["confidence"].isin(["LOW", "MEDIUM"])).all()


def test_chai_house_amount_not_invented(frames):
    g = frames["nfp_grants"]
    chai = g[g["funder"] == "Chai House, Inc."].iloc[0]
    assert chai["amount_display"] == "RESEARCH REQUIRED"
    assert "AMOUNT NOT PUBLICLY CONFIRMED" in chai["note"]
    assert chai["value_class"] == "PUBLIC_RESEARCH"


def test_funding_cliff_windows(s):
    cliff = funding_cliff(s)
    windows = cliff.set_index("grant_id")["window"]
    assert windows["G-SYN-3"] == "0-3 MONTHS"     # ends 2026-10-31
    assert windows["G-SYN-2"] == "0-3 MONTHS"     # ends 2026-11-30
    assert windows["G-SYN-4"] == "6-12 MONTHS"    # ends 2027-08-31
    scen = cliff[cliff["window"] == "SCENARIO"]
    assert len(scen) == 7                          # 5 renewal + 2 delay
    zero = scen[scen["grant"] == "0% RENEWAL"].iloc[0]
    assert zero["at_risk_funding"] == zero["amount"]


# ---------------- campaign / financing controls ----------------

def test_sources_equal_uses(frames):
    camp = frames["nfp_campaign"]
    assert camp[camp["section"] == "SOURCES"]["amount"].sum() == \
        camp[camp["section"] == "USES"]["amount"].sum() == 7_000_000


def test_pledges_reconcile_and_expected_cash(frames):
    plg = frames["nfp_pledges"]
    assert ((plg["collected"] + plg["outstanding"])
            == plg["amount"]).all()
    assert plg["expected_pledge_cash"].sum() == 675_000


def test_project_cash_rolls_and_never_negative(frames):
    proj = frames["nfp_project_cash"]
    for _, r in proj.iterrows():
        assert (r["beginning_cash"] + r["campaign_receipts"]
                + r["debt_draws"] - r["project_spending"]
                - r["financing_costs"]) == pytest.approx(r["ending_cash"])
    assert (proj["warning"] == "").all()


def test_no_current_debt_from_historical_data(frames):
    dr = frames["nfp_debt_reserves"]
    hist = dr[dr["status"] == "HISTORICAL"]
    assert (hist["note"].str.contains("HISTORICAL")).all()
    loc_row = dr[dr["status"] == "RESEARCH"].iloc[0]
    assert "NO CURRENT LOC CONFIRMED" in loc_row["note"]
    dscr = dr[dr["item"] == "DSCR"].iloc[0]
    assert "No current debt confirmed" in dscr["note"]


# ---------------- scenarios / sensitivity ----------------

def test_scenarios_flow_through(frames):
    scen = frames["nfp_scenarios"].set_index(["tab", "scenario"])
    base = scen.loc[("ORG", "BASE")]
    down = scen.loc[("ORG", "DOWNSIDE")]
    stress = scen.loc[("ORG", "STRESS")]
    assert down["funding_gap"] < base["funding_gap"]
    assert stress["funding_gap"] < down["funding_gap"]
    assert stress["months_cash_end"] < base["months_cash_end"]


def test_sensitivity_ranked_by_swing(frames):
    sens = frames["nfp_sensitivity"]
    swings = sens["swing"].tolist()
    assert swings == sorted(swings, reverse=True)
    assert sens.iloc[0]["rank"] == 1


# ---------------- controls & committed exports ----------------

def test_all_controls_pass(frames):
    ctrl = frames["nfp_controls"]
    assert len(ctrl) == 10
    assert (ctrl["status"] == "PASS").all(), \
        ctrl[ctrl["status"] != "PASS"]["control"].tolist()


def test_exec_board_answers_the_ten_questions(frames):
    execb = frames["nfp_exec_board"]
    q = execb[execb["section"] == "QUESTIONS"]
    assert len(q) == 10
    assert execb[execb["section"] == "CFO_SCRIPT"]["value_text"].iloc[0] \
        .startswith("We evaluated five uses")
    assert (execb["section"] == "DEBATE").any()
    assert (execb["section"] == "CONTROLS").sum() == 10


def assert_export_values_match(df, path):
    """Value-level committed-export check (audit finding: the earlier
    shape-only comparison let any same-shape formula regression through).
    Round-trip the fresh frame through CSV so blank-vs-NaN and dtype
    differences compare like-for-like."""
    import io
    roundtrip = pd.read_csv(io.StringIO(df.to_csv(index=False)))
    committed = pd.read_csv(path)
    pd.testing.assert_frame_equal(committed, roundtrip, check_dtype=False)


def test_public_financials_register(frames):
    """Every public-financials row is PUBLIC_RESEARCH with a working-
    format URL and confidence; the similarly named Jewish Family
    Services org is present only as an explicit do-not-conflate row."""
    pf = frames["nfp_public_financials"]
    assert (pf["value_class"] == "PUBLIC_RESEARCH").all()
    assert (pf["url"].str.startswith("http")).all()
    # web-searched rows keep the MEDIUM ceiling; HIGH is earned only by
    # rows sourced from documents the owner provided (the filed 990s)
    high = pf[pf["confidence"] == "HIGH"]
    assert high["source"].str.contains("Owner-provided").all()
    assert (pf["confidence"].isin(["LOW", "MEDIUM", "HIGH"])).all()
    jsv = pf[pf["organization"] == "Jewish Silicon Valley"]
    assert (jsv["ein"].astype(str) == "94-2222989").all()
    other = pf[pf["ein"].astype(str) == "94-2536452"]
    assert len(other) == 1
    assert "DIFFERENT ORGANIZATION" in other.iloc[0]["note"]


def test_ratio_playbook(frames):
    """18 matrix areas verbatim; every ratio row carries a source type,
    and 990-sourced rows carry a part/line reference."""
    matrix = frames["nfp_role_matrix"]
    assert len(matrix) == 18
    assert matrix["area"].iloc[0] == "Annual campaign"
    assert matrix["area"].iloc[-1] == "Board decisions"
    dict_ = frames["nfp_ratio_990"]
    allowed = {"FORM 990", "990 SCHEDULE", "990 PARTIAL",
               "INTERNAL DATA", "MODULE"}
    assert set(dict_["source_type"]) <= allowed
    form_rows = dict_[dict_["source_type"].isin(
        ["FORM 990", "990 SCHEDULE", "990 PARTIAL"])]
    assert (form_rows["form_990_ref"].str.len() > 3).all()
    assert (dict_["worked_example"].str.len() > 10).all()
    assert (dict_["how_to_use"].str.len() > 10).all()
    # every matrix area appears in the dictionary
    dict_areas = set(dict_["area"])
    for area in ["Annual campaign", "Grants", "Major gifts",
                 "Restricted gifts", "Capital campaign", "Endowment",
                 "Existing programs", "New programs", "Membership",
                 "Facilities", "Staffing", "Technology",
                 "Cash/liquidity", "Budget", "Risk", "Board decisions"]:
        assert area in dict_areas, area


def test_survey_findings_and_alignment(frames):
    """Findings are PUBLIC_RESEARCH with URLs; the quantified findings
    are quoted intact; alignments reference only real programs and
    real finding ids, with live stats enriched in."""
    f = frames["nfp_survey_findings"]
    assert (f["value_class"] == "PUBLIC_RESEARCH").all()
    assert (f["url"].str.startswith("http")).all()
    mental = f[f["finding_id"] == "F6"].iloc[0]["finding"]
    assert "40%" in mental and "5%" in mental
    aid = f[f["finding_id"] == "F5"].iloc[0]["finding"]
    assert "57%" in aid
    assert "RESEARCH REQUIRED" in f[f["finding_id"] == "F10"].iloc[0][
        "finding"]

    a = frames["nfp_survey_alignment"]
    finding_ids = set(f["finding_id"])
    prog_ids = set(frames["nfp_programs"]["program_id"])
    for _, r in a.iterrows():
        assert set(str(r["based_on_findings"]).split(";")) <= finding_ids
        assert set(str(r["program_ids"]).split(";")) <= prog_ids
        assert "util" in r["aligned_programs"]     # live stats enriched
    assert set(a["impact_potential"]) <= {"HIGH", "MEDIUM-HIGH", "MEDIUM"}
    assert (a["value_class"] == "MANAGEMENT ASSUMPTION").all()


def test_time_dimension_layers(frames, s):
    """The over-time exports reconcile and tell honest stories."""
    g = frames["nfp_gap_history"]
    assert len(g) == 48
    # each column rounds independently, so allow 1-dollar slack
    assert ((g["funding"] - g["cost"] - g["gap"]).abs() <= 1.5).all()
    assert g["cash_balance"].iloc[-1] == s["unrestricted_liquid_cash"]
    # the seeded winter 2022-23 liquidity episode is visible
    breaches = g[g["breach_flag"] == "BREACH"]
    assert len(breaches) >= 1
    assert (g["months_cash"] > 0).all()

    sm = frames["nfp_support_map"]
    parts = (sm["funded_by_earned"] + sm["funded_by_grants"]
             + sm["funded_by_restricted_gifts"]
             + sm["funded_by_sponsorship"] + sm["funded_by_unrestricted"])
    assert (parts == sm["expense_total"]).all()

    tl = frames["nfp_alt_timeline"].set_index("year")
    alts = frames["nfp_alternatives"].set_index("alternative_id")
    assert tl.loc[5, "expand_program"] == pytest.approx(
        -alts.loc["ALT-1", "capital_required"]
        + alts.loc["ALT-1", "five_year_cash"], abs=2)
    # invest shows net position - strictly rising, never a fake loss
    assert (tl["invest_capital"].diff().dropna() > 0).all()
    assert tl.loc[0, "invest_capital"] == 0

    ps = frames["nfp_pledge_schedule"]
    assert ps["expected_collections"].sum() == pytest.approx(
        frames["nfp_pledges"]["expected_pledge_cash"].sum())

    mix = frames["nfp_funding_mix"]
    assert set(mix["recurrence_class"]) == {"RECURRING", "ONE-TIME",
                                            "LONG-TERM FORECAST"}
    rec = mix[mix["recurrence_class"] == "RECURRING"]["amount"].sum()
    assert rec > 4_000_000                     # earned + campaign + more

    rv = frames["nfp_ratio_values"]
    months = rv[rv["ratio"] == "Months cash on hand"].iloc[0]
    assert float(months["value"]) == pytest.approx(4.0, abs=0.01)


def test_investments_and_rentals(frames):
    """Tab-16 data: every scenario is a labeled PROPOSAL; rentals and
    pools carry provenance labels."""
    sc = frames["nfp_invest_scenarios"]
    assert (sc["status"] == "PROPOSAL").all()
    assert (sc["value_class"] == "ASSUMPTION").all()
    assert len(sc) == 4
    r = frames["nfp_rentals"]
    assert (r["value_class"] == "SYNTHETIC").all()
    assert r["annual_income"].sum() == 550000
    pools = frames["nfp_investment_pools"]
    assert set(pools["value_class"]) <= {"PUBLIC_RESEARCH", "SYNTHETIC",
                                         "ASSUMPTION"}
    st = frames["nfp_initiative_status"]
    assert (st["status"] == "NOT STARTED - PROPOSED").all()
    assert (st["note"].str.contains("NOT confirm")).all()


def test_committed_exports_match_fresh_build(frames):
    for name, df in frames.items():
        assert_export_values_match(df, REPORTS / f"{name}.csv")


def test_990_actuals_real_figures_and_honesty():
    """The only export whose dollars describe the real organization -
    now read directly from the five filed 990s the owner provided.
    Pins the filed figures (a silent change to a real number must fail
    loudly) and enforces the honesty rules."""
    from financials.nfp import actuals_990
    a = actuals_990()
    assert (a["value_class"] == "PUBLIC_RESEARCH").all()
    assert (a["confidence"] == "HIGH").all()
    assert a["url"].str.startswith("https://").all()
    assert set(a["basis"]) == {"FILED", "REPORTED (PRE-MERGER APJCC)",
                               "DERIVED"}

    def amt(fy, item):
        row = a[(a["fiscal_year"] == fy) & (a["line_item"] == item)]
        return int(row.iloc[0]["amount"])

    # FILED pins across the five years
    assert amt("FY2021", "total_revenue") == 7792094
    assert amt("FY2022", "total_revenue") == 15100113
    assert amt("FY2023", "total_revenue") == 10172090
    assert amt("FY2024", "total_revenue") == 12604759
    assert amt("FY2025", "total_revenue") == 14208155
    assert amt("FY2025", "total_expenses") == 13539303
    assert amt("FY2025", "surplus_deficit") == 668852
    assert amt("FY2025", "net_assets_end") == 17716051
    assert amt("FY2024", "total_assets_end") == 20879276  # exact now
    assert amt("FY2025", "investments_securities_end") == 18951917
    assert amt("FY2025", "cash_end") == 515092
    # the filings' own arithmetic must hold in our copy, every year
    for fy in ("FY2021", "FY2022", "FY2023", "FY2024", "FY2025"):
        assert (amt(fy, "contributions_and_grants")
                + amt(fy, "program_service_revenue")
                + amt(fy, "investment_income")
                + amt(fy, "other_revenue")) == amt(fy, "total_revenue")
        assert (amt(fy, "total_revenue") - amt(fy, "total_expenses")
                == amt(fy, "surplus_deficit"))
        assert (amt(fy, "total_assets_end")
                - amt(fy, "total_liabilities_end")
                == amt(fy, "net_assets_end"))
        # Part IX functional expense columns cross-foot to the total
        assert (amt(fy, "program_expenses")
                + amt(fy, "mgmt_general_expenses")
                + amt(fy, "fundraising_expenses")
                == amt(fy, "total_expenses"))
    # nothing blank, nothing estimated
    assert (a["amount"] != "").all()
    # pre-merger context is present but loudly flagged
    pre = a[a["fiscal_year"] == "FY2020"]
    assert len(pre) == 4
    assert pre["basis"].eq("REPORTED (PRE-MERGER APJCC)").all()
    assert pre["note"].str.contains("NOT comparable").all()
    # derived rows are net-assets deltas only, formula shown
    d = a[a["basis"] == "DERIVED"]
    assert len(d) == 4
    assert (d["line_item"] == "net_assets_change").all()


def test_990_ratio_actuals_computed_from_filed_only():
    from financials.nfp import actuals_990, ratio_actuals_990
    r = ratio_actuals_990(actuals_990())
    assert len(r) == 122
    assert (r["value_class"] == "PUBLIC_RESEARCH").all()
    assert (r["confidence"] == "HIGH").all()
    assert (r["value"] != "").all()          # nothing unresearched left
    assert r["formula_990"].str.len().gt(5).all()
    # pre-merger year gets no ratios; growth starts FY2022
    assert not r["fiscal_year"].str.contains("2020").any()
    assert len(r[r["ratio_kind"] == "revenue_growth_pct"]) == 4

    def val(kind, fy):
        return float(r[(r["ratio_kind"] == kind)
                       & (r["fiscal_year"] == fy)].iloc[0]["value"])

    assert val("op_margin_pct", "FY2023") == pytest.approx(-10.7885,
                                                           abs=0.001)
    assert val("op_margin_pct", "FY2025") == pytest.approx(4.7075,
                                                           abs=0.001)
    assert val("expense_coverage", "FY2024") == pytest.approx(1.0056,
                                                              abs=0.001)
    assert val("program_reliance_pct", "FY2025") == pytest.approx(
        59.87, abs=0.01)
    assert val("revenue_growth_pct", "FY2025") == pytest.approx(
        12.72, abs=0.01)
    # the real liquidity finding: cash fell through the 3.0-month
    # policy floor and collapsed in FY2025 as money moved to the
    # portfolio - the module must show it, not smooth it
    assert val("months_cash_on_hand", "FY2021") == pytest.approx(
        5.5692, abs=0.001)
    assert val("months_cash_on_hand", "FY2024") == pytest.approx(
        3.0852, abs=0.001)
    assert val("months_cash_on_hand", "FY2025") == pytest.approx(
        0.4565, abs=0.001)
    assert val("months_cash_on_hand", "FY2025") < 3.0
    # Part IX ratios vs the sector benchmark
    assert val("program_expense_ratio_pct", "FY2025") == pytest.approx(
        81.9894, abs=0.001)
    assert val("overhead_ratio_pct", "FY2025") == pytest.approx(
        18.0106, abs=0.001)


def test_990_targets_and_benchmarks():
    """Every reference line is either an internal policy (MANAGEMENT
    ASSUMPTION) or a sourced sector benchmark (PUBLIC_RESEARCH) - never
    an invented 'industry norm' - and the MEETS/MISSES verdict is
    computed, not asserted by hand."""
    from financials.nfp import actuals_990, ratio_actuals_990
    r = ratio_actuals_990(actuals_990())
    with_t = r[r["target_value"] != ""]
    assert len(with_t) == 70                    # 14 targeted kinds x 5 yrs
    assert set(with_t["target_class"]) == {"MANAGEMENT ASSUMPTION",
                                           "PUBLIC_RESEARCH"}
    bbb = with_t[with_t["target_class"] == "PUBLIC_RESEARCH"]
    assert bbb["target_label"].str.contains(
        "BBB|BENCHMARK|SECTOR|RULE", regex=True).all()
    assert (r[r["target_value"] == ""]["vs_target"] == "").all()

    def verdict(kind, fy):
        return r[(r["ratio_kind"] == kind)
                 & (r["fiscal_year"] == fy)].iloc[0]["vs_target"]

    # program ratio beats the BBB 65% standard in every filed year
    for fy in ("FY2021", "FY2022", "FY2023", "FY2024", "FY2025"):
        assert verdict("program_expense_ratio_pct", fy) == "MEETS"
        assert verdict("overhead_ratio_pct", fy) == "MEETS"
    # months of cash: fine until the FY2025 collapse
    assert verdict("months_cash_on_hand", "FY2024") == "MEETS"
    assert verdict("months_cash_on_hand", "FY2025") == "MISSES"
    assert verdict("op_margin_pct", "FY2023") == "MISSES"
    assert verdict("op_margin_pct", "FY2025") == "MEETS"


def test_fin_statements_are_the_filings_own_numbers(frames):
    """Tab 17: every subtotal must be the filing's own figure and the
    statement identities must hold in every year."""
    fs = frames["nfp_fin_statements"].set_index("line_label")
    years = [f"fy{y}" for y in range(2021, 2026)]
    for y in years:
        rev = [fs.loc[l, y] for l in ("Contributions & grants",
               "Program service revenue", "Investment income",
               "Other revenue")]
        assert sum(rev) == fs.loc["TOTAL REVENUE", y]
        exp = [fs.loc[l, y] for l in ("Program services",
               "Management & general", "Fundraising")]
        assert sum(exp) == fs.loc["TOTAL EXPENSES", y]
        assert (fs.loc["TOTAL REVENUE", y] - fs.loc["TOTAL EXPENSES", y]
                == fs.loc["CHANGE IN NET ASSETS (surplus/deficit)", y])
        na = (fs.loc["Without donor restrictions", y]
              + fs.loc["With donor restrictions", y])
        assert na == fs.loc["TOTAL NET ASSETS", y]
        assets = fs[fs["section"] == "ASSETS"]
        listed = assets[~assets.index.str.startswith("TOTAL")][y].sum()
        assert listed == fs.loc["TOTAL ASSETS", y]
    # FY2025 pins incl. the new filed lines 27/28
    assert fs.loc["Without donor restrictions", "fy2025"] == 5761112
    assert fs.loc["With donor restrictions", "fy2025"] == 11954939
    # statement YoY variance columns
    assert fs.loc["TOTAL REVENUE", "var_25v24"] == 1603396
    assert float(fs.loc["TOTAL REVENUE", "var_25v24_pct"]) == \
        pytest.approx(12.7, abs=0.1)
    assert fs.loc["Cash (non-interest-bearing)", "var_25v24"] == -2595276


def test_cfo_review_and_kpis(frames):
    cr = frames["nfp_cfo_review"]
    assert len(cr) == 10
    assert (cr["cfo_reading"].str.len() > 20).all()
    assert (cr["trend_fy2021_fy2025"].str.count(">") == 4).all()
    k = frames["nfp_990_kpis"].set_index("kpi")
    assert len(k) == 12
    assert (k["description"].str.len() > 100).all()
    assert (k["benchmark_or_policy"].str.len() > 10).all()
    assert k.loc["Months of Cash on Hand", "vs_target"] == "MISSES"
    assert k.loc["Program Expense Ratio", "vs_target"] == "MEETS"
    assert float(k.loc["Operating Reserve (months)",
                       "fy2025_value"]) == pytest.approx(5.1061,
                                                         abs=0.001)
    conc = k.loc["Revenue Concentration"]
    assert conc["vs_target"] == "MISSES"
    assert "many" in k.loc["Revenue Concentration", "description"]


def test_990_yoy_and_rules(frames):
    """Tab-17 YoY and the tab-18 rules register - filed inputs only,
    honest verdicts."""
    y = frames["nfp_990_yoy"]
    assert len(y) == 42                     # 20 statement lines + 22 ratios
    rev = y[y["line_label"] == "TOTAL REVENUE"].iloc[0]
    assert rev["fy2024"] == 12604759 and rev["fy2025"] == 14208155
    assert rev["variance"] == 1603396
    assert float(rev["variance_pct"]) == pytest.approx(12.7, abs=0.1)
    ru = frames["nfp_990_rules"].set_index("rule")
    assert len(ru) == 8
    assert (ru["description"].str.len() > 80).all()
    assert ru.loc["IRS 33 1/3% public support test", "vs_rule"] == "MEETS"
    assert "74.89" in ru.loc["IRS 33 1/3% public support test",
                             "actual_fy2025"]
    assert ru.loc["IRS 5% payout rule", "vs_rule"] == "N/A"
    assert ru.loc["Current ratio >= 1.0", "vs_rule"] == "MISSES"
    assert (ru.loc["Donor concentration Pareto (80/20)", "vs_rule"]
            == "RESEARCH REQUIRED")
    # the YoY ratio rows show their arithmetic with filed amounts
    ratios = y[y["section"] == "RATIOS"]
    assert (ratios["math_fy2025"].str.len() > 10).all()
    om = ratios[ratios["line_label"] == "Operating margin %"].iloc[0]
    assert "total revenue 14,208,155" in om["math_fy2025"]
    assert (ratios["variance_pct"] != "").all()
    assert (y[y["section"] != "RATIOS"]["math_fy2025"] == "").all()
    # CFO review carries years-as-columns, target and variances
    cr2 = frames["nfp_cfo_review"].set_index("ratio_kind")
    mc = cr2.loc["months_cash_on_hand"]
    assert float(mc["fy2021"]) == pytest.approx(5.5692, abs=0.001)
    assert float(mc["target_value"]) == 3.0
    assert float(mc["variance_to_target"]) == pytest.approx(-2.54,
                                                            abs=0.01)
    assert float(mc["yoy_variance"]) == pytest.approx(-2.63, abs=0.01)
    k2 = frames["nfp_990_kpis"].set_index("kpi")
    row = k2.loc["Months of Cash on Hand"]
    assert float(row["variance_to_target"]) == pytest.approx(-2.54,
                                                             abs=0.01)
    assert float(row["fy2024_value"]) == pytest.approx(3.0852,
                                                       abs=0.001)


def test_new_ratio_kinds_pinned():
    from financials.nfp import actuals_990, ratio_actuals_990
    r = ratio_actuals_990(actuals_990())

    def val(kind, fy):
        return float(r[(r["ratio_kind"] == kind)
                       & (r["fiscal_year"] == fy)].iloc[0]["value"])

    assert val("public_support_pct", "FY2025") == pytest.approx(74.89)
    assert val("public_support_pct", "FY2021") == pytest.approx(74.16)
    assert val("current_ratio", "FY2025") == pytest.approx(0.7109,
                                                           abs=0.001)
    assert val("current_ratio", "FY2024") == pytest.approx(1.882,
                                                           abs=0.001)
    assert val("days_cash_on_hand", "FY2025") == pytest.approx(
        13.886, abs=0.01)
    assert val("mgmt_general_pct_exp", "FY2025") == pytest.approx(
        13.26, abs=0.01)
    assert val("fundraise_dollars_per_dollar", "FY2025") == \
        pytest.approx(7.88, abs=0.01)
    assert val("leverage_ratio", "FY2025") == pytest.approx(0.2711,
                                                            abs=0.001)
    assert val("savings_indicator_pct", "FY2025") == pytest.approx(
        4.94, abs=0.01)
    assert val("roa_pct", "FY2025") == pytest.approx(2.97, abs=0.01)
    # audience column present on every KPI
    from financials.nfp import kpis_990
    k = kpis_990(r)
    assert (k["typical_audience"].str.len() > 3).all()
    assert k[k["kpi"] == "IRS Public Support %"].iloc[0][
        "typical_audience"].startswith("BOARD")

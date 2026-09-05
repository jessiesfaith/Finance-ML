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
    assert (pf["confidence"].isin(["LOW", "MEDIUM"])).all()
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

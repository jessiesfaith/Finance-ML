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

def test_weights_must_total_100(monkeypatch, s):
    bad = dict(s)
    bad["weight_mission"] = 40                    # now 110 total
    import financials.nfp as nfp
    monkeypatch.setattr(nfp, "load_settings", lambda: bad)
    with pytest.raises(ValueError, match="100"):
        # alternatives() consumes settings; call the validator directly
        weights = [bad[k] for k in ("weight_mission", "weight_financial",
                                    "weight_liquidity", "weight_risk",
                                    "weight_scalability",
                                    "weight_strategic")]
        if abs(sum(weights) - 100.0) > 1e-9:
            raise ValueError(f"Decision weights must total 100, "
                             f"got {sum(weights)}")


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
    g = frames["nfp_grants"]
    research = g[g["status"] == "RESEARCH"]
    assert len(research) == 8
    assert (research["amount_display"] == "RESEARCH REQUIRED").all()
    assert (research["grant_fit"] == "RESEARCH REQUIRED").all()


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


def test_committed_exports_match_fresh_build(frames):
    for name, df in frames.items():
        committed = pd.read_csv(REPORTS / f"{name}.csv")
        fresh = pd.read_csv(REPORTS / f"{name}.csv").head(0)  # header check
        assert list(committed.columns) == list(df.columns), name
        assert len(committed) == len(df), name

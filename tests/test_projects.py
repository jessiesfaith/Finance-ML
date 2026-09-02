"""
Tests for the project-appraisal engine (Page 6).

The fixture's automation project is recomputed BY HAND for the Base
scenario - same formulas, written out longhand - and the engine must
agree. Then the properties: NPV(IRR) = 0 by definition, the fixture's
expansion project fails its cash tests while passing ROIC (=> REVIEW,
the tests disagree and NPV rules), validation fails loudly, and the
committed export matches a fresh rebuild.
"""

import pandas as pd
import pytest

from financials.loader import ClientFSValidationError
from financials.projects import (
    DEFAULT_OUTPUT,
    OUTPUT_COLUMNS,
    build_project_appraisal,
    load_projects,
    load_rates,
)


@pytest.fixture(scope="module")
def appraisal():
    tables, issues = load_projects(strict=True)
    assert issues == []
    rates = load_rates()
    return build_project_appraisal(
        tables["project_master"], tables["project_assumptions"], rates)


def pick(frame, project, scenario):
    rows = frame[(frame["project_id"] == project)
                 & (frame["scenario"] == scenario)]
    assert len(rows) == 1
    return rows.iloc[0]


def test_schema_and_coverage(appraisal):
    assert list(appraisal.columns) == OUTPUT_COLUMNS
    assert len(appraisal) == 15                  # 5 options x 3 scenarios
    assert set(appraisal["value_class"]) == {"CALCULATED"}


def test_automation_project_recomputed_by_hand(appraisal):
    """PROJ-002, Base: savings-only, no NWC, no maintenance capex."""
    row = pick(appraisal, "PROJ-002", "Base")
    tax, hurdle = row["tax_rate_pct"] / 100, row["hurdle_rate_pct"] / 100
    da = 45.0 / 5
    flows = []
    for t in range(1, 6):
        savings = 14.0 * 1.02 ** (t - 1)
        nopat = (savings - da) * (1 - tax)
        flows.append(nopat + da)
    npv = -45.0 + sum(f / (1 + hurdle) ** t for t, f in enumerate(flows, 1))
    assert row["npv_at_hurdle"] == pytest.approx(npv, abs=1e-3)
    assert row["ufcf_y1"] == pytest.approx(flows[0], abs=1e-3)
    assert row["ufcf_y5"] == pytest.approx(flows[4], abs=1e-3)
    assert row["recommendation"] == "APPROVE"


def test_irr_is_the_rate_where_npv_is_zero(appraisal):
    row = pick(appraisal, "PROJ-002", "Base")
    irr = row["irr_pct"] / 100
    flows = [row[f"ufcf_y{t}"] for t in range(1, 6)]
    npv_at_irr = -45.0 + sum(
        f / (1 + irr) ** t for t, f in enumerate(flows, 1))
    assert npv_at_irr == pytest.approx(0.0, abs=1e-4)


def test_expansion_project_shows_the_tests_disagreeing(appraisal):
    """Accounting return above WACC, cash tests failing -> REVIEW."""
    row = pick(appraisal, "PROJ-001", "Base")
    assert row["npv_at_hurdle"] < 0
    assert row["irr_pct"] < row["hurdle_rate_pct"]
    assert row["incr_roic_pct"] > row["wacc_pct"]
    assert (row["npv_test"], row["irr_test"], row["roic_test"]) == (
        "FAIL", "FAIL", "PASS")
    assert row["recommendation"] == "REVIEW"


def test_final_year_recovers_working_capital(appraisal):
    """PROJ-001's year-5 flow jumps by the accumulated NWC balance."""
    row = pick(appraisal, "PROJ-001", "Base")
    assert row["ufcf_y5"] > row["ufcf_y4"] + 10   # 17.5 of NWC comes back


def test_bad_intake_fails_loudly(tmp_path):
    master = pd.read_csv("data/projects/project_master.csv")
    master.loc[0, "horizon_years"] = 9            # > MAX_HORIZON
    master.to_csv(tmp_path / "project_master.csv", index=False)
    assumptions = pd.read_csv("data/projects/project_assumptions.csv")
    assumptions.loc[0, "project_id"] = "PROJ-999"  # unknown project
    assumptions.to_csv(tmp_path / "project_assumptions.csv", index=False)

    with pytest.raises(ClientFSValidationError) as excinfo:
        load_projects(projects_dir=tmp_path, strict=True)
    message = str(excinfo.value)
    assert "horizon_out_of_range" in message
    assert "unknown_project" in message


def test_committed_export_matches_a_fresh_rebuild(appraisal):
    from conftest import assert_matches_committed
    assert_matches_committed(appraisal, DEFAULT_OUTPUT)


def test_new_product_line_approves_across_scenarios(appraisal):
    rows = appraisal[appraisal["project_id"] == "PROJ-003"]
    assert set(rows["recommendation"]) == {"APPROVE"}
    assert (rows["npv_at_hurdle"] > 0).all()


def test_debt_paydown_irr_is_exactly_the_after_tax_cost_of_debt(appraisal):
    """
    Retiring debt at par = buying back your own bond: coupon saved each
    year, capacity restored at horizon, so IRR == Kd x (1 - tax). It
    reads REJECT at the equity hurdle by construction - right
    arithmetic, wrong ruler for a risk-free use of cash; the page
    carries that caveat.
    """
    from financials.projects import load_rates
    rates = load_rates().set_index("scenario")
    rows = appraisal[appraisal["project_id"] == "PROJ-004"]
    assert set(rows["recommendation"]) == {"REJECT"}
    for _, row in rows.iterrows():
        r = rates.loc[row["scenario"]]
        expected = float(r["cost_of_debt_pct"]) * (1 - float(r["tax_rate_pct"]) / 100)
        assert row["irr_pct"] == pytest.approx(expected, abs=1e-2)
        assert row["incr_roic_pct"] == pytest.approx(expected, abs=1e-2)



# ------------------------------------------------
# OPTION SENSITIVITY (the deal tables under Step 5)
# ------------------------------------------------

@pytest.fixture(scope="module")
def sensitivity():
    from financials.projects import build_option_sensitivity
    tables, _ = load_projects(strict=True)
    return build_option_sensitivity(
        tables["project_master"], tables["project_assumptions"], load_rates())


def test_grid_and_verdict_shape(sensitivity):
    from financials.projects import (FLOWS_DELIVERED_PCT, SENSITIVITY_RATES,
                                     rate_column)
    grid, verdicts = sensitivity
    assert list(grid.columns) == (["project_id", "flows_delivered_pct"]
                                  + [rate_column(r) for r in SENSITIVITY_RATES])
    assert len(grid) == 5 * len(FLOWS_DELIVERED_PCT)
    assert len(verdicts) == 5
    assert set(verdicts.columns[2:]) == {
        "at_" + f"{r:.0f}pct" for r in SENSITIVITY_RATES}


def test_paydown_breaks_even_at_its_own_floor(sensitivity):
    """NPV ~ 0 at 5% because the paydown's IRR IS ~4.9% - the floor."""
    grid, _ = sensitivity
    row = grid[(grid["project_id"] == "PROJ-004")
               & (grid["flows_delivered_pct"] == 100)].iloc[0]
    assert abs(row["npv_at_5pct"]) < 1.0
    paydown = grid[grid["project_id"] == "PROJ-004"]
    assert paydown["npv_at_9pct"].nunique() == 1   # contractual: rows equal


def test_grid_monotonicity(sensitivity):
    from financials.projects import SENSITIVITY_RATES, rate_column
    grid, _ = sensitivity
    cols = [rate_column(r) for r in SENSITIVITY_RATES]
    for _, row in grid.iterrows():
        values = [row[c] for c in cols]
        assert values == sorted(values, reverse=True)   # NPV falls with rate
    for pid in ("PROJ-001", "PROJ-002", "PROJ-003"):
        block = grid[grid["project_id"] == pid].sort_values(
            "flows_delivered_pct")
        assert block["npv_at_9pct"].is_monotonic_increasing


def test_verdict_strip_matches_the_grid(sensitivity):
    from financials.projects import SENSITIVITY_RATES, rate_column
    grid, verdicts = sensitivity
    for _, verdict in verdicts.iterrows():
        base = grid[(grid["project_id"] == verdict["project_id"])
                    & (grid["flows_delivered_pct"] == 100)].iloc[0]
        for rate in SENSITIVITY_RATES:
            expected = "APPROVE" if base[rate_column(rate)] > 0 else "REJECT"
            assert verdict["at_" + f"{rate:.0f}pct"] == expected


def test_committed_sensitivity_matches_a_fresh_rebuild(sensitivity):
    from financials.projects import SENSITIVITY_OUTPUT, VERDICT_STRIP_OUTPUT
    from conftest import assert_matches_committed
    grid, verdicts = sensitivity
    assert_matches_committed(grid, SENSITIVITY_OUTPUT)
    assert_matches_committed(verdicts, VERDICT_STRIP_OUTPUT)


# ------------------------------------------------
# OPTION SIZING ("how much?" scenarios)
# ------------------------------------------------

@pytest.fixture(scope="module")
def sizing():
    from financials.projects import build_option_sizing
    tables, _ = load_projects(strict=True)
    return build_option_sizing(
        tables["project_master"], tables["project_assumptions"], load_rates())


def test_sizing_shape_and_verdicts(sizing):
    from financials.projects import AMOUNT_PCTS, SIZING_COLUMNS
    assert list(sizing.columns) == SIZING_COLUMNS
    assert len(sizing) == 5 * len(AMOUNT_PCTS)
    assert ((sizing["npv_at_hurdle"] > 0)
            == (sizing["verdict"] == "APPROVE")).all()


def test_acquisition_price_scenarios_flows_stay_fixed(sizing):
    """The amount is the PRICE: NPV falls dollar-for-dollar as it rises."""
    acq = sizing[sizing["project_id"] == "PROJ-005"].sort_values("amount_pct")
    assert acq["npv_at_hurdle"].is_monotonic_decreasing
    # price down $37.5 (150->112.5) lifts NPV by exactly that amount
    npv = acq.set_index("amount_pct")["npv_at_hurdle"]
    assert npv[75] - npv[100] == pytest.approx(37.5, abs=0.02)
    assert npv[75] > 0                    # the deal works at a lower price


def test_paydown_scales_linearly(sizing):
    pay = sizing[sizing["project_id"] == "PROJ-004"].set_index("amount_pct")
    assert pay.loc[150, "npv_at_hurdle"] == pytest.approx(
        1.5 * pay.loc[100, "npv_at_hurdle"], rel=1e-6)


def test_product_line_scales_proportionally(sizing):
    pl = sizing[sizing["project_id"] == "PROJ-003"].set_index("amount_pct")
    assert pl.loc[50, "npv_at_hurdle"] == pytest.approx(
        0.5 * pl.loc[100, "npv_at_hurdle"], rel=1e-6)


def test_capacity_share_uses_headroom_plus_cash(sizing):
    from financials.projects import funding_capacity
    capacity = funding_capacity()
    row = sizing[(sizing["project_id"] == "PROJ-001")
                 & (sizing["amount_pct"] == 100)].iloc[0]
    assert row["pct_of_funding_capacity"] == pytest.approx(
        100 * 120.0 / capacity, abs=0.05)


def test_committed_sizing_matches_a_fresh_rebuild(sizing):
    from financials.projects import SIZING_OUTPUT
    from conftest import assert_matches_committed
    assert_matches_committed(sizing, SIZING_OUTPUT)

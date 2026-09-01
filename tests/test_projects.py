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
    assert len(appraisal) == 6                   # 2 projects x 3 scenarios
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

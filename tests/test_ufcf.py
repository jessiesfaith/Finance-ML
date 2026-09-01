"""
Tests for the Phase 5 NWC / NOPAT / UFCF engine and the scenario layer.

Ground truth (hand-verified against the consolidated fixture):
FY2025: revenue 1,274.0 → EBITDA 314.8 → EBIT 248.6 → NOPAT 186.45;
NWC 133.2 → 159.5 (ΔNWC 26.3); CapEx 70 → UFCF 156.35.
FY2026 (Base drivers): revenue 1,350.44 → UFCF 186.2438.
"""

import io

import pandas as pd
import pytest

from financials import (
    apply_consolidation_status,
    build_ufcf_forecast,
    consolidate,
    income_walk,
    load_client_fs,
    load_scenarios,
    nwc_components,
    translate_statements,
)
from financials.loader import ClientFSValidationError
from financials.ufcf import DEFAULT_OUTPUT


@pytest.fixture(scope="module")
def pipeline():
    tables = load_client_fs(strict=True).tables
    translated = translate_statements(tables)
    consolidated = apply_consolidation_status(
        consolidate(translated, tables["entity_master"]),
        translated, tables["entity_master"],
    )
    scenario_tables, _ = load_scenarios(strict=True)
    return tables, consolidated, scenario_tables


@pytest.fixture(scope="module")
def frame(pipeline):
    tables, consolidated, scenario_tables = pipeline
    return build_ufcf_forecast(tables, consolidated, scenario_tables)


def row(frame, period):
    match = frame[frame["period_id"] == period]
    assert len(match) == 1
    return match.iloc[0]


# ------------------------------------------------
# THE HISTORICAL WALK
# ------------------------------------------------

def test_income_walk_fy2025(pipeline):
    _, consolidated, _ = pipeline
    walk = income_walk(consolidated).set_index("period_id")
    fy25 = walk.loc["FY2025"]
    assert fy25["revenue"] == pytest.approx(1274.0)
    assert fy25["ebitda"] == pytest.approx(314.8)
    assert fy25["da"] == pytest.approx(66.2)
    assert fy25["ebit"] == pytest.approx(248.6)
    assert fy25["effective_tax_rate_pct"] == pytest.approx(25.0)


def test_reported_effective_rate_stays_separate_from_normalized(pipeline, frame):
    """Spec section 14: FY2024's reported rate is 24.70%, not 25%."""
    _, consolidated, _ = pipeline
    walk = income_walk(consolidated).set_index("period_id")
    assert walk.loc["FY2024", "effective_tax_rate_pct"] == pytest.approx(24.701)
    # ...while NOPAT in the walk table uses the normalized driver rate.
    assert set(frame["tax_rate_pct"]) == {25.0}


def test_nwc_build_and_delta(pipeline):
    tables, consolidated, _ = pipeline
    nwc = nwc_components(consolidated, tables["account_mapping"]).set_index("period_id")
    fy25 = nwc.loc["FY2025"]
    assert fy25["accounts_receivable"] == pytest.approx(165.0)
    assert fy25["inventory"] == pytest.approx(113.0)
    assert fy25["accounts_payable"] == pytest.approx(118.5)
    assert fy25["operating_nwc"] == pytest.approx(159.5)
    assert fy25["delta_nwc"] == pytest.approx(26.3)          # 159.5 − 133.2
    assert pd.isna(nwc.loc["FY2024", "delta_nwc"])           # no prior on file


def test_cash_and_debt_never_enter_nwc(pipeline):
    """Spec section 13: cash/debt are excluded via classification, not luck."""
    tables, consolidated, _ = pipeline
    nwc = nwc_components(consolidated, tables["account_mapping"]).set_index("period_id")
    # FY2025 consolidated cash is 194 and debt 366 — NWC must not move if
    # we recompute after dropping them, because they were never included.
    without = consolidated[
        ~consolidated["standard_account_id"].isin(["cash", "long_term_debt"])
    ]
    nwc2 = nwc_components(without, tables["account_mapping"]).set_index("period_id")
    assert nwc.loc["FY2025", "operating_nwc"] == nwc2.loc["FY2025", "operating_nwc"]


def test_ufcf_bridge_fy2025(frame):
    fy25 = row(frame, "FY2025")
    assert fy25["forecast_method"] == "ACTUAL"
    assert fy25["nopat"] == pytest.approx(186.45)
    assert fy25["capex"] == pytest.approx(70.0)
    assert fy25["ufcf"] == pytest.approx(156.35)   # 186.45 + 66.2 − 70 − 26.3


def test_fy2024_does_not_invent_missing_capex(frame):
    """FY2024 has no CFS: capex and ufcf stay blank, never fabricated."""
    fy24 = row(frame, "FY2024")
    assert pd.isna(fy24["capex"])
    assert pd.isna(fy24["ufcf"])
    assert pd.isna(fy24["revenue_growth_pct"])


# ------------------------------------------------
# THE DRIVER-BASED FORECAST
# ------------------------------------------------

def test_forecast_first_year_math(frame):
    fy26 = row(frame, "FY2026")
    assert fy26["forecast_method"] == "DRIVER_BASED"
    assert fy26["review_status"] == "REVIEW"
    assert fy26["revenue"] == pytest.approx(1350.44)          # 1274 × 1.06
    assert fy26["ebitda"] == pytest.approx(337.61)            # × 25%
    assert fy26["da"] == pytest.approx(67.522)                # × 5%
    assert fy26["nopat"] == pytest.approx(202.566)            # EBIT × 0.75
    assert fy26["operating_nwc"] == pytest.approx(169.07)     # components ×1.06
    assert fy26["delta_nwc"] == pytest.approx(9.57)
    assert fy26["capex"] == pytest.approx(74.2742)            # × 5.5%
    assert fy26["ufcf"] == pytest.approx(186.2438)


def test_five_forecast_years_and_growth_consistency(frame):
    forecast = frame[frame["forecast_method"] == "DRIVER_BASED"]
    assert list(forecast["period_id"]) == [
        "FY2026", "FY2027", "FY2028", "FY2029", "FY2030",
    ]
    # constant drivers → each year's UFCF grows ~6% too
    ratios = forecast["ufcf"].values[1:] / forecast["ufcf"].values[:-1]
    assert all(abs(r - 1.06) < 0.001 for r in ratios)


def test_missing_required_driver_fails_loudly(pipeline):
    tables, consolidated, scenario_tables = pipeline
    broken = {k: v.copy() for k, v in scenario_tables.items()}
    a = broken["scenario_assumptions"]
    broken["scenario_assumptions"] = a[
        a["target_id"] != "CAPEX_PCT_REVENUE"
    ].reset_index(drop=True)
    with pytest.raises(ValueError, match="CAPEX_PCT_REVENUE"):
        build_ufcf_forecast(tables, consolidated, broken)


def test_scenario_layer_validates_references():
    tables, issues = load_scenarios(strict=True)
    assert issues == []
    # break a reference in memory: unknown driver id
    broken = {k: v.copy() for k, v in tables.items()}
    broken["scenario_assumptions"].loc[0, "target_id"] = "NOT_A_DRIVER"
    import shutil, tempfile
    from financials.scenarios import DEFAULT_SCENARIO_DIR
    with tempfile.TemporaryDirectory() as tmp:
        for f in DEFAULT_SCENARIO_DIR.glob("*.csv"):
            shutil.copy(f, tmp)
        broken["scenario_assumptions"].to_csv(
            f"{tmp}/scenario_assumptions.csv", index=False
        )
        with pytest.raises(ClientFSValidationError, match="unknown_driver"):
            load_scenarios(tmp, strict=True)


# ------------------------------------------------
# COMMITTED OUTPUT LOCK
# ------------------------------------------------

def test_committed_ufcf_forecast_matches_a_fresh_rebuild(frame):
    committed = pd.read_csv(DEFAULT_OUTPUT, dtype=str, keep_default_na=False)
    fresh = pd.read_csv(
        io.StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=False
    )
    pd.testing.assert_frame_equal(committed, fresh)

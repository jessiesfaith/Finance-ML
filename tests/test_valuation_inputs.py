"""
Tests for Phase 6: net debt, invested capital/ROIC, diluted shares,
market-value capital structure, and the valuation_inputs hand-off table.

Ground truth (consolidated fixture):
FY2025: debt 366.0 − cash 194.0 = net debt 172.0;
IC = NWC 159.5 + net PP&E 632.0 = 791.5; NOPAT 186.45 → ROIC 23.5566%;
diluted = 100 + 1.6667 (TSM) + 2 RSUs = 103.6667M;
market equity 18.00 × 103.6667 = 1,866.0 → E 83.6% / D 16.4%.
"""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

from financials import (
    apply_consolidation_status,
    consolidate,
    income_walk,
    invested_capital_components,
    load_client_fs,
    load_scenarios,
    load_shares_dilution,
    market_value_weights,
    net_debt_components,
    roic,
    translate_statements,
    treasury_stock_method,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_valuation_inputs import OUTPUT, build_valuation_inputs  # noqa: E402


@pytest.fixture(scope="module")
def pipeline():
    tables = load_client_fs(strict=True).tables
    translated = translate_statements(tables)
    consolidated = apply_consolidation_status(
        consolidate(translated, tables["entity_master"]),
        translated, tables["entity_master"],
    )
    return tables, consolidated


# ------------------------------------------------
# NET DEBT
# ------------------------------------------------

def test_net_debt_build(pipeline):
    tables, consolidated = pipeline
    nd = net_debt_components(
        consolidated, tables["account_mapping"]
    ).set_index("period_id")
    fy25 = nd.loc["FY2025"]
    assert fy25["long_term_debt"] == pytest.approx(366.0)   # 300 + 66
    assert fy25["cash_and_equivalents"] == pytest.approx(194.0)  # 150 + 44
    assert fy25["net_debt"] == pytest.approx(172.0)
    assert nd.loc["FY2024", "net_debt"] == pytest.approx(237.8)  # 390.2 − 152.4


def test_not_every_liability_is_debt(pipeline):
    """AP is a liability but carries no DEBT election — it must not count."""
    tables, consolidated = pipeline
    nd = net_debt_components(
        consolidated, tables["account_mapping"]
    ).set_index("period_id")
    # FY2025 consolidated AP is 118.5; total debt must exclude it entirely.
    assert nd.loc["FY2025", "total_debt"] == pytest.approx(366.0)


# ------------------------------------------------
# INVESTED CAPITAL / ROIC
# ------------------------------------------------

def test_invested_capital_components(pipeline):
    tables, consolidated = pipeline
    ic = invested_capital_components(
        consolidated, tables["account_mapping"]
    ).set_index("period_id")
    assert ic.loc["FY2025", "invested_capital"] == pytest.approx(791.5)
    assert ic.loc["FY2024", "invested_capital"] == pytest.approx(732.0)


def test_roic_ending_and_average_bases(pipeline):
    tables, consolidated = pipeline
    ic = invested_capital_components(consolidated, tables["account_mapping"])
    walk = income_walk(consolidated).set_index("period_id")
    nopat = {p: walk.loc[p, "ebit"] * 0.75 for p in walk.index}

    ending = roic(ic, nopat, basis="ENDING").set_index("period_id")
    assert ending.loc["FY2025", "roic_pct"] == pytest.approx(23.5566, abs=1e-3)

    average = roic(ic, nopat, basis="AVERAGE").set_index("period_id")
    # (732.0 + 791.5) / 2 = 761.75 → 186.45 / 761.75 = 24.4767%
    assert average.loc["FY2025", "roic_denominator"] == pytest.approx(761.75)
    assert average.loc["FY2025", "roic_pct"] == pytest.approx(24.4767, abs=1e-3)

    with pytest.raises(ValueError, match="basis"):
        roic(ic, nopat, basis="WHATEVER")


# ------------------------------------------------
# DILUTED SHARES (treasury-stock method)
# ------------------------------------------------

def test_treasury_stock_method_cases():
    assert treasury_stock_method(5.0, 12.0, 18.0) == pytest.approx(5 * (1 - 12 / 18))
    assert treasury_stock_method(5.0, 18.0, 18.0) == 0.0   # at the money
    assert treasury_stock_method(5.0, 25.0, 18.0) == 0.0   # anti-dilutive
    with pytest.raises(ValueError):
        treasury_stock_method(5.0, 12.0, 0.0)


def test_shares_file_loads_and_reproduces(pipeline):
    shares = load_shares_dilution()
    row = shares.iloc[0]
    assert row["diluted_shares_m"] == pytest.approx(103.6667)
    assert row["anti_dilutive_shares_excluded_m"] == pytest.approx(1.5)


def test_tampered_share_count_refuses_to_load(tmp_path):
    frame = load_shares_dilution()
    broken = frame.copy()
    broken.loc[0, "diluted_shares_m"] = 110.0   # doesn't reproduce from inputs
    path = tmp_path / "shares_dilution.csv"
    broken.to_csv(path, index=False)
    with pytest.raises(ValueError, match="never\\s+silently reconciles"):
        load_shares_dilution(path)


# ------------------------------------------------
# CAPITAL STRUCTURE
# ------------------------------------------------

def test_market_value_weights():
    w = market_value_weights(18.0, 103.6667, 366.0)
    assert w["equity_market_value_m"] == pytest.approx(1866.0006)
    assert w["equity_weight_pct"] == pytest.approx(83.6022, abs=1e-3)
    assert w["debt_weight_pct"] == pytest.approx(16.3978, abs=1e-3)
    # ...vs the assumed 70/30 the WACC currently uses.


# ------------------------------------------------
# THE HAND-OFF TABLE
# ------------------------------------------------

def test_valuation_inputs_table(pipeline):
    tables, consolidated = pipeline
    scenario_tables, _ = load_scenarios(strict=True)
    frame = build_valuation_inputs(tables, consolidated, scenario_tables)

    fy25 = frame[frame["period_id"] == "FY2025"].iloc[0]
    assert fy25["net_debt"] == pytest.approx(172.0)
    assert fy25["invested_capital"] == pytest.approx(791.5)
    assert fy25["net_ppe"] == pytest.approx(632.0)
    assert fy25["diluted_shares_m"] == pytest.approx(103.6667)

    fy24 = frame[frame["period_id"] == "FY2024"].iloc[0]
    assert pd.isna(fy24["diluted_shares_m"])   # no share data for FY2024


def test_committed_valuation_inputs_match_a_fresh_rebuild(pipeline):
    tables, consolidated = pipeline
    scenario_tables, _ = load_scenarios(strict=True)
    frame = build_valuation_inputs(tables, consolidated, scenario_tables)

    from conftest import assert_matches_committed
    assert_matches_committed(frame, OUTPUT)

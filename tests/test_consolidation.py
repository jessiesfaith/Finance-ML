"""
Tests for entity consolidation: the elimination layer, the FX adjustment
column, consolidated identities, and the rebuild lock on the committed
entity_consolidation.csv.
"""

import io

import pandas as pd
import pytest

from financials.consolidation import (
    DEFAULT_OUTPUT,
    consolidate,
    consolidated_balance_gap,
    consolidated_net_income,
)
from financials.controls import apply_consolidation_status
from financials.fx_translation import translate_statements
from financials.loader import load_client_fs
from financials.schemas import ENTITY_CONSOLIDATION


@pytest.fixture(scope="module")
def consolidated():
    tables = load_client_fs(strict=True).tables
    translated = translate_statements(tables)
    combined = consolidate(translated, tables["entity_master"])
    # Same step the build script applies (Phase 4 control engine).
    return apply_consolidation_status(combined, translated, tables["entity_master"])


def account(consolidated, period, account_id):
    match = consolidated[
        (consolidated["period_id"] == period)
        & (consolidated["standard_account_id"] == account_id)
    ]
    assert len(match) == 1
    return match.iloc[0]


def test_schema_and_single_consolidated_entity(consolidated):
    assert list(consolidated.columns) == ENTITY_CONSOLIDATION.column_names()
    assert set(consolidated["entity_id"]) == {"CONSOLIDATED"}
    # Phase 4: the control engine fills the status from an independent
    # recomputation of the roll-up.
    assert set(consolidated["control_status"]) == {"PASS"}
    assert consolidated["control_variance"].abs().max() <= 0.01


def test_intercompany_revenue_is_eliminated(consolidated):
    revenue = account(consolidated, "FY2025", "revenue")
    # Parent 1000 + sub 324 (avg rate) = 1324 pre-elimination.
    assert revenue["pre_elimination_amount"] == pytest.approx(1324.0)
    assert revenue["intercompany_elimination"] == pytest.approx(-50.0)
    assert revenue["consolidated_amount"] == pytest.approx(1274.0)


def test_elimination_nets_to_zero_on_the_income_statement(consolidated):
    """IC revenue vs IC COGS cancel: eliminations change mix, not profit."""
    is_rows = consolidated[
        (consolidated["period_id"] == "FY2025")
        & (consolidated["statement_type"] == "IS")
    ]
    assert is_rows["intercompany_elimination"].sum() == pytest.approx(0.0)


def test_intercompany_ar_ap_are_eliminated(consolidated):
    ar = account(consolidated, "FY2025", "accounts_receivable")
    ap = account(consolidated, "FY2025", "accounts_payable")
    assert ar["intercompany_elimination"] == pytest.approx(-10.0)
    assert ap["intercompany_elimination"] == pytest.approx(-10.0)
    assert ar["consolidated_amount"] == pytest.approx(165.0)   # 120 + 55 - 10
    assert ap["consolidated_amount"] == pytest.approx(118.5)   # 90 + 38.5 - 10


def test_cta_lands_in_the_fx_adjustment_column(consolidated):
    cta = account(consolidated, "FY2025", "cta_aoci")
    assert cta["pre_elimination_amount"] == pytest.approx(0.0)
    assert cta["fx_translation_adjustment"] == pytest.approx(10.75)
    assert cta["consolidated_amount"] == pytest.approx(10.75)


def test_consolidated_net_income(consolidated):
    ni = consolidated_net_income(consolidated)
    assert ni["FY2025"] == pytest.approx(167.40)   # 135 + 32.40, eliminations net 0
    assert ni["FY2024"] == pytest.approx(135.35)   # 107 + 28.35


def test_consolidated_balance_sheet_balances(consolidated):
    gaps = consolidated_balance_gap(consolidated)
    for gap in gaps:
        assert gap == pytest.approx(0.0, abs=1e-9)


def test_committed_output_matches_a_fresh_rebuild(consolidated):
    """data/client_fs/entity_consolidation.csv must never go stale."""
    from conftest import assert_matches_committed
    assert_matches_committed(consolidated, DEFAULT_OUTPUT)

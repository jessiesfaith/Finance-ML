"""
Tests for the normalized-statement builder (REPORTED view).

The headline properties:
  * canonical signs make subtotals into sums (IS rows sum to net income,
    CFS rows sum to the change in cash, the balance sheet nets to zero),
  * every row keeps its sign-transformation audit trail and lineage,
  * the committed client_fs_normalized.csv exactly matches a fresh rebuild
    (derived data in the repo can never silently go stale).
"""

import io

import pandas as pd
import pytest

from financials.loader import load_client_fs
from financials.normalized_statements import (
    DEFAULT_OUTPUT,
    balance_sheet_gap,
    build_normalized_statements,
    cash_flow_by_entity_period,
    net_income_by_entity_period,
)
from financials.schemas import CLIENT_FS_NORMALIZED


@pytest.fixture(scope="module")
def normalized():
    tables = load_client_fs(strict=True).tables
    return build_normalized_statements(tables)


def test_one_normalized_row_per_raw_row(normalized):
    raw = load_client_fs(strict=True).tables["client_fs_raw"]
    assert len(normalized) == len(raw)
    assert list(normalized.columns) == CLIENT_FS_NORMALIZED.column_names()


def test_reported_defaults(normalized):
    assert set(normalized["reported_or_adjusted"]) == {"REPORTED"}
    assert set(normalized["include_in_normalized"]) == {"YES"}
    assert set(normalized["include_in_proforma"]) == {"YES"}
    assert (normalized["adjustment_id"] == "").all()


def test_magnitude_expense_flips_to_negative(normalized):
    cogs = normalized[
        (normalized["entity_id"] == "ENT_PARENT")
        & (normalized["period_id"] == "FY2025")
        & (normalized["standard_account_id"] == "cogs")
    ].iloc[0]
    assert cogs["amount_source"] == 600      # as presented (MAGNITUDE)
    assert cogs["amount_reporting"] == -600  # canonical


def test_signed_capex_is_not_double_flipped(normalized):
    capex = normalized[
        (normalized["entity_id"] == "ENT_PARENT")
        & (normalized["standard_account_id"] == "cfs_capex")
    ].iloc[0]
    assert capex["amount_source"] == -70
    assert capex["amount_reporting"] == -70  # SIGNED passes through untouched
    assert capex["sign_multiplier"] == -1    # the rule is still on record


def test_income_statement_sums_to_net_income(normalized):
    ni = net_income_by_entity_period(normalized)
    assert ni[("ENT_PARENT", "FY2025")] == pytest.approx(135.0)
    assert ni[("ENT_PARENT", "FY2024")] == pytest.approx(107.0)
    assert ni[("ENT_GMBH", "FY2025")] == pytest.approx(32.40)
    assert ni[("ENT_GMBH", "FY2024")] == pytest.approx(28.35)


def test_cash_flow_sums_to_change_in_cash(normalized):
    delta = cash_flow_by_entity_period(normalized)
    # Parent cash: 120 (FY2024) -> 150 (FY2025).
    assert delta[("ENT_PARENT", "FY2025")] == pytest.approx(30.0)


def test_balance_sheets_balance(normalized):
    gaps = balance_sheet_gap(normalized)
    # Parent and sub for two periods each, plus the elimination entity in
    # FY2025 (whose IC AR/AP reversals net to zero by construction).
    assert len(gaps) == 5
    for gap in gaps:
        assert gap == pytest.approx(0.0, abs=1e-9)


def test_lineage_survives_normalization(normalized):
    row = normalized[
        (normalized["entity_id"] == "ENT_GMBH")
        & (normalized["period_id"] == "FY2025")
        & (normalized["standard_account_id"] == "revenue")
    ].iloc[0]
    assert row["source_system"] == "DATEV"
    assert row["source_account_code"] == "8400"
    assert row["load_id"] == "L20260831-001"


def test_committed_output_matches_a_fresh_rebuild(normalized):
    """
    data/client_fs/client_fs_normalized.csv must never go stale. Since
    Phase 8 the committed file is REPORTED rows + applied ADJUSTED rows.
    """
    from financials.adjustments import apply_adjustments, load_adjustments
    from financials.proforma import (
        apply_proforma, load_proforma_adjustments, load_transaction_events,
    )
    tables = load_client_fs(strict=True).tables
    adjustments, _ = load_adjustments(tables, strict=True)
    combined = apply_adjustments(
        normalized, adjustments, tables["account_mapping"]
    )
    events, _ = load_transaction_events(strict=True)
    proforma, _ = load_proforma_adjustments(events, strict=True)
    combined = apply_proforma(combined, proforma, tables["account_mapping"])

    committed = pd.read_csv(DEFAULT_OUTPUT, dtype=str, keep_default_na=False)
    fresh = pd.read_csv(
        io.StringIO(combined.to_csv(index=False)),
        dtype=str, keep_default_na=False,
    )
    pd.testing.assert_frame_equal(committed, fresh)

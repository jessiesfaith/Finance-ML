"""
Tests for the Phase 9 transaction-event layer and pro forma engine.

Fixture: TXN-001 (Project Rhine acquisition, price 40 = cash 30 + debt
10 + equity 0) with PF-001A (+3 opex run-rate synergy) and PF-001B
(−0.75 tax effect). FY2025 views: REPORTED 314.8/167.40 → NORMALIZED
326.8/176.40 → PRO FORMA 329.8/178.65 (EBITDA / net income).
"""

import pandas as pd
import pytest

from financials import (
    apply_adjustments,
    apply_proforma,
    build_normalized_statements,
    link_events_to_outliers,
    load_adjustments,
    load_client_fs,
    load_proforma_adjustments,
    load_transaction_events,
    view_income_summary,
)
from financials.loader import ClientFSValidationError


@pytest.fixture(scope="module")
def setup():
    tables = load_client_fs(strict=True).tables
    reported = build_normalized_statements(tables)
    adjustments, _ = load_adjustments(tables, strict=True)
    combined = apply_adjustments(reported, adjustments,
                                 tables["account_mapping"])
    events, e_issues = load_transaction_events(strict=True)
    proforma, p_issues = load_proforma_adjustments(events, strict=True)
    assert e_issues == [] and p_issues == []
    full = apply_proforma(combined, proforma, tables["account_mapping"])
    return tables, reported, events, proforma, full


def test_consideration_reconciles(setup):
    _, _, events, _, _ = setup
    deal = events.iloc[0]
    assert deal["cash_paid"] + deal["debt_assumed"] + deal["equity_issued"] \
        == pytest.approx(deal["purchase_price_or_proceeds"])


def test_consideration_mismatch_fails_loudly(setup, tmp_path):
    _, _, events, _, _ = setup
    broken = events.copy()
    broken.loc[0, "cash_paid"] = 50.0   # 50 + 10 + 0 != 40
    path = tmp_path / "transaction_events.csv"
    broken.to_csv(path, index=False)
    with pytest.raises(ClientFSValidationError, match="consideration_mismatch"):
        load_transaction_events(path=path, strict=True)


def test_orphan_proforma_row_fails_loudly(setup, tmp_path):
    _, _, events, proforma, _ = setup
    broken = proforma.copy()
    broken.loc[0, "transaction_id"] = "TXN-GHOST"
    path = tmp_path / "proforma_adjustments.csv"
    broken.to_csv(path, index=False)
    with pytest.raises(ClientFSValidationError, match="unknown_transaction"):
        load_proforma_adjustments(events, path=path, strict=True)


def test_proforma_stacks_on_verified_normalized_base(setup):
    tables, reported, _, proforma, _ = setup
    adjustments, _ = load_adjustments(tables, strict=True)
    combined = apply_adjustments(reported, adjustments,
                                 tables["account_mapping"])
    drifted = proforma.copy()
    drifted.loc[0, "reported_amount"] = -150.0     # normalized says -138
    drifted.loc[0, "proforma_amount"] = -147.0     # keep arithmetic valid
    with pytest.raises(ClientFSValidationError, match="normalized_base_mismatch"):
        apply_proforma(combined, drifted, tables["account_mapping"])


def test_three_views_are_now_distinct(setup):
    _, _, _, _, full = setup
    summaries = {
        view: view_income_summary(full, view)
        for view in ("REPORTED", "NORMALIZED", "PROFORMA")
    }
    fy25 = {v: s[s["period_id"] == "FY2025"].iloc[0]
            for v, s in summaries.items()}

    assert fy25["REPORTED"]["ebitda"] == pytest.approx(314.8)
    assert fy25["NORMALIZED"]["ebitda"] == pytest.approx(326.8)
    assert fy25["PROFORMA"]["ebitda"] == pytest.approx(329.8)   # +3 synergy
    assert fy25["REPORTED"]["net_income"] == pytest.approx(167.40)
    assert fy25["NORMALIZED"]["net_income"] == pytest.approx(176.40)
    assert fy25["PROFORMA"]["net_income"] == pytest.approx(178.65)  # +3 − 0.75


def test_normalized_view_never_sees_proforma_rows(setup):
    _, _, _, _, full = setup
    from financials import select_view
    norm = select_view(full, "NORMALIZED")
    assert not (norm["transaction_id"] != "").any()


def test_proforma_rows_carry_transaction_lineage(setup):
    _, _, _, _, full = setup
    pf_rows = full[full["source_system"] == "PROFORMA"]
    assert len(pf_rows) == 2
    assert set(pf_rows["transaction_id"]) == {"TXN-001"}
    assert set(pf_rows["include_in_normalized"]) == {"NO"}
    assert set(pf_rows["include_in_proforma"]) == {"YES"}


def test_event_outlier_linkage_links_but_never_concludes(setup):
    tables, _, events, _, _ = setup
    flags = pd.read_csv(
        "/home/user/finance-ml/data/client_fs/outlier_flags.csv"
    )
    links = link_events_to_outliers(events, flags, tables["period_master"])
    row = links.iloc[0]
    assert row["period_id"] == "FY2025"
    assert row["outlier_flag_count"] == 9
    assert "causation is not concluded" in row["note"]
    assert "caused" not in row["note"].replace("causation", "")

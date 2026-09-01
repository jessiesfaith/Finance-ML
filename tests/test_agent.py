"""
Tests for the Phase 10 analyst-review agent: evidence gathering,
deterministic interpretation, and — most importantly — the guardrails
(spec section 27: READ/ANALYZE/FLAG/EXPLAIN/PROPOSE, never
DELETE/OVERWRITE/APPROVE).
"""

import io

import pandas as pd
import pytest

from agents.financial_review_agent import (
    AgentGuardrailError,
    DEFAULT_LOG_FILE,
    DeterministicInterpreter,
    findings_frame,
    gather_evidence,
    propose_adjustment,
    write_review_log,
)
from financials import (
    apply_consolidation_status,
    build_normalized_statements,
    consolidate,
    flags_frame,
    load_adjustments,
    load_client_fs,
    load_transaction_events,
    results_frame,
    run_all_controls,
    run_outlier_engine,
    translate_statements,
)


@pytest.fixture(scope="module")
def findings():
    tables = load_client_fs(strict=True).tables
    normalized = build_normalized_statements(tables)
    translated = translate_statements(tables)
    consolidated = apply_consolidation_status(
        consolidate(translated, tables["entity_master"]),
        translated, tables["entity_master"],
    )
    controls = results_frame(run_all_controls(
        tables, normalized, translated, consolidated))
    outliers = flags_frame(run_outlier_engine(
        tables, consolidated, translated))
    events, _ = load_transaction_events(strict=True)
    adjustments, _ = load_adjustments(tables, strict=True)

    packets = gather_evidence(
        controls, outliers, events, adjustments, tables["period_master"])
    interpreter = DeterministicInterpreter()
    return findings_frame([interpreter.interpret(p) for p in packets])


def pick(frame, needle):
    rows = frame[frame["item_reference"].str.contains(needle, regex=False)]
    assert len(rows) >= 1, needle
    return rows.iloc[0]


def test_one_finding_per_open_item(findings):
    assert len(findings) == 14   # 5 control exceptions + 9 outlier flags
    assert (findings["item_type"] == "CONTROL_EXCEPTION").sum() == 5
    assert (findings["item_type"] == "OUTLIER_FLAG").sum() == 9


def test_confidence_bounded_and_never_certain(findings):
    assert findings["agent_confidence"].between(0, 1).all()
    assert (findings["agent_confidence"] < 1.0).all()


def test_benign_re_flags_cite_the_control_that_proves_it(findings):
    row = pick(findings, "retained_earnings|ENT_PARENT")
    assert "C4" in row["explanation"]
    assert row["agent_confidence"] == pytest.approx(0.9)
    assert "C4" in row["related_control_ids"]


def test_new_item_findings_link_the_deal_without_concluding(findings):
    row = pick(findings, "NEW_ITEM|revenue")
    assert "TXN-001" in row["explanation"]
    assert "Causation is not concluded" in row["explanation"]
    assert "ADJ-001A" in row["explanation"]   # existing coverage cited
    assert "organic vs acquired" in row["recommended_action"]


def test_consolidated_cash_is_honestly_partial(findings):
    row = pick(findings, "cash|CONSOLIDATED")
    assert "PARTIALLY explained" in row["explanation"]
    assert "ENT_GMBH" in row["recommended_action"]


def test_fx_exception_explained_via_cta_identity(findings):
    row = pick(findings, "C9|ENT_GMBH|FY2025")
    assert "CTA" in row["explanation"]
    assert "not a data error" in row["explanation"]


# ------------------------------------------------
# GUARDRAILS
# ------------------------------------------------

@pytest.fixture()
def adjustments_copy(tmp_path):
    src = pd.read_csv(
        "/home/user/finance-ml/data/client_fs/adjustments.csv",
        dtype=str, keep_default_na=False,
    )
    path = tmp_path / "adjustments.csv"
    src.to_csv(path, index=False)
    return path, src


def _proposal(**overrides):
    base = {
        "adjustment_id": "ADJ-AGENT-001", "company_id": "COMP001",
        "entity_id": "ENT_PARENT", "period_id": "FY2025",
        "standard_account_id": "opex", "adjustment_type": "NORMALIZATION",
        "original_amount": -150.0, "adjustment_amount": 2.0,
        "normalized_amount": -148.0, "reporting_currency": "USD",
        "reason": "test proposal", "event_classification": "ONE_TIME",
        "source_document": "test.pdf", "source_reference": "p.1",
        "agent_confidence": 0.6, "include_in_normalized": "REVIEW",
        "review_status": "REVIEW", "reviewer": "", "approval_timestamp": "",
    }
    base.update(overrides)
    return base


def test_agent_cannot_approve_its_own_proposal(adjustments_copy):
    path, _ = adjustments_copy
    with pytest.raises(AgentGuardrailError, match="never APPROVE"):
        propose_adjustment(path, _proposal(review_status="APPROVED"))


def test_agent_cannot_overwrite_an_existing_adjustment(adjustments_copy):
    path, _ = adjustments_copy
    with pytest.raises(AgentGuardrailError, match="never overwrites"):
        propose_adjustment(path, _proposal(adjustment_id="ADJ-001A"))


def test_agent_must_state_confidence(adjustments_copy):
    path, _ = adjustments_copy
    with pytest.raises(AgentGuardrailError, match="agent_confidence"):
        propose_adjustment(path, _proposal(agent_confidence=""))


def test_agent_writes_are_append_only_and_forced_to_review(adjustments_copy):
    path, before = adjustments_copy
    propose_adjustment(path, _proposal())
    after = pd.read_csv(path, dtype=str, keep_default_na=False)

    # every pre-existing row byte-identical
    pd.testing.assert_frame_equal(
        after.iloc[: len(before)].reset_index(drop=True), before)
    new = after.iloc[-1]
    assert new["review_status"] == "REVIEW"
    assert new["include_in_normalized"] == "REVIEW"
    assert new["reviewer"] == "" and new["approval_timestamp"] == ""


def test_agent_refuses_forbidden_write_targets(tmp_path):
    with pytest.raises(AgentGuardrailError, match="refusing to touch"):
        write_review_log(pd.DataFrame(), tmp_path / "client_fs_raw.csv")


def test_committed_review_log_matches_a_fresh_rebuild(findings):
    committed = pd.read_csv(DEFAULT_LOG_FILE, dtype=str, keep_default_na=False)
    fresh = pd.read_csv(
        io.StringIO(findings.to_csv(index=False)),
        dtype=str, keep_default_na=False,
    )
    pd.testing.assert_frame_equal(committed, fresh)

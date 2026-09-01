"""
Tests for the Phase 7 deterministic outlier engine.

Fixture ground truth: exactly 9 flags —
consolidated: ebitda_margin +2.28pp, ebit_margin +2.09pp, cash +27.3%,
retained_earnings +59.3% (HIGH); entity: parent cash +25.0%, parent RE
+52.9% (HIGH), GmbH RE +89.1% (HIGH), ENT_ELIM new items revenue/cogs.
Revenue (+6.7%) must NOT flag; z-score must self-report not-applicable.
"""

import io

import pandas as pd
import pytest

from financials import (
    apply_consolidation_status,
    consolidate,
    flags_frame,
    load_client_fs,
    run_outlier_engine,
    translate_statements,
)
from financials.outliers import DEFAULT_OUTPUT, THRESHOLDS, zscore_flags
from financials.schemas import OUTLIER_FLAGS


@pytest.fixture(scope="module")
def pipeline():
    tables = load_client_fs(strict=True).tables
    translated = translate_statements(tables)
    consolidated = apply_consolidation_status(
        consolidate(translated, tables["entity_master"]),
        translated, tables["entity_master"],
    )
    return tables, consolidated, translated


@pytest.fixture(scope="module")
def frame(pipeline):
    tables, consolidated, translated = pipeline
    return flags_frame(run_outlier_engine(tables, consolidated, translated))


def one(frame, **filters):
    rows = frame
    for column, value in filters.items():
        rows = rows[rows[column] == value]
    assert len(rows) == 1, f"{filters}: {len(rows)} rows"
    return rows.iloc[0]


def test_exact_flag_landscape(frame):
    assert len(frame) == 9
    assert list(frame.columns) == OUTLIER_FLAGS.column_names()
    assert set(frame["review_status"]) == {"PENDING"}


def test_margin_variance_flags(frame):
    ebitda = one(frame, method="MARGIN_VARIANCE", metric_name="ebitda_margin")
    assert ebitda["variance_pct"] == pytest.approx(2.28, abs=0.01)  # pp
    assert ebitda["severity"] == "MEDIUM"
    ebit = one(frame, method="MARGIN_VARIANCE", metric_name="ebit_margin")
    assert ebit["variance_pct"] == pytest.approx(2.09, abs=0.01)


def test_pop_variance_needs_both_bars(frame):
    """Revenue rose 6.7% ($80M): big dollars, small percent — no flag."""
    assert frame[
        (frame["method"] == "POP_VARIANCE")
        & (frame["metric_name"] == "revenue")
    ].empty

    cash = one(frame, method="POP_VARIANCE", metric_name="cash",
               level="CONSOLIDATED")
    assert cash["variance_pct"] == pytest.approx(27.3, abs=0.1)
    assert cash["severity"] == "MEDIUM"


def test_high_severity_at_twice_threshold(frame):
    re_cons = one(frame, method="POP_VARIANCE",
                  metric_name="retained_earnings", level="CONSOLIDATED")
    assert re_cons["variance_pct"] == pytest.approx(59.3, abs=0.1)
    assert re_cons["severity"] == "HIGH"   # ≥ 2 × 15%


def test_new_intercompany_activity_is_flagged_not_judged(frame):
    revenue = one(frame, method="NEW_ITEM", entity_id="ENT_ELIM",
                  metric_name="revenue")
    assert revenue["current_value"] == pytest.approx(-50.0)
    assert "not a conclusion" in revenue["possible_causes"]
    cogs = one(frame, method="NEW_ITEM", entity_id="ENT_ELIM",
               metric_name="cogs")
    assert cogs["current_value"] == pytest.approx(50.0)


def test_small_new_items_do_not_flag(frame):
    """ENT_ELIM's AR/AP (±10) sit under the materiality bar."""
    elim = frame[frame["entity_id"] == "ENT_ELIM"]
    assert set(elim["metric_name"]) == {"revenue", "cogs"}


def test_zscore_declares_itself_not_applicable(pipeline):
    tables, consolidated, _ = pipeline
    from financials.controls import _prior_period_map
    prior_of = _prior_period_map(tables["period_master"])
    assert zscore_flags(consolidated, prior_of) == []
    assert THRESHOLDS["min_history_for_zscore"] == 4


def test_flags_are_questions_not_conclusions(frame):
    assert frame["possible_causes"].str.contains("error").all()
    assert not frame["possible_causes"].str.contains("IS an error").any()


def test_committed_flags_match_a_fresh_rebuild(frame):
    committed = pd.read_csv(DEFAULT_OUTPUT, dtype=str, keep_default_na=False)
    fresh = pd.read_csv(
        io.StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=False
    )
    pd.testing.assert_frame_equal(committed, fresh)

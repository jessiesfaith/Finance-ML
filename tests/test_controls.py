"""
Tests for the Phase 4 control engine.

Two halves: (1) the fixture produces exactly the expected control
landscape — 29 PASS, 5 legitimate REVIEW exceptions, 0 FAIL — and
(2) corrupting the data one way per test flips exactly the right
control to FAIL with the right variance.
"""

import io

import pandas as pd
import pytest

from financials import (
    apply_consolidation_status,
    build_normalized_statements,
    consolidate,
    load_client_fs,
    results_frame,
    run_all_controls,
    translate_statements,
)
from financials.controls import (
    DEFAULT_OUTPUT,
    _prior_period_map,
    control_1_balance_sheet,
    control_2_cash_flow,
    control_8_consolidation,
    control_10_source_total,
)
from financials.schemas import CONTROL_CHECKS


@pytest.fixture(scope="module")
def pipeline():
    tables = load_client_fs(strict=True).tables
    normalized = build_normalized_statements(tables)
    translated = translate_statements(tables)
    consolidated = apply_consolidation_status(
        consolidate(translated, tables["entity_master"]),
        translated, tables["entity_master"],
    )
    return tables, normalized, translated, consolidated


@pytest.fixture(scope="module")
def frame(pipeline):
    tables, normalized, translated, consolidated = pipeline
    results = run_all_controls(tables, normalized, translated, consolidated)
    return results_frame(results)


def pick(frame, control_id, entity, period):
    rows = frame[
        (frame["control_id"] == control_id)
        & (frame["entity_id"] == entity)
        & (frame["period_id"] == period)
    ]
    assert len(rows) >= 1
    return rows


# ------------------------------------------------
# THE FIXTURE'S CONTROL LANDSCAPE
# ------------------------------------------------

def test_overall_landscape(frame):
    counts = frame["status"].value_counts().to_dict()
    assert counts.get("FAIL", 0) == 0, frame[frame["status"] == "FAIL"]
    assert counts["PASS"] == 29
    assert counts["REVIEW"] == 5


def test_output_matches_schema(frame):
    assert list(frame.columns) == CONTROL_CHECKS.column_names()
    assert set(frame["status"]) <= {"PASS", "REVIEW", "FAIL"}
    assert set(frame["severity"]) <= {"LOW", "MEDIUM", "HIGH"}
    assert set(frame["review_status"]) == {"PENDING"}


def test_c1_every_balance_sheet_balances(frame):
    c1 = frame[frame["control_id"] == "C1"]
    assert len(c1) == 5  # parent x2, sub x2, elimination entity x1
    assert set(c1["status"]) == {"PASS"}


def test_c2_parent_cash_walk_passes(frame):
    row = pick(frame, "C2", "ENT_PARENT", "FY2025").iloc[0]
    assert row["status"] == "PASS"
    assert row["expected_value"] == pytest.approx(150.0)  # ending cash
    assert row["actual_value"] == pytest.approx(150.0)    # 120 + 30


def test_c2_missing_cfs_is_review_not_silent(frame):
    row = pick(frame, "C2", "ENT_GMBH", "FY2025").iloc[0]
    assert row["status"] == "REVIEW"
    assert "No cash flow statement rows" in row["agent_comment"]


def test_c3_net_income_ties_to_cfs(frame):
    row = pick(frame, "C3", "ENT_PARENT", "FY2025").iloc[0]
    assert row["status"] == "PASS"
    assert row["expected_value"] == pytest.approx(135.0)


def test_c4_retained_earnings_roll_passes_in_local_currency(frame):
    parent = pick(frame, "C4", "ENT_PARENT", "FY2025").iloc[0]
    sub = pick(frame, "C4", "ENT_GMBH", "FY2025").iloc[0]
    assert parent["status"] == "PASS"   # 170 + 135 - 45 = 260
    assert sub["status"] == "PASS"      # 35 + 30 - 0 = 65 (EUR)


def test_c5_oci_without_aoci_account_is_review(frame):
    row = pick(frame, "C5", "ENT_PARENT", "FY2025").iloc[0]
    assert row["status"] == "REVIEW"
    assert "aoci" in row["agent_comment"].lower()


def test_c6_debt_roll(frame):
    parent = pick(frame, "C6", "ENT_PARENT", "FY2025").iloc[0]
    assert parent["status"] == "PASS"   # 320 - 20 = 300
    sub = pick(frame, "C6", "ENT_GMBH", "FY2025").iloc[0]
    assert sub["status"] == "REVIEW"    # 65 -> 60 EUR, nothing explains it


def test_c8_consolidation_passes(frame):
    c8 = frame[frame["control_id"] == "C8"]
    assert len(c8) == 4  # roll-up check + BS identity, for two periods
    assert set(c8["status"]) == {"PASS"}
    assert set(c8["entity_id"]) == {"CONSOLIDATED"}


def test_c9_fx_shortcut_flagged_as_review_with_cta_sized_variance(frame):
    bs_2025 = frame[
        (frame["control_id"] == "C9")
        & (frame["entity_id"] == "ENT_GMBH")
        & (frame["period_id"] == "FY2025")
        & (frame["status"] == "REVIEW")
    ].iloc[0]
    # The source's closing-rate shortcut on equity == the CTA (10.75).
    assert bs_2025["actual_value"] == pytest.approx(10.75)
    assert "CTA" in bs_2025["agent_comment"]

    is_rows = frame[
        (frame["control_id"] == "C9")
        & (frame["entity_id"] == "ENT_GMBH")
        & (frame["status"] == "PASS")
    ]
    assert len(is_rows) == 2  # IS translation matches exactly, both years


def test_c10_normalized_reconciles_to_source(frame):
    c10 = frame[frame["control_id"] == "C10"]
    assert set(c10["status"]) == {"PASS"}
    assert len(c10) > 0


# ------------------------------------------------
# CORRUPTION FLIPS THE RIGHT CONTROL TO FAIL
# ------------------------------------------------

def test_broken_balance_sheet_fails_c1(pipeline):
    _, normalized, _, _ = pipeline
    broken = normalized.copy()
    idx = broken.index[
        (broken["entity_id"] == "ENT_PARENT")
        & (broken["period_id"] == "FY2025")
        & (broken["standard_account_id"] == "cash")
    ][0]
    broken.loc[idx, "amount_reporting"] += 25.0

    results = control_1_balance_sheet(broken)
    hit = [r for r in results
           if r.entity_id == "ENT_PARENT" and r.period_id == "FY2025"][0]
    assert hit.status == "FAIL"
    assert hit.severity == "HIGH"
    assert hit.variance_amount == pytest.approx(25.0)


def test_broken_cash_walk_fails_c2(pipeline):
    tables, _, translated, _ = pipeline
    broken = translated.copy()
    idx = broken.index[
        (broken["entity_id"] == "ENT_PARENT")
        & (broken["period_id"] == "FY2025")
        & (broken["standard_account_id"] == "cfs_capex")
    ][0]
    broken.loc[idx, "amount_local_canonical"] -= 40.0  # CapEx now -110

    results = control_2_cash_flow(broken, tables["period_master"])
    hit = [r for r in results
           if r.entity_id == "ENT_PARENT" and r.period_id == "FY2025"][0]
    assert hit.status == "FAIL"
    assert hit.variance_amount == pytest.approx(-40.0)


def test_dropped_normalized_row_fails_c10(pipeline):
    _, normalized, translated, _ = pipeline
    broken = normalized.copy()
    idx = broken.index[
        (broken["entity_id"] == "ENT_PARENT")
        & (broken["period_id"] == "FY2025")
        & (broken["standard_account_id"] == "revenue")
    ][0]
    broken = broken.drop(index=idx)

    results = control_10_source_total(broken, translated)
    hit = [r for r in results
           if r.entity_id == "ENT_PARENT" and r.period_id == "FY2025"
           and "IS" in r.agent_comment][0]
    assert hit.status == "FAIL"
    assert "ROW COUNT MISMATCH" in hit.agent_comment


def test_account_dropped_from_consolidated_output_fails_c8(pipeline):
    """
    Review finding (2026-08-31): C8 used to check only rows that survived
    consolidation, so a dropped account group passed silently. It now
    checks the UNION of keys.
    """
    tables, _, translated, consolidated = pipeline
    broken = consolidated[
        consolidated["standard_account_id"] != "revenue"
    ].reset_index(drop=True)

    results = control_8_consolidation(translated, broken, tables["entity_master"])
    rollup = [r for r in results
              if "MISSING" in r.agent_comment and r.period_id == "FY2025"]
    assert rollup and rollup[0].status == "FAIL"


def test_misallocated_elimination_fails_c8_even_when_total_is_right(pipeline):
    """
    Review finding: folding the elimination into the pre-elimination
    column (breakdown wrong, total right) used to pass — the buckets are
    now compared individually.
    """
    tables, _, translated, consolidated = pipeline
    broken = consolidated.copy()
    mask = broken["intercompany_elimination"] != 0
    broken.loc[mask, "pre_elimination_amount"] += broken.loc[
        mask, "intercompany_elimination"]
    broken.loc[mask, "intercompany_elimination"] = 0.0

    results = control_8_consolidation(translated, broken, tables["entity_master"])
    fy2025 = [r for r in results
              if r.period_id == "FY2025" and "union" in r.agent_comment][0]
    assert fy2025.status == "FAIL"

    from financials.controls import apply_consolidation_status
    stamped = apply_consolidation_status(broken, translated, tables["entity_master"])
    assert (stamped.loc[mask.values, "control_status"] == "FAIL").all()


def test_whole_dropped_statement_group_fails_c10(pipeline):
    """
    Review finding: a normalized group missing in its entirety used to
    emit no result at all; the union of keys now surfaces it as FAIL.
    """
    _, normalized, translated, _ = pipeline
    broken = normalized[
        ~((normalized["entity_id"] == "ENT_GMBH")
          & (normalized["period_id"] == "FY2024")
          & (normalized["statement_type"] == "IS"))
    ].reset_index(drop=True)

    results = control_10_source_total(broken, translated)
    hit = [r for r in results
           if r.entity_id == "ENT_GMBH" and r.period_id == "FY2024"
           and "IS" in r.agent_comment][0]
    assert hit.status == "FAIL"
    assert "0 normalized rows vs 6 raw rows" in hit.agent_comment


def test_control_check_rows_have_unique_schema_keys(frame):
    """Review finding: C10 rows used to collide on the declared key."""
    key = list(CONTROL_CHECKS.key)
    assert not frame.duplicated(subset=key).any()


def test_prior_period_map_skips_gap_years_and_non_annual(pipeline):
    tables, _, _, _ = pipeline
    pm = tables["period_master"].copy()
    gap = pm[pm["period_id"] == "FY2026"].copy()
    gap["period_id"], gap["fiscal_year"] = "FY2032", 2032
    pm = pd.concat([pm, gap], ignore_index=True)

    prior = _prior_period_map(pm)
    assert prior["FY2025"] == "FY2024"
    assert "FY2032" not in prior          # gap year: no prior link
    assert prior.get("FY2026") == "FY2025"
    assert prior.get("FY2030") == "FY2029"


def test_unknown_balance_sheet_section_fails_loudly(pipeline):
    """Review finding: an unmapped section silently vanished from the identity."""
    _, normalized, _, _ = pipeline
    from financials.normalized_statements import balance_sheet_gap
    broken = normalized.copy()
    idx = broken.index[broken["statement_type"] == "BS"][0]
    broken.loc[idx, "statement_section"] = "mystery_section"
    with pytest.raises(ValueError, match="mystery_section"):
        balance_sheet_gap(broken)


# ------------------------------------------------
# COMMITTED OUTPUT LOCK
# ------------------------------------------------

def test_committed_control_checks_match_a_fresh_rebuild(frame):
    committed = pd.read_csv(DEFAULT_OUTPUT, dtype=str, keep_default_na=False)
    fresh = pd.read_csv(
        io.StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=False
    )
    pd.testing.assert_frame_equal(committed, fresh)

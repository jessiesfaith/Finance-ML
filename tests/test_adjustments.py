"""
Tests for the Phase 8 adjustment engine and the three views.

Fixture: ADJ-001A (+12 opex, APPROVED) + ADJ-001B (−3 tax effect,
APPROVED) apply; ADJ-002 (+5 opex, REVIEW) must not. FY2025:
REPORTED EBITDA 314.8 / NI 167.4 → NORMALIZED 326.8 / 176.4.
"""

import pandas as pd
import pytest

from financials import (
    apply_adjustments,
    build_normalized_statements,
    load_adjustments,
    load_client_fs,
    select_view,
    view_income_summary,
)
from financials.loader import ClientFSValidationError


@pytest.fixture(scope="module")
def setup():
    tables = load_client_fs(strict=True).tables
    reported = build_normalized_statements(tables)
    adjustments, issues = load_adjustments(tables, strict=True)
    assert issues == []
    combined = apply_adjustments(reported, adjustments,
                                 tables["account_mapping"])
    return tables, reported, adjustments, combined


def test_only_approved_and_included_adjustments_apply(setup):
    _, reported, adjustments, combined = setup
    adjusted = combined[combined["reported_or_adjusted"] == "ADJUSTED"]
    assert len(adjustments) == 3
    assert set(adjusted["adjustment_id"]) == {"ADJ-001A", "ADJ-001B"}
    assert "ADJ-002" not in set(adjusted["adjustment_id"])


def test_reported_rows_pass_through_byte_identical(setup):
    _, reported, _, combined = setup
    surviving = combined[
        combined["reported_or_adjusted"] == "REPORTED"
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(surviving, reported.reset_index(drop=True))


def test_three_views_income_summary(setup):
    _, _, _, combined = setup
    rep = view_income_summary(combined, "REPORTED")
    norm = view_income_summary(combined, "NORMALIZED")

    rep25 = rep[rep["period_id"] == "FY2025"].iloc[0]
    norm25 = norm[norm["period_id"] == "FY2025"].iloc[0]

    assert rep25["ebitda"] == pytest.approx(314.8)
    assert rep25["net_income"] == pytest.approx(167.4)
    assert norm25["ebitda"] == pytest.approx(326.8)    # +12 add-back
    assert norm25["ebit"] == pytest.approx(260.6)
    assert norm25["net_income"] == pytest.approx(176.4)  # +12 − 3 tax

    # FY2024 has no adjustments: views identical.
    assert rep[rep["period_id"] == "FY2024"]["net_income"].iloc[0] == \
        norm[norm["period_id"] == "FY2024"]["net_income"].iloc[0]


def test_proforma_equals_normalized_until_phase9(setup):
    _, _, _, combined = setup
    norm = view_income_summary(combined, "NORMALIZED")
    pf = view_income_summary(combined, "PROFORMA")
    pd.testing.assert_frame_equal(
        norm.drop(columns="view"), pf.drop(columns="view")
    )


def test_unknown_view_rejected(setup):
    _, _, _, combined = setup
    with pytest.raises(ValueError, match="unknown view"):
        select_view(combined, "ADJUSTED-ONLY")


def test_arithmetic_mismatch_fails_loudly(setup, tmp_path):
    tables, _, adjustments, _ = setup
    broken = adjustments.copy()
    broken.loc[0, "normalized_amount"] = -100.0   # 12 - 150 != -100
    path = tmp_path / "adjustments.csv"
    broken.to_csv(path, index=False)
    with pytest.raises(ClientFSValidationError, match="arithmetic_mismatch"):
        load_adjustments(tables, path=path, strict=True)


def test_drifted_original_quote_fails_loudly(setup):
    tables, reported, adjustments, _ = setup
    drifted = adjustments.copy()
    drifted.loc[0, "original_amount"] = -160.0    # REPORTED says -150
    drifted.loc[0, "normalized_amount"] = -148.0  # keep arithmetic valid
    with pytest.raises(ClientFSValidationError, match="original_mismatch"):
        apply_adjustments(reported, drifted, tables["account_mapping"])


def test_c10_still_reconciles_with_adjusted_rows_present(setup):
    """The Phase 8 guard: C10 counts REPORTED rows only, so appending
    ADJUSTED rows must not break source reconciliation."""
    from financials import translate_statements
    from financials.controls import control_10_source_total
    tables, _, _, combined = setup
    translated = translate_statements(tables)
    results = control_10_source_total(combined, translated)
    assert all(r.status == "PASS" for r in results), [
        r.agent_comment for r in results if r.status != "PASS"
    ]

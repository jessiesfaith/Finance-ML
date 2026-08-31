"""
Tests for the canonical sign-normalization policy (docs/SIGN_CONVENTION.md).

The critical property: two sources presenting the same economics
differently must normalize to the SAME canonical value, and a sign must
never be flipped twice (-50 expense x -1 = +50 would silently overstate
profit).

Cases A-F mirror the correction-pass requirements:
    A. positive-presented expense      (MAGNITUDE source)
    B. negative-presented expense      (SIGNED source)
    C. revenue
    D. asset
    E. liability
    F. CapEx / investing cash flow
"""

import pytest

from financials.sign_normalizer import normalize_sign


def test_a_positive_presented_expense_becomes_negative():
    # Source shows: Expense 50   (magnitude convention)
    assert normalize_sign(50, -1, "MAGNITUDE") == -50


def test_b_negative_presented_expense_stays_negative():
    # Source shows: Expense -50  (already signed) — the multiplier must NOT
    # be applied a second time.
    assert normalize_sign(-50, -1, "SIGNED") == -50


def test_a_and_b_are_economically_identical():
    """The same 50 expense, presented both ways, normalizes identically."""
    assert (
        normalize_sign(50, -1, "MAGNITUDE")
        == normalize_sign(-50, -1, "SIGNED")
        == -50
    )


def test_c_revenue_positive_under_both_presentations():
    assert normalize_sign(100, 1, "MAGNITUDE") == 100
    assert normalize_sign(100, 1, "SIGNED") == 100


def test_d_asset_stays_positive():
    assert normalize_sign(480, 1, "MAGNITUDE") == 480


def test_e_liability_stays_positive():
    # Liabilities are positive in their natural (credit) direction.
    assert normalize_sign(320, 1, "MAGNITUDE") == 320


def test_f_capex_is_a_negative_cash_flow_under_both_presentations():
    # Signed CFS export: CapEx already -70.
    assert normalize_sign(-70, -1, "SIGNED") == -70
    # Magnitude presentation: "Capital expenditures  70".
    assert normalize_sign(70, -1, "MAGNITUDE") == -70


def test_contra_activity_in_a_magnitude_source_flips_meaningfully():
    # An expense line of -5 in a magnitude source is a credit/refund:
    # economically income-increasing, so canonical +5.
    assert normalize_sign(-5, -1, "MAGNITUDE") == 5


def test_unknown_convention_is_rejected():
    with pytest.raises(ValueError, match="source_sign_convention"):
        normalize_sign(100, 1, "NEGATED")


def test_invalid_multiplier_is_rejected():
    with pytest.raises(ValueError, match="sign_multiplier"):
        normalize_sign(100, 0, "MAGNITUDE")

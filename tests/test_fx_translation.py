"""
Tests for the FX translation engine: correct rate type per item, RE
roll-forward, the CTA plug, and the per-row variance against the source
file's own translation. Expected values are hand-calculated from the
COMP001 fixture (EUR sub, FY2024 avg 1.05 / closing 1.08 / historical
1.00; FY2025 avg 1.08 / closing 1.10).
"""

import pandas as pd
import pytest

from financials.fx_translation import rate_type_for, translate_statements
from financials.loader import ClientFSValidationError, load_client_fs


@pytest.fixture(scope="module")
def translated():
    return translate_statements(load_client_fs(strict=True).tables)


def row(translated, entity, period, account):
    match = translated[
        (translated["entity_id"] == entity)
        & (translated["period_id"] == period)
        & (translated["standard_account_id"] == account)
    ]
    assert len(match) == 1
    return match.iloc[0]


def test_rate_type_policy():
    assert rate_type_for("IS", "revenue") == "AVERAGE"
    assert rate_type_for("CFS", "cfs_capex") == "AVERAGE"
    assert rate_type_for("BS", "cash") == "CLOSING"
    assert rate_type_for("BS", "common_stock") == "HISTORICAL"
    assert rate_type_for("BS", "retained_earnings") == "ROLLFORWARD"


def test_domestic_entity_translates_at_one_with_zero_variance(translated):
    parent = translated[
        (translated["entity_id"] == "ENT_PARENT")
        & (translated["origin"] == "SOURCE")
    ]
    assert set(parent["rate_type_applied"]) == {"NONE"}
    assert parent["fx_translation_variance"].abs().max() == pytest.approx(0.0)


def test_income_statement_uses_average_rate(translated):
    revenue = row(translated, "ENT_GMBH", "FY2025", "revenue")
    assert revenue["rate_type_applied"] == "AVERAGE"
    assert revenue["fx_rate_applied"] == pytest.approx(1.08)
    assert revenue["calculated_reporting_amount"] == pytest.approx(324.0)
    assert revenue["fx_translation_variance"] == pytest.approx(0.0)


def test_balance_sheet_uses_closing_rate(translated):
    cash = row(translated, "ENT_GMBH", "FY2025", "cash")
    assert cash["rate_type_applied"] == "CLOSING"
    assert cash["calculated_reporting_amount"] == pytest.approx(44.0)  # 40 x 1.10


def test_common_stock_uses_historical_rate(translated):
    cs = row(translated, "ENT_GMBH", "FY2025", "common_stock")
    assert cs["rate_type_applied"] == "HISTORICAL"
    assert cs["calculated_reporting_amount"] == pytest.approx(80.0)   # 80 x 1.00
    # The source translated it at closing (88) — the shortcut is visible:
    assert cs["source_reported_canonical"] == pytest.approx(88.0)
    assert cs["fx_translation_variance"] == pytest.approx(8.0)


def test_retained_earnings_roll_forward(translated):
    # Beginning RE (EUR 8 @ historical 1.00) + FY2024 NI 27 @ 1.05
    # = 36.35; + FY2025 NI 30 @ 1.08 = 68.75.
    re_2024 = row(translated, "ENT_GMBH", "FY2024", "retained_earnings")
    re_2025 = row(translated, "ENT_GMBH", "FY2025", "retained_earnings")
    assert re_2024["calculated_reporting_amount"] == pytest.approx(36.35)
    assert re_2025["calculated_reporting_amount"] == pytest.approx(68.75)


def test_cta_plug_balances_the_translated_books(translated):
    cta_2024 = row(translated, "ENT_GMBH", "FY2024", "cta_aoci")
    cta_2025 = row(translated, "ENT_GMBH", "FY2025", "cta_aoci")
    assert cta_2024["origin"] == "FX_ENGINE"
    assert cta_2024["calculated_reporting_amount"] == pytest.approx(7.85)
    assert cta_2025["calculated_reporting_amount"] == pytest.approx(10.75)


def test_source_shortcut_variances_sum_exactly_to_the_cta(translated):
    """
    The fixture's source file translated the whole balance sheet at the
    closing rate. Its equity-account variances against the correct
    methodology must equal the CTA — the shortcut and the missing CTA are
    the same money.
    """
    for period, cta_expected in (("FY2024", 7.85), ("FY2025", 10.75)):
        variances = translated[
            (translated["entity_id"] == "ENT_GMBH")
            & (translated["period_id"] == period)
            & (translated["origin"] == "SOURCE")
            & (translated["statement_type"] == "BS")
        ]["fx_translation_variance"].sum()
        assert variances == pytest.approx(cta_expected)


def test_missing_rate_fails_loudly():
    tables = load_client_fs(strict=True).tables
    fx = tables["fx_rates"]
    tables["fx_rates"] = fx[fx["rate_type"] != "AVERAGE"].reset_index(drop=True)

    with pytest.raises(ClientFSValidationError, match="missing_fx_rate"):
        translate_statements(tables)


def test_lineage_survives_translation(translated):
    revenue = row(translated, "ENT_GMBH", "FY2025", "revenue")
    assert revenue["source_system"] == "DATEV"
    assert revenue["source_account_code"] == "8400"
    assert revenue["load_id"] == "L20260831-001"

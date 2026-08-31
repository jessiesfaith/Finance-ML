"""
Tests for account-mapping resolution (financials/account_mapper.py):
per-system identity, company-specific override precedence, and the
fail-loudly guards.
"""

import pandas as pd
import pytest

from financials.account_mapper import resolve_mapping
from financials.loader import load_client_fs


@pytest.fixture(scope="module")
def tables():
    return load_client_fs(strict=True).tables


def test_every_fixture_row_resolves(tables):
    mapped, issues = resolve_mapping(
        tables["client_fs_raw"], tables["account_mapping"]
    )
    assert issues == [], [str(i) for i in issues]
    assert len(mapped) == len(tables["client_fs_raw"])
    assert mapped["standard_account_id"].notna().all()


def test_same_code_resolves_differently_per_system(tables):
    """Code 4000 = Revenue in NETSUITE but Other Operating Income in DATEV."""
    raw = tables["client_fs_raw"].copy()
    datev_row = raw[raw["source_system"] == "DATEV"].iloc[[0]].copy()
    datev_row["source_account_code"] = "4000"
    raw = pd.concat([raw, datev_row], ignore_index=True)

    mapped, issues = resolve_mapping(raw, tables["account_mapping"])
    # A statement_type mismatch is fine here only if types agree; the DATEV
    # 4000 mapping row is an IS account and we cloned an IS row.
    assert issues == [], [str(i) for i in issues]

    by_system = mapped[mapped["source_account_code"] == "4000"]
    assert set(
        by_system.loc[by_system["source_system"] == "NETSUITE", "standard_account_id"]
    ) == {"revenue"}
    assert set(
        by_system.loc[by_system["source_system"] == "DATEV", "standard_account_id"]
    ) == {"other_operating_income"}


def test_company_specific_mapping_overrides_the_default(tables):
    """
    When both a reusable default and a COMP001-specific row exist for the
    same (system, code), the company-specific row must win.
    """
    mapping = tables["account_mapping"].copy()
    override = mapping[
        (mapping["source_system"] == "NETSUITE")
        & (mapping["source_account_code"] == "4000")
    ].iloc[[0]].copy()
    override["company_id"] = "COMP001"
    override["standard_account_id"] = "revenue_company_specific"
    mapping = pd.concat([mapping, override], ignore_index=True)

    mapped, issues = resolve_mapping(tables["client_fs_raw"], mapping)
    assert issues == []

    netsuite_4000 = mapped[
        (mapped["source_system"] == "NETSUITE")
        & (mapped["source_account_code"] == "4000")
    ]
    assert set(netsuite_4000["standard_account_id"]) == {"revenue_company_specific"}


def test_unmapped_row_is_an_error(tables):
    raw = tables["client_fs_raw"].copy()
    raw.loc[0, "source_account_code"] = "NOPE-404"

    _, issues = resolve_mapping(raw, tables["account_mapping"])
    assert [i.rule for i in issues] == ["unmapped_account"]
    assert "NETSUITE/NOPE-404" in issues[0].message


def test_statement_type_mismatch_is_an_error(tables):
    """A raw row typed BS mapping to an IS account must fail loudly."""
    raw = tables["client_fs_raw"].copy()
    assert raw.loc[0, "source_account_code"] == "4000"   # an IS account
    raw.loc[0, "statement_type"] = "BS"

    _, issues = resolve_mapping(raw, tables["account_mapping"])
    assert [i.rule for i in issues] == ["statement_type_mismatch"]
    assert "raw=BS" in issues[0].message
    assert "mapping=IS" in issues[0].message

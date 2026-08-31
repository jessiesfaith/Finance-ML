"""
Unit tests for every validation rule, exercised by deliberately breaking
in-memory copies of the valid COMP001 fixture data.

Each test corrupts ONE thing and asserts the validator reports exactly that
problem — this is how the loader spec's failure modes (spec section 24) are
proven to work.
"""

import pandas as pd
import pytest

from financials import validator
from financials.loader import DEFAULT_DATA_DIR, _read_csv
from financials.schemas import ALL_SCHEMAS, SCHEMAS_BY_TABLE


@pytest.fixture()
def tables():
    """Fresh string-typed copies of the valid fixture CSVs for mutating."""
    return {s.table: _read_csv(DEFAULT_DATA_DIR / s.filename) for s in ALL_SCHEMAS}


def rules(issues):
    return {i.rule for i in issues}


def test_valid_fixture_has_no_issues(tables):
    all_issues = []
    for schema in ALL_SCHEMAS:
        all_issues += validator.validate_table(tables[schema.table], schema)
    all_issues += validator.validate_cross_table(tables)
    assert all_issues == [], [str(i) for i in all_issues]


# ------------------------------------------------
# SINGLE-TABLE RULES
# ------------------------------------------------

def test_missing_column_is_an_error(tables):
    broken = tables["client_fs_raw"].drop(columns=["amount_reporting"])
    issues = validator.check_columns(broken, SCHEMAS_BY_TABLE["client_fs_raw"])
    assert rules(issues) == {"missing_columns"}
    assert "amount_reporting" in issues[0].message


def test_unexpected_column_is_a_warning(tables):
    extra = tables["fx_rates"].copy()
    extra["typo_column"] = "x"
    issues = validator.check_columns(extra, SCHEMAS_BY_TABLE["fx_rates"])
    assert issues[0].severity == "WARNING"
    assert issues[0].rule == "unexpected_columns"


def test_blank_required_value_is_an_error(tables):
    broken = tables["client_fs_raw"].copy()
    broken.loc[0, "entity_id"] = ""
    issues = validator.check_required_values(broken, SCHEMAS_BY_TABLE["client_fs_raw"])
    assert rules(issues) == {"missing_value"}
    assert "entity_id" in issues[0].message


def test_non_numeric_amount_is_an_error(tables):
    broken = tables["client_fs_raw"].copy()
    broken.loc[0, "amount_local"] = "nine hundred"
    issues = validator.check_types(broken, SCHEMAS_BY_TABLE["client_fs_raw"])
    assert "bad_type" in rules(issues)


def test_bad_date_is_an_error(tables):
    broken = tables["period_master"].copy()
    broken.loc[0, "period_start"] = "01/01/2024"   # not ISO format
    issues = validator.check_types(broken, SCHEMAS_BY_TABLE["period_master"])
    assert "bad_type" in rules(issues)


def test_invalid_statement_type_is_an_error(tables):
    broken = tables["client_fs_raw"].copy()
    broken.loc[0, "statement_type"] = "INCOME"     # must be IS
    issues = validator.check_types(broken, SCHEMAS_BY_TABLE["client_fs_raw"])
    assert "invalid_value" in rules(issues)
    assert "statement_type" in " ".join(i.message for i in issues)


def test_duplicate_key_is_an_error(tables):
    raw = tables["client_fs_raw"]
    broken = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
    issues = validator.check_duplicate_keys(broken, SCHEMAS_BY_TABLE["client_fs_raw"])
    assert rules(issues) == {"duplicate_key"}


# ------------------------------------------------
# CROSS-TABLE RULES
# ------------------------------------------------

def test_unknown_entity_id_is_an_error(tables):
    tables["client_fs_raw"].loc[0, "entity_id"] = "ENT_DOES_NOT_EXIST"
    issues = validator.check_references(tables)
    assert "unknown_entity" in rules(issues)
    assert "ENT_DOES_NOT_EXIST" in " ".join(i.message for i in issues)


def test_unknown_period_id_is_an_error(tables):
    tables["client_fs_raw"].loc[0, "period_id"] = "FY1999"
    issues = validator.check_references(tables)
    assert "unknown_period" in rules(issues)


def test_unknown_parent_entity_is_an_error(tables):
    tables["entity_master"].loc[1, "parent_entity_id"] = "ENT_GHOST"
    issues = validator.check_references(tables)
    assert "unknown_parent_entity" in rules(issues)


def test_unmapped_account_is_an_error(tables):
    tables["client_fs_raw"].loc[0, "source_account_code"] = "9999-UNMAPPED"
    issues = validator.check_unmapped_accounts(tables)
    assert rules(issues) == {"unmapped_account"}
    assert "9999-UNMAPPED" in issues[0].message


def test_missing_fx_rate_is_an_error(tables):
    """A foreign-currency row whose period has no FX rate must be flagged."""
    fx = tables["fx_rates"]
    tables["fx_rates"] = fx[fx["period_id"] != "FY2025"].reset_index(drop=True)
    issues = validator.check_currencies(tables)
    assert rules(issues) == {"missing_fx_rate"}
    assert "EUR->USD" in issues[0].message

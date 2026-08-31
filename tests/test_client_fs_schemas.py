"""
Sanity checks on the schema registry itself.

If someone edits financials/schemas.py carelessly (typo in a key column,
duplicate table registration), these tests catch it before the loader
starts producing confusing errors.
"""

from financials.schemas import ALL_SCHEMAS, SCHEMAS_BY_TABLE


def test_registry_covers_the_expected_tables():
    expected = {
        # Phase 1 inputs
        "company_master",
        "entity_master",
        "period_master",
        "account_mapping",
        "fx_rates",
        "client_fs_raw",
        # Phase 2 output
        "client_fs_normalized",
    }
    assert set(SCHEMAS_BY_TABLE) == expected


def test_key_columns_exist_in_their_schema():
    for schema in ALL_SCHEMAS:
        names = schema.column_names()
        for key_col in schema.key:
            assert key_col in names, (
                f"{schema.table}: key column '{key_col}' is not a defined column"
            )


def test_no_duplicate_column_names():
    for schema in ALL_SCHEMAS:
        names = schema.column_names()
        assert len(names) == len(set(names)), f"{schema.table} has duplicate columns"


def test_lineage_columns_are_part_of_the_raw_schema():
    """Spec section 30: every number must be traceable to file/sheet/row."""
    raw = SCHEMAS_BY_TABLE["client_fs_raw"].column_names()
    for lineage_col in ("source_file", "source_sheet", "source_row",
                        "load_id", "load_timestamp"):
        assert lineage_col in raw

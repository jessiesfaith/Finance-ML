"""
Schema registry for the client financial-statement CSV layer.

This is the single source of truth for what each CSV in data/client_fs/
must look like: exact columns, data types, which values are allowed, and
which columns form each table's unique key. The loader and validator both
read from this registry — nothing about file shape is defined anywhere else.

Column "kind" values:
    text     any string
    number   decimal number (amounts, rates, percentages)
    integer  whole number (years, row numbers, sort orders)
    date     ISO date, YYYY-MM-DD
    flag     Y or N

A column with required=False may be blank; every other column must have a
value in every row.
"""

from dataclasses import dataclass


# ------------------------------------------------
# CONTROLLED VOCABULARIES
# ------------------------------------------------

# Financial-statement types the raw layer accepts (spec section 4).
# New types (e.g. future source schedules) are added HERE, in one place.
STATEMENT_TYPES = ("IS", "BS", "CFS", "OCI", "EQUITY", "SEGMENT", "CONSOL")

# FX rate types (spec section 6).
RATE_TYPES = ("AVERAGE", "CLOSING", "HISTORICAL")

# How an entity rolls up into the consolidated company (spec section 3).
CONSOLIDATION_METHODS = ("FULL", "EQUITY_METHOD", "NOT_CONSOLIDATED", "ELIMINATION")

ENTITY_TYPES = ("PARENT", "SUBSIDIARY", "ELIMINATION")

PERIOD_TYPES = ("ANNUAL", "QUARTERLY", "MONTHLY")

NORMAL_BALANCES = ("DR", "CR")

REVIEW_STATUSES = ("APPROVED", "REVIEW", "REJECTED")

FLAGS = ("Y", "N")


# ------------------------------------------------
# SCHEMA BUILDING BLOCKS
# ------------------------------------------------

@dataclass(frozen=True)
class Column:
    name: str
    kind: str = "text"        # text | number | integer | date | flag
    required: bool = True     # False -> blank cells are allowed
    allowed: tuple = ()       # closed list of valid values; empty = any value


@dataclass(frozen=True)
class TableSchema:
    table: str                # short name used in error messages
    filename: str             # file inside data/client_fs/
    columns: tuple            # ordered Column definitions
    key: tuple                # column names that must be unique together

    def column_names(self):
        return [c.name for c in self.columns]


# ------------------------------------------------
# TABLE SCHEMAS (spec sections 3, 4, 6)
# ------------------------------------------------

COMPANY_MASTER = TableSchema(
    table="company_master",
    filename="company_master.csv",
    columns=(
        Column("company_id"),
        Column("company_name"),
        Column("reporting_currency"),
        Column("fiscal_year_end"),
        Column("accounting_standard"),
        Column("source_system"),
        Column("industry"),
        Column("subindustry", required=False),
        Column("country"),
        Column("active_flag", kind="flag", allowed=FLAGS),
    ),
    key=("company_id",),
)

ENTITY_MASTER = TableSchema(
    table="entity_master",
    filename="entity_master.csv",
    columns=(
        Column("company_id"),
        Column("entity_id"),
        Column("parent_entity_id", required=False),  # blank for the top parent
        Column("entity_name"),
        Column("entity_type", allowed=ENTITY_TYPES),
        Column("ownership_pct", kind="number"),
        Column("functional_currency"),
        Column("country"),
        Column("consolidation_method", allowed=CONSOLIDATION_METHODS),
        Column("elimination_entity_flag", kind="flag", allowed=FLAGS),
        Column("active_flag", kind="flag", allowed=FLAGS),
    ),
    key=("entity_id",),
)

PERIOD_MASTER = TableSchema(
    table="period_master",
    filename="period_master.csv",
    columns=(
        Column("period_id"),
        Column("fiscal_year", kind="integer"),
        Column("fiscal_quarter", kind="integer", required=False),  # blank for annual
        Column("period_start", kind="date"),
        Column("period_end", kind="date"),
        Column("period_type", allowed=PERIOD_TYPES),
        Column("is_historical", kind="flag", allowed=FLAGS),
        Column("is_forecast", kind="flag", allowed=FLAGS),
        Column("days_in_period", kind="integer"),
    ),
    key=("period_id",),
)

ACCOUNT_MAPPING = TableSchema(
    table="account_mapping",
    filename="account_mapping.csv",
    columns=(
        Column("source_account_code"),
        Column("source_account_name"),
        Column("standard_account_id"),
        Column("standard_account_name"),
        Column("statement_type", allowed=STATEMENT_TYPES),
        Column("statement_section"),
        Column("normal_balance", allowed=NORMAL_BALANCES),
        # Applied by the Phase 2 sign normalizer; stored here so the mapping
        # table fully describes each account's treatment.
        Column("sign_multiplier", kind="integer"),
        Column("operating_classification", required=False),
        Column("nwc_classification", required=False),
        Column("ufcf_classification", required=False),
        Column("roic_classification", required=False),
        Column("share_classification", required=False),
        Column("cash_flow_classification", required=False),
        Column("oci_classification", required=False),
        Column("review_status", allowed=REVIEW_STATUSES),
    ),
    key=("source_account_code",),
)

FX_RATES = TableSchema(
    table="fx_rates",
    filename="fx_rates.csv",
    columns=(
        Column("rate_date", kind="date"),
        Column("period_id"),
        Column("from_currency"),
        Column("to_currency"),
        Column("rate_type", allowed=RATE_TYPES),
        Column("fx_rate", kind="number"),
        Column("source"),
        Column("source_reference", required=False),
    ),
    key=("period_id", "from_currency", "to_currency", "rate_type"),
)

CLIENT_FS_RAW = TableSchema(
    table="client_fs_raw",
    filename="client_fs_raw.csv",
    columns=(
        Column("company_id"),
        Column("entity_id"),
        Column("period_id"),
        Column("statement_type", allowed=STATEMENT_TYPES),
        Column("source_account_code"),
        Column("source_account_name"),
        Column("amount_local", kind="number"),
        Column("local_currency"),
        Column("fx_rate_to_reporting", kind="number"),
        Column("amount_reporting", kind="number"),
        Column("reporting_currency"),
        Column("scenario"),
        # Source lineage: exactly which file / sheet / row produced the number.
        Column("source_file"),
        Column("source_sheet"),
        Column("source_row", kind="integer"),
        Column("source_note", required=False),
        Column("load_id"),
        Column("load_timestamp"),
    ),
    # One amount per account, per entity, per period, per statement, per
    # scenario. A re-load replaces rows; it must not silently duplicate them.
    key=(
        "company_id",
        "entity_id",
        "period_id",
        "statement_type",
        "source_account_code",
        "scenario",
    ),
)


# Registry the loader iterates over. Order matters only for readability.
ALL_SCHEMAS = (
    COMPANY_MASTER,
    ENTITY_MASTER,
    PERIOD_MASTER,
    ACCOUNT_MAPPING,
    FX_RATES,
    CLIENT_FS_RAW,
)

SCHEMAS_BY_TABLE = {s.table: s for s in ALL_SCHEMAS}

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

# How a SOURCE presents an account's numbers (see docs/SIGN_CONVENTION.md):
#   MAGNITUDE  the source shows the account's size with direction implied by
#              the account itself (an expense appears as +50)
#   SIGNED     the source already reports signed economic values
#              (an expense appears as -50, CapEx as -70)
# The sign normalizer uses this to reach ONE canonical convention without
# ever double-flipping a sign.
SIGN_CONVENTIONS = ("MAGNITUDE", "SIGNED")

ENTITY_TYPES = ("PARENT", "SUBSIDIARY", "ELIMINATION")

PERIOD_TYPES = ("ANNUAL", "QUARTERLY", "MONTHLY")

NORMAL_BALANCES = ("DR", "CR")

# Scale of monetary amounts in a company's statements (docs/SCHEMAS.md
# "Units"). Canonical internal convention is MILLIONS; ingestion adapters
# convert anything else at load and record the conversion in lineage.
AMOUNT_SCALES = ("MILLIONS", "THOUSANDS", "ONES")

REVIEW_STATUSES = ("APPROVED", "REVIEW", "REJECTED")

FLAGS = ("Y", "N")

# Normalized-layer row provenance (spec section 5): REPORTED rows come
# straight from the source statements; ADJUSTED rows are created by the
# Phase 8 adjustment engine and always reference an adjustment_id.
REPORTED_OR_ADJUSTED = ("REPORTED", "ADJUSTED")

# Whether a row participates in the normalized / pro forma views
# (spec section 10): YES, NO, or REVIEW while an analyst decides.
INCLUDE_FLAGS = ("YES", "NO", "REVIEW")


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
        # Scale of every monetary amount in this company's files —
        # added 2026-08-31 (audit conformance gap g): amounts were
        # implicitly $M with nothing declaring it.
        Column("amount_scale", allowed=AMOUNT_SCALES),
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
        # Blank company_id = a reusable default mapping that applies to any
        # company. A row with a company_id is company-specific and overrides
        # the default for that company (override resolution lands in the
        # Phase 2 mapper; the validator already honors applicability).
        Column("company_id", required=False),
        # ERP systems reuse account codes (two systems can both have a
        # "4000"), so a code alone can never identify an account.
        Column("source_system"),
        Column("source_account_code"),
        Column("source_account_name"),
        Column("standard_account_id"),
        Column("standard_account_name"),
        Column("statement_type", allowed=STATEMENT_TYPES),
        Column("statement_section"),
        Column("normal_balance", allowed=NORMAL_BALANCES),
        # The account's CANONICAL sign (+1/-1) in the analytical convention
        # defined in docs/SIGN_CONVENTION.md. Only applied when the source
        # presents magnitudes — see source_sign_convention.
        Column("sign_multiplier", kind="integer"),
        # How THIS source presents THIS account's numbers (MAGNITUDE/SIGNED).
        # This is what prevents -100 expense x -1 = +100 accidents.
        Column("source_sign_convention", allowed=SIGN_CONVENTIONS),
        Column("operating_classification", required=False),
        Column("nwc_classification", required=False),
        # Net-debt membership (Phase 6): DEBT / CASH_AND_EQUIVALENTS /
        # RESTRICTED_CASH / EXCLUDED; blank = not part of net debt. Not
        # every liability is debt — membership is an explicit election.
        Column("netdebt_classification", required=False,
               allowed=("", "DEBT", "CASH_AND_EQUIVALENTS",
                        "RESTRICTED_CASH", "EXCLUDED")),
        Column("ufcf_classification", required=False),
        Column("roic_classification", required=False),
        Column("share_classification", required=False),
        Column("cash_flow_classification", required=False),
        Column("oci_classification", required=False),
        Column("review_status", allowed=REVIEW_STATUSES),
    ),
    key=("company_id", "source_system", "source_account_code"),
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
        # Which ERP/export produced this row — needed to resolve the account
        # against account_mapping, whose key includes source_system.
        Column("source_system"),
        Column("source_account_code"),
        Column("source_account_name"),
        # amount_local is the authoritative source amount.
        Column("amount_local", kind="number"),
        Column("local_currency"),
        Column("fx_rate_to_reporting", kind="number"),
        # amount_reporting is the SOURCE-REPORTED reporting-currency amount,
        # kept for reconciliation only. Phase 3's FX engine computes its own
        # calculated_reporting_amount from amount_local x the correct rate
        # and reports any variance against this — it never trusts this
        # column as the translation result.
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


CLIENT_FS_NORMALIZED = TableSchema(
    table="client_fs_normalized",
    filename="client_fs_normalized.csv",
    columns=(
        Column("company_id"),
        Column("entity_id"),
        Column("period_id"),
        Column("statement_type", allowed=STATEMENT_TYPES),
        Column("standard_account_id"),
        Column("standard_account_name"),
        Column("statement_section"),
        # Canonical-sign amount in reporting currency. Until the Phase 3 FX
        # engine lands, this derives from the SOURCE-REPORTED reporting
        # amount (see decision #19/#22 in docs/DECISIONS.md).
        Column("amount_reporting", kind="number"),
        Column("reporting_currency"),
        Column("scenario"),
        Column("reported_or_adjusted", allowed=REPORTED_OR_ADJUSTED),
        Column("adjustment_id", required=False),    # Phase 8
        Column("transaction_id", required=False),   # Phase 9
        Column("include_in_normalized", allowed=INCLUDE_FLAGS),
        Column("include_in_proforma", allowed=INCLUDE_FLAGS),
        # Sign-normalization audit trail: raw amount -> rule -> normalized
        # amount, so every transformation is reproducible and reviewable.
        Column("source_system"),
        Column("source_account_code"),
        Column("amount_source", kind="number"),
        Column("sign_multiplier", kind="integer"),
        Column("source_sign_convention", allowed=SIGN_CONVENTIONS),
        Column("load_id"),
    ),
    # Row-level (one normalized row per raw row) so lineage survives; the
    # adjustment layer adds ADJUSTED rows alongside REPORTED ones.
    key=(
        "company_id",
        "entity_id",
        "period_id",
        "statement_type",
        "source_system",
        "source_account_code",
        "scenario",
        "reported_or_adjusted",
        "adjustment_id",
    ),
)


# Registry of INPUT files the loader requires in data/client_fs/.
# (client_fs_normalized is an OUTPUT the pipeline writes, never a
# required input — see OUTPUT_SCHEMAS.)
ALL_SCHEMAS = (
    COMPANY_MASTER,
    ENTITY_MASTER,
    PERIOD_MASTER,
    ACCOUNT_MAPPING,
    FX_RATES,
    CLIENT_FS_RAW,
)

ENTITY_CONSOLIDATION = TableSchema(
    table="entity_consolidation",
    filename="entity_consolidation.csv",
    columns=(
        Column("company_id"),
        Column("period_id"),
        # "CONSOLIDATED" on every row — per-entity detail lives in the
        # translated layer, this table is the roll-up (DECISIONS.md #27).
        Column("entity_id"),
        Column("standard_account_id"),
        Column("standard_account_name"),
        Column("statement_type", allowed=STATEMENT_TYPES),
        Column("statement_section"),
        Column("pre_elimination_amount", kind="number"),
        Column("intercompany_elimination", kind="number"),
        Column("other_consolidation_adjustment", kind="number"),
        Column("fx_translation_adjustment", kind="number"),
        Column("consolidated_amount", kind="number"),
        Column("reporting_currency"),
        Column("scenario"),
        Column("control_status", allowed=("PASS", "REVIEW", "FAIL", "PENDING")),
        Column("control_variance", kind="number", required=False),
    ),
    key=("company_id", "period_id", "standard_account_id", "scenario"),
)


CONTROL_CHECKS = TableSchema(
    table="control_checks",
    filename="control_checks.csv",
    columns=(
        Column("company_id"),
        Column("period_id"),
        Column("entity_id"),
        Column("control_id"),
        Column("control_name"),
        Column("control_category"),
        Column("expected_value", kind="number"),
        Column("actual_value", kind="number"),
        Column("variance_amount", kind="number"),
        Column("variance_pct", kind="number", required=False),
        Column("tolerance_amount", kind="number"),
        Column("tolerance_pct", kind="number", required=False),
        Column("status", allowed=("PASS", "REVIEW", "FAIL")),
        Column("severity", allowed=("LOW", "MEDIUM", "HIGH")),
        # Engine-generated explanation today; the Phase 10 analyst agent
        # appends its interpretation here without ever changing status.
        Column("agent_comment", required=False),
        Column("reviewer_comment", required=False),
        Column("review_status", allowed=("PENDING", "APPROVED", "REJECTED")),
        Column("source_reference"),
    ),
    key=("company_id", "period_id", "entity_id", "control_id",
         "control_name", "source_reference"),
)


UFCF_FORECAST = TableSchema(
    table="ufcf_forecast",
    filename="ufcf_forecast.csv",
    columns=(
        Column("company_id"),
        Column("period_id"),
        Column("scenario"),
        # Analyst-facing walk table: every value a positive magnitude,
        # with the math UFCF = nopat + da - capex - delta_nwc.
        Column("revenue", kind="number"),
        Column("revenue_growth_pct", kind="number", required=False),
        Column("ebitda", kind="number"),
        Column("ebitda_margin_pct", kind="number"),
        Column("da", kind="number"),
        Column("ebit", kind="number"),
        Column("ebit_margin_pct", kind="number"),
        # The rate USED for NOPAT (analyst normalized). The reported
        # effective rate stays visible in the income-walk output/docs.
        Column("tax_rate_pct", kind="number"),
        Column("nopat", kind="number"),
        Column("accounts_receivable", kind="number", required=False),
        Column("inventory", kind="number", required=False),
        Column("other_operating_current_assets", kind="number", required=False),
        Column("accounts_payable", kind="number", required=False),
        Column("other_operating_current_liabilities", kind="number", required=False),
        Column("operating_nwc", kind="number", required=False),
        Column("delta_nwc", kind="number", required=False),
        Column("capex", kind="number", required=False),
        Column("ufcf", kind="number", required=False),
        Column("reporting_currency"),
        Column("forecast_method", allowed=("ACTUAL", "DRIVER_BASED")),
        Column("review_status", allowed=REVIEW_STATUSES),
    ),
    key=("company_id", "period_id", "scenario"),
)


# ------------------------------------------------
# SCENARIO / DRIVER TABLES (data/scenarios/ — kind C, analyst assumptions)
# ------------------------------------------------

SCENARIO_TYPES = ("BASE", "UPSIDE", "DOWNSIDE", "STRESS", "CUSTOM")
SCENARIO_STATUSES = ("DRAFT", "APPROVED", "REJECTED")
TARGET_TYPES = ("COMPANY_DRIVER", "MARKET_METRIC")
OVERRIDE_TYPES = ("ABSOLUTE", "DELTA")

DRIVER_MASTER = TableSchema(
    table="driver_master",
    filename="driver_master.csv",
    columns=(
        Column("driver_id"),
        Column("driver_name"),
        Column("unit"),
        Column("description"),
    ),
    key=("driver_id",),
)

SCENARIO_MASTER = TableSchema(
    table="scenario_master",
    filename="scenario_master.csv",
    columns=(
        Column("scenario_id"),
        Column("scenario_name"),
        Column("scenario_type", allowed=SCENARIO_TYPES),
        Column("as_of_date", kind="date"),
        Column("narrative"),
        Column("status", allowed=SCENARIO_STATUSES),
        Column("scenario_sort", kind="integer"),
        Column("created_by"),
    ),
    key=("scenario_id",),
)

SCENARIO_ASSUMPTIONS = TableSchema(
    table="scenario_assumptions",
    filename="scenario_assumptions.csv",
    columns=(
        Column("scenario_id"),
        Column("target_type", allowed=TARGET_TYPES),
        Column("target_id"),
        Column("company_id", required=False),   # blank = every company
        Column("period_id", required=False),    # blank = every forecast period
        Column("override_type", allowed=OVERRIDE_TYPES),
        Column("value", kind="number"),
        Column("unit"),
        Column("rationale"),
        Column("source"),
    ),
    key=("scenario_id", "target_type", "target_id", "company_id", "period_id"),
)

# Event classifications for adjustments / transactions (spec section 10).
EVENT_CLASSIFICATIONS = (
    "NORMAL_OPERATIONS", "ONE_TIME", "ACQUISITION", "DIVESTITURE",
    "RESTRUCTURING", "IMPAIRMENT", "LITIGATION", "FX", "FINANCING",
    "TAX", "ACCOUNTING_CHANGE", "OTHER",
)

ADJUSTMENTS = TableSchema(
    table="adjustments",
    filename="adjustments.csv",
    columns=(
        Column("adjustment_id"),
        Column("company_id"),
        Column("entity_id"),
        Column("period_id"),
        Column("standard_account_id"),
        Column("adjustment_type",
               allowed=("NORMALIZATION", "RECLASSIFICATION", "OTHER")),
        # Canonical signs throughout: original + adjustment = normalized,
        # verified on every load. The original REPORTED amount is quoted,
        # never modified — adjustments are separate, additive rows.
        Column("original_amount", kind="number"),
        Column("adjustment_amount", kind="number"),
        Column("normalized_amount", kind="number"),
        Column("reporting_currency"),
        Column("reason"),
        Column("event_classification", allowed=EVENT_CLASSIFICATIONS),
        Column("source_document"),
        Column("source_reference"),
        # Filled by the Phase 10 agent for agent-proposed adjustments;
        # blank for analyst-entered ones.
        Column("agent_confidence", kind="number", required=False),
        Column("include_in_normalized", allowed=INCLUDE_FLAGS),
        Column("review_status", allowed=REVIEW_STATUSES),
        Column("reviewer", required=False),
        Column("approval_timestamp", required=False),
    ),
    key=("adjustment_id",),
)

EVENT_TYPES = (
    "ACQUISITION", "MERGER", "DIVESTITURE", "ASSET_SALE", "RESTRUCTURING",
    "IMPAIRMENT", "DEBT_REFINANCING", "EQUITY_ISSUANCE", "MAJOR_INVESTMENT",
    "OTHER",
)

TRANSACTION_EVENTS = TableSchema(
    table="transaction_events",
    filename="transaction_events.csv",
    columns=(
        Column("transaction_id"),
        Column("company_id"),
        Column("event_date", kind="date"),
        Column("event_type", allowed=EVENT_TYPES),
        Column("event_name"),
        Column("target_or_asset"),
        Column("purchase_price_or_proceeds", kind="number"),
        Column("currency"),
        Column("debt_assumed", kind="number"),
        Column("cash_paid", kind="number"),
        Column("equity_issued", kind="number"),
        Column("goodwill_created", kind="number"),
        Column("intangibles_created", kind="number"),
        Column("expected_synergies", kind="number"),
        Column("restructuring_costs", kind="number"),
        Column("divested_revenue", kind="number"),
        Column("divested_ebitda", kind="number"),
        Column("narrative_summary"),
        Column("source_document"),
        Column("source_reference"),
        # Y when deterministic outlier flags exist in the event's period —
        # a LINK for investigation, never a causation conclusion.
        Column("agent_outlier_flag", kind="flag", allowed=FLAGS),
        Column("review_status", allowed=REVIEW_STATUSES),
    ),
    key=("transaction_id",),
)

PROFORMA_TYPES = (
    "ACQUISITION", "DIVESTITURE", "COST_SYNERGY", "REVENUE_SYNERGY",
    "RESTRUCTURING", "FINANCING", "RUN_RATE", "PURCHASE_ACCOUNTING",
    "DISCONTINUED_OPS", "OTHER",
)

PROFORMA_ADJUSTMENTS = TableSchema(
    table="proforma_adjustments",
    filename="proforma_adjustments.csv",
    columns=(
        Column("proforma_id"),
        Column("transaction_id"),
        Column("company_id"),
        Column("entity_id"),      # spec addition: pro forma rows land at
        Column("period_id"),      # entity grain like every other layer
        Column("standard_account_id"),
        Column("proforma_type", allowed=PROFORMA_TYPES),
        # Pro forma builds on NORMALIZED STANDALONE (spec section 12 flow),
        # so reported_amount here quotes the NORMALIZED base — verified
        # against the normalized view on every apply.
        Column("reported_amount", kind="number"),
        Column("proforma_adjustment", kind="number"),
        Column("proforma_amount", kind="number"),
        Column("reporting_currency"),
        Column("synergy_flag", kind="flag", allowed=FLAGS),
        Column("run_rate_flag", kind="flag", allowed=FLAGS),
        Column("one_time_flag", kind="flag", allowed=FLAGS),
        Column("source_document"),
        Column("source_reference"),
        Column("review_status", allowed=REVIEW_STATUSES),
    ),
    key=("proforma_id",),
)

SCENARIO_SCHEMAS = (DRIVER_MASTER, SCENARIO_MASTER, SCENARIO_ASSUMPTIONS)

SHARES_DILUTION = TableSchema(
    table="shares_dilution",
    filename="shares_dilution.csv",
    columns=(
        Column("company_id"),
        Column("period_id"),
        Column("scenario"),
        Column("basic_shares_m", kind="number"),
        Column("options_outstanding_m", kind="number"),
        Column("weighted_avg_strike", kind="number"),
        # Market price is MARKET DATA (synthetic + labeled until live).
        Column("market_price", kind="number"),
        # Calculated by financials/shares.py (treasury-stock method) and
        # verified on every load — a stated count that doesn't reproduce
        # from its own inputs refuses to load.
        Column("incremental_option_shares_m", kind="number"),
        Column("rsus_psus_m", kind="number"),
        Column("convertible_shares_m", kind="number"),
        Column("other_dilutive_shares_m", kind="number"),
        Column("anti_dilutive_shares_excluded_m", kind="number"),
        Column("diluted_shares_m", kind="number"),
        Column("treasury_stock_method_flag", kind="flag", allowed=FLAGS),
        Column("source_reference"),
    ),
    key=("company_id", "period_id", "scenario"),
)

VALUATION_INPUTS = TableSchema(
    table="valuation_inputs",
    filename="valuation_inputs.csv",
    columns=(
        Column("company_id"),
        Column("period_id"),
        Column("scenario"),
        # Net-debt build (magnitudes)
        Column("short_term_debt", kind="number"),
        Column("long_term_debt", kind="number"),
        Column("finance_leases", kind="number"),
        Column("total_debt", kind="number"),
        Column("cash_and_equivalents", kind="number"),
        Column("restricted_cash", kind="number"),
        Column("net_debt", kind="number"),
        # Invested-capital build
        Column("operating_nwc", kind="number"),
        Column("net_ppe", kind="number"),
        Column("other_operating_assets", kind="number"),
        Column("other_operating_liabilities", kind="number"),
        Column("invested_capital", kind="number"),
        Column("nopat", kind="number"),
        Column("roic_basis", allowed=("ENDING", "AVERAGE")),
        Column("roic_pct", kind="number"),
        # Share counts (blank where no share data exists for the period)
        Column("basic_shares_m", kind="number", required=False),
        Column("diluted_shares_m", kind="number", required=False),
        Column("reporting_currency"),
    ),
    key=("company_id", "period_id", "scenario"),
)

RISK_FREE_POLICY = TableSchema(
    table="risk_free_policy",
    filename="risk_free_policy.csv",
    columns=(
        Column("rf_methodology_id"),
        Column("source_metric_id"),
        Column("maturity_years", kind="integer"),
        Column("observation_rule", allowed=("SPOT_AS_OF", "TRAILING_AVG",
                                            "MODEL_PREDICTION")),
        Column("source"),
        Column("description"),
        Column("active_flag", kind="flag", allowed=FLAGS),
    ),
    key=("rf_methodology_id",),
)

MARKET_SCHEMAS = (RISK_FREE_POLICY,)

OUTLIER_FLAGS = TableSchema(
    table="outlier_flags",
    filename="outlier_flags.csv",
    columns=(
        Column("company_id"),
        Column("level", allowed=("CONSOLIDATED", "ENTITY")),
        Column("entity_id"),
        Column("period_id"),
        Column("prior_period_id"),
        Column("method", allowed=("POP_VARIANCE", "MARGIN_VARIANCE",
                                  "RATIO_VARIANCE", "NEW_ITEM", "ZSCORE")),
        Column("metric_name"),
        Column("statement_type", allowed=STATEMENT_TYPES),
        Column("baseline_value", kind="number"),
        Column("current_value", kind="number"),
        Column("variance_amount", kind="number"),
        # % for POP/RATIO/ZSCORE (z units), percentage POINTS for MARGIN.
        Column("variance_pct", kind="number"),
        Column("threshold_desc"),
        Column("severity", allowed=("MEDIUM", "HIGH")),
        # Deterministic candidates only — an outlier is never concluded
        # to be an error by the engine (docs/OUTLIERS.md).
        Column("possible_causes"),
        Column("review_status", allowed=("PENDING", "APPROVED", "REJECTED")),
    ),
    key=("company_id", "level", "entity_id", "period_id", "method",
         "metric_name"),
)

OUTPUT_SCHEMAS = (
    CLIENT_FS_NORMALIZED, ENTITY_CONSOLIDATION, CONTROL_CHECKS, UFCF_FORECAST,
    VALUATION_INPUTS, OUTLIER_FLAGS,
)

SCHEMAS_BY_TABLE = {
    s.table: s
    for s in (ALL_SCHEMAS + OUTPUT_SCHEMAS + SCENARIO_SCHEMAS
              + (SHARES_DILUTION, ADJUSTMENTS, TRANSACTION_EVENTS,
                 PROFORMA_ADJUSTMENTS) + MARKET_SCHEMAS)
}

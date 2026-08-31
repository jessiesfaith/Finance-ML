"""
Deterministic validation rules for the client financial-statement CSV layer.

Every rule returns a list of Issue records instead of raising immediately,
so one load surfaces ALL problems at once (an analyst fixes a file in one
pass, not one error at a time). The loader decides what to do with them:
by default any ERROR stops the pipeline — critical validation failures are
never silently ignored.

Severities:
    ERROR    the data cannot be trusted / downstream phases cannot run
    WARNING  suspicious but not blocking; surfaced for review
"""

from dataclasses import dataclass

import pandas as pd

from financials.schemas import TableSchema


@dataclass(frozen=True)
class Issue:
    severity: str      # ERROR | WARNING
    table: str         # which CSV the problem is in
    rule: str          # short machine-readable rule name
    message: str       # human-readable explanation with row references

    def __str__(self):
        return f"[{self.severity}] {self.table} :: {self.rule} :: {self.message}"


def _rows(index_list, limit=8):
    """Format offending CSV row numbers (header = line 1, data starts line 2)."""
    lines = [str(i + 2) for i in index_list[:limit]]
    suffix = "" if len(index_list) <= limit else f" (+{len(index_list) - limit} more)"
    return "csv line(s) " + ", ".join(lines) + suffix


# ------------------------------------------------
# SINGLE-TABLE RULES
# ------------------------------------------------

def check_columns(df: pd.DataFrame, schema: TableSchema):
    """The file must have exactly the schema's columns (order-insensitive)."""
    issues = []
    expected = schema.column_names()

    missing = [c for c in expected if c not in df.columns]
    unexpected = [c for c in df.columns if c not in expected]

    if missing:
        issues.append(Issue(
            "ERROR", schema.table, "missing_columns",
            f"required column(s) not found: {missing}",
        ))
    if unexpected:
        issues.append(Issue(
            "WARNING", schema.table, "unexpected_columns",
            f"column(s) not in the schema (ignored downstream): {unexpected}",
        ))
    return issues


def check_required_values(df: pd.DataFrame, schema: TableSchema):
    """Required columns must have a value in every row."""
    issues = []
    for col in schema.columns:
        if not col.required or col.name not in df.columns:
            continue
        blank = df.index[df[col.name].astype(str).str.strip() == ""].tolist()
        if blank:
            issues.append(Issue(
                "ERROR", schema.table, "missing_value",
                f"column '{col.name}' is blank on {_rows(blank)}",
            ))
    return issues


def check_types(df: pd.DataFrame, schema: TableSchema):
    """Values must parse as their declared kind and sit in the allowed list."""
    issues = []
    for col in schema.columns:
        if col.name not in df.columns:
            continue

        values = df[col.name].astype(str).str.strip()
        present = values != ""          # blanks are handled by check_required_values

        if col.kind in ("number", "integer"):
            parsed = pd.to_numeric(values[present], errors="coerce")
            bad = parsed.index[parsed.isna()].tolist()
            if col.kind == "integer" and not bad:
                nonint = parsed.index[parsed != parsed.round()].tolist()
                bad = nonint
            if bad:
                issues.append(Issue(
                    "ERROR", schema.table, "bad_type",
                    f"column '{col.name}' must be a {col.kind}; "
                    f"invalid on {_rows(bad)}",
                ))

        elif col.kind == "date":
            parsed = pd.to_datetime(values[present], format="%Y-%m-%d", errors="coerce")
            bad = parsed.index[parsed.isna()].tolist()
            if bad:
                issues.append(Issue(
                    "ERROR", schema.table, "bad_type",
                    f"column '{col.name}' must be an ISO date (YYYY-MM-DD); "
                    f"invalid on {_rows(bad)}",
                ))

        if col.allowed:
            invalid = values.index[present & ~values.isin(col.allowed)].tolist()
            if invalid:
                issues.append(Issue(
                    "ERROR", schema.table, "invalid_value",
                    f"column '{col.name}' only allows {list(col.allowed)}; "
                    f"other values on {_rows(invalid)}",
                ))
    return issues


def check_duplicate_keys(df: pd.DataFrame, schema: TableSchema):
    """The schema's key columns must be unique together."""
    key = [k for k in schema.key if k in df.columns]
    if len(key) != len(schema.key) or df.empty:
        return []

    dupes = df.index[df.duplicated(subset=key, keep=False)].tolist()
    if dupes:
        return [Issue(
            "ERROR", schema.table, "duplicate_key",
            f"duplicate {key} combination(s) on {_rows(dupes)} — a re-load "
            "must replace rows, never append duplicates",
        )]
    return []


# ------------------------------------------------
# CROSS-TABLE RULES
# ------------------------------------------------

def _missing_refs(child, child_col, parent, parent_col):
    """Rows in child whose child_col value does not exist in parent[parent_col]."""
    if child_col not in child.columns or parent_col not in parent.columns:
        return [], []
    known = set(parent[parent_col].astype(str).str.strip())
    values = child[child_col].astype(str).str.strip()
    bad_mask = (values != "") & ~values.isin(known)
    return child.index[bad_mask].tolist(), sorted(set(values[bad_mask]))


def check_references(tables):
    """Every ID used in a data row must exist in its master table."""
    issues = []
    raw = tables["client_fs_raw"]

    for child_col, parent_table, parent_col, rule in (
        ("company_id", "company_master", "company_id", "unknown_company"),
        ("entity_id", "entity_master", "entity_id", "unknown_entity"),
        ("period_id", "period_master", "period_id", "unknown_period"),
    ):
        rows, values = _missing_refs(raw, child_col, tables[parent_table], parent_col)
        if rows:
            issues.append(Issue(
                "ERROR", "client_fs_raw", rule,
                f"{child_col} value(s) {values} not found in "
                f"{parent_table} — {_rows(rows)}",
            ))

    # Entities must belong to a known company, and non-blank parents must exist.
    entities = tables["entity_master"]
    rows, values = _missing_refs(entities, "company_id", tables["company_master"], "company_id")
    if rows:
        issues.append(Issue(
            "ERROR", "entity_master", "unknown_company",
            f"company_id value(s) {values} not found in company_master — {_rows(rows)}",
        ))
    rows, values = _missing_refs(entities, "parent_entity_id", entities, "entity_id")
    if rows:
        issues.append(Issue(
            "ERROR", "entity_master", "unknown_parent_entity",
            f"parent_entity_id value(s) {values} not found in entity_master — {_rows(rows)}",
        ))

    # FX rates must reference known periods.
    rows, values = _missing_refs(tables["fx_rates"], "period_id", tables["period_master"], "period_id")
    if rows:
        issues.append(Issue(
            "ERROR", "fx_rates", "unknown_period",
            f"period_id value(s) {values} not found in period_master — {_rows(rows)}",
        ))

    return issues


def check_unmapped_accounts(tables):
    """
    Every source account in the raw data must resolve to an account_mapping
    row. Because ERP systems reuse account codes, an account is identified
    by (source_system, source_account_code) — NEVER by code alone. A
    mapping row applies to a raw row when system and code match AND the
    mapping's company_id is blank (reusable default) or equals the raw
    row's company_id (company-specific override).

    Unmapped accounts block the Phase 2 mapping step, so this is an ERROR:
    an analyst either maps the account or explicitly excludes it — the
    pipeline never guesses.
    """
    raw = tables["client_fs_raw"]
    mapping = tables["account_mapping"]

    needed_raw = {"company_id", "source_system", "source_account_code"}
    needed_map = {"company_id", "source_system", "source_account_code"}
    if not needed_raw.issubset(raw.columns) or not needed_map.issubset(mapping.columns):
        return []

    def col(df, name):
        return df[name].astype(str).str.strip()

    map_company = col(mapping, "company_id")
    map_system = col(mapping, "source_system")
    map_code = col(mapping, "source_account_code")

    # Reusable defaults (blank company_id) vs company-specific rows.
    default_keys = set(zip(map_system[map_company == ""], map_code[map_company == ""]))
    specific_keys = set(zip(
        map_company[map_company != ""],
        map_system[map_company != ""],
        map_code[map_company != ""],
    ))

    raw_company = col(raw, "company_id")
    raw_system = col(raw, "source_system")
    raw_code = col(raw, "source_account_code")

    unmapped_mask = [
        not (
            (system, code) in default_keys
            or (company, system, code) in specific_keys
        )
        for company, system, code in zip(raw_company, raw_system, raw_code)
    ]

    bad = raw.index[unmapped_mask].tolist()
    if bad:
        pairs = sorted(set(
            f"{raw_system.loc[i]}/{raw_code.loc[i]}" for i in bad
        ))
        return [Issue(
            "ERROR", "client_fs_raw", "unmapped_account",
            f"account(s) {pairs} have no applicable row in account_mapping "
            f"(matched on source_system + source_account_code, honoring "
            f"company-specific overrides) — {_rows(bad)}",
        )]
    return []


def check_currencies(tables):
    """
    Raw rows in a currency other than the reporting currency need an FX rate,
    and an FX row translating that currency for that period must exist.
    (Whether the CORRECT rate type was applied is Phase 3/4 territory —
    here we only guarantee the inputs exist.)
    """
    issues = []
    raw = tables["client_fs_raw"]
    fx = tables["fx_rates"]
    needed_cols = {"local_currency", "reporting_currency", "fx_rate_to_reporting", "period_id"}
    if not needed_cols.issubset(raw.columns):
        return issues

    local = raw["local_currency"].astype(str).str.strip()
    reporting = raw["reporting_currency"].astype(str).str.strip()
    foreign = raw[(local != "") & (reporting != "") & (local != reporting)]

    if foreign.empty:
        return issues

    if {"period_id", "from_currency", "to_currency"}.issubset(fx.columns):
        fx_pairs = set(zip(
            fx["period_id"].astype(str).str.strip(),
            fx["from_currency"].astype(str).str.strip(),
            fx["to_currency"].astype(str).str.strip(),
        ))
        missing = [
            i for i, row in foreign.iterrows()
            if (
                str(row["period_id"]).strip(),
                str(row["local_currency"]).strip(),
                str(row["reporting_currency"]).strip(),
            ) not in fx_pairs
        ]
        if missing:
            pairs = sorted(set(
                f"{foreign.loc[i, 'local_currency']}->{foreign.loc[i, 'reporting_currency']} "
                f"({foreign.loc[i, 'period_id']})"
                for i in missing
            ))
            issues.append(Issue(
                "ERROR", "fx_rates", "missing_fx_rate",
                f"no fx_rates row for {pairs} — needed by client_fs_raw {_rows(missing)}",
            ))
    return issues


# ------------------------------------------------
# ENTRY POINT
# ------------------------------------------------

def validate_table(df: pd.DataFrame, schema: TableSchema):
    """All single-table rules for one DataFrame."""
    issues = check_columns(df, schema)
    # Column-level rules only make sense on columns that exist.
    issues += check_required_values(df, schema)
    issues += check_types(df, schema)
    issues += check_duplicate_keys(df, schema)
    return issues


def validate_cross_table(tables):
    """All rules that need more than one table."""
    return (
        check_references(tables)
        + check_unmapped_accounts(tables)
        + check_currencies(tables)
    )

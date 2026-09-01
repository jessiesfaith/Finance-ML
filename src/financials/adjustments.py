"""
Adjustment engine + the three views — Phase 8 (spec sections 5, 10).

    REPORTED    = the source statements, exactly as loaded. IMMUTABLE.
    NORMALIZED  = REPORTED + approved normalization adjustments
    PRO FORMA   = NORMALIZED + transaction adjustments (Phase 9)

Adjustments live in data/client_fs/adjustments.csv as their own rows —
the original reported number is quoted, verified, and NEVER modified.
An adjustment reaches the numbers only when review_status = APPROVED
AND include_in_normalized = YES; a proposal under REVIEW stays visible
but outside every total. Every load re-verifies
original + adjustment = normalized, and that the quoted original matches
what the REPORTED layer actually says — a drifted quote fails loudly.

Applied adjustments become ADJUSTED delta rows in the normalized layer
(reported_or_adjusted = ADJUSTED, adjustment_id set), so every view is
still just a SUM over rows — the same subtotals-are-sums property the
canonical sign convention bought us.
"""

import logging
from pathlib import Path

import pandas as pd

from financials import validator
from financials.loader import ClientFSValidationError, _coerce_types, _read_csv
from financials.schemas import ADJUSTMENTS, CLIENT_FS_NORMALIZED

log = logging.getLogger("financials.adjustments")

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ADJUSTMENTS_FILE = BASE_DIR / "data" / "client_fs" / ADJUSTMENTS.filename

VIEWS = ("REPORTED", "NORMALIZED", "PROFORMA")


def load_adjustments(tables, path=None, strict=True):
    """Load + validate adjustments.csv against the loaded client tables."""
    path = Path(path) if path else DEFAULT_ADJUSTMENTS_FILE
    if not path.exists():
        return pd.DataFrame(columns=ADJUSTMENTS.column_names()), []

    raw = _read_csv(path)
    issues = validator.validate_table(raw, ADJUSTMENTS)
    frame = _coerce_types(raw, ADJUSTMENTS)

    # Arithmetic: original + adjustment = normalized, on every row.
    bad = frame.index[
        (frame["original_amount"] + frame["adjustment_amount"]
         - frame["normalized_amount"]).abs() > 0.01
    ].tolist()
    if bad:
        issues.append(validator.Issue(
            "ERROR", "adjustments", "arithmetic_mismatch",
            f"original + adjustment != normalized on {validator._rows(bad)}",
        ))

    # References: entity and period must exist.
    for column, table, ref, rule in (
        ("entity_id", "entity_master", "entity_id", "unknown_entity"),
        ("period_id", "period_master", "period_id", "unknown_period"),
    ):
        known = set(tables[table][ref])
        rows = frame.index[~frame[column].isin(known)].tolist()
        if rows:
            issues.append(validator.Issue(
                "ERROR", "adjustments", rule,
                f"{column} not found in {table} — {validator._rows(rows)}",
            ))

    if strict and any(i.severity == "ERROR" for i in issues):
        raise ClientFSValidationError(issues)
    return frame, issues


def _verify_originals(adjustments, reported):
    """The quoted original must equal what REPORTED actually says."""
    issues = []
    totals = reported.groupby(
        ["entity_id", "period_id", "standard_account_id"]
    )["amount_reporting"].sum()
    for i, row in adjustments.iterrows():
        key = (row["entity_id"], row["period_id"], row["standard_account_id"])
        actual = totals.get(key)
        if actual is None or abs(actual - row["original_amount"]) > 0.01:
            issues.append(validator.Issue(
                "ERROR", "adjustments", "original_mismatch",
                f"{row['adjustment_id']}: quotes original "
                f"{row['original_amount']} for {key}, but the REPORTED "
                f"layer says {actual} — the quote has drifted from source",
            ))
    return issues


def apply_adjustments(reported, adjustments, account_mapping, strict=True):
    """
    Append ADJUSTED delta rows for every APPROVED + include_in_normalized
    = YES adjustment. REPORTED rows pass through byte-identical.
    """
    issues = _verify_originals(adjustments, reported)
    if strict and issues:
        raise ClientFSValidationError(issues)

    applicable = adjustments[
        (adjustments["review_status"] == "APPROVED")
        & (adjustments["include_in_normalized"] == "YES")
    ]
    skipped = len(adjustments) - len(applicable)
    if skipped:
        log.info("%d adjustment(s) NOT applied (not APPROVED+YES)", skipped)

    section_of = {}
    statement_of = {}
    for row in account_mapping.itertuples():
        section_of.setdefault(row.standard_account_id, row.statement_section)
        statement_of.setdefault(row.standard_account_id, row.statement_type)
        # standard_account_name reuse below keys off the first mapping row
    name_of = {}
    for row in account_mapping.itertuples():
        name_of.setdefault(row.standard_account_id, row.standard_account_name)

    adjusted_rows = []
    for row in applicable.itertuples():
        adjusted_rows.append({
            "company_id": row.company_id,
            "entity_id": row.entity_id,
            "period_id": row.period_id,
            "statement_type": statement_of[row.standard_account_id],
            "standard_account_id": row.standard_account_id,
            "standard_account_name": name_of[row.standard_account_id],
            "statement_section": section_of[row.standard_account_id],
            "amount_reporting": row.adjustment_amount,   # the DELTA
            "reporting_currency": row.reporting_currency,
            "scenario": "ACTUAL",
            "reported_or_adjusted": "ADJUSTED",
            "adjustment_id": row.adjustment_id,
            "transaction_id": "",
            "include_in_normalized": row.include_in_normalized,
            "include_in_proforma": "YES",
            "source_system": "ADJUSTMENT",
            "source_account_code": row.adjustment_id,
            "amount_source": row.adjustment_amount,
            "sign_multiplier": 1,
            "source_sign_convention": "SIGNED",
            "load_id": "ADJ-LOAD-001",
        })

    combined = pd.concat(
        [reported, pd.DataFrame(adjusted_rows, columns=reported.columns)],
        ignore_index=True,
    ) if adjusted_rows else reported.copy()
    combined = combined[CLIENT_FS_NORMALIZED.column_names()]

    log.info("applied %d adjustment(s) as ADJUSTED rows", len(adjusted_rows))
    return combined


def select_view(combined, view):
    """The rows that make up one of the three views."""
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}: one of {VIEWS}")
    reported = combined["reported_or_adjusted"] == "REPORTED"
    adjusted = combined["reported_or_adjusted"] == "ADJUSTED"
    if view == "REPORTED":
        return combined[reported]
    if view == "NORMALIZED":
        return combined[
            reported | (adjusted & (combined["include_in_normalized"] == "YES"))
        ]
    # PROFORMA = normalized + transaction adjustments; until Phase 9 adds
    # transaction rows it equals rows flagged include_in_proforma = YES.
    return combined[
        reported | (adjusted & (combined["include_in_proforma"] == "YES"))
    ]


def view_income_summary(combined, view):
    """Revenue / EBITDA / EBIT / net income per period for one view."""
    rows = select_view(combined, view)
    is_rows = rows[rows["statement_type"] == "IS"]

    out = []
    for period, group in is_rows.groupby("period_id"):
        def section(*names):
            return group.loc[
                group["statement_section"].isin(names), "amount_reporting"
            ].sum()
        revenue = section("revenue", "other_income")
        ebitda = revenue + section("cogs", "operating_expenses")
        ebit = ebitda + section("depreciation_amortization")
        net_income = group["amount_reporting"].sum()
        out.append({
            "view": view, "period_id": period,
            "revenue": round(revenue, 4), "ebitda": round(ebitda, 4),
            "ebit": round(ebit, 4), "net_income": round(net_income, 4),
        })
    return pd.DataFrame(out)

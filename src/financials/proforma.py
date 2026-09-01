"""
M&A / transaction-event layer + pro forma engine — Phase 9
(spec sections 11–12).

    Reported Company
        → normalization adjustments (Phase 8)
    Normalized Standalone
        → transaction adjustments (this module)
    Pro Forma Company

Transaction events (transaction_events.csv) record deals — price,
consideration, goodwill/intangibles created, expected synergies,
restructuring costs — with a narrative and source document. Pro forma
adjustments (proforma_adjustments.csv) reference a transaction and quote
the NORMALIZED base they adjust; the quote is verified against the
normalized view on every apply, and consideration must reconcile to the
purchase price (cash + debt assumed + equity issued).

Applied pro forma rows become ADJUSTED rows with `transaction_id` set,
include_in_normalized = NO and include_in_proforma = YES — so the
NORMALIZED view never sees them, the PRO FORMA view is still a plain
SUM, and the REPORTED layer stays untouched as always.

Outlier ↔ event linkage: the engine can say "outlier flags exist in the
period this deal closed — investigate together." It NEVER concludes
causation (spec: "Do not automatically conclude causation without
supporting evidence").
"""

import logging
from pathlib import Path

import pandas as pd

from financials import validator
from financials.loader import ClientFSValidationError, _coerce_types, _read_csv
from financials.schemas import (
    CLIENT_FS_NORMALIZED,
    PROFORMA_ADJUSTMENTS,
    TRANSACTION_EVENTS,
)

log = logging.getLogger("financials.proforma")

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_EVENTS_FILE = BASE_DIR / "data" / "client_fs" / TRANSACTION_EVENTS.filename
DEFAULT_PROFORMA_FILE = BASE_DIR / "data" / "client_fs" / PROFORMA_ADJUSTMENTS.filename

CONSIDERATION_TOLERANCE = 0.01


def load_transaction_events(path=None, strict=True):
    """Load + validate transaction_events.csv."""
    path = Path(path) if path else DEFAULT_EVENTS_FILE
    if not path.exists():
        return pd.DataFrame(columns=TRANSACTION_EVENTS.column_names()), []

    raw = _read_csv(path)
    issues = validator.validate_table(raw, TRANSACTION_EVENTS)
    frame = _coerce_types(raw, TRANSACTION_EVENTS)

    # Consideration must reconcile: cash + debt assumed + equity issued
    # = purchase price (for deal-type events that carry a price).
    deals = frame[frame["event_type"].isin(["ACQUISITION", "MERGER"])]
    bad = deals.index[
        (deals["cash_paid"] + deals["debt_assumed"] + deals["equity_issued"]
         - deals["purchase_price_or_proceeds"]).abs() > CONSIDERATION_TOLERANCE
    ].tolist()
    if bad:
        issues.append(validator.Issue(
            "ERROR", "transaction_events", "consideration_mismatch",
            f"cash + debt assumed + equity issued != purchase price on "
            f"{validator._rows(bad)}",
        ))

    if strict and any(i.severity == "ERROR" for i in issues):
        raise ClientFSValidationError(issues)
    return frame, issues


def load_proforma_adjustments(events, path=None, strict=True):
    """Load + validate proforma_adjustments.csv against the event log."""
    path = Path(path) if path else DEFAULT_PROFORMA_FILE
    if not path.exists():
        return pd.DataFrame(columns=PROFORMA_ADJUSTMENTS.column_names()), []

    raw = _read_csv(path)
    issues = validator.validate_table(raw, PROFORMA_ADJUSTMENTS)
    frame = _coerce_types(raw, PROFORMA_ADJUSTMENTS)

    bad = frame.index[
        (frame["reported_amount"] + frame["proforma_adjustment"]
         - frame["proforma_amount"]).abs() > 0.01
    ].tolist()
    if bad:
        issues.append(validator.Issue(
            "ERROR", "proforma_adjustments", "arithmetic_mismatch",
            f"base + adjustment != proforma on {validator._rows(bad)}",
        ))

    known = set(events["transaction_id"])
    orphans = frame.index[~frame["transaction_id"].isin(known)].tolist()
    if orphans:
        issues.append(validator.Issue(
            "ERROR", "proforma_adjustments", "unknown_transaction",
            f"transaction_id not in transaction_events — "
            f"{validator._rows(orphans)}",
        ))

    if strict and any(i.severity == "ERROR" for i in issues):
        raise ClientFSValidationError(issues)
    return frame, issues


def apply_proforma(combined, proforma, account_mapping, strict=True):
    """
    Append APPROVED pro forma rows to the combined normalized frame as
    ADJUSTED rows flagged include_in_normalized=NO / include_in_proforma
    =YES. The quoted NORMALIZED base is verified first — pro forma stacks
    on normalized standalone, never on a stale quote.
    """
    from financials.adjustments import select_view

    normalized_view = select_view(combined, "NORMALIZED")
    totals = normalized_view.groupby(
        ["entity_id", "period_id", "standard_account_id"]
    )["amount_reporting"].sum()

    issues = []
    for row in proforma.itertuples():
        key = (row.entity_id, row.period_id, row.standard_account_id)
        actual = totals.get(key)
        if actual is None or abs(actual - row.reported_amount) > 0.01:
            issues.append(validator.Issue(
                "ERROR", "proforma_adjustments", "normalized_base_mismatch",
                f"{row.proforma_id}: quotes normalized base "
                f"{row.reported_amount} for {key}, but the NORMALIZED view "
                f"says {actual}",
            ))
    if strict and issues:
        raise ClientFSValidationError(issues)

    applicable = proforma[proforma["review_status"] == "APPROVED"]
    skipped = len(proforma) - len(applicable)
    if skipped:
        log.info("%d pro forma row(s) NOT applied (not APPROVED)", skipped)

    section_of, statement_of, name_of = {}, {}, {}
    for row in account_mapping.itertuples():
        section_of.setdefault(row.standard_account_id, row.statement_section)
        statement_of.setdefault(row.standard_account_id, row.statement_type)
        name_of.setdefault(row.standard_account_id, row.standard_account_name)

    rows = []
    for row in applicable.itertuples():
        rows.append({
            "company_id": row.company_id,
            "entity_id": row.entity_id,
            "period_id": row.period_id,
            "statement_type": statement_of[row.standard_account_id],
            "standard_account_id": row.standard_account_id,
            "standard_account_name": name_of[row.standard_account_id],
            "statement_section": section_of[row.standard_account_id],
            "amount_reporting": row.proforma_adjustment,   # the DELTA
            "reporting_currency": row.reporting_currency,
            "scenario": "ACTUAL",
            "reported_or_adjusted": "ADJUSTED",
            "adjustment_id": row.proforma_id,
            "transaction_id": row.transaction_id,
            "include_in_normalized": "NO",
            "include_in_proforma": "YES",
            "source_system": "PROFORMA",
            "source_account_code": row.proforma_id,
            "amount_source": row.proforma_adjustment,
            "sign_multiplier": 1,
            "source_sign_convention": "SIGNED",
            "load_id": "PF-LOAD-001",
        })

    out = pd.concat(
        [combined, pd.DataFrame(rows, columns=combined.columns)],
        ignore_index=True,
    ) if rows else combined.copy()
    out = out[CLIENT_FS_NORMALIZED.column_names()]

    log.info("applied %d pro forma row(s)", len(rows))
    return out


def link_events_to_outliers(events, outlier_flags, period_master):
    """
    For each event: which deterministic outlier flags fall in the period
    the event closed? Returns investigation LINKS — 'may relate to',
    never 'caused by'.
    """
    starts = pd.to_datetime(period_master["period_start"])
    ends = pd.to_datetime(period_master["period_end"])
    period_windows = list(zip(period_master["period_id"], starts, ends))

    links = []
    for event in events.itertuples():
        event_date = pd.to_datetime(event.event_date)
        period = next(
            (p for p, s, e in period_windows if s <= event_date <= e), None
        )
        if period is None:
            continue
        related = outlier_flags[outlier_flags["period_id"] == period]
        links.append({
            "transaction_id": event.transaction_id,
            "event_name": event.event_name,
            "period_id": period,
            "outlier_flag_count": len(related),
            "flagged_metrics": sorted(set(related["metric_name"])),
            "note": ("outlier flags exist in the event's period and MAY "
                     "relate to it - investigate together; causation is "
                     "not concluded" if len(related) else
                     "no outlier flags in the event's period"),
        })
    return pd.DataFrame(links)

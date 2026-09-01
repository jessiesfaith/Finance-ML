"""
Build the normalized financial statements (REPORTED view).

Usage (from the repo root):
    python src/build_client_fs_normalized.py

Loads and validates data/client_fs/, maps every source account to its
standard account, applies the canonical sign convention, writes
data/client_fs/client_fs_normalized.csv, and prints the identity checks
that the canonical convention makes possible:

    income-statement rows  sum to  net income
    cash-flow rows         sum to  the change in cash
    assets − liabilities − equity = 0
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials import (
    ClientFSValidationError,
    build_normalized_statements,
    load_client_fs,
    write_normalized_statements,
)
from financials.adjustments import (
    apply_adjustments,
    load_adjustments,
    view_income_summary,
)
from financials.proforma import (
    apply_proforma,
    load_proforma_adjustments,
    load_transaction_events,
)
from financials.normalized_statements import (
    balance_sheet_gap,
    cash_flow_by_entity_period,
    net_income_by_entity_period,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        result = load_client_fs(strict=True)
        reported = build_normalized_statements(result.tables)
        # Phase 8: append APPROVED adjustments as ADJUSTED delta rows.
        adjustments, _ = load_adjustments(result.tables, strict=True)
        normalized = apply_adjustments(
            reported, adjustments, result.tables["account_mapping"],
        )
        # Phase 9: append APPROVED pro forma rows (transaction layer).
        events, _ = load_transaction_events(strict=True)
        proforma, _ = load_proforma_adjustments(events, strict=True)
        normalized = apply_proforma(
            normalized, proforma, result.tables["account_mapping"],
        )
    except ClientFSValidationError as exc:
        print()
        print("BUILD FAILED — fix these before continuing:")
        print(exc)
        raise SystemExit(1)

    path = write_normalized_statements(normalized)

    print()
    print("NORMALIZED FINANCIAL STATEMENTS (REPORTED VIEW)")
    print("=" * 60)
    print(f"rows written : {len(normalized)}")
    print(f"output       : {path}")

    print()
    print("NET INCOME  (REPORTED rows summed, canonical signs)")
    print(net_income_by_entity_period(reported).round(2).to_string())

    print()
    print("CHANGE IN CASH  (cash-flow rows summed)")
    print(cash_flow_by_entity_period(reported).round(2).to_string())

    print()
    print("BALANCE SHEET GAP  (assets - liabilities - equity; 0 = balanced)")
    print(balance_sheet_gap(reported).round(2).to_string())

    # ------------------------------------------------
    # THE THREE VIEWS (Phase 8)
    # ------------------------------------------------
    print()
    print("REPORTED vs NORMALIZED vs PRO FORMA  (income summary, FY2025)")
    print("-" * 60)
    import pandas as pd
    views = pd.concat([
        view_income_summary(normalized, v)
        for v in ("REPORTED", "NORMALIZED", "PROFORMA")
    ])
    print(views[views["period_id"] == "FY2025"].to_string(index=False))
    not_applied = adjustments[
        ~((adjustments["review_status"] == "APPROVED")
          & (adjustments["include_in_normalized"] == "YES"))
    ]
    if len(not_applied):
        print()
        print("Adjustments NOT applied (awaiting review — visible, outside the numbers):")
        for row in not_applied.itertuples():
            print(f"  {row.adjustment_id}: {row.adjustment_amount:+.1f} "
                  f"{row.standard_account_id} — {row.review_status}")

    # ------------------------------------------------
    # TRANSACTION EVENTS ↔ OUTLIER LINKAGE (Phase 9)
    # ------------------------------------------------
    if len(events):
        from financials.outliers import DEFAULT_OUTPUT as FLAGS_FILE
        from financials.proforma import link_events_to_outliers
        flags = pd.read_csv(FLAGS_FILE) if FLAGS_FILE.exists() else pd.DataFrame(
            columns=["period_id", "metric_name"])
        links = link_events_to_outliers(
            events, flags, result.tables["period_master"])
        print()
        print("TRANSACTION EVENTS — investigation links, not conclusions")
        print("-" * 60)
        for row in links.itertuples():
            print(f"  {row.transaction_id} ({row.event_name}) closed in "
                  f"{row.period_id}: {row.outlier_flag_count} outlier "
                  f"flag(s) in that period")
            print(f"    {row.note}")

    # One audit example: the sign transformation on a magnitude-presented
    # expense, traceable end to end.
    example = normalized[
        (normalized["entity_id"] == "ENT_PARENT")
        & (normalized["period_id"] == "FY2025")
        & (normalized["source_account_code"] == "5000")
    ].iloc[0]

    print()
    print("SIGN AUDIT EXAMPLE (one row)")
    print("-" * 60)
    print(f"  account          : {example['standard_account_id']} "
          f"({example['source_system']}/{example['source_account_code']})")
    print(f"  source amount    : {example['amount_source']:,.2f} "
          f"({example['source_sign_convention']})")
    print(f"  rule             : x {example['sign_multiplier']:+d} "
          f"(canonical sign)")
    print(f"  normalized       : {example['amount_reporting']:,.2f} "
          f"{example['reporting_currency']}")


if __name__ == "__main__":
    main()

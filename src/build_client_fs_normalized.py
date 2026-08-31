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
from financials.normalized_statements import (
    balance_sheet_gap,
    cash_flow_by_entity_period,
    net_income_by_entity_period,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        result = load_client_fs(strict=True)
        normalized = build_normalized_statements(result.tables)
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
    print("NET INCOME  (income-statement rows summed, canonical signs)")
    print(net_income_by_entity_period(normalized).round(2).to_string())

    print()
    print("CHANGE IN CASH  (cash-flow rows summed)")
    print(cash_flow_by_entity_period(normalized).round(2).to_string())

    print()
    print("BALANCE SHEET GAP  (assets - liabilities - equity; 0 = balanced)")
    print(balance_sheet_gap(normalized).round(2).to_string())

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

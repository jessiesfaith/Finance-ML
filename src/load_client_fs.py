"""
Run a full client financial-statement load and show what happened.

Usage (from the repo root):
    python src/load_client_fs.py

Loads data/client_fs/, prints a summary of every table, any validation
issues, and one end-to-end lineage trace proving a number can be followed
back to its source file, sheet, and row.
"""

import logging
import sys
from pathlib import Path

# Same import bootstrap the tests use (see conftest.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials import ClientFSValidationError, load_client_fs

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        result = load_client_fs(strict=True)
    except ClientFSValidationError as exc:
        print()
        print("LOAD FAILED — fix these before continuing:")
        print(exc)
        raise SystemExit(1)

    print()
    print("CLIENT FINANCIAL STATEMENT LOAD")
    print("=" * 60)
    print(result.summary())

    if result.warnings:
        print()
        print("WARNINGS")
        for issue in result.warnings:
            print(f"  {issue}")

    # ------------------------------------------------
    # LINEAGE TRACE
    # Pick one raw number and walk it back to its source.
    # ------------------------------------------------

    raw = result.tables["client_fs_raw"]
    mapping = result.tables["account_mapping"]

    example = raw[
        (raw["entity_id"] == "ENT_GMBH")
        & (raw["period_id"] == "FY2025")
        & (raw["source_account_code"] == "8400")
    ]

    if not example.empty:
        row = example.iloc[0]
        std = mapping.loc[
            (mapping["source_system"] == row["source_system"])
            & (mapping["source_account_code"] == row["source_account_code"]),
            "standard_account_id",
        ].iloc[0]

        print()
        print("LINEAGE TRACE (one number, back to its source)")
        print("-" * 60)
        print(f"  standard account : {std}")
        print(f"  source account   : {row['source_system']} / "
              f"{row['source_account_code']} ({row['source_account_name']})")
        print(f"  entity / period  : {row['entity_id']} / {row['period_id']}")
        print(f"  local amount     : {row['local_currency']} {row['amount_local']:,.2f}")
        print(f"  fx to reporting  : × {row['fx_rate_to_reporting']}")
        print(f"  reporting amount : {row['reporting_currency']} "
              f"{row['amount_reporting']:,.2f}")
        print(f"  came from        : {row['source_file']} / "
              f"{row['source_sheet']} / row {row['source_row']}")
        print(f"  load             : {row['load_id']} at {row['load_timestamp']}")


if __name__ == "__main__":
    main()

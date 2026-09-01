"""
Stage a public company's SEC XBRL financials through the client pipeline.

Usage (from the repo root):
    python src/ingest_sec.py --cik 320193 --from 2022 --to 2024
    python src/ingest_sec.py --file path/to/companyfacts.json --from 2022 --to 2024

Fetches (or reads) EDGAR companyfacts JSON, translates it through the
starter XBRL tag mapping into the CANONICAL format, writes it to
data/sec_staging/<company>/ (gitignored - staged data is reviewed, never
auto-promoted), runs the standard loader validation over the staged
files, and prints the honest state: rows staged, unmapped material tags
(the analyst's mapping to-do), and whether the balance sheet can balance
yet with a partial mapping (it usually cannot - that is the point).
"""

import argparse
import logging
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import json

from financials import load_client_fs
from financials.sec_adapter import (
    fetch_companyfacts,
    parse_companyfacts,
    write_staging,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cik", help="SEC CIK number (fetches from EDGAR)")
    parser.add_argument("--file", help="local companyfacts JSON file")
    parser.add_argument("--from", dest="fy_min", type=int, required=True)
    parser.add_argument("--to", dest="fy_max", type=int, required=True)
    args = parser.parse_args()

    if bool(args.cik) == bool(args.file):
        parser.error("provide exactly one of --cik or --file")

    facts = (fetch_companyfacts(args.cik) if args.cik
             else json.loads(Path(args.file).read_text()))
    parsed = parse_companyfacts(facts, args.fy_min, args.fy_max)
    target = write_staging(parsed)

    result = load_client_fs(data_dir=target, strict=False)

    raw = parsed["tables"]["client_fs_raw.csv"]
    unmapped = parsed["unmapped"]

    print()
    print(f"SEC STAGING — {parsed['company_id']}")
    print("=" * 64)
    print(f"staged rows        : {len(raw)} across "
          f"{raw['period_id'].nunique()} fiscal year(s)")
    print(f"loader validation  : {len(result.errors)} error(s), "
          f"{len(result.warnings)} warning(s)")
    print(f"unmapped tags      : {len(unmapped)} with material 10-K facts "
          f"(full list: {target / 'unmapped_tags.csv'})")
    if len(unmapped):
        print()
        print("  Top unmapped by magnitude ($M) — the analyst's mapping to-do:")
        for row in unmapped.head(10).itertuples():
            print(f"    {row.latest_amount_musd:>12,.1f}  {row.source_account_code}")
    print()
    print("NOTE: with a partial starter mapping the staged balance sheet")
    print("will NOT balance (Control C1) - complete and APPROVE the mapping")
    print("for this filer, re-stage, and only then promote. Staged data is")
    print("never auto-merged into data/client_fs/.")
    print()
    print(f"staging directory: {target}")


if __name__ == "__main__":
    main()

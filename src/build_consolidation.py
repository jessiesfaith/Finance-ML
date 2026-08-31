"""
Run the full Phase 3 pipeline: FX translation + entity consolidation.

Usage (from the repo root):
    python src/build_consolidation.py

Loads and validates data/client_fs/, translates every entity into the
reporting currency with the correct rate type per item (average / closing /
historical / RE roll-forward), computes the CTA plug, applies the
intercompany elimination layer, writes
data/client_fs/entity_consolidation.csv, and prints the checks:

    consolidated income statement sums to consolidated net income
    consolidated balance sheet balances (CTA included)
    the source file's FX shortcut exactly explains the CTA
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials import (
    ClientFSValidationError,
    consolidate,
    load_client_fs,
    translate_statements,
    write_consolidation,
)
from financials.consolidation import (
    consolidated_balance_gap,
    consolidated_net_income,
)
from financials.controls import apply_consolidation_status

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        result = load_client_fs(strict=True)
        translated = translate_statements(result.tables)
        consolidated = consolidate(translated, result.tables["entity_master"])
        # Phase 4: fill control_status/control_variance from an
        # independent recomputation (replaces the PENDING placeholder).
        consolidated = apply_consolidation_status(
            consolidated, translated, result.tables["entity_master"]
        )
    except ClientFSValidationError as exc:
        print()
        print("BUILD FAILED — fix these before continuing:")
        print(exc)
        raise SystemExit(1)

    path = write_consolidation(consolidated)

    print()
    print("ENTITY CONSOLIDATION")
    print("=" * 64)
    print(f"rows written : {len(consolidated)}")
    print(f"output       : {path}")

    print()
    print("CONSOLIDATED NET INCOME (IS rows summed)")
    print(consolidated_net_income(consolidated).round(2).to_string())

    print()
    print("CONSOLIDATED BALANCE SHEET GAP (0 = balanced, CTA included)")
    print(consolidated_balance_gap(consolidated).round(6).to_string())

    print()
    print("ELIMINATION LAYER (nonzero rows)")
    elim = consolidated[consolidated["intercompany_elimination"] != 0]
    print(elim[[
        "period_id", "standard_account_id",
        "pre_elimination_amount", "intercompany_elimination",
        "consolidated_amount",
    ]].to_string(index=False))

    print()
    print("FX TRANSLATION — CTA AND WHERE IT COMES FROM")
    cta = translated[translated["origin"] == "FX_ENGINE"]
    print(cta[[
        "entity_id", "period_id", "calculated_reporting_amount",
    ]].rename(columns={"calculated_reporting_amount": "cta"})
      .to_string(index=False))

    # The teaching moment: the source file translated the whole balance
    # sheet at the closing rate (a common shortcut). The per-row variances
    # against the correct methodology land exactly where theory says:
    # on the equity accounts, and they sum to the CTA.
    foreign_bs = translated[
        (translated["origin"] == "SOURCE")
        & (translated["statement_type"] == "BS")
        & (translated["fx_translation_variance"].abs() > 1e-9)
    ]
    if not foreign_bs.empty:
        print()
        print("SOURCE-vs-CALCULATED VARIANCES (the source's closing-rate shortcut)")
        print(foreign_bs[[
            "entity_id", "period_id", "standard_account_id",
            "source_reported_canonical", "calculated_reporting_amount",
            "fx_translation_variance",
        ]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()

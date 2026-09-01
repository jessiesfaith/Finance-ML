"""
Build the market-data layer (Phase 13 architecture).

Usage (from the repo root):
    python src/build_market_data.py

Re-platforms the seed-42 synthetic macro history into the canonical
append-only market_observations format (honestly labeled SYNTHETIC),
validates the layer with its own independent loader, and prints the
current view. The live FRED source flip is gated on the reporting tool
being finalized (DECISIONS #5) and runs from the analyst's machine.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials.loader import ClientFSValidationError
from financials.market_data import (
    MARKET_DIR,
    current_view,
    load_market_data,
    replatform_synthetic_history,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    observations = replatform_synthetic_history()
    observations.to_csv(MARKET_DIR / "market_observations.csv", index=False)

    try:
        tables, issues = load_market_data(strict=True)
    except ClientFSValidationError as exc:
        print()
        print("MARKET LAYER INVALID:")
        print(exc)
        raise SystemExit(1)

    obs = tables["market_observations"]
    print()
    print("MARKET DATA LAYER — append-only observations")
    print("=" * 64)
    print(f"metrics      : {len(tables['market_metric_master'])}")
    print(f"observations : {len(obs)} (all SYNTHETIC until the live cutover)")
    print()
    print("CURRENT VIEW (latest observation per metric)")
    view = current_view(obs)
    print(view[["metric_id", "observation_date", "value", "unit",
                "source", "revision_status"]].to_string(index=False))
    print()
    print("The FRED adapter is coded and tested; flipping preferred_source")
    print("SYNTHETIC -> FRED happens per metric, after the reporting tool")
    print("is finalized (DECISIONS #5), from a machine FRED doesn't block.")


if __name__ == "__main__":
    main()

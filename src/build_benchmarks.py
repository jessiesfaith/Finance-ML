"""
Build the benchmarking layer (Phase 14 architecture).

Usage (from the repo root):
    python src/build_benchmarks.py

Computes COMPANY statistic rows from the internal pipeline through the
shared derivation rules and writes data/market/benchmark_observations.csv.
Peer/industry statistics stay EMPTY until the SEC pipeline supplies real
peer data - inventing them is structurally blocked by validation.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials import (
    apply_consolidation_status,
    consolidate,
    income_walk,
    load_client_fs,
    load_scenarios,
    nwc_components,
    translate_statements,
)
from financials.benchmarking import (
    MARKET_DIR,
    build_company_benchmarks,
    load_benchmarks,
)
from financials.invested_capital import invested_capital_components, roic
from financials.scenarios import company_drivers

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    tables = load_client_fs(strict=True).tables
    translated = translate_statements(tables)
    consolidated = apply_consolidation_status(
        consolidate(translated, tables["entity_master"]),
        translated, tables["entity_master"],
    )
    walk = income_walk(consolidated)
    nwc = nwc_components(consolidated, tables["account_mapping"])
    scenario_tables, _ = load_scenarios(strict=True)
    drivers = company_drivers(
        scenario_tables, "BASE", tables["company_master"]["company_id"].iloc[0])
    ic = invested_capital_components(consolidated, tables["account_mapping"])
    walk_idx = walk.set_index("period_id")
    nopat = {p: walk_idx.loc[p, "ebit"] * (1 - drivers["TAX_RATE_PCT"] / 100)
             for p in walk_idx.index}
    roic_frame = roic(ic, nopat, basis="ENDING")

    company_rows = build_company_benchmarks(
        walk, nwc, roic_frame, consolidated, tables["period_master"],
        peer_group_id="PG_SOFTWARE_US",
    )
    company_rows.to_csv(MARKET_DIR / "benchmark_observations.csv", index=False)

    bench, _ = load_benchmarks(strict=True)
    obs = bench["benchmark_observations"]

    print()
    print("BENCHMARKING LAYER — company vs (future) peers")
    print("=" * 64)
    print(obs[obs["period_id"] == "FY2025"][
        ["benchmark_metric_id", "statistic", "value", "unit"]
    ].to_string(index=False))
    print()
    print("PEER_MEDIAN / INDUSTRY_MEDIAN / P25 / P75 slots exist and stay")
    print("EMPTY until the Phase 12 SEC pipeline supplies cited peer data -")
    print("inventing them fails validation (invented_peer_data).")


if __name__ == "__main__":
    main()

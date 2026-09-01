"""
Build the Page 4 sensitivity export: implied share price across the
WACC x terminal-growth grid.

Usage (from the repo root):
    python src/build_sensitivity.py

Requires reports/finance_scenario_report.csv and reports/client_fs_ufcf.csv
(the export and UFCF builders), because the grid re-prices the SAME
UFCF path with the SAME net debt and share count - only the two
assumptions under test move.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials.sensitivity import (
    OUTPUT,
    build_sensitivity,
    load_inputs,
)


def main():
    base, ufcf_path = load_inputs()
    frame = build_sensitivity(base, ufcf_path)
    frame.to_csv(OUTPUT, index=False)
    print()
    print("SENSITIVITY — implied share price ($), WACC rows x growth columns")
    print("=" * 68)
    print(frame.drop(columns=["scenario", "value_class"]).to_string(index=False))
    print()
    print(f"center cell (Base WACC, growth 2.5%) must equal the reported")
    print(f"Base implied price — the test suite enforces it.")
    print(f"written: {OUTPUT}")


if __name__ == "__main__":
    main()

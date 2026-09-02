"""
Build the Page 6 curated export: project appraisal per scenario.

Usage (from the repo root):
    python src/build_project_appraisal.py

Add your own case with a row in data/projects/project_master.csv plus
its assumption rows in project_assumptions.csv, rerun this script, and
refresh the report. Requires reports/finance_scenario_report.csv (run
models/export_finance_report.py first) so projects are judged with the
same per-scenario rates as the company DCF.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials.loader import ClientFSValidationError
from financials.projects import (
    DEFAULT_OUTPUT,
    SENSITIVITY_OUTPUT,
    VERDICT_STRIP_OUTPUT,
    build_option_sensitivity,
    build_project_appraisal,
    load_projects,
    load_rates,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        tables, _ = load_projects(strict=True)
    except ClientFSValidationError as exc:
        print()
        print("PROJECT INTAKE INVALID:")
        print(exc)
        raise SystemExit(1)

    rates = load_rates()
    frame = build_project_appraisal(
        tables["project_master"], tables["project_assumptions"], rates)
    frame.to_csv(DEFAULT_OUTPUT, index=False)
    grid, verdicts = build_option_sensitivity(
        tables["project_master"], tables["project_assumptions"], rates)
    grid.to_csv(SENSITIVITY_OUTPUT, index=False)
    verdicts.to_csv(VERDICT_STRIP_OUTPUT, index=False)

    print()
    print("PROJECT APPRAISAL — incremental, per scenario, hurdle basis")
    print("=" * 72)
    show = ["project_id", "scenario", "npv_at_hurdle", "irr_pct",
            "payback_years", "incr_roic_pct", "recommendation"]
    print(frame[show].to_string(index=False))
    print()
    print(f"written: {DEFAULT_OUTPUT}")
    print(f"sensitivity grid: {len(grid)} rows -> {SENSITIVITY_OUTPUT}")
    print(f"verdict strips  : {len(verdicts)} rows -> {VERDICT_STRIP_OUTPUT}")


if __name__ == "__main__":
    main()

"""
Run the deterministic outlier engine (Phase 7).

Usage (from the repo root):
    python src/run_outliers.py

Scans period-over-period account movements, margins, working-capital
ratios, and new items; writes data/client_fs/outlier_flags.csv. Flags
are questions for a human (and, later, the Phase 10 analyst agent) —
never conclusions.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials import (
    ClientFSValidationError,
    apply_consolidation_status,
    consolidate,
    load_client_fs,
    translate_statements,
)
from financials.outliers import (
    THRESHOLDS,
    flags_frame,
    run_outlier_engine,
    write_outlier_flags,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        result = load_client_fs(strict=True)
        tables = result.tables
        translated = translate_statements(tables)
        consolidated = apply_consolidation_status(
            consolidate(translated, tables["entity_master"]),
            translated, tables["entity_master"],
        )
        flags = run_outlier_engine(tables, consolidated, translated)
    except ClientFSValidationError as exc:
        print()
        print("OUTLIER ENGINE NOT RUN — the load itself failed:")
        print(exc)
        raise SystemExit(1)

    frame = flags_frame(flags)
    path = write_outlier_flags(frame)

    print()
    print("OUTLIER FLAGS — deterministic, pre-agent")
    print("=" * 70)
    print(f"thresholds : ±{THRESHOLDS['pop_pct']}% AND ±${THRESHOLDS['pop_amount']}M "
          f"(PoP) · ±{THRESHOLDS['margin_pp']}pp (margins) · "
          f"±{THRESHOLDS['ratio_pct']}% (ratios)")
    print(f"flags      : {len(frame)}")
    print()
    if frame.empty:
        print("No outliers at current thresholds.")
    else:
        for row in frame.itertuples():
            unit = "pp" if row.method == "MARGIN_VARIANCE" else (
                "z" if row.method == "ZSCORE" else "%")
            print(f"  [{row.severity}] {row.method:<15} {row.level:<12} "
                  f"{row.entity_id:<12} {row.metric_name:<22} "
                  f"{row.baseline_value:>10,.1f} → {row.current_value:>10,.1f}  "
                  f"({row.variance_pct:+,.1f}{unit})")
        print()
        print("Every flag is a question, not a conclusion — possible causes")
        print("are listed per row; review_status starts PENDING.")
    print()
    print(f"output: {path}")


if __name__ == "__main__":
    main()

"""
Run the full deterministic control suite (Phase 4).

Usage (from the repo root):
    python src/run_controls.py

Loads and validates data/client_fs/, rebuilds the normalized, translated,
and consolidated layers, runs Controls 1-10, writes
data/client_fs/control_checks.csv, and prints the PASS/REVIEW/FAIL
summary plus every non-PASS exception. Nothing is ever silently fixed.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials import (
    ClientFSValidationError,
    build_normalized_statements,
    consolidate,
    load_client_fs,
    translate_statements,
)
from financials.controls import (
    results_frame,
    run_all_controls,
    write_control_checks,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        result = load_client_fs(strict=True)
        normalized = build_normalized_statements(result.tables)
        translated = translate_statements(result.tables)
        consolidated = consolidate(translated, result.tables["entity_master"])
        controls = run_all_controls(
            result.tables, normalized, translated, consolidated
        )
    except ClientFSValidationError as exc:
        print()
        print("CONTROLS NOT RUN — the load itself failed:")
        print(exc)
        raise SystemExit(1)

    frame = results_frame(controls)
    path = write_control_checks(frame)

    print()
    print("CONTROL RESULTS")
    print("=" * 64)
    print(f"controls evaluated : {len(frame)}")
    print(f"output             : {path}")
    print()
    print(frame["status"].value_counts().to_string())

    exceptions = frame[frame["status"] != "PASS"]
    if not exceptions.empty:
        print()
        print("EXCEPTIONS (REVIEW / FAIL) — for a human, never auto-fixed")
        print("-" * 64)
        for row in exceptions.itertuples():
            print(f"  [{row.status}] {row.control_id} {row.control_name}")
            print(f"          entity {row.entity_id} / {row.period_id} — "
                  f"variance {row.variance_amount}")
            print(f"          {row.agent_comment}")
    else:
        print()
        print("All controls PASS.")


if __name__ == "__main__":
    main()

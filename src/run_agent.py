"""
Run the analyst-review agent (Phase 10).

Usage (from the repo root):
    python src/run_agent.py

Rebuilds the pipeline outputs in memory, gathers one evidence packet per
open item (non-PASS controls + outlier flags), interprets each with the
deterministic rule interpreter, and writes
data/client_fs/agent_review_log.csv. The agent READS / ANALYZES / FLAGS
/ EXPLAINS / PROPOSES - it cannot DELETE, OVERWRITE, or APPROVE
(guardrails enforced in code, see agents/financial_review_agent.py).
"""

import logging
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))          # src/  -> financials package
sys.path.insert(0, str(BASE.parent))   # repo root -> agents package

import pandas as pd

from agents.financial_review_agent import (
    DeterministicInterpreter,
    findings_frame,
    gather_evidence,
    write_review_log,
)
from financials import (
    ClientFSValidationError,
    apply_consolidation_status,
    build_normalized_statements,
    consolidate,
    flags_frame,
    load_adjustments,
    load_client_fs,
    load_transaction_events,
    results_frame,
    run_all_controls,
    run_outlier_engine,
    translate_statements,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        result = load_client_fs(strict=True)
        tables = result.tables
        normalized = build_normalized_statements(tables)
        translated = translate_statements(tables)
        consolidated = apply_consolidation_status(
            consolidate(translated, tables["entity_master"]),
            translated, tables["entity_master"],
        )
        controls = results_frame(run_all_controls(
            tables, normalized, translated, consolidated))
        outliers = flags_frame(run_outlier_engine(
            tables, consolidated, translated))
        events, _ = load_transaction_events(strict=True)
        adjustments, _ = load_adjustments(tables, strict=True)
    except ClientFSValidationError as exc:
        print()
        print("AGENT NOT RUN — the pipeline itself failed:")
        print(exc)
        raise SystemExit(1)

    packets = gather_evidence(
        controls, outliers, events, adjustments, tables["period_master"]
    )
    interpreter = DeterministicInterpreter()
    findings = [interpreter.interpret(p) for p in packets]
    frame = findings_frame(findings)
    path = write_review_log(frame)

    print()
    print("ANALYST-REVIEW AGENT — findings (READ/ANALYZE/FLAG/EXPLAIN/PROPOSE)")
    print("=" * 72)
    print(f"items reviewed : {len(frame)}  "
          f"(controls: {(frame['item_type'] == 'CONTROL_EXCEPTION').sum()}, "
          f"outliers: {(frame['item_type'] == 'OUTLIER_FLAG').sum()})")
    print(f"interpreter    : {interpreter.name}")
    print()
    for row in frame.itertuples():
        print(f"[{row.review_id}] ({row.agent_confidence:.2f}) "
              f"{row.item_reference}")
        print(f"    {row.explanation}")
        print(f"    → {row.recommended_action}")
    print()
    print("proposals written to adjustments.csv this run: 0 — the TXN-001")
    print("restructuring is already normalized by ADJ-001A/B (the agent")
    print("checks for existing coverage and never duplicates).")
    print()
    print("Guardrails: proposals are forced to review_status=REVIEW;")
    print("APPROVED is a human-only value; all writes are append-only and")
    print("verified. Source financials are never touched.")
    print()
    print(f"output: {path}")


if __name__ == "__main__":
    main()

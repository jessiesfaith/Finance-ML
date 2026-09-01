"""
Build every curated Power BI export (Phase 11a).

Usage (from the repo root):
    python src/build_powerbi_exports.py

Runs the full pipeline in memory and writes the schema-locked report
files Power BI Pages 1-3 will consume. The legacy 33-column
finance_scenario_report.csv is untouched beside them.
"""

import logging
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent))

from agents.financial_review_agent import (
    DeterministicInterpreter,
    findings_frame,
    gather_evidence,
)
from financials import (
    ClientFSValidationError,
    apply_adjustments,
    apply_consolidation_status,
    apply_proforma,
    build_normalized_statements,
    build_ufcf_forecast,
    consolidate,
    flags_frame,
    load_adjustments,
    load_client_fs,
    load_proforma_adjustments,
    load_scenarios,
    load_transaction_events,
    results_frame,
    run_all_controls,
    run_outlier_engine,
    translate_statements,
)
from financials.loader import _coerce_types, _read_csv
from financials.powerbi_exports import build_exports, write_exports
from financials.schemas import RISK_FREE_POLICY
from build_valuation_inputs import build_valuation_inputs

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")

RF_POLICY_FILE = BASE.parent / "data" / "market" / RISK_FREE_POLICY.filename


def main():
    try:
        result = load_client_fs(strict=True)
        tables = result.tables
        reported = build_normalized_statements(tables)
        adjustments, _ = load_adjustments(tables, strict=True)
        combined = apply_adjustments(reported, adjustments,
                                     tables["account_mapping"])
        events, _ = load_transaction_events(strict=True)
        proforma, _ = load_proforma_adjustments(events, strict=True)
        combined = apply_proforma(combined, proforma,
                                  tables["account_mapping"])

        translated = translate_statements(tables)
        consolidated = apply_consolidation_status(
            consolidate(translated, tables["entity_master"]),
            translated, tables["entity_master"],
        )
        controls = results_frame(run_all_controls(
            tables, reported, translated, consolidated))
        outliers = flags_frame(run_outlier_engine(
            tables, consolidated, translated))
        scenario_tables, _ = load_scenarios(strict=True)
        ufcf = build_ufcf_forecast(tables, consolidated, scenario_tables)
        valuation = build_valuation_inputs(tables, consolidated,
                                           scenario_tables)
        packets = gather_evidence(controls, outliers, events, adjustments,
                                  tables["period_master"])
        interpreter = DeterministicInterpreter()
        review = findings_frame(
            [interpreter.interpret(p) for p in packets])
        rf_policy = _coerce_types(_read_csv(RF_POLICY_FILE), RISK_FREE_POLICY)
    except ClientFSValidationError as exc:
        print()
        print("EXPORTS NOT BUILT — the pipeline itself failed:")
        print(exc)
        raise SystemExit(1)

    currency = tables["company_master"]["reporting_currency"].iloc[0]
    exports = build_exports(
        combined, controls, ufcf, valuation, review, rf_policy, currency
    )
    paths = write_exports(exports)

    print()
    print("CURATED POWER BI EXPORTS")
    print("=" * 60)
    for path in paths:
        import pandas as pd
        rows = len(pd.read_csv(path))
        print(f"  {path.name:<36} {rows:>4} rows")
    print()
    print("Each file is schema-locked by tests and carries value_class")
    print("labels; finance_scenario_report.csv is untouched beside them.")


if __name__ == "__main__":
    main()

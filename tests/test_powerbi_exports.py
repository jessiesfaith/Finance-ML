"""
Schema locks for the curated Power BI exports (Phase 11a) — the same
protection finance_scenario_report.csv has: exact columns frozen, and
every committed file must match a fresh rebuild.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

from financials.powerbi_exports import EXPORT_COLUMNS, REPORTS_DIR

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

VALUE_CLASSES = {
    "CLIENT_FS", "MARKET_DATA", "INDUSTRY_PEER", "ANALYST_ASSUMPTION",
    "CALCULATED", "MODEL_OUTPUT",
}


@pytest.fixture(scope="module")
def fresh_exports():
    """Rebuild every export in memory through the real pipeline."""
    from agents.financial_review_agent import (
        DeterministicInterpreter, findings_frame, gather_evidence,
    )
    from financials import (
        apply_adjustments, apply_consolidation_status, apply_proforma,
        build_normalized_statements, build_ufcf_forecast, consolidate,
        flags_frame, load_adjustments, load_client_fs,
        load_proforma_adjustments, load_scenarios, load_transaction_events,
        results_frame, run_all_controls, run_outlier_engine,
        translate_statements,
    )
    from financials.loader import _coerce_types, _read_csv
    from financials.powerbi_exports import build_exports
    from financials.schemas import RISK_FREE_POLICY
    from build_valuation_inputs import build_valuation_inputs

    tables = load_client_fs(strict=True).tables
    reported = build_normalized_statements(tables)
    adjustments, _ = load_adjustments(tables, strict=True)
    combined = apply_adjustments(reported, adjustments,
                                 tables["account_mapping"])
    events, _ = load_transaction_events(strict=True)
    proforma, _ = load_proforma_adjustments(events, strict=True)
    combined = apply_proforma(combined, proforma, tables["account_mapping"])
    translated = translate_statements(tables)
    consolidated = apply_consolidation_status(
        consolidate(translated, tables["entity_master"]),
        translated, tables["entity_master"],
    )
    controls = results_frame(run_all_controls(
        tables, reported, translated, consolidated))
    outliers = flags_frame(run_outlier_engine(tables, consolidated, translated))
    scenario_tables, _ = load_scenarios(strict=True)
    ufcf = build_ufcf_forecast(tables, consolidated, scenario_tables)
    valuation = build_valuation_inputs(tables, consolidated, scenario_tables)
    packets = gather_evidence(controls, outliers, events, adjustments,
                              tables["period_master"])
    review = findings_frame(
        [DeterministicInterpreter().interpret(p) for p in packets])
    rf_policy = _coerce_types(
        _read_csv(Path(REPORTS_DIR).parent / "data" / "market"
                  / RISK_FREE_POLICY.filename),
        RISK_FREE_POLICY,
    )
    currency = tables["company_master"]["reporting_currency"].iloc[0]
    return build_exports(combined, controls, ufcf, valuation, review,
                         rf_policy, currency)


def test_every_export_exists_with_locked_columns():
    for name, columns in EXPORT_COLUMNS.items():
        path = REPORTS_DIR / name
        assert path.exists(), f"{name} missing — run build_powerbi_exports.py"
        frame = pd.read_csv(path)
        assert list(frame.columns) == columns, name


def test_value_class_labels_are_valid_taxonomy():
    for name in EXPORT_COLUMNS:
        frame = pd.read_csv(REPORTS_DIR / name)
        assert set(frame["value_class"]) <= VALUE_CLASSES, name


def test_committed_exports_match_a_fresh_rebuild(fresh_exports):
    import io
    for name, frame in fresh_exports.items():
        committed = pd.read_csv(
            REPORTS_DIR / name, dtype=str, keep_default_na=False)
        fresh = pd.read_csv(
            io.StringIO(frame.to_csv(index=False)),
            dtype=str, keep_default_na=False,
        )
        pd.testing.assert_frame_equal(committed, fresh, obj=name)


def test_statement_export_carries_the_view_machinery():
    frame = pd.read_csv(REPORTS_DIR / "client_fs_statements.csv",
                        keep_default_na=False)
    assert set(frame["reported_or_adjusted"]) == {"REPORTED", "ADJUSTED"}
    adjusted = frame[frame["reported_or_adjusted"] == "ADJUSTED"]
    assert set(adjusted["value_class"]) == {"ANALYST_ASSUMPTION"}
    assert (frame[frame["reported_or_adjusted"] == "REPORTED"]["value_class"]
            == "CLIENT_FS").all()


def test_income_walk_has_three_views():
    frame = pd.read_csv(REPORTS_DIR / "client_fs_income_walk.csv")
    assert set(frame["view"]) == {"REPORTED", "NORMALIZED", "PROFORMA"}
    fy25 = frame[frame["period_id"] == "FY2025"].set_index("view")
    assert fy25.loc["REPORTED", "ebitda"] == pytest.approx(314.8)
    assert fy25.loc["PROFORMA", "ebitda"] == pytest.approx(329.8)


def test_legacy_report_contract_is_untouched():
    legacy = pd.read_csv(REPORTS_DIR / "finance_scenario_report.csv")
    assert len(legacy.columns) == 33
    assert len(legacy) == 3

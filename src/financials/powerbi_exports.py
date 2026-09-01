"""
Curated Power BI exports — Phase 11(a).

Power BI reads ONLY curated reports/*.csv files (docs/POWERBI_CONTRACT.md).
This module builds the client-financials set. Every file:

  * is written by this one builder, never by hand;
  * carries a `value_class` column — the six-class taxonomy
    (CLIENT_FS / MARKET_DATA / INDUSTRY_PEER / ANALYST_ASSUMPTION /
    CALCULATED / MODEL_OUTPUT) — so the report can label every number's
    nature (audit deliverable AB);
  * has its exact column list frozen in EXPORT_COLUMNS below and locked
    by tests, the same protection finance_scenario_report.csv has;
  * is append/extend-only as a contract: columns are added, never
    renamed or removed.

The legacy 33-column finance_scenario_report.csv is NOT touched.
"""

import logging
from pathlib import Path

import pandas as pd

from financials.adjustments import view_income_summary
from financials.schemas import (
    AGENT_REVIEW_LOG,
    CONTROL_CHECKS,
    RISK_FREE_POLICY,
    UFCF_FORECAST,
    VALUATION_INPUTS,
)

log = logging.getLogger("financials.powerbi")

BASE_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = BASE_DIR / "reports"

EXPORT_COLUMNS = {
    "client_fs_statements.csv": [
        "company_id", "entity_id", "period_id", "statement_type",
        "statement_section", "standard_account_id", "standard_account_name",
        "amount_reporting", "reporting_currency", "scenario",
        "reported_or_adjusted", "adjustment_id", "transaction_id",
        "include_in_normalized", "include_in_proforma", "value_class",
    ],
    "client_fs_income_walk.csv": [
        "company_id", "view", "period_id", "revenue", "ebitda",
        "ebitda_margin_pct", "ebit", "ebit_margin_pct", "net_income",
        "reporting_currency", "value_class",
    ],
    "client_fs_ufcf.csv": list(UFCF_FORECAST.column_names()) + ["value_class"],
    "client_fs_valuation_inputs.csv":
        list(VALUATION_INPUTS.column_names()) + ["value_class"],
    "client_fs_controls.csv":
        list(CONTROL_CHECKS.column_names()) + ["value_class"],
    "client_fs_review.csv":
        list(AGENT_REVIEW_LOG.column_names()) + ["value_class"],
    "market_rf_policy.csv":
        list(RISK_FREE_POLICY.column_names()) + ["value_class"],
}


def build_exports(combined_statements, controls_frame, ufcf_frame,
                  valuation_frame, review_frame, rf_policy,
                  reporting_currency) -> dict:
    """Assemble every curated export as {filename: DataFrame}."""
    out = {}

    statements = combined_statements.copy()
    statements["value_class"] = [
        "CLIENT_FS" if kind == "REPORTED" else "ANALYST_ASSUMPTION"
        for kind in statements["reported_or_adjusted"]
    ]
    out["client_fs_statements.csv"] = statements[
        EXPORT_COLUMNS["client_fs_statements.csv"]
    ]

    walks = []
    for view in ("REPORTED", "NORMALIZED", "PROFORMA"):
        walk = view_income_summary(combined_statements, view)
        walk["ebitda_margin_pct"] = round(
            walk["ebitda"] / walk["revenue"] * 100, 4)
        walk["ebit_margin_pct"] = round(
            walk["ebit"] / walk["revenue"] * 100, 4)
        walks.append(walk)
    income = pd.concat(walks, ignore_index=True)
    income["company_id"] = combined_statements["company_id"].iloc[0]
    income["reporting_currency"] = reporting_currency
    income["value_class"] = "CALCULATED"
    out["client_fs_income_walk.csv"] = income[
        EXPORT_COLUMNS["client_fs_income_walk.csv"]
    ]

    ufcf = ufcf_frame.copy()
    ufcf["value_class"] = "CALCULATED"
    out["client_fs_ufcf.csv"] = ufcf[EXPORT_COLUMNS["client_fs_ufcf.csv"]]

    valuation = valuation_frame.copy()
    valuation["value_class"] = "CALCULATED"
    out["client_fs_valuation_inputs.csv"] = valuation[
        EXPORT_COLUMNS["client_fs_valuation_inputs.csv"]
    ]

    controls = controls_frame.copy()
    controls["value_class"] = "CALCULATED"
    out["client_fs_controls.csv"] = controls[
        EXPORT_COLUMNS["client_fs_controls.csv"]
    ]

    review = review_frame.copy()
    review["value_class"] = "MODEL_OUTPUT"
    out["client_fs_review.csv"] = review[
        EXPORT_COLUMNS["client_fs_review.csv"]
    ]

    policy = rf_policy.copy()
    policy["value_class"] = [
        "MODEL_OUTPUT" if rule == "MODEL_PREDICTION" else "MARKET_DATA"
        for rule in policy["observation_rule"]
    ]
    out["market_rf_policy.csv"] = policy[
        EXPORT_COLUMNS["market_rf_policy.csv"]
    ]

    for name, frame in out.items():
        assert list(frame.columns) == EXPORT_COLUMNS[name]
    return out


def write_exports(exports: dict, reports_dir=None) -> list:
    reports_dir = Path(reports_dir) if reports_dir else REPORTS_DIR
    paths = []
    for name, frame in exports.items():
        path = reports_dir / name
        frame.to_csv(path, index=False)
        paths.append(path)
        log.info("wrote %s (%d rows)", path, len(frame))
    return paths

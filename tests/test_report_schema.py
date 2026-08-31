"""
Regression test that locks the Power BI contract.

reports/finance_scenario_report.csv is the file the Power BI report
(reports/ML Tool.pbix) reads. If any code change ever renames, removes,
or reorders its columns — or drops a scenario row — the Power BI visuals
break silently the next time the data refreshes.

This test freezes the exact schema produced by
models/export_finance_report.py, so any change that would break the
report fails HERE first, before it can be pushed.

Run it from the repo root with:
    pytest tests/test_report_schema.py -v
"""

from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
REPORT_FILE = BASE_DIR / "reports" / "finance_scenario_report.csv"

# The full column contract, in order, exactly as produced by
# models/export_finance_report.py. Power BI depends on these names.
EXPECTED_COLUMNS = [
    # Scenario identity
    "scenario",
    "scenario_sort",
    # Macro inputs
    "treasury_2y",
    "fed_funds",
    "cpi",
    "unemployment",
    # ML output (predicted 10Y Treasury = risk-free rate)
    "risk_free_rate_pct",
    # WACC assumptions
    "beta",
    "equity_risk_premium_pct",
    "equity_weight_pct",
    "debt_weight_pct",
    "credit_spread_pct",
    "tax_rate_pct",
    # Cost of capital
    "cost_of_equity_pct",
    "cost_of_debt_pct",
    "wacc_pct",
    # ROIC / value creation
    "revenue",
    "ebit_margin_pct",
    "ebit",
    "nopat",
    "invested_capital",
    "roic_pct",
    "roic_wacc_spread_pct",
    "project_irr_pct",
    # Valuation assumptions
    "debt",
    "cash",
    "net_debt",
    "shares_outstanding",
    "terminal_growth_pct",
    # Valuation outputs
    "enterprise_value",
    "equity_value",
    "implied_share_price",
]

# The three rate scenarios the report is built around.
EXPECTED_SCENARIOS = {"Lower Rate", "Base", "Higher Rate"}


def test_report_file_exists():
    assert REPORT_FILE.exists(), (
        f"{REPORT_FILE} is missing. Regenerate it with: "
        "python models/export_finance_report.py"
    )


def test_report_columns_match_power_bi_contract():
    df = pd.read_csv(REPORT_FILE)

    actual = list(df.columns)

    missing = [c for c in EXPECTED_COLUMNS if c not in actual]
    unexpected = [c for c in actual if c not in EXPECTED_COLUMNS]

    assert actual == EXPECTED_COLUMNS, (
        "finance_scenario_report.csv no longer matches the Power BI "
        f"contract.\nMissing columns: {missing}\n"
        f"Unexpected columns: {unexpected}\n"
        "If this change is intentional, the Power BI report (ML Tool.pbix) "
        "must be updated at the same time, and this test's EXPECTED_COLUMNS "
        "list updated to match."
    )


def test_report_has_all_three_scenarios():
    df = pd.read_csv(REPORT_FILE)

    assert set(df["scenario"]) == EXPECTED_SCENARIOS, (
        f"Expected scenarios {EXPECTED_SCENARIOS}, "
        f"found {set(df['scenario'])}"
    )

    assert len(df) == 3, f"Expected exactly 3 scenario rows, found {len(df)}"


def test_key_outputs_are_numeric_and_populated():
    df = pd.read_csv(REPORT_FILE)

    key_outputs = [
        "risk_free_rate_pct",
        "wacc_pct",
        "enterprise_value",
        "equity_value",
        "implied_share_price",
        "roic_pct",
    ]

    for column in key_outputs:
        values = pd.to_numeric(df[column], errors="coerce")
        assert values.notna().all(), (
            f"Column '{column}' contains missing or non-numeric values — "
            "Power BI visuals built on it would show blanks."
        )

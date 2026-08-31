from pathlib import Path

import joblib
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]

MODEL_FILE = BASE_DIR / "models" / "treasury_10y_model.joblib"
REPORT_FILE = BASE_DIR / "reports" / "finance_scenario_report.csv"


# ------------------------------------------------
# LOAD ML MODEL
# ------------------------------------------------

saved = joblib.load(MODEL_FILE)

model = saved["model"]
features = saved["features"]


# ------------------------------------------------
# MACRO SCENARIOS
# ------------------------------------------------

scenarios = pd.DataFrame(
    [
        {
            "scenario": "Lower Rate",
            "treasury_2y": 3.50,
            "fed_funds": 3.75,
            "cpi": 2.20,
            "unemployment": 4.70,
        },
        {
            "scenario": "Base",
            "treasury_2y": 4.25,
            "fed_funds": 4.25,
            "cpi": 2.80,
            "unemployment": 4.40,
        },
        {
            "scenario": "Higher Rate",
            "treasury_2y": 5.00,
            "fed_funds": 5.25,
            "cpi": 4.00,
            "unemployment": 4.00,
        },
    ]
)


# ------------------------------------------------
# ML PREDICTION
# Predict 10Y Treasury / risk-free rate
# ------------------------------------------------

scenarios["risk_free_rate"] = (
    model.predict(scenarios[features]) / 100
)


# ------------------------------------------------
# CORPORATE FINANCE ASSUMPTIONS
# These are the current model assumptions.
# Later these can be replaced with dynamic company /
# market data without changing the Power BI structure.
# ------------------------------------------------

beta = 1.20
equity_risk_premium = 0.045

equity_weight = 0.70
debt_weight = 0.30

credit_spread = 0.020
tax_rate = 0.25

debt = 500
cash = 150
shares = 100

fcf = [
    100,
    110,
    121,
    133,
    146,
]

terminal_growth = 0.025


# ------------------------------------------------
# CAPM / COST OF DEBT / WACC
# ------------------------------------------------

scenarios["cost_of_equity"] = (
    scenarios["risk_free_rate"]
    + beta * equity_risk_premium
)

scenarios["cost_of_debt"] = (
    scenarios["risk_free_rate"]
    + credit_spread
)

scenarios["wacc"] = (
    equity_weight * scenarios["cost_of_equity"]
    +
    debt_weight
    * scenarios["cost_of_debt"]
    * (1 - tax_rate)
)


# ------------------------------------------------
# DCF
# ------------------------------------------------

def calculate_ev(wacc):
    pv_fcf = sum(
        cash_flow / ((1 + wacc) ** year)
        for year, cash_flow in enumerate(
            fcf,
            start=1,
        )
    )

    terminal_value = (
        fcf[-1] * (1 + terminal_growth)
        /
        (wacc - terminal_growth)
    )

    pv_terminal = (
        terminal_value
        /
        ((1 + wacc) ** len(fcf))
    )

    return pv_fcf + pv_terminal


scenarios["enterprise_value"] = (
    scenarios["wacc"].apply(calculate_ev)
)

scenarios["equity_value"] = (
    scenarios["enterprise_value"]
    - debt
    + cash
)

scenarios["implied_share_price"] = (
    scenarios["equity_value"]
    / shares
)


# ------------------------------------------------
# REPORTING / DISPLAY COLUMNS
# Convert decimal finance rates to percentage values
# for easier use inside Power BI.
# ------------------------------------------------

scenarios["risk_free_rate_pct"] = (
    scenarios["risk_free_rate"] * 100
)

scenarios["cost_of_equity_pct"] = (
    scenarios["cost_of_equity"] * 100
)

scenarios["cost_of_debt_pct"] = (
    scenarios["cost_of_debt"] * 100
)

scenarios["wacc_pct"] = (
    scenarios["wacc"] * 100
)


# ------------------------------------------------
# EXPOSE WACC ASSUMPTIONS TO POWER BI
# ------------------------------------------------

scenarios["beta"] = beta

scenarios["equity_risk_premium_pct"] = (
    equity_risk_premium * 100
)

scenarios["equity_weight_pct"] = (
    equity_weight * 100
)

scenarios["debt_weight_pct"] = (
    debt_weight * 100
)

scenarios["credit_spread_pct"] = (
    credit_spread * 100
)

scenarios["tax_rate_pct"] = (
    tax_rate * 100
)


# ------------------------------------------------
# EXPOSE VALUATION ASSUMPTIONS TO POWER BI
# ------------------------------------------------

scenarios["debt"] = debt
scenarios["cash"] = cash

scenarios["net_debt"] = (
    debt - cash
)

scenarios["shares_outstanding"] = shares

scenarios["terminal_growth_pct"] = (
    terminal_growth * 100
)


# ------------------------------------------------
# SCENARIO SORT ORDER
# ------------------------------------------------

scenario_sort_map = {
    "Lower Rate": 1,
    "Base": 2,
    "Higher Rate": 3,
}

scenarios["scenario_sort"] = (
    scenarios["scenario"]
    .map(scenario_sort_map)
)


# ------------------------------------------------
# FINAL REPORT
# ------------------------------------------------
# ROIC operating inputs
revenue = 1000
ebit_margin = 0.20
invested_capital = 1500

# Operating profit
ebit = revenue * ebit_margin

# After-tax operating profit
nopat = ebit * (1 - tax_rate)

# Return on invested capital
roic_pct = (nopat / invested_capital) * 100

scenarios["revenue"] = revenue
scenarios["ebit_margin_pct"] = ebit_margin * 100
scenarios["ebit"] = ebit
scenarios["invested_capital"] = invested_capital
scenarios["nopat"] = nopat
scenarios["roic_pct"] = roic_pct
scenarios["roic_wacc_spread_pct"] = (
    scenarios["roic_pct"] - scenarios["wacc_pct"]
)

# Project IRR assumptions
initial_investment = 1500

# Calculate IRR separately for each rate scenario
project_irr_pct = []
# IRR calculation function
def calculate_irr(cash_flows, low=-0.99, high=10.0, tolerance=0.000001):
    def npv(rate):
        return sum(
            cash_flow / ((1 + rate) ** year)
            for year, cash_flow in enumerate(cash_flows)
        )

    low_npv = npv(low)
    high_npv = npv(high)

    if low_npv * high_npv > 0:
        return None

    for _ in range(200):
        mid = (low + high) / 2
        mid_npv = npv(mid)

        if abs(mid_npv) < tolerance:
            return mid

        if low_npv * mid_npv <= 0:
            high = mid
        else:
            low = mid
            low_npv = mid_npv

    return mid


# Project cash flows
# Use Base-case WACC for terminal value in the IRR cash-flow set
base_wacc = scenarios.loc[
    scenarios["scenario"] == "Base",
    "wacc_pct"
].iloc[0] / 100

project_terminal_value = (
    fcf[-1] * (1 + terminal_growth)
) / (base_wacc - terminal_growth)

project_cash_flows = [
    -initial_investment,
    fcf[0],
    fcf[1],
    fcf[2],
    fcf[3],
    fcf[4] + project_terminal_value,
]

# Calculate project IRR
irr = calculate_irr(project_cash_flows)

project_irr_pct = (
    irr * 100 if irr is not None else None
)

# Add IRR to all scenarios
scenarios["project_irr_pct"] = project_irr_pct

report_columns = [
    
    "scenario",
    "scenario_sort",

    # Macro inputs
    "treasury_2y",
    "fed_funds",
    "cpi",
    "unemployment",

    # ML / rates
    "risk_free_rate_pct",

    # Corporate finance assumptions
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

report = scenarios[report_columns].copy()


# ------------------------------------------------
# EXPORT
# ------------------------------------------------

REPORT_FILE.parent.mkdir(
    parents=True,
    exist_ok=True,
)

report.to_csv(
    REPORT_FILE,
    index=False,
)


# ------------------------------------------------
# OUTPUT
# ------------------------------------------------

print(report.round(2))

print()

print("Report saved to:")
print(REPORT_FILE)


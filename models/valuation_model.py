from pathlib import Path
import joblib
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_FILE = BASE_DIR / "models" / "treasury_10y_model.joblib"

saved = joblib.load(MODEL_FILE)
model = saved["model"]
features = saved["features"]

# ============================================================
# 1. MACRO SCENARIOS
# ============================================================

scenarios = pd.DataFrame([
    {
        "scenario": "Lower Rate",
        "treasury_2y": 3.50,
        "fed_funds": 3.75,
        "cpi": 2.20,
        "unemployment": 4.70
    },
    {
        "scenario": "Base",
        "treasury_2y": 4.25,
        "fed_funds": 4.25,
        "cpi": 2.80,
        "unemployment": 4.40
    },
    {
        "scenario": "Higher Rate",
        "treasury_2y": 5.00,
        "fed_funds": 5.25,
        "cpi": 4.00,
        "unemployment": 4.00
    }
])

# ============================================================
# 2. MACHINE LEARNING
# Predict 10Y Treasury
# ============================================================

scenarios["risk_free_rate"] = (
    model.predict(scenarios[features]) / 100
)

# ============================================================
# 3. CORPORATE FINANCE ASSUMPTIONS
# ============================================================

beta = 1.20
equity_risk_premium = 0.045

equity_weight = 0.70
debt_weight = 0.30

credit_spread = 0.020
tax_rate = 0.25

# ============================================================
# 4. CAPM + COST OF DEBT
# ============================================================

scenarios["cost_of_equity"] = (
    scenarios["risk_free_rate"]
    + beta * equity_risk_premium
)

scenarios["cost_of_debt"] = (
    scenarios["risk_free_rate"]
    + credit_spread
)

# ============================================================
# 5. WACC
# ============================================================

scenarios["wacc"] = (
    equity_weight * scenarios["cost_of_equity"]
    +
    debt_weight
    * scenarios["cost_of_debt"]
    * (1 - tax_rate)
)

# ============================================================
# 6. FREE CASH FLOW FORECAST
# $ millions
# ============================================================

fcf = [
    100,
    110,
    121,
    133,
    146
]

terminal_growth = 0.025

# ============================================================
# 7. DCF
# ============================================================

def calculate_dcf(wacc, growth=terminal_growth):

    pv_fcf = 0

    for year, cash_flow in enumerate(fcf, start=1):

        pv_fcf += (
            cash_flow
            / ((1 + wacc) ** year)
        )

    terminal_value = (
        fcf[-1] * (1 + growth)
        / (wacc - growth)
    )

    pv_terminal = (
        terminal_value
        / ((1 + wacc) ** len(fcf))
    )

    enterprise_value = (
        pv_fcf + pv_terminal
    )

    return enterprise_value

scenarios["enterprise_value"] = (
    scenarios["wacc"].apply(calculate_dcf)
)

# ============================================================
# 8. EV → EQUITY VALUE
# $ millions
# ============================================================

debt = 500
cash = 150

net_debt = debt - cash

scenarios["equity_value"] = (
    scenarios["enterprise_value"]
    - net_debt
)

# ============================================================
# 9. EQUITY VALUE → SHARE PRICE
# millions of shares
# ============================================================

shares_outstanding = 100

scenarios["implied_share_price"] = (
    scenarios["equity_value"]
    / shares_outstanding
)

# ============================================================
# 10. DISPLAY RESULTS
# ============================================================

results = scenarios[
    [
        "scenario",
        "risk_free_rate",
        "wacc",
        "enterprise_value",
        "equity_value",
        "implied_share_price"
    ]
].copy()

results["risk_free_rate"] *= 100
results["wacc"] *= 100

print()
print("VALUATION SCENARIO ANALYSIS")
print("=" * 75)

print(
    results.round(2).to_string(index=False)
)

# ============================================================
# 11. WACC × TERMINAL GROWTH SENSITIVITY
# ============================================================

print()
print("IMPLIED SHARE PRICE SENSITIVITY")
print("=" * 75)

wacc_values = [
    0.075,
    0.080,
    0.085,
    0.090,
    0.095
]

growth_values = [
    0.015,
    0.020,
    0.025,
    0.030,
    0.035
]

sensitivity = pd.DataFrame(
    index=[f"{g:.1%}" for g in growth_values],
    columns=[f"{w:.1%}" for w in wacc_values]
)

for growth in growth_values:

    for wacc in wacc_values:

        ev = calculate_dcf(
            wacc,
            growth
        )

        equity_value = (
            ev - net_debt
        )

        share_price = (
            equity_value
            / shares_outstanding
        )

        sensitivity.loc[
            f"{growth:.1%}",
            f"{wacc:.1%}"
        ] = round(share_price, 2)

print()
print("Rows = Terminal Growth")
print("Columns = WACC")
print()

print(sensitivity)
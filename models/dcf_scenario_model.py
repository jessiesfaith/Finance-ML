from pathlib import Path
import joblib
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_FILE = BASE_DIR / "models" / "treasury_10y_model.joblib"

saved = joblib.load(MODEL_FILE)
model = saved["model"]
features = saved["features"]

# --------------------------------
# MACRO SCENARIOS
# --------------------------------

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

# ML prediction
scenarios["risk_free_rate"] = model.predict(
    scenarios[features]
) / 100


# --------------------------------
# WACC ASSUMPTIONS
# --------------------------------

beta = 1.20
equity_risk_premium = 0.045

equity_weight = 0.70
debt_weight = 0.30

credit_spread = 0.020
tax_rate = 0.25


# CAPM
scenarios["cost_of_equity"] = (
    scenarios["risk_free_rate"]
    + beta * equity_risk_premium
)

# Cost of debt
scenarios["cost_of_debt"] = (
    scenarios["risk_free_rate"]
    + credit_spread
)

# WACC
scenarios["wacc"] = (
    equity_weight * scenarios["cost_of_equity"]
    +
    debt_weight
    * scenarios["cost_of_debt"]
    * (1 - tax_rate)
)


# --------------------------------
# COMPANY FREE CASH FLOW
# $ millions
# --------------------------------

fcf = [
    100,
    110,
    121,
    133,
    146
]

terminal_growth = 0.025


# --------------------------------
# DCF FUNCTION
# --------------------------------

def calculate_dcf(wacc):

    pv_fcf = 0

    for year, cash_flow in enumerate(fcf, start=1):

        pv = cash_flow / ((1 + wacc) ** year)

        pv_fcf += pv


    # Terminal Value
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

    enterprise_value = (
        pv_fcf
        + pv_terminal
    )

    return enterprise_value


# --------------------------------
# VALUE EACH SCENARIO
# --------------------------------

scenarios["enterprise_value"] = (
    scenarios["wacc"].apply(calculate_dcf)
)


# --------------------------------
# DISPLAY
# --------------------------------

results = scenarios[
    [
        "scenario",
        "risk_free_rate",
        "wacc",
        "enterprise_value"
    ]
].copy()

results["risk_free_rate"] *= 100
results["wacc"] *= 100

print()
print("DCF SCENARIO ANALYSIS")
print("-----------------------------")

print(
    results.round(2).to_string(index=False)
)
from pathlib import Path
import joblib
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_FILE = BASE_DIR / "models" / "treasury_10y_model.joblib"

saved = joblib.load(MODEL_FILE)
model = saved["model"]
features = saved["features"]

# --------------------------------
# ECONOMIC SCENARIOS
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

# ML predicts Treasury 10Y
scenarios["risk_free_rate"] = model.predict(
    scenarios[features]
)

# --------------------------------
# CORPORATE FINANCE ASSUMPTIONS
# --------------------------------

beta = 1.20
equity_risk_premium = 0.045

debt_weight = 0.30
equity_weight = 0.70

credit_spread = 0.020
tax_rate = 0.25

# --------------------------------
# CAPM
# Cost of Equity = Rf + Beta × ERP
# --------------------------------

scenarios["cost_of_equity"] = (
    scenarios["risk_free_rate"] / 100
    + beta * equity_risk_premium
)

# --------------------------------
# COST OF DEBT
# --------------------------------

scenarios["cost_of_debt"] = (
    scenarios["risk_free_rate"] / 100
    + credit_spread
)

# --------------------------------
# WACC
# --------------------------------

scenarios["wacc"] = (
    equity_weight * scenarios["cost_of_equity"]
    +
    debt_weight
    * scenarios["cost_of_debt"]
    * (1 - tax_rate)
)

# Convert to percentages for display

scenarios["predicted_10y_%"] = scenarios["risk_free_rate"]

scenarios["cost_of_equity_%"] = (
    scenarios["cost_of_equity"] * 100
)

scenarios["cost_of_debt_%"] = (
    scenarios["cost_of_debt"] * 100
)

scenarios["wacc_%"] = (
    scenarios["wacc"] * 100
)

print(
    scenarios[
        [
            "scenario",
            "predicted_10y_%",
            "cost_of_equity_%",
            "cost_of_debt_%",
            "wacc_%"
        ]
    ].round(2).to_string(index=False)
)
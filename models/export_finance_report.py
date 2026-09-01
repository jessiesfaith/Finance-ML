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
# ANALYST / MARKET ASSUMPTIONS
# Still explicit and documented (audit list AD): beta and the credit
# spread until peer/market data lands, ERP and terminal growth by design.
# ------------------------------------------------

beta = 1.20
equity_risk_premium = 0.045
credit_spread = 0.020
terminal_growth = 0.025


# ------------------------------------------------
# STATEMENT-DERIVED INPUTS — the 2026-08-31 cutover (DECISIONS #62)
# The DCF no longer runs on hard-coded placeholders. Every input below
# is read from the client-financials pipeline outputs, each rebuild-
# locked by tests and traceable back to source statements
# (file / sheet / row) through the lineage the pipeline preserves.
# ------------------------------------------------

CLIENT_FS_DIR = BASE_DIR / "data" / "client_fs"

ufcf_forecast = pd.read_csv(CLIENT_FS_DIR / "ufcf_forecast.csv")
valuation_inputs = pd.read_csv(CLIENT_FS_DIR / "valuation_inputs.csv")
shares_dilution = pd.read_csv(CLIENT_FS_DIR / "shares_dilution.csv")
scenario_assumptions = pd.read_csv(
    BASE_DIR / "data" / "scenarios" / "scenario_assumptions.csv"
)

# Normalized tax rate: one source of truth, the analyst driver table.
tax_rate = float(scenario_assumptions.loc[
    scenario_assumptions["target_id"] == "TAX_RATE_PCT", "value"
].iloc[0]) / 100

# UFCF path: the driver-based forecast (Base scenario) replaces the old
# fcf = [100, 110, 121, 133, 146] placeholder list.
fcf = ufcf_forecast.loc[
    ufcf_forecast["forecast_method"] == "DRIVER_BASED", "ufcf"
].tolist()

# Net-debt components from the consolidated balance sheet (was 500/150).
latest_valuation = valuation_inputs.sort_values("period_id").iloc[-1]
debt = float(latest_valuation["total_debt"])
cash = float(latest_valuation["cash_and_equivalents"])

# Diluted shares via the treasury-stock method (was a 100M basic count).
share_row = shares_dilution.iloc[0]
shares = float(share_row["diluted_shares_m"])

# Market-value capital structure (was an assumed 70/30):
# E = price x diluted shares; weights = E/(D+E) and D/(D+E).
equity_market_value = float(share_row["market_price"]) * shares
total_capitalization = equity_market_value + debt
equity_weight = equity_market_value / total_capitalization
debt_weight = debt / total_capitalization


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
# ROIC inputs from the consolidated statements (latest actual period) —
# replacing the old revenue 1000 / 20% margin / $1.5B invested capital.
latest_actual = ufcf_forecast[
    ufcf_forecast["forecast_method"] == "ACTUAL"
].iloc[-1]

revenue = float(latest_actual["revenue"])
ebit = float(latest_actual["ebit"])
ebit_margin = ebit / revenue

# Component-built invested capital (operating NWC + net PP&E) and the
# ROIC computed on it (ENDING basis) — see valuation_inputs.csv.
invested_capital = float(latest_valuation["invested_capital"])
nopat = float(latest_valuation["nopat"])
roic_pct = float(latest_valuation["roic_pct"])

scenarios["revenue"] = revenue
scenarios["ebit_margin_pct"] = ebit_margin * 100
scenarios["ebit"] = ebit
scenarios["invested_capital"] = invested_capital
scenarios["nopat"] = nopat
scenarios["roic_pct"] = roic_pct
scenarios["roic_wacc_spread_pct"] = (
    scenarios["roic_pct"] - scenarios["wacc_pct"]
)

# ------------------------------------------------
# PROJECT APPRAISAL ASSUMPTIONS
# ------------------------------------------------
# Hurdle rate = WACC + a project risk premium. This was previously
# defined ONLY as a DAX measure inside the Power BI model ("Hurdle
# Rate (%)" = Calculated WACC + 2.00), invisible to this pipeline.
# Promoted here per the 2026-08-31 model audit (docs/MODEL_AUDIT.md,
# finding D2) so one engine owns the project cash-flow set.
# initial_investment stays an EXPLICIT project-budget assumption,
# deliberately decoupled from invested capital (audit dependency map).
initial_investment = 1500
hurdle_premium = 0.02

scenarios["hurdle_rate_pct"] = (
    scenarios["wacc_pct"] + hurdle_premium * 100
)


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


# ------------------------------------------------
# PROJECT IRR — one IRR per scenario, on the SAME cash-flow set the
# report's Project NPV panel displays.
# ------------------------------------------------
# The project terminal value is built at each scenario's HURDLE rate,
# matching the Power BI "Project Terminal Value ($M)" and "Project NPV
# ($M)" measures (which discount at the hurdle). Before the audit this
# used the Base-scenario WACC instead, so the IRR shown on the report
# (17.54%) could not be reproduced from the terminal value shown next
# to it (audit finding D2). Now the displayed inputs and the displayed
# IRR reconcile, scenario by scenario.
project_irr_pct = []

for hurdle_pct in scenarios["hurdle_rate_pct"]:

    hurdle = hurdle_pct / 100

    project_terminal_value = (
        fcf[-1] * (1 + terminal_growth)
    ) / (hurdle - terminal_growth)

    project_cash_flows = [
        -initial_investment,
        fcf[0],
        fcf[1],
        fcf[2],
        fcf[3],
        fcf[4] + project_terminal_value,
    ]

    irr = calculate_irr(project_cash_flows)

    project_irr_pct.append(
        irr * 100 if irr is not None else None
    )

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
"hurdle_rate_pct",
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


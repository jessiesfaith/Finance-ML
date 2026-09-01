"""
NOPAT + UFCF engine — Phase 5 (spec sections 14–15).

The walk this module makes explicit, statement by statement:

    Revenue − Operating costs = EBITDA
    EBITDA − D&A            = EBIT
    EBIT × (1 − tax)        = NOPAT
    NOPAT + D&A − CapEx − ΔNWC = UFCF

Two tax rates exist and are never conflated (spec section 14): the
REPORTED EFFECTIVE rate (tax expense ÷ pretax income, shown in the
income walk for what actually happened) and the ANALYST NORMALIZED rate
(a scenario driver, used for NOPAT on a forecast-consistent basis). The
`tax_rate_pct` column records the rate actually used per row.

Forecasts are DRIVER-BASED, never arbitrary hard-coded cash flows: the
drivers live in data/scenarios/scenario_assumptions.csv (revenue growth,
EBITDA margin, D&A and CapEx as % of revenue, normalized tax). NWC
components are held at their last-actual percent of revenue unless an
explicit NWC_PCT_REVENUE override exists. This is what will replace the
valuation model's hard-coded fcf = [100, 110, 121, 133, 146].

Output: data/client_fs/ufcf_forecast.csv — actuals + forecast in one
walk table, every intermediate visible (positive magnitudes;
UFCF = nopat + da − capex − delta_nwc).
"""

import logging
from pathlib import Path

import pandas as pd

from financials.nwc import nwc_components
from financials.scenarios import company_drivers
from financials.schemas import UFCF_FORECAST

log = logging.getLogger("financials.ufcf")

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = BASE_DIR / "data" / "client_fs" / UFCF_FORECAST.filename

REQUIRED_DRIVERS = (
    "REVENUE_GROWTH_PCT", "EBITDA_MARGIN_PCT", "DA_PCT_REVENUE",
    "CAPEX_PCT_REVENUE", "TAX_RATE_PCT",
)


def _amount(consolidated, period, account):
    match = consolidated[
        (consolidated["period_id"] == period)
        & (consolidated["standard_account_id"] == account)
    ]
    return float(match["consolidated_amount"].iloc[0]) if len(match) else 0.0


def income_walk(consolidated: pd.DataFrame) -> pd.DataFrame:
    """
    Revenue → EBITDA → EBIT → pretax → reported effective tax rate, per
    period, from consolidated canonical-sign amounts. Positive-magnitude
    presentation for the walk (costs shown as positive costs).
    """
    periods = sorted(
        consolidated.loc[
            consolidated["statement_type"] == "IS", "period_id"
        ].unique()
    )
    rows = []
    for period in periods:
        revenue = (_amount(consolidated, period, "revenue")
                   + _amount(consolidated, period, "other_operating_income"))
        cogs = -_amount(consolidated, period, "cogs")
        opex = -_amount(consolidated, period, "opex")
        da = -_amount(consolidated, period, "depreciation_amortization")
        interest = -_amount(consolidated, period, "interest_expense")
        tax = -_amount(consolidated, period, "income_tax_expense")

        ebitda = revenue - cogs - opex
        ebit = ebitda - da
        pretax = ebit - interest
        rows.append({
            "period_id": period,
            "revenue": revenue,
            "operating_costs": cogs + opex,
            "ebitda": ebitda,
            "da": da,
            "ebit": ebit,
            "interest_expense": interest,
            "pretax_income": pretax,
            "tax_expense": tax,
            "effective_tax_rate_pct": (
                round(tax / pretax * 100, 4) if pretax else None
            ),
        })
    return pd.DataFrame(rows)


def build_ufcf_forecast(tables, consolidated, scenario_tables,
                        scenario_id="BASE", forecast_years=5) -> pd.DataFrame:
    """
    Actual + driver-based forecast walk rows for one scenario.
    Historical rows carry what the statements support (FY2024 has no
    CFS, so its capex/ufcf stay blank rather than invented).
    """
    company_id = tables["company_master"]["company_id"].iloc[0]
    currency = tables["company_master"]["reporting_currency"].iloc[0]
    period_master = tables["period_master"]

    drivers = company_drivers(scenario_tables, scenario_id, company_id)
    missing = [d for d in REQUIRED_DRIVERS if d not in drivers]
    if missing:
        raise ValueError(
            f"scenario '{scenario_id}' is missing required driver(s) "
            f"{missing} in scenario_assumptions.csv"
        )

    walk = income_walk(consolidated).set_index("period_id")
    nwc = nwc_components(
        consolidated, tables["account_mapping"]
    ).set_index("period_id")
    normalized_tax = drivers["TAX_RATE_PCT"] / 100

    rows = []

    # ---------- historical actuals ----------
    actual_periods = list(walk.index)
    for i, period in enumerate(actual_periods):
        w, n = walk.loc[period], nwc.loc[period]
        capex = -_amount(consolidated, period, "cfs_capex")  # canonical −
        has_capex = capex != 0.0
        prior_rev = (
            walk.loc[actual_periods[i - 1], "revenue"] if i > 0 else None
        )
        ufcf = (
            w["ebit"] * (1 - normalized_tax) + w["da"] - capex - n["delta_nwc"]
            if has_capex and pd.notna(n["delta_nwc"]) else None
        )
        rows.append({
            "period_id": period,
            "revenue": w["revenue"],
            "revenue_growth_pct": (
                round((w["revenue"] / prior_rev - 1) * 100, 4)
                if prior_rev else None
            ),
            "ebitda": w["ebitda"],
            "da": w["da"],
            "ebit": w["ebit"],
            "nopat": w["ebit"] * (1 - normalized_tax),
            "accounts_receivable": n["accounts_receivable"],
            "inventory": n["inventory"],
            "other_operating_current_assets": n["other_operating_current_assets"],
            "accounts_payable": n["accounts_payable"],
            "other_operating_current_liabilities": n["other_operating_current_liabilities"],
            "operating_nwc": n["operating_nwc"],
            "delta_nwc": n["delta_nwc"] if pd.notna(n["delta_nwc"]) else None,
            "capex": capex if has_capex else None,
            "ufcf": ufcf,
            "forecast_method": "ACTUAL",
            "review_status": "APPROVED",
        })

    # ---------- driver-based forecast ----------
    forecast_periods = (
        period_master[period_master["is_forecast"] == "Y"]
        .sort_values("fiscal_year")["period_id"]
        .tolist()[:forecast_years]
    )

    last = rows[-1]
    growth = drivers["REVENUE_GROWTH_PCT"] / 100
    margin = drivers["EBITDA_MARGIN_PCT"] / 100
    da_pct = drivers["DA_PCT_REVENUE"] / 100
    capex_pct = drivers["CAPEX_PCT_REVENUE"] / 100

    base_rev = last["revenue"]
    component_ratios = {
        c: last[c] / base_rev
        for c in ("accounts_receivable", "inventory",
                  "other_operating_current_assets", "accounts_payable",
                  "other_operating_current_liabilities")
    }
    nwc_override = drivers.get("NWC_PCT_REVENUE")

    prev_rev, prev_nwc = base_rev, last["operating_nwc"]
    for period in forecast_periods:
        revenue = prev_rev * (1 + growth)
        ebitda = revenue * margin
        da = revenue * da_pct
        ebit = ebitda - da
        nopat = ebit * (1 - normalized_tax)
        capex = revenue * capex_pct

        components = {c: revenue * r for c, r in component_ratios.items()}
        operating_nwc = (
            revenue * nwc_override / 100 if nwc_override is not None
            else components["accounts_receivable"] + components["inventory"]
            + components["other_operating_current_assets"]
            - components["accounts_payable"]
            - components["other_operating_current_liabilities"]
        )
        delta_nwc = operating_nwc - prev_nwc
        ufcf = nopat + da - capex - delta_nwc

        rows.append({
            "period_id": period,
            "revenue": revenue,
            "revenue_growth_pct": round(growth * 100, 4),
            "ebitda": ebitda,
            "da": da,
            "ebit": ebit,
            "nopat": nopat,
            **components,
            "operating_nwc": operating_nwc,
            "delta_nwc": delta_nwc,
            "capex": capex,
            "ufcf": ufcf,
            "forecast_method": "DRIVER_BASED",
            "review_status": "REVIEW",
        })
        prev_rev, prev_nwc = revenue, operating_nwc

    frame = pd.DataFrame(rows)
    frame["company_id"] = company_id
    frame["scenario"] = scenario_id
    frame["ebitda_margin_pct"] = round(
        frame["ebitda"] / frame["revenue"] * 100, 4
    )
    frame["ebit_margin_pct"] = round(frame["ebit"] / frame["revenue"] * 100, 4)
    frame["tax_rate_pct"] = drivers["TAX_RATE_PCT"]

    frame["reporting_currency"] = currency

    numeric = frame.select_dtypes("number").columns
    frame[numeric] = frame[numeric].round(4)

    frame = frame[UFCF_FORECAST.column_names()]

    log.info(
        "built UFCF walk: %d actual + %d forecast period(s), scenario %s",
        len(actual_periods), len(forecast_periods), scenario_id,
    )
    return frame


def write_ufcf_forecast(frame: pd.DataFrame, path=None) -> Path:
    path = Path(path) if path else DEFAULT_OUTPUT
    frame.to_csv(path, index=False)
    log.info("wrote %s", path)
    return path

"""
Build the valuation inputs: net debt, invested capital/ROIC, diluted
shares, and the market-value capital structure.

Usage (from the repo root):
    python src/build_valuation_inputs.py

Prints each build transparently (the Page 2 right-rail / Page 3 inputs)
and writes data/client_fs/valuation_inputs.csv — the hand-off table that
lets the DCF drop its hard-coded debt 500 / cash 150 / 100M shares /
$1.5B invested capital when that cutover is approved.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from financials import (
    ClientFSValidationError,
    apply_consolidation_status,
    consolidate,
    income_walk,
    load_client_fs,
    load_scenarios,
    nwc_components,
    translate_statements,
)
from financials.capital_structure import market_value_weights
from financials.invested_capital import invested_capital_components, roic
from financials.net_debt import net_debt_components
from financials.scenarios import company_drivers
from financials.schemas import VALUATION_INPUTS
from financials.shares import load_shares_dilution

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT = BASE_DIR / "data" / "client_fs" / VALUATION_INPUTS.filename


def build_valuation_inputs(tables, consolidated, scenario_tables):
    company = tables["company_master"]["company_id"].iloc[0]
    currency = tables["company_master"]["reporting_currency"].iloc[0]
    mapping = tables["account_mapping"]

    drivers = company_drivers(scenario_tables, "BASE", company)
    normalized_tax = drivers["TAX_RATE_PCT"] / 100

    nd = net_debt_components(consolidated, mapping).set_index("period_id")
    nwc = nwc_components(consolidated, mapping).set_index("period_id")
    ic = invested_capital_components(consolidated, mapping)
    walk = income_walk(consolidated).set_index("period_id")
    nopat = {p: walk.loc[p, "ebit"] * (1 - normalized_tax) for p in walk.index}
    roic_frame = roic(ic, nopat, basis="ENDING").set_index("period_id")

    shares = load_shares_dilution().set_index("period_id")

    rows = []
    for period in nd.index:
        n, w, r = nd.loc[period], nwc.loc[period], roic_frame.loc[period]
        net_ppe = r["operating_assets"] - (
            w["accounts_receivable"] + w["inventory"]
            + w["other_operating_current_assets"]
        )
        share_row = shares.loc[period] if period in shares.index else None
        rows.append({
            "company_id": company, "period_id": period, "scenario": "ACTUAL",
            "short_term_debt": n["short_term_debt"],
            "long_term_debt": n["long_term_debt"],
            "finance_leases": n["finance_leases"],
            "total_debt": n["total_debt"],
            "cash_and_equivalents": n["cash_and_equivalents"],
            "restricted_cash": n["restricted_cash"],
            "net_debt": n["net_debt"],
            "operating_nwc": w["operating_nwc"],
            "net_ppe": net_ppe,
            "other_operating_assets": 0.0,
            "other_operating_liabilities": (
                r["operating_liabilities"] - w["accounts_payable"]
                - w["other_operating_current_liabilities"]
            ),
            "invested_capital": r["invested_capital"],
            "nopat": round(r["nopat"], 4),
            "roic_basis": r["roic_basis"],
            "roic_pct": r["roic_pct"],
            "basic_shares_m": (
                share_row["basic_shares_m"] if share_row is not None else None
            ),
            "diluted_shares_m": (
                share_row["diluted_shares_m"] if share_row is not None else None
            ),
            "reporting_currency": currency,
        })

    frame = pd.DataFrame(rows)
    numeric = frame.select_dtypes("number").columns
    frame[numeric] = frame[numeric].round(4)
    return frame[VALUATION_INPUTS.column_names()]


def main():
    try:
        result = load_client_fs(strict=True)
        tables = result.tables
        translated = translate_statements(tables)
        consolidated = apply_consolidation_status(
            consolidate(translated, tables["entity_master"]),
            translated, tables["entity_master"],
        )
        scenario_tables, _ = load_scenarios(strict=True)
        frame = build_valuation_inputs(tables, consolidated, scenario_tables)
    except (ClientFSValidationError, ValueError) as exc:
        print()
        print("BUILD FAILED — fix these before continuing:")
        print(exc)
        raise SystemExit(1)

    frame.to_csv(OUTPUT, index=False)
    latest = frame.iloc[-1]
    shares = load_shares_dilution().iloc[0]

    print()
    print(f"VALUATION INPUTS — consolidated, {latest['period_id']} ($M)")
    print("=" * 66)
    print("NET DEBT:   ST debt {:,.1f} + LT debt {:,.1f} + leases {:,.1f} = Debt {:,.1f}".format(
        latest["short_term_debt"], latest["long_term_debt"],
        latest["finance_leases"], latest["total_debt"]))
    print("            Debt {:,.1f} − Cash {:,.1f} = NET DEBT {:,.1f}   (was hard-coded 350)".format(
        latest["total_debt"], latest["cash_and_equivalents"], latest["net_debt"]))
    print()
    print("INVESTED    Operating NWC {:,.1f} + Net PP&E {:,.1f} = INVESTED CAPITAL {:,.1f}".format(
        latest["operating_nwc"], latest["net_ppe"], latest["invested_capital"]))
    print("CAPITAL:    NOPAT {:,.2f} ÷ IC {:,.1f} = ROIC {:,.2f}%   (was 150 ÷ 1,500 = 10.00%)".format(
        latest["nopat"], latest["invested_capital"], latest["roic_pct"]))
    print()
    print("DILUTED     Basic {:,.1f} + TSM options {:,.4f} + RSUs {:,.1f} = DILUTED {:,.4f}M".format(
        shares["basic_shares_m"], shares["incremental_option_shares_m"],
        shares["rsus_psus_m"], shares["diluted_shares_m"]))
    print("SHARES:     TSM: 5.0 × (1 − 12.00/18.00) = 1.6667; {:,.1f}M @ $25 anti-dilutive, excluded".format(
        shares["anti_dilutive_shares_excluded_m"]))
    print()
    weights = market_value_weights(
        shares["market_price"], shares["diluted_shares_m"], latest["total_debt"]
    )
    print("CAPITAL     Equity MV = ${:,.2f} × {:,.4f}M = {:,.1f}".format(
        shares["market_price"], shares["diluted_shares_m"],
        weights["equity_market_value_m"]))
    print("STRUCTURE:  E/(D+E) = {:,.1f}/{:,.1f} = {:,.1f}%  |  D = {:,.1f}%".format(
        weights["equity_market_value_m"], weights["total_capitalization_m"],
        weights["equity_weight_pct"], weights["debt_weight_pct"]))
    print("            vs the current WACC assumption of 70.0% / 30.0% —")
    print("            derived, not assumed; cutover is a separate approval.")
    print()
    print(f"output: {OUTPUT}")


if __name__ == "__main__":
    main()

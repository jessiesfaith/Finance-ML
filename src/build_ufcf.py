"""
Build the UFCF walk: statements → EBITDA → EBIT → NOPAT → NWC → ΔNWC → UFCF.

Usage (from the repo root):
    python src/build_ufcf.py

Prints the full decision-chain walk the way it should read on the client
page — the math starts at the consolidated statements and ends at the
UFCF path that feeds the DCF — and writes
data/client_fs/ufcf_forecast.csv.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials import (
    ClientFSValidationError,
    apply_consolidation_status,
    consolidate,
    load_client_fs,
    translate_statements,
)
from financials.nwc import nwc_components
from financials.scenarios import load_scenarios
from financials.ufcf import (
    build_ufcf_forecast,
    income_walk,
    write_ufcf_forecast,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


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
        frame = build_ufcf_forecast(tables, consolidated, scenario_tables)
    except (ClientFSValidationError, ValueError) as exc:
        print()
        print("BUILD FAILED — fix these before continuing:")
        print(exc)
        raise SystemExit(1)

    path = write_ufcf_forecast(frame)
    latest_actual = frame[frame["forecast_method"] == "ACTUAL"].iloc[-1]
    walk = income_walk(consolidated).set_index("period_id")
    nwc = nwc_components(consolidated, tables["account_mapping"])
    p = latest_actual["period_id"]
    w = walk.loc[p]

    print()
    print(f"THE WALK — consolidated, {p} actuals ($M)")
    print("=" * 66)
    print("INCOME:   Revenue {:,.1f} − Operating costs {:,.1f} = EBITDA {:,.1f}".format(
        w["revenue"], w["operating_costs"], w["ebitda"]))
    print("          EBITDA {:,.1f} − D&A {:,.1f} = EBIT {:,.1f}".format(
        w["ebitda"], w["da"], w["ebit"]))
    print("          reported effective tax rate {:,.2f}% | normalized {:,.2f}%".format(
        w["effective_tax_rate_pct"], latest_actual["tax_rate_pct"]))
    print("          EBIT {:,.1f} × (1 − {:,.0f}%) = NOPAT {:,.2f}".format(
        w["ebit"], latest_actual["tax_rate_pct"], latest_actual["nopat"]))
    print()
    print("NWC:      AR {:,.1f} + Inventory {:,.1f} − AP {:,.1f} = Operating NWC {:,.1f}".format(
        latest_actual["accounts_receivable"], latest_actual["inventory"],
        latest_actual["accounts_payable"], latest_actual["operating_nwc"]))
    print("          ΔNWC = {:,.1f} − prior {:,.1f} = {:,.1f}  (increase = use of cash)".format(
        latest_actual["operating_nwc"],
        latest_actual["operating_nwc"] - latest_actual["delta_nwc"],
        latest_actual["delta_nwc"]))
    print()
    print("UFCF:     NOPAT {:,.2f} + D&A {:,.1f} − CapEx {:,.1f} − ΔNWC {:,.1f} = UFCF {:,.2f}".format(
        latest_actual["nopat"], latest_actual["da"],
        latest_actual["capex"], latest_actual["delta_nwc"],
        latest_actual["ufcf"]))

    print()
    print("FORECAST (driver-based, scenario BASE) → this is what feeds the DCF")
    print("-" * 66)
    forecast = frame[frame["forecast_method"] == "DRIVER_BASED"]
    print(forecast[[
        "period_id", "revenue", "ebitda", "ebit", "nopat",
        "operating_nwc", "delta_nwc", "capex", "ufcf",
    ]].round(1).to_string(index=False))
    print()
    print(f"output: {path}")
    print()
    print("Hand-off: these UFCF values are the statement-derived replacement")
    print("for the hard-coded fcf = [100, 110, 121, 133, 146] in")
    print("models/export_finance_report.py — switched over when approved.")


if __name__ == "__main__":
    main()

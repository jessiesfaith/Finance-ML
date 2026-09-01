"""
Sensitivity analysis (Page 4): implied share price across a WACC x
terminal-growth grid.

The point of a sensitivity table: a DCF is only as strong as its two
most powerful assumptions - the discount rate and the terminal growth
rate. The grid re-runs the SAME valuation math at +/- 1.0pt of WACC
(rows) and +/- 1.0pt of terminal growth (columns), so the reader sees
how much of the share price is conviction and how much is assumption.

The math is a deliberate re-implementation of the export's DCF (UFCF
path -> PV -> terminal value -> EV -> equity -> per share) and the
center cell is TESTED to equal the reported Base implied price - if
the two ever drift, the suite fails rather than the page lying.
"""

from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RATES_FILE = BASE_DIR / "reports" / "finance_scenario_report.csv"
UFCF_FILE = BASE_DIR / "reports" / "client_fs_ufcf.csv"
OUTPUT = BASE_DIR / "reports" / "client_fs_sensitivity.csv"

WACC_DELTAS = (-1.0, -0.5, 0.0, 0.5, 1.0)
GROWTH_GRID = (1.5, 2.0, 2.5, 3.0, 3.5)   # base terminal growth 2.5 center


def growth_column(growth_pct: float) -> str:
    return "price_at_g_" + f"{growth_pct:.1f}".replace(".", "_")


OUTPUT_COLUMNS = (["scenario", "wacc_delta_pts", "wacc_pct"]
                  + [growth_column(g) for g in GROWTH_GRID]
                  + ["value_class"])


def implied_price(ufcf, wacc_pct, growth_pct, net_debt, shares_m):
    """One DCF: five explicit years + growing perpetuity, to per-share."""
    w = wacc_pct / 100.0
    g = growth_pct / 100.0
    if w <= g:
        raise ValueError(
            f"WACC {wacc_pct}% must exceed terminal growth {growth_pct}% - "
            "a perpetuity growing faster than its discount rate is infinite")
    pv_explicit = sum(f / (1 + w) ** t for t, f in enumerate(ufcf, start=1))
    terminal = ufcf[-1] * (1 + g) / (w - g)
    ev = pv_explicit + terminal / (1 + w) ** len(ufcf)
    return (ev - net_debt) / shares_m


def load_inputs(rates_path=None, ufcf_path=None):
    rates_path = Path(rates_path) if rates_path else RATES_FILE
    ufcf_path = Path(ufcf_path) if ufcf_path else UFCF_FILE
    for path in (rates_path, ufcf_path):
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found - run the export/UFCF builders first")
    rates = pd.read_csv(rates_path)
    base = rates[rates["scenario"] == "Base"].iloc[0]
    ufcf = pd.read_csv(ufcf_path)
    path = (ufcf[ufcf["forecast_method"] == "DRIVER_BASED"]
            .sort_values("period_id")["ufcf"].astype(float).tolist())
    if len(path) != 5:
        raise ValueError(f"expected a 5-year driver-based UFCF path, "
                         f"got {len(path)} years")
    return base, path


def build_sensitivity(base, ufcf_path) -> pd.DataFrame:
    rows = []
    for delta in WACC_DELTAS:
        wacc = float(base["wacc_pct"]) + delta
        row = {"scenario": base["scenario"],
               "wacc_delta_pts": delta,
               "wacc_pct": round(wacc, 4)}
        for growth in GROWTH_GRID:
            row[growth_column(growth)] = round(
                implied_price(ufcf_path, wacc, growth,
                              float(base["net_debt"]),
                              float(base["shares_outstanding"])), 4)
        rows.append(row)
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS[:-1])
    frame["value_class"] = "CALCULATED"
    return frame

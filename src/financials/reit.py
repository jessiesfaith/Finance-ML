"""REIT market layer for the Macro History tab (owner request
2026-09-17: "add this to the macro tab regarding the REIT market and
the summary").

Three exports, same honesty rules as everything else:
  * market_reit_returns       - calendar-year total returns for the
    owner's five REITs (WELL, PLD, EQIX, AMT, DLR), 2017-2025 plus
    2026 YTD, from data/market/reit_return_inputs.csv. All rows are
    PUBLIC_RESEARCH / MEDIUM with click-through URLs; two cells
    (EQIX 2017, DLR 2017) are honestly blank - RESEARCH REQUIRED -
    because no verifiable source was reachable at build time.
  * market_reit_returns_wide  - the same numbers with years as
    columns, one row per company (the owner's preferred browse
    layout).
  * market_reit_summary       - the owner's own era-by-era narrative
    for each company, transcribed from the page she provided.
    Narrative, never data; labeled OWNER-PROVIDED SUMMARY.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MARKET_DIR = ROOT / "data" / "market"
REPORTS = ROOT / "reports"

_YEAR_COLS = {
    "2017": "y2017", "2018": "y2018", "2019": "y2019",
    "2020": "y2020", "2021": "y2021", "2022": "y2022",
    "2023": "y2023", "2024": "y2024", "2025": "y2025",
    "2026 YTD": "y2026_ytd"}


def reit_returns() -> pd.DataFrame:
    r = pd.read_csv(MARKET_DIR / "reit_return_inputs.csv").fillna("")
    r["value_class"] = "PUBLIC_RESEARCH"
    return r


def reit_returns_wide(returns: pd.DataFrame) -> pd.DataFrame:
    out = []
    for ticker in returns["ticker"].unique():
        rows = returns[returns["ticker"] == ticker]
        rec = {"company": rows.iloc[0]["company"], "ticker": ticker}
        for label, col in _YEAR_COLS.items():
            m = rows[rows["year_label"] == label]
            rec[col] = m.iloc[0]["return_pct"] if len(m) else ""
        missing = [label for label, col in _YEAR_COLS.items()
                   if rec[col] == ""]
        rec["note"] = (f"RESEARCH REQUIRED: {', '.join(missing)}"
                       if missing else "")
        out.append(rec)
    df = pd.DataFrame(out)
    df["value_class"] = "PUBLIC_RESEARCH"
    return df


def reit_summary() -> pd.DataFrame:
    s = pd.read_csv(MARKET_DIR / "reit_summary_inputs.csv").fillna("")
    s["value_class"] = "PUBLIC_RESEARCH"
    return s


def cycle_indicators() -> pd.DataFrame:
    """U.S. business-cycle indicator snapshot (leading / coincident /
    lagging) transcribed from the infographic the owner provided
    2026-09-17 - readings as of 2026-09-15 per its footer. Signals
    (EXPANSION / MIXED / CONTRACTION) are the page's own dot colors,
    not this pipeline's judgment."""
    c = pd.read_csv(MARKET_DIR / "cycle_indicator_inputs.csv").fillna("")
    c["value_class"] = "PUBLIC_RESEARCH"
    return c


def build_reit_frames() -> dict[str, pd.DataFrame]:
    returns = reit_returns()
    frames = {
        "market_reit_returns": returns,
        "market_reit_returns_wide": reit_returns_wide(returns),
        "market_reit_summary": reit_summary(),
        "market_cycle_indicators": cycle_indicators(),
    }
    for df in frames.values():
        df.insert(0, "row_id",
                  [f"R{i:03d}" for i in range(1, len(df) + 1)])
    return frames

"""
Build the Page 5 curated export: monthly macro history with rolling
24-month averages.

Usage (from the repo root):
    python src/build_market_history.py

Reads the append-only market_observations layer (latest vintage per
metric/date), derives the curve spread, computes each metric's rolling
24-month mean (blank until a full window exists), and writes
reports/market_history_rolling24.csv for the report to read. Rolling
averages smooth month-to-month noise into regime shape - the cost is
lag: a 24-month mean turns after the raw series does.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from financials.loader import ClientFSValidationError
from financials.market_data import (
    HISTORY_OUTPUT,
    WINDOWS_OUTPUT,
    load_market_data,
    rolling_24m_history,
    windowed_history,
)

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")


def main():
    try:
        tables, _ = load_market_data(strict=True)
    except ClientFSValidationError as exc:
        print()
        print("MARKET LAYER INVALID:")
        print(exc)
        raise SystemExit(1)

    history = rolling_24m_history(tables["market_observations"])
    history.to_csv(HISTORY_OUTPUT, index=False)
    windows = windowed_history(tables["market_observations"])
    windows.to_csv(WINDOWS_OUTPUT, index=False)

    windowed = history.dropna(subset=["cpi_yoy_r24"])
    print()
    print("MARKET HISTORY — rolling 24-month averages")
    print("=" * 64)
    print(f"months            : {len(history)} "
          f"({history['observation_date'].iloc[0]} .. "
          f"{history['observation_date'].iloc[-1]})")
    print(f"with full window  : {len(windowed)} "
          f"(first: {windowed['observation_date'].iloc[0]})")
    print(f"metrics           : {(len(history.columns) - 3) // 2}")
    print(f"written           : {HISTORY_OUTPUT}")
    print(f"windowed rows     : {len(windows)} ({windows['window'].nunique()} windows) -> {WINDOWS_OUTPUT}")


if __name__ == "__main__":
    main()

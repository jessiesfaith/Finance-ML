"""
Fetch REAL monthly closes for the owner's equity watchlist (run this on
YOUR machine - the hosted environment's egress blocks market sites).

Usage (from the repo root):
    python src/fetch_equity_prices.py

Reads the watchlist from data/market/market_metric_master.csv (rows
with preferred_source = STOOQ), downloads each symbol's monthly history
from stooq.com (free, keyless), and writes canonical observation rows
to data/market_staging/equity_observations.csv for YOUR review - the
canonical store is never touched by this script. Real names only ever
carry real data; until this staging file is reviewed and merged at the
live cutover, the report's firm panels stay placeholder-labeled.

After a successful run: inspect the staging file, then tell Claude
"wire the real tickers" and the charts get bound to the fetched series.
"""

import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
MASTER = BASE_DIR / "data" / "market" / "market_metric_master.csv"
STAGING_DIR = BASE_DIR / "data" / "market_staging"
OUTPUT = STAGING_DIR / "equity_observations.csv"
STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=m"

OBSERVATION_COLUMNS = [
    "metric_id", "observation_date", "value", "unit", "source",
    "source_reference", "retrieval_timestamp", "frequency",
    "revision_status",
]


def watchlist():
    master = pd.read_csv(MASTER, dtype=str)
    rows = master[master["preferred_source"] == "STOOQ"]
    return list(zip(rows["metric_id"], rows["source_series_id"]))


def parse_stooq_csv(text, metric_id, symbol, retrieved_at) -> pd.DataFrame:
    """
    Stooq monthly CSV: Date,Open,High,Low,Close[,Volume]. We keep the
    monthly Close; rows with no close stay absent (never filled).
    """
    frame = pd.read_csv(pd.io.common.StringIO(text))
    frame = frame.dropna(subset=["Close"])
    return pd.DataFrame({
        "metric_id": metric_id,
        "observation_date": pd.to_datetime(frame["Date"]).dt.strftime("%Y-%m-%d"),
        "value": frame["Close"].astype(float).round(6),
        "unit": "USD",
        "source": "STOOQ",
        "source_reference": f"stooq.com {symbol} monthly close",
        "retrieval_timestamp": retrieved_at,
        "frequency": "MONTHLY",
        "revision_status": "FINAL",
    }, columns=OBSERVATION_COLUMNS)


def main():
    STAGING_DIR.mkdir(exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    frames, failures = [], []
    symbols = watchlist()
    if not symbols:
        raise SystemExit("no STOOQ watchlist rows in the metric master")
    for metric_id, symbol in symbols:
        url = STOOQ_URL.format(symbol=symbol)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                text = response.read().decode("utf-8")
            rows = parse_stooq_csv(text, metric_id, symbol, retrieved_at)
            frames.append(rows)
            print(f"  {metric_id:<10} {symbol:<9} {len(rows):>4} months "
                  f"({rows['observation_date'].iloc[0]} .. "
                  f"{rows['observation_date'].iloc[-1]})")
        except Exception as exc:                       # noqa: BLE001
            failures.append((metric_id, symbol, str(exc)))
            print(f"  {metric_id:<10} {symbol:<9} FAILED: {exc}")

    if frames:
        staged = pd.concat(frames, ignore_index=True)
        staged.to_csv(OUTPUT, index=False)
        print()
        print(f"staged {len(staged)} REAL observations -> {OUTPUT}")
        print("review the file, then tell Claude: wire the real tickers")
    if failures:
        print(f"{len(failures)} symbol(s) failed - rerun or check the symbol")


if __name__ == "__main__":
    main()

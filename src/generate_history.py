from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
OUT_FILE = BASE_DIR / "data" / "raw" / "macro_history.csv"

np.random.seed(42)

dates = pd.date_range(
    start="2018-01-01",
    end="2026-08-28",
    freq="ME"
)

n = len(dates)

fed_funds = np.clip(
    2.0 + np.cumsum(np.random.normal(0, 0.12, n)),
    0.0,
    6.0
)

cpi = np.clip(
    2.2 + np.random.normal(0, 0.8, n),
    0.0,
    9.0
)

unemployment = np.clip(
    4.5 + np.random.normal(0, 0.9, n),
    2.5,
    12.0
)

treasury_2y = (
    0.75 * fed_funds
    + 0.15 * cpi
    + np.random.normal(0, 0.25, n)
)

treasury_10y = (
    0.55 * treasury_2y
    + 0.20 * cpi
    - 0.08 * unemployment
    + 1.8
    + np.random.normal(0, 0.20, n)
)

df = pd.DataFrame(
    {
        "date": dates,
        "treasury_2y": treasury_2y,
        "treasury_10y": treasury_10y,
        "fed_funds": fed_funds,
        "cpi": cpi,
        "unemployment": unemployment,
    }
)

df["yield_spread_10y_2y"] = (
    df["treasury_10y"] - df["treasury_2y"]
)

df["real_10y_proxy"] = (
    df["treasury_10y"] - df["cpi"]
)

df.to_csv(OUT_FILE, index=False)

print(f"Created: {OUT_FILE}")
print(f"Rows: {len(df)}")
print(df.head())
print(df.tail())
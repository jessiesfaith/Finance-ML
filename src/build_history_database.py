from pathlib import Path
import sqlite3
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]

CSV_FILE = BASE_DIR / "data" / "raw" / "macro_history.csv"
DB_FILE = BASE_DIR / "data" / "finance_ml.db"

df = pd.read_csv(CSV_FILE, parse_dates=["date"])

conn = sqlite3.connect(DB_FILE)

df.to_sql(
    "macro_history",
    conn,
    if_exists="replace",
    index=False
)

conn.close()

print(f"Loaded {len(df)} rows into macro_history")
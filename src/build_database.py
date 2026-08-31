from pathlib import Path
import sqlite3
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]

CSV_FILE = BASE_DIR / "data" / "raw" / "macro_sample.csv"
DB_FILE = BASE_DIR / "data" / "finance_ml.db"

def build_database():
    df = pd.read_csv(CSV_FILE, parse_dates=["date"])

    conn = sqlite3.connect(DB_FILE)

    df.to_sql(
        "macro_data",
        conn,
        if_exists="replace",
        index=False
    )

    conn.close()

    print(f"Database created: {DB_FILE}")
    print(f"Rows loaded: {len(df)}")

if __name__ == "__main__":
    build_database()

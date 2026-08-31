from pathlib import Path
import sqlite3
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]

DB_FILE = BASE_DIR / "data" / "finance_ml.db"
SQL_FILE = BASE_DIR / "sql" / "macro.sql"

def run_query():
    # Read the SQL query
    query = SQL_FILE.read_text(encoding="utf-8")

    # Connect Python to our SQLite database
    conn = sqlite3.connect(DB_FILE)

    # Execute the SQL and return the results to Python
    df = pd.read_sql_query(query, conn)

    conn.close()

    return df


if __name__ == "__main__":
    results = run_query()
    print(results)
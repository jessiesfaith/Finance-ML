from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = BASE_DIR / "data" / "raw" / "macro_sample.csv"

def load_macro_data():
    df = pd.read_csv(DATA_FILE, parse_dates=["date"])

    df["yield_spread_10y_2y"] = (
        df["treasury_10y"] - df["treasury_2y"]
    )

    df["real_10y_proxy"] = (
        df["treasury_10y"] - df["cpi"]
    )

    return df

if __name__ == "__main__":
    data = load_macro_data()
    print(data)
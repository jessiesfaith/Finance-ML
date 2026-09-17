"""Build the REIT market exports (reports/market_reit_*.csv).

Run after editing anything in data/market/reit_*.csv:

    python src/build_reit.py
"""

from financials.reit import REPORTS, build_reit_frames


def main() -> None:
    frames = build_reit_frames()
    for name, df in frames.items():
        out = REPORTS / f"{name}.csv"
        df.to_csv(out, index=False)
        print(f"{name}.csv  {len(df)} rows x {len(df.columns)} cols")


if __name__ == "__main__":
    main()

"""Build the NFP module's curated exports (reports/nfp_*.csv).

Run after editing anything in data/nfp/:

    python src/build_nfp.py
"""

from financials.nfp import REPORTS, build_all


def main() -> None:
    frames = build_all()
    for name, df in frames.items():
        out = REPORTS / f"{name}.csv"
        df.to_csv(out, index=False)
        print(f"{name}.csv  {len(df)} rows x {len(df.columns)} cols")


if __name__ == "__main__":
    main()

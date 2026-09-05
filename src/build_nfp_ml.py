"""Build the NFP ML layer (§55) — SEPARATE from the deterministic build.

    python src/build_nfp_ml.py                  # fit models, write exports
    python src/build_nfp_ml.py --regen-history  # also regenerate the
                                                # SYNTHETIC training history
                                                # (seed 777, byte-identical)

The deterministic nfp_* exports are never touched by this builder —
that separation is §54 and a test enforces it.
"""

import sys

from financials.nfp_ml import (
    NFP_DIR, REPORTS, build_ml, load_history, regen_history,
)


def main() -> None:
    if "--regen-history" in sys.argv:
        for name, df in regen_history().items():
            df.to_csv(NFP_DIR / f"{name}.csv", index=False)
            print(f"history {name}.csv  {len(df)} rows")
    else:
        load_history()          # fail fast if the history is missing
    for name, df in build_ml().items():
        df.to_csv(REPORTS / f"{name}.csv", index=False)
        print(f"{name}.csv  {len(df)} rows x {len(df.columns)} cols")


if __name__ == "__main__":
    main()

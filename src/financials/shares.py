"""
Diluted shares — Phase 6 (spec section 18).

    Diluted = Basic
            + incremental option shares (treasury-stock method)
            + RSUs / PSUs
            + convertible shares
            + other dilutive securities
    (anti-dilutive instruments excluded, reported, never mixed in)

TREASURY-STOCK METHOD: exercising N in-the-money options raises
N × strike of cash, which the company notionally uses to buy back
N × strike ÷ price shares — so the net new shares are:

    incremental = N × (1 − strike ÷ price)

An option with strike ≥ price is anti-dilutive: exercising it would
shrink the count, so it is EXCLUDED (and disclosed), never allowed to
reduce dilution.

Inputs live in data/client_fs/shares_dilution.csv (basic count, option
tranches, RSUs, converts, market price — each with a source reference);
the engine recomputes the calculated columns and REFUSES a file whose
stated diluted count disagrees with its own inputs.
"""

from pathlib import Path

import pandas as pd

from financials.schemas import SHARES_DILUTION

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SHARES_FILE = BASE_DIR / "data" / "client_fs" / SHARES_DILUTION.filename

TOLERANCE = 0.0001


def treasury_stock_method(options_m: float, strike: float,
                          price: float) -> float:
    """Incremental shares from in-the-money options; 0 if anti-dilutive."""
    if price <= 0:
        raise ValueError("market price must be positive")
    if options_m < 0 or strike < 0:
        raise ValueError("options and strike must be non-negative")
    if strike >= price:
        return 0.0   # anti-dilutive: exclude, never subtract
    return options_m * (1 - strike / price)


def compute_dilution(row) -> dict:
    """Recompute the calculated columns from one input row."""
    incremental = treasury_stock_method(
        row["options_outstanding_m"], row["weighted_avg_strike"],
        row["market_price"],
    )
    diluted = (
        row["basic_shares_m"] + incremental + row["rsus_psus_m"]
        + row["convertible_shares_m"] + row["other_dilutive_shares_m"]
    )
    return {
        "incremental_option_shares_m": round(incremental, 4),
        "diluted_shares_m": round(diluted, 4),
    }


def load_shares_dilution(path=None) -> pd.DataFrame:
    """
    Read shares_dilution.csv, recompute its calculated columns, and fail
    loudly if the committed values disagree with the inputs — a share
    count that doesn't reproduce from its own components is not usable.
    """
    path = Path(path) if path else DEFAULT_SHARES_FILE
    frame = pd.read_csv(path)

    for i, row in frame.iterrows():
        computed = compute_dilution(row)
        for column, value in computed.items():
            stated = row[column]
            if abs(stated - value) > TOLERANCE:
                raise ValueError(
                    f"shares_dilution.csv row {i + 2}: {column} is "
                    f"{stated} but the inputs produce {value} — fix the "
                    "inputs or the stated value; the engine never "
                    "silently reconciles them"
                )
    return frame

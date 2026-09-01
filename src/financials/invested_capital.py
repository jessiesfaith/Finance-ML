"""
Invested capital + ROIC — Phase 6 (spec section 16).

Operating approach, built from components — never a single entered value:

    Invested capital = Operating NWC
                     + Net PP&E
                     + other ROIC-classified operating assets
                     − other ROIC-classified operating liabilities
                       (where not already inside NWC)

Membership comes from `account_mapping.roic_classification`
(ROIC_OPERATING_ASSET / ROIC_OPERATING_LIABILITY). In the fixture the
NWC accounts carry both classifications, so invested capital resolves to
Operating NWC + Net PP&E — visible, decomposable, and different by
construction from the old hand-entered $1.5B.

    ROIC = NOPAT ÷ Invested capital

The denominator basis is CONFIGURABLE (spec: transaction/project ROIC
may differ from company-level ROIC): "ENDING" uses period-end invested
capital (default — simplest to trace on a balance sheet), "AVERAGE" uses
the mean of beginning and ending where a prior period exists.
"""

import pandas as pd


def invested_capital_components(consolidated: pd.DataFrame,
                                account_mapping: pd.DataFrame) -> pd.DataFrame:
    """One row per period: operating assets, operating liabilities, IC."""
    rows_map = account_mapping[account_mapping["roic_classification"] != ""]
    classes = {}
    for row in rows_map.itertuples():
        existing = classes.get(row.standard_account_id)
        if existing not in (None, row.roic_classification):
            raise ValueError(
                f"conflicting roic_classification for "
                f"'{row.standard_account_id}': {existing} vs "
                f"{row.roic_classification} — fix account_mapping.csv"
            )
        classes[row.standard_account_id] = row.roic_classification

    bs = consolidated[consolidated["statement_type"] == "BS"]
    rows = []
    for period in sorted(bs["period_id"].unique()):
        per = bs[bs["period_id"] == period]
        assets = liabilities = 0.0
        for row in per.itertuples():
            cls = classes.get(row.standard_account_id, "")
            if cls == "ROIC_OPERATING_ASSET":
                assets += row.consolidated_amount
            elif cls == "ROIC_OPERATING_LIABILITY":
                liabilities += row.consolidated_amount
        rows.append({
            "period_id": period,
            "operating_assets": assets,
            "operating_liabilities": liabilities,
            "invested_capital": assets - liabilities,
        })
    return pd.DataFrame(rows)


def roic(ic_frame: pd.DataFrame, nopat_by_period: dict,
         basis: str = "ENDING") -> pd.DataFrame:
    """ROIC per period on the chosen denominator basis."""
    if basis not in ("ENDING", "AVERAGE"):
        raise ValueError(f"unknown ROIC basis {basis!r}: ENDING or AVERAGE")

    frame = ic_frame.copy().reset_index(drop=True)
    denominators = []
    for i, row in frame.iterrows():
        ending = row["invested_capital"]
        if basis == "AVERAGE" and i > 0:
            denominators.append(
                (frame.loc[i - 1, "invested_capital"] + ending) / 2
            )
        else:
            denominators.append(ending)
    frame["roic_basis"] = basis
    frame["nopat"] = [nopat_by_period.get(p) for p in frame["period_id"]]
    frame["roic_denominator"] = denominators
    frame["roic_pct"] = [
        round(n / d * 100, 4) if n is not None and d else None
        for n, d in zip(frame["nopat"], denominators)
    ]
    return frame

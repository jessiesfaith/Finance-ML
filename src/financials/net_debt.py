"""
Net debt — Phase 6 (spec section 17).

    Short-term debt + Long-term debt (+ finance leases where elected)
    − Cash & equivalents
    = Net debt

Membership is an explicit election in `account_mapping.netdebt_
classification` (DEBT / CASH_AND_EQUIVALENTS / RESTRICTED_CASH /
EXCLUDED) — **not every liability is debt**, and nothing here keys off an
account's name. Restricted cash is reported as its own component and
deliberately NOT netted by default (its treatment is an analyst election,
documented per engagement).

Bridges enterprise value to equity value on Page 3.
"""

import pandas as pd

NET_DEBT_COLUMNS = [
    "short_term_debt", "long_term_debt", "finance_leases", "total_debt",
    "cash_and_equivalents", "restricted_cash", "net_debt",
]


def netdebt_classification_map(account_mapping: pd.DataFrame) -> dict:
    rows = account_mapping[account_mapping["netdebt_classification"] != ""]
    out = {}
    for row in rows.itertuples():
        existing = out.get(row.standard_account_id)
        if existing not in (None, row.netdebt_classification):
            raise ValueError(
                f"conflicting netdebt_classification for "
                f"'{row.standard_account_id}': {existing} vs "
                f"{row.netdebt_classification} — fix account_mapping.csv"
            )
        out[row.standard_account_id] = row.netdebt_classification
    return out


def net_debt_components(consolidated: pd.DataFrame,
                        account_mapping: pd.DataFrame) -> pd.DataFrame:
    """One row per period: the transparent net-debt build (magnitudes)."""
    classes = netdebt_classification_map(account_mapping)
    bs = consolidated[consolidated["statement_type"] == "BS"]

    rows = []
    for period in sorted(bs["period_id"].unique()):
        per = bs[bs["period_id"] == period]
        buckets = {c: 0.0 for c in NET_DEBT_COLUMNS}
        for row in per.itertuples():
            cls = classes.get(row.standard_account_id, "")
            if cls == "DEBT":
                if row.statement_section == "current_liabilities":
                    buckets["short_term_debt"] += row.consolidated_amount
                else:
                    buckets["long_term_debt"] += row.consolidated_amount
            elif cls == "CASH_AND_EQUIVALENTS":
                buckets["cash_and_equivalents"] += row.consolidated_amount
            elif cls == "RESTRICTED_CASH":
                buckets["restricted_cash"] += row.consolidated_amount

        buckets["total_debt"] = (
            buckets["short_term_debt"] + buckets["long_term_debt"]
            + buckets["finance_leases"]
        )
        buckets["net_debt"] = (
            buckets["total_debt"] - buckets["cash_and_equivalents"]
        )
        rows.append({"period_id": period, **buckets})
    return pd.DataFrame(rows)

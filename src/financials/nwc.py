"""
Net working capital — Phase 5 (spec section 13).

Operating NWC =
      Accounts Receivable
    + Inventory
    + Other Operating Current Assets
    − Accounts Payable
    − Other Operating Current Liabilities

Cash, debt, and short-term borrowings are NEVER included by default:
membership comes only from `account_mapping.nwc_classification`
(NWC_OPERATING_ASSET / NWC_OPERATING_LIABILITY; cash and debt carry
EXCLUDED). Nothing about NWC is hard-coded to an account name.

All intermediates are kept transparent: the components frame carries the
five buckets per period, so every NWC number decomposes on sight.

An INCREASE in NWC is a use of cash: delta_nwc enters UFCF with a minus.
"""

import pandas as pd

# statement-level named buckets; any other classified operating item
# falls into the "other" columns, so new accounts need no code change.
_ASSET_BUCKETS = {"accounts_receivable": "accounts_receivable",
                  "inventory": "inventory"}
_LIABILITY_BUCKETS = {"accounts_payable": "accounts_payable"}

COMPONENT_COLUMNS = [
    "accounts_receivable", "inventory", "other_operating_current_assets",
    "accounts_payable", "other_operating_current_liabilities",
    "operating_nwc",
]


def nwc_classification_map(account_mapping: pd.DataFrame) -> dict:
    """standard_account_id -> NWC classification (from the mapping layer)."""
    rows = account_mapping[account_mapping["nwc_classification"] != ""]
    out = {}
    for row in rows.itertuples():
        existing = out.get(row.standard_account_id)
        if existing not in (None, row.nwc_classification):
            raise ValueError(
                f"conflicting nwc_classification for standard account "
                f"'{row.standard_account_id}': {existing} vs "
                f"{row.nwc_classification} — fix account_mapping.csv"
            )
        out[row.standard_account_id] = row.nwc_classification
    return out


def nwc_components(consolidated: pd.DataFrame,
                   account_mapping: pd.DataFrame) -> pd.DataFrame:
    """
    One row per period with the five NWC buckets and their total, from
    consolidated balance-sheet amounts (positive magnitudes).
    """
    classes = nwc_classification_map(account_mapping)
    bs = consolidated[consolidated["statement_type"] == "BS"]

    periods = sorted(bs["period_id"].unique())
    rows = []
    for period in periods:
        per = bs[bs["period_id"] == period]
        buckets = {c: 0.0 for c in COMPONENT_COLUMNS}
        for row in per.itertuples():
            cls = classes.get(row.standard_account_id, "")
            amount = row.consolidated_amount
            if cls == "NWC_OPERATING_ASSET":
                col = _ASSET_BUCKETS.get(
                    row.standard_account_id, "other_operating_current_assets"
                )
                buckets[col] += amount
            elif cls == "NWC_OPERATING_LIABILITY":
                col = _LIABILITY_BUCKETS.get(
                    row.standard_account_id,
                    "other_operating_current_liabilities",
                )
                buckets[col] += amount

        buckets["operating_nwc"] = (
            buckets["accounts_receivable"]
            + buckets["inventory"]
            + buckets["other_operating_current_assets"]
            - buckets["accounts_payable"]
            - buckets["other_operating_current_liabilities"]
        )
        rows.append({"period_id": period, **buckets})

    frame = pd.DataFrame(rows)
    # Delta vs the prior period on file (periods are sorted annual ids).
    frame["delta_nwc"] = frame["operating_nwc"].diff()
    return frame

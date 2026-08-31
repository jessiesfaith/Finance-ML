"""
Entity consolidation — Phase 3.

Rolls translated entity statements up into consolidated financial
statements with an explicit elimination layer:

    translated entity amounts        (pre_elimination_amount)
  + intercompany eliminations        (rows on elimination entities)
  + other consolidation adjustments  (none yet — future phases)
  + FX translation adjustment        (the engine's CTA rows)
  = consolidated_amount

Subsidiaries are never simply added together: elimination entities carry
the intercompany reversals (IC revenue/COGS, IC AR/AP in the fixture), and
they land in their own column so a reviewer sees exactly what was removed.

Output: data/client_fs/entity_consolidation.csv — one row per standard
account and period, entity_id = "CONSOLIDATED" (per-entity detail lives in
the translated layer; see DECISIONS.md #27). control_status stays PENDING
until the Phase 4 control engine populates it.
"""

import logging
from pathlib import Path

import pandas as pd

from financials.schemas import ENTITY_CONSOLIDATION

log = logging.getLogger("financials.consolidation")

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = BASE_DIR / "data" / "client_fs" / ENTITY_CONSOLIDATION.filename

CONSOLIDATED_ENTITY_ID = "CONSOLIDATED"


def consolidate(translated: pd.DataFrame, entity_master: pd.DataFrame) -> pd.DataFrame:
    """Build the consolidated view from the FX-translated entity rows."""
    elim_entities = set(
        entity_master.loc[
            entity_master["elimination_entity_flag"] == "Y", "entity_id"
        ]
    )

    is_cta = translated["origin"] == "FX_ENGINE"
    is_elim = translated["entity_id"].isin(elim_entities)

    group_cols = [
        "company_id", "period_id", "standard_account_id",
        "standard_account_name", "statement_type", "statement_section",
        "reporting_currency", "scenario",
    ]

    def _sum(mask, name):
        subset = translated[mask]
        if subset.empty:
            return pd.DataFrame(columns=group_cols + [name])
        return (
            subset.groupby(group_cols, as_index=False)["calculated_reporting_amount"]
            .sum()
            .rename(columns={"calculated_reporting_amount": name})
        )

    pre = _sum(~is_elim & ~is_cta, "pre_elimination_amount")
    elim = _sum(is_elim & ~is_cta, "intercompany_elimination")
    fx = _sum(is_cta, "fx_translation_adjustment")

    combined = (
        pre.merge(elim, on=group_cols, how="outer")
        .merge(fx, on=group_cols, how="outer")
        .fillna({
            "pre_elimination_amount": 0.0,
            "intercompany_elimination": 0.0,
            "fx_translation_adjustment": 0.0,
        })
    )
    combined["other_consolidation_adjustment"] = 0.0
    combined["consolidated_amount"] = (
        combined["pre_elimination_amount"]
        + combined["intercompany_elimination"]
        + combined["other_consolidation_adjustment"]
        + combined["fx_translation_adjustment"]
    )

    combined["entity_id"] = CONSOLIDATED_ENTITY_ID
    combined["control_status"] = "PENDING"   # Phase 4 populates
    combined["control_variance"] = ""

    combined = (
        combined[ENTITY_CONSOLIDATION.column_names()]
        .sort_values(
            ["company_id", "period_id", "statement_type",
             "statement_section", "standard_account_id"]
        )
        .reset_index(drop=True)
    )

    log.info(
        "consolidated %d account/period rows (%d with eliminations, %d with FX adj)",
        len(combined),
        int((combined["intercompany_elimination"] != 0).sum()),
        int((combined["fx_translation_adjustment"] != 0).sum()),
    )
    return combined


def write_consolidation(consolidated: pd.DataFrame, path=None) -> Path:
    path = Path(path) if path else DEFAULT_OUTPUT
    consolidated.to_csv(path, index=False)
    log.info("wrote %s", path)
    return path


# ------------------------------------------------
# VERIFICATION HELPERS (formalized by Phase 4 controls)
# ------------------------------------------------

def consolidated_net_income(consolidated: pd.DataFrame) -> pd.Series:
    is_rows = consolidated[consolidated["statement_type"] == "IS"]
    return is_rows.groupby("period_id")["consolidated_amount"].sum()


def consolidated_balance_gap(consolidated: pd.DataFrame) -> pd.Series:
    bs = consolidated[consolidated["statement_type"] == "BS"]
    sign = bs["statement_section"].map({
        "current_assets": 1, "noncurrent_assets": 1,
        "current_liabilities": -1, "noncurrent_liabilities": -1,
        "equity": -1,
    })
    return (bs["consolidated_amount"] * sign).groupby(bs["period_id"]).sum()

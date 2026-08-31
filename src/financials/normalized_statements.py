"""
Normalized financial statements (REPORTED view) — Phase 2.

Turns validated raw statements into client_fs_normalized.csv:

    client_fs_raw
        → account mapping        (source account → standard account)
        → sign normalization     (source presentation → canonical signs)
        → client_fs_normalized   (REPORTED rows)

Properties this layer guarantees:
  * Raw source data is never modified — this is a new, derived table.
  * One normalized row per raw row (no aggregation), so every number still
    traces to its source file / sheet / row via source_system +
    source_account_code + load_id back into client_fs_raw.
  * The sign transformation is auditable on every row:
    amount_source (as presented) → sign_multiplier + source_sign_convention
    (the rule) → amount_reporting (canonical).
  * In the canonical convention, subtotals are sums: income-statement rows
    sum to net income, cash-flow rows sum to the change in cash. The
    builder's verification helpers (and tests) prove it on every build.

Until the Phase 3 FX engine lands, amount_reporting derives from the
source-reported reporting-currency amount (DECISIONS.md #19/#22). Phase 8
adds ADJUSTED rows alongside these REPORTED ones; the include_* flags
default to YES until adjustments exist.
"""

import logging
from pathlib import Path

import pandas as pd

from financials.account_mapper import resolve_mapping
from financials.loader import ClientFSValidationError
from financials.schemas import CLIENT_FS_NORMALIZED
from financials.sign_normalizer import normalize_sign

log = logging.getLogger("financials.normalized")

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = BASE_DIR / "data" / "client_fs" / CLIENT_FS_NORMALIZED.filename


def build_normalized_statements(tables) -> pd.DataFrame:
    """
    Build the REPORTED normalized layer from loaded client-FS tables
    (the dict a strict load_client_fs() returns in result.tables).
    """
    mapped, issues = resolve_mapping(
        tables["client_fs_raw"], tables["account_mapping"]
    )
    if issues:
        raise ClientFSValidationError(issues)

    # Apply the sign policy row by row through the ONE policy function —
    # at this scale, keeping a single implementation of the rule beats
    # vectorizing a second copy of it.
    canonical_amounts = [
        normalize_sign(amount, int(multiplier), convention)
        for amount, multiplier, convention in zip(
            mapped["amount_reporting"],
            mapped["sign_multiplier"],
            mapped["source_sign_convention"],
        )
    ]

    normalized = pd.DataFrame({
        "company_id": mapped["company_id"],
        "entity_id": mapped["entity_id"],
        "period_id": mapped["period_id"],
        "statement_type": mapped["statement_type"],
        "standard_account_id": mapped["standard_account_id"],
        "standard_account_name": mapped["standard_account_name"],
        "statement_section": mapped["statement_section"],
        "amount_reporting": canonical_amounts,
        "reporting_currency": mapped["reporting_currency"],
        "scenario": mapped["scenario"],
        "reported_or_adjusted": "REPORTED",
        "adjustment_id": "",
        "transaction_id": "",
        "include_in_normalized": "YES",
        "include_in_proforma": "YES",
        "source_system": mapped["source_system"],
        "source_account_code": mapped["source_account_code"],
        "amount_source": mapped["amount_reporting"],
        "sign_multiplier": mapped["sign_multiplier"].astype(int),
        "source_sign_convention": mapped["source_sign_convention"],
        "load_id": mapped["load_id"],
    })

    # Column order comes from the schema registry — one source of truth.
    normalized = normalized[CLIENT_FS_NORMALIZED.column_names()]

    log.info(
        "built %d normalized REPORTED rows from %d raw rows",
        len(normalized), len(mapped),
    )
    return normalized


def write_normalized_statements(normalized: pd.DataFrame, path=None) -> Path:
    path = Path(path) if path else DEFAULT_OUTPUT
    normalized.to_csv(path, index=False)
    log.info("wrote %s", path)
    return path


# ------------------------------------------------
# VERIFICATION HELPERS
# In the canonical sign convention these identities are pure sums.
# The Phase 4 control engine formalizes them; tests use them today.
# ------------------------------------------------

def net_income_by_entity_period(normalized: pd.DataFrame) -> pd.Series:
    """IS rows sum straight to net income under canonical signs."""
    is_rows = normalized[normalized["statement_type"] == "IS"]
    return is_rows.groupby(["entity_id", "period_id"])["amount_reporting"].sum()


BS_SECTION_SIGNS = {
    "current_assets": 1,
    "noncurrent_assets": 1,
    "current_liabilities": -1,
    "noncurrent_liabilities": -1,
    "equity": -1,
}


def balance_sheet_gap(normalized: pd.DataFrame) -> pd.Series:
    """Assets − Liabilities − Equity per entity/period (0 when it balances)."""
    bs = normalized[normalized["statement_type"] == "BS"]
    section_sign = bs["statement_section"].map(BS_SECTION_SIGNS)

    # A BS row whose section is not in the map would silently vanish from
    # the identity (NaN × amount, skipna sum) — fail loudly instead.
    unknown = sorted(bs.loc[section_sign.isna(), "statement_section"].unique())
    if unknown:
        raise ValueError(
            f"unknown balance-sheet statement_section(s) {unknown} — add "
            "them to BS_SECTION_SIGNS so the identity stays complete"
        )

    gap = bs["amount_reporting"] * section_sign
    return gap.groupby([bs["entity_id"], bs["period_id"]]).sum()


def cash_flow_by_entity_period(normalized: pd.DataFrame) -> pd.Series:
    """CFS rows sum straight to the period's change in cash."""
    cfs = normalized[normalized["statement_type"] == "CFS"]
    return cfs.groupby(["entity_id", "period_id"])["amount_reporting"].sum()

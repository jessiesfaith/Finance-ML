"""
Account mapping resolution — Phase 2.

Attaches each raw financial-statement row to its account_mapping row, so
every source account ("Umsatzerloese", "Trade Receivables", …) resolves to
one standardized concept (revenue, accounts_receivable, …).

Resolution rules (docs/SCHEMAS.md, DECISIONS.md #16):
  * An account is identified by (source_system, source_account_code) —
    never by code alone, because ERP systems reuse codes.
  * A COMPANY-SPECIFIC mapping row (company_id filled) beats a REUSABLE
    DEFAULT row (company_id blank) for that company.
  * A raw row with no applicable mapping is an ERROR — the pipeline never
    guesses (the loader already enforces this; the mapper re-guards it).
  * The mapping's statement_type must agree with the raw row's: a mismatch
    means either the mapping or the source data is wrong, and that is an
    ERROR, not something to reconcile silently.
"""

import pandas as pd

from financials.validator import Issue, _rows

# Everything a mapping row knows about an account, carried onto the raw
# rows. statement_type is renamed so it can be compared against the raw
# row's own statement_type instead of colliding with it.
MAPPING_ATTRIBUTES = (
    "standard_account_id",
    "standard_account_name",
    "statement_type",
    "statement_section",
    "normal_balance",
    "sign_multiplier",
    "source_sign_convention",
    "operating_classification",
    "nwc_classification",
    "ufcf_classification",
    "roic_classification",
    "share_classification",
    "cash_flow_classification",
    "oci_classification",
    "review_status",
)

_RENAMES = {"statement_type": "mapping_statement_type"}
ATTRIBUTE_COLUMNS = tuple(_RENAMES.get(a, a) for a in MAPPING_ATTRIBUTES)

KEY_SPECIFIC = ["company_id", "source_system", "source_account_code"]
KEY_DEFAULT = ["source_system", "source_account_code"]


def _attribute_frame(mapping_rows, key):
    return (
        mapping_rows[key + list(MAPPING_ATTRIBUTES)]
        .rename(columns=_RENAMES)
    )


def resolve_mapping(raw: pd.DataFrame, mapping: pd.DataFrame):
    """
    Return (mapped, issues): a copy of `raw` with the account's mapping
    attributes attached, and any resolution problems as Issue records.
    """
    issues = []

    specific = mapping[mapping["company_id"] != ""]
    default = mapping[mapping["company_id"] == ""]

    # Left-merges against unique mapping keys keep raw's row count and
    # order, so the two candidate resolutions line up row-for-row.
    by_company = raw.merge(
        _attribute_frame(specific, KEY_SPECIFIC), on=KEY_SPECIFIC, how="left"
    )
    by_default = raw.merge(
        _attribute_frame(default, KEY_DEFAULT), on=KEY_DEFAULT, how="left"
    )

    use_specific = by_company["standard_account_id"].notna()

    mapped = raw.copy()
    for col in ATTRIBUTE_COLUMNS:
        mapped[col] = by_default[col].where(~use_specific, by_company[col])

    unmapped = mapped.index[mapped["standard_account_id"].isna()].tolist()
    if unmapped:
        pairs = sorted(set(
            f"{mapped.loc[i, 'source_system']}/{mapped.loc[i, 'source_account_code']}"
            for i in unmapped
        ))
        issues.append(Issue(
            "ERROR", "client_fs_raw", "unmapped_account",
            f"account(s) {pairs} have no applicable account_mapping row — "
            f"{_rows(unmapped)}",
        ))

    mismatched = mapped.index[
        mapped["standard_account_id"].notna()
        & (mapped["statement_type"] != mapped["mapping_statement_type"])
    ].tolist()
    if mismatched:
        details = sorted(set(
            f"{mapped.loc[i, 'source_system']}/{mapped.loc[i, 'source_account_code']} "
            f"(raw={mapped.loc[i, 'statement_type']}, "
            f"mapping={mapped.loc[i, 'mapping_statement_type']})"
            for i in mismatched
        ))
        issues.append(Issue(
            "ERROR", "client_fs_raw", "statement_type_mismatch",
            f"raw statement_type disagrees with account_mapping for "
            f"{details} — {_rows(mismatched)}; fix the mapping or the "
            "source data, do not reconcile silently",
        ))

    return mapped, issues

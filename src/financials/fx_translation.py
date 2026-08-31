"""
FX translation engine — Phase 3.

Translates each entity's statements into the company's reporting currency
using the CORRECT rate type per financial-statement item (current-rate
method), instead of trusting whatever translation the source file carried:

    income statement / cash flow / OCI / equity movements  →  AVERAGE rate
    balance sheet (except equity)                          →  CLOSING rate
    common stock                                           →  HISTORICAL rate
    retained earnings                                      →  ROLL-FORWARD
    CTA (cumulative translation adjustment)                →  the plug

Retained earnings cannot be translated at one rate: each year's income was
earned at that year's average rate. So RE is rolled forward in reporting
currency: beginning RE (historical rate for the earliest period on file,
derived as ending RE − net income − dividends in local currency) plus each
period's net income and dividends at that period's average rate.

Translating different items at different rates makes the balance sheet
stop balancing — deliberately. The gap IS the cumulative translation
adjustment (CTA), an equity line this engine emits as its own row
(origin FX_ENGINE, standard account cta_aoci). That is the translation
effect the spec requires OCI/AOCI to be able to capture.

Per DECISIONS.md #19/#22/#25: amount_local is authoritative; the
source-reported reporting amount is kept alongside as
source_reported_canonical and the difference is reported per row as
fx_translation_variance — never silently overwritten.
"""

import logging

import pandas as pd

from financials.account_mapper import resolve_mapping
from financials.loader import ClientFSValidationError
from financials.sign_normalizer import normalize_sign
from financials.validator import Issue

log = logging.getLogger("financials.fx")

# The engine's synthetic equity line for translation effects.
CTA_ACCOUNT_ID = "cta_aoci"
CTA_ACCOUNT_NAME = "Cumulative Translation Adjustment (AOCI)"

TRANSLATED_COLUMNS = [
    "company_id",
    "entity_id",
    "period_id",
    "statement_type",
    "standard_account_id",
    "standard_account_name",
    "statement_section",
    "scenario",
    "local_currency",
    "amount_local_canonical",
    "rate_type_applied",
    "fx_rate_applied",
    "calculated_reporting_amount",
    "source_reported_canonical",
    "fx_translation_variance",
    "reporting_currency",
    "origin",             # SOURCE = translated source row, FX_ENGINE = CTA plug
]


def rate_type_for(statement_type, standard_account_id):
    """The translation methodology, one account at a time."""
    if standard_account_id == "common_stock":
        return "HISTORICAL"
    if standard_account_id == "retained_earnings":
        return "ROLLFORWARD"
    if statement_type == "BS":
        return "CLOSING"
    # IS, CFS, OCI and equity-statement movements happen through the
    # period, so they translate at the average rate.
    return "AVERAGE"


def _rate_lookup(fx_rates):
    return {
        (r.period_id, r.from_currency, r.to_currency, r.rate_type): r.fx_rate
        for r in fx_rates.itertuples()
    }


def translate_statements(tables) -> pd.DataFrame:
    """
    Return one row per raw row (plus one CTA row per foreign entity and
    period) with the deterministic translation applied.
    """
    mapped, issues = resolve_mapping(
        tables["client_fs_raw"], tables["account_mapping"]
    )
    if issues:
        raise ClientFSValidationError(issues)

    df = mapped.copy()
    df["amount_local_canonical"] = [
        normalize_sign(a, int(m), c)
        for a, m, c in zip(
            df["amount_local"], df["sign_multiplier"], df["source_sign_convention"]
        )
    ]
    df["source_reported_canonical"] = [
        normalize_sign(a, int(m), c)
        for a, m, c in zip(
            df["amount_reporting"], df["sign_multiplier"], df["source_sign_convention"]
        )
    ]

    rates = _rate_lookup(tables["fx_rates"])
    year_of = dict(zip(
        tables["period_master"]["period_id"],
        tables["period_master"]["fiscal_year"],
    ))

    issues = []
    df["rate_type_applied"] = [
        "NONE" if row.local_currency == row.reporting_currency
        else rate_type_for(row.statement_type, row.standard_account_id)
        for row in df.itertuples()
    ]

    def rate_for(row):
        if row.rate_type_applied in ("NONE", "ROLLFORWARD"):
            return 1.0
        key = (row.period_id, row.local_currency,
               row.reporting_currency, row.rate_type_applied)
        if key not in rates:
            issues.append(Issue(
                "ERROR", "fx_rates", "missing_fx_rate",
                f"no {row.rate_type_applied} rate for "
                f"{row.local_currency}->{row.reporting_currency} in {row.period_id}",
            ))
            return float("nan")
        return rates[key]

    df["fx_rate_applied"] = [rate_for(row) for row in df.itertuples()]
    if issues:
        raise ClientFSValidationError(issues)

    df["calculated_reporting_amount"] = (
        df["amount_local_canonical"] * df["fx_rate_applied"]
    )

    # ------------------------------------------------
    # RETAINED EARNINGS ROLL-FORWARD (foreign entities)
    # ------------------------------------------------
    foreign = df["local_currency"] != df["reporting_currency"]

    for entity_id in sorted(df.loc[foreign, "entity_id"].unique()):
        ent = df[df["entity_id"] == entity_id]
        periods = sorted(
            ent["period_id"].unique(), key=lambda p: year_of.get(p, 0)
        )
        re_translated = None
        for i, period in enumerate(periods):
            per = ent[ent["period_id"] == period]
            re_mask = (
                (df["entity_id"] == entity_id)
                & (df["period_id"] == period)
                & (df["standard_account_id"] == "retained_earnings")
            )
            if not re_mask.any():
                continue

            re_end_local = df.loc[re_mask, "amount_local_canonical"].iloc[0]
            ni_local = per.loc[
                per["statement_type"] == "IS", "amount_local_canonical"
            ].sum()
            dividends_local = per.loc[
                per["standard_account_id"] == "cfs_dividends_paid",
                "amount_local_canonical",
            ].sum()
            avg = rates[(
                period,
                per["local_currency"].iloc[0],
                per["reporting_currency"].iloc[0],
                "AVERAGE",
            )]

            if i == 0:
                # Earliest period on file: derive beginning RE in local
                # currency and translate it at the HISTORICAL rate.
                hist = rates[(
                    period,
                    per["local_currency"].iloc[0],
                    per["reporting_currency"].iloc[0],
                    "HISTORICAL",
                )]
                re_begin_local = re_end_local - ni_local - dividends_local
                re_translated = re_begin_local * hist
            re_translated += (ni_local + dividends_local) * avg

            df.loc[re_mask, "calculated_reporting_amount"] = re_translated
            df.loc[re_mask, "fx_rate_applied"] = float("nan")

    df["fx_translation_variance"] = (
        df["source_reported_canonical"] - df["calculated_reporting_amount"]
    )
    df["origin"] = "SOURCE"

    # ------------------------------------------------
    # CTA — the equity plug that makes translated books balance
    # ------------------------------------------------
    cta_rows = []
    bs = df[(df["statement_type"] == "BS") & foreign]
    section_sign = {
        "current_assets": 1, "noncurrent_assets": 1,
        "current_liabilities": -1, "noncurrent_liabilities": -1,
        "equity": -1,
    }
    for (entity_id, period), group in bs.groupby(["entity_id", "period_id"]):
        gap = sum(
            row.calculated_reporting_amount * section_sign[row.statement_section]
            for row in group.itertuples()
        )
        cta = gap  # assets − liabilities − equity-so-far = missing equity line
        first = group.iloc[0]
        cta_rows.append({
            "company_id": first["company_id"],
            "entity_id": entity_id,
            "period_id": period,
            "statement_type": "BS",
            "standard_account_id": CTA_ACCOUNT_ID,
            "standard_account_name": CTA_ACCOUNT_NAME,
            "statement_section": "equity",
            "scenario": first["scenario"],
            "local_currency": first["local_currency"],
            "amount_local_canonical": 0.0,
            "rate_type_applied": "PLUG",
            "fx_rate_applied": float("nan"),
            "calculated_reporting_amount": cta,
            "source_reported_canonical": 0.0,
            "fx_translation_variance": -cta,
            "reporting_currency": first["reporting_currency"],
            "origin": "FX_ENGINE",
            "source_system": "FX_ENGINE",
            "source_account_code": CTA_ACCOUNT_ID,
            "load_id": first["load_id"],
        })

    translated = pd.concat(
        [df, pd.DataFrame(cta_rows)], ignore_index=True
    )[TRANSLATED_COLUMNS + [
        # keep lineage so any translated number still walks back to source
        c for c in ("source_system", "source_account_code", "load_id")
        if c in df.columns
    ]]

    log.info(
        "translated %d rows (+%d CTA rows) into reporting currency",
        len(df), len(cta_rows),
    )
    return translated

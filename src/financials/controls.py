"""
Control engine — Phase 4.

Deterministic financial controls (spec section 8) that run BEFORE any
agent/AI analysis. Each control is a reusable function returning
structured ControlResult records; the engine writes them to
data/client_fs/control_checks.csv so they can be tested, logged,
reviewed, and displayed in Power BI.

Statuses:
    PASS    the identity holds within tolerance
    REVIEW  the identity cannot be (fully) tested with the data on hand,
            or a variance has a known, documented cause needing sign-off
    FAIL    the identity is violated beyond tolerance

Failed or reviewable controls are never silently fixed — they are
exceptions for a human (spec: "Do not silently fix failed controls").

Controls implemented in Phase 4 (against the data layers that exist):
    C1  balance sheet        assets = liabilities + equity, per entity/period
    C2  cash flow            beginning cash + CFS = ending cash
    C3  net income           IS net income = CFS net income
    C4  retained earnings    begin RE + NI - dividends = end RE (local ccy)
    C5  OCI / AOCI           coverage check (no AOCI balance account yet)
    C6  debt                 begin debt + issued - repaid = end debt
    C8  consolidation        entity totals + eliminations + FX = consolidated,
                             and the consolidated balance sheet balances
    C9  FX                   source-reported vs engine-calculated translation
    C10 source total         normalized layer reconciles 1:1 to the raw layer

    C7  shares               NOT YET IMPLEMENTABLE - share data arrives in
                             Phase 6 (shares_dilution.csv); documented, not
                             silently skipped (see docs/CONTROLS.md).

Roll-forward controls (C2/C4/C6) run in LOCAL currency: the identities
hold in an entity's own books regardless of translation, so FX effects
cannot mask (or fake) a broken roll. Translation differences are C9's job.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from financials.normalized_statements import balance_sheet_gap
from financials.schemas import CONTROL_CHECKS

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = BASE_DIR / "data" / "client_fs" / CONTROL_CHECKS.filename

# Absolute tolerance for accounting identities: anything beyond rounding
# noise is a finding. C9 uses a wider band because source-vs-engine FX
# differences are expected until sources adopt the full methodology.
IDENTITY_TOLERANCE = 0.01
FX_VARIANCE_TOLERANCE = 0.50

_SEVERITY = {"PASS": "LOW", "REVIEW": "MEDIUM", "FAIL": "HIGH"}


@dataclass
class ControlResult:
    company_id: str
    period_id: str
    entity_id: str
    control_id: str
    control_name: str
    control_category: str
    expected_value: float
    actual_value: float
    variance_amount: float
    variance_pct: object          # blank when expected == 0
    tolerance_amount: float
    tolerance_pct: object
    status: str
    severity: str
    agent_comment: str            # engine-generated; Phase 10 agent appends
    source_reference: str
    reviewer_comment: str = ""
    review_status: str = "PENDING"


def _result(control_id, name, category, company, period, entity,
            expected, actual, tolerance, comment, source_ref,
            force_status=None):
    variance = round(actual - expected, 10)
    status = force_status or (
        "PASS" if abs(variance) <= tolerance else "FAIL"
    )
    variance_pct = (
        round(variance / abs(expected) * 100, 6) if expected else ""
    )
    return ControlResult(
        company_id=company, period_id=period, entity_id=entity,
        control_id=control_id, control_name=name, control_category=category,
        expected_value=round(expected, 6), actual_value=round(actual, 6),
        variance_amount=round(variance, 6), variance_pct=variance_pct,
        tolerance_amount=tolerance, tolerance_pct="",
        status=status, severity=_SEVERITY[status],
        agent_comment=comment, source_reference=source_ref,
    )


# ------------------------------------------------
# SHARED LOOKUP HELPERS (over the FX-translated frame,
# which carries canonical-sign LOCAL amounts + statement metadata)
# ------------------------------------------------

def _source_rows(translated):
    return translated[translated["origin"] == "SOURCE"]


def _local_sum(translated, entity, period, statement=None, account=None):
    rows = _source_rows(translated)
    mask = (rows["entity_id"] == entity) & (rows["period_id"] == period)
    if statement is not None:
        mask &= rows["statement_type"] == statement
    if account is not None:
        mask &= rows["standard_account_id"] == account
    return rows.loc[mask, "amount_local_canonical"].sum()


def _has_rows(translated, entity, period, statement=None, account=None):
    rows = _source_rows(translated)
    mask = (rows["entity_id"] == entity) & (rows["period_id"] == period)
    if statement is not None:
        mask &= rows["statement_type"] == statement
    if account is not None:
        mask &= rows["standard_account_id"] == account
    return bool(mask.any())


def _prior_period_map(period_master):
    """
    period_id -> the immediately preceding ANNUAL period_id.

    A prior link exists only between CONSECUTIVE fiscal years — a gap year
    on file must not silently chain FY2023 to FY2025 and corrupt the
    roll-forward controls. Quarterly/monthly rolls get their own linking
    logic when such periods first carry data.
    """
    annual = period_master[
        period_master["period_type"] == "ANNUAL"
    ].sort_values("fiscal_year")

    out = {}
    prev_id, prev_year = None, None
    for row in annual.itertuples():
        year = int(row.fiscal_year)
        if prev_id is not None and year == prev_year + 1:
            out[row.period_id] = prev_id
        prev_id, prev_year = row.period_id, year
    return out


def _entity_periods(translated):
    """(company, entity, period) triples that actually carry source data."""
    rows = _source_rows(translated)
    return sorted(set(zip(
        rows["company_id"], rows["entity_id"], rows["period_id"]
    )))


# ------------------------------------------------
# THE CONTROLS
# ------------------------------------------------

def control_1_balance_sheet(normalized):
    """C1: Assets - Liabilities - Equity = 0 per entity/period (REPORTED view)."""
    results = []
    company = normalized["company_id"].iloc[0]
    for (entity, period), gap in balance_sheet_gap(normalized).items():
        results.append(_result(
            "C1", "Balance sheet balances", "BALANCE_SHEET",
            company, period, entity,
            expected=0.0, actual=float(gap), tolerance=IDENTITY_TOLERANCE,
            comment="Assets - Liabilities - Equity, canonical signs, "
                    "source-reported amounts",
            source_ref="client_fs_normalized.csv statement_type=BS",
        ))
    return results


def control_2_cash_flow(translated, period_master):
    """C2: beginning cash + CFS total = ending cash (local currency)."""
    results = []
    prior_of = _prior_period_map(period_master)
    for company, entity, period in _entity_periods(translated):
        prior = prior_of.get(period)
        if prior is None or not _has_rows(translated, entity, prior, "BS", "cash"):
            continue  # first period on file: no beginning balance to test
        if not _has_rows(translated, entity, period, "BS", "cash"):
            continue

        begin = _local_sum(translated, entity, prior, "BS", "cash")
        end = _local_sum(translated, entity, period, "BS", "cash")

        if not _has_rows(translated, entity, period, "CFS"):
            results.append(_result(
                "C2", "Cash walks from beginning to ending balance",
                "CASH_FLOW", company, period, entity,
                expected=end, actual=begin, tolerance=IDENTITY_TOLERANCE,
                comment=f"No cash flow statement rows for this entity/period; "
                        f"cash moved {begin:g} -> {end:g} unexplained",
                source_ref="client_fs_raw.csv statement_type=CFS (absent)",
                force_status="REVIEW",
            ))
            continue

        cfs_total = _local_sum(translated, entity, period, "CFS")
        results.append(_result(
            "C2", "Cash walks from beginning to ending balance", "CASH_FLOW",
            company, period, entity,
            expected=end, actual=begin + cfs_total,
            tolerance=IDENTITY_TOLERANCE,
            comment=f"beginning {begin:g} + CFS {cfs_total:g} vs ending {end:g} "
                    "(local currency; FX effect line not yet in fixture)",
            source_ref="client_fs_raw.csv statement_type=CFS,BS",
        ))
    return results


def control_3_net_income(translated):
    """C3: IS net income = net income reported on the CFS."""
    results = []
    for company, entity, period in _entity_periods(translated):
        if not _has_rows(translated, entity, period, "CFS", "cfs_net_income"):
            continue  # missing CFS coverage is already surfaced by C2
        is_ni = _local_sum(translated, entity, period, "IS")
        cfs_ni = _local_sum(translated, entity, period, "CFS", "cfs_net_income")
        results.append(_result(
            "C3", "IS net income matches CFS net income", "NET_INCOME",
            company, period, entity,
            expected=is_ni, actual=cfs_ni, tolerance=IDENTITY_TOLERANCE,
            comment="Income statement rows summed (canonical signs) vs "
                    "cfs_net_income",
            source_ref="client_fs_raw.csv statement_type=IS,CFS",
        ))
    return results


def control_4_retained_earnings(translated, period_master):
    """C4: begin RE + net income + dividends = end RE (local currency)."""
    results = []
    prior_of = _prior_period_map(period_master)
    for company, entity, period in _entity_periods(translated):
        prior = prior_of.get(period)
        if prior is None:
            continue
        if not (_has_rows(translated, entity, prior, "BS", "retained_earnings")
                and _has_rows(translated, entity, period, "BS", "retained_earnings")):
            continue

        begin = _local_sum(translated, entity, prior, "BS", "retained_earnings")
        ni = _local_sum(translated, entity, period, "IS")
        dividends = _local_sum(translated, entity, period, "CFS",
                               "cfs_dividends_paid")  # canonical negative
        end = _local_sum(translated, entity, period, "BS", "retained_earnings")

        results.append(_result(
            "C4", "Retained earnings roll forward", "EQUITY_ROLL",
            company, period, entity,
            expected=end, actual=begin + ni + dividends,
            tolerance=IDENTITY_TOLERANCE,
            comment=f"begin {begin:g} + NI {ni:g} + dividends {dividends:g} "
                    f"vs ending {end:g} (local currency)",
            source_ref="client_fs_raw.csv statement_type=BS,IS,CFS",
        ))
    return results


def control_5_oci_aoci(translated):
    """
    C5: begin AOCI + OCI = end AOCI. The fixture reports OCI activity but
    maps no AOCI balance-sheet account yet, so the roll cannot be tested —
    surfaced as REVIEW, never silently skipped.
    """
    results = []
    for company, entity, period in _entity_periods(translated):
        if not _has_rows(translated, entity, period, "OCI"):
            continue
        oci = _local_sum(translated, entity, period, "OCI")
        has_aoci = _has_rows(translated, entity, period, "BS", "aoci")
        if has_aoci:
            continue  # full roll-forward lands when an AOCI account exists
        results.append(_result(
            "C5", "AOCI roll forward", "OCI",
            company, period, entity,
            expected=0.0, actual=oci, tolerance=IDENTITY_TOLERANCE,
            comment=f"OCI of {oci:g} reported but no 'aoci' balance-sheet "
                    "account is mapped - AOCI roll cannot be verified",
            source_ref="client_fs_raw.csv statement_type=OCI; account_mapping.csv",
            force_status="REVIEW",
        ))
    return results


def control_6_debt(translated, period_master):
    """C6: begin debt + issued - repaid = end debt (local currency)."""
    results = []
    prior_of = _prior_period_map(period_master)
    for company, entity, period in _entity_periods(translated):
        prior = prior_of.get(period)
        if prior is None:
            continue
        if not (_has_rows(translated, entity, prior, "BS", "long_term_debt")
                and _has_rows(translated, entity, period, "BS", "long_term_debt")):
            continue

        begin = _local_sum(translated, entity, prior, "BS", "long_term_debt")
        end = _local_sum(translated, entity, period, "BS", "long_term_debt")
        # cfs_debt_repayment is canonically negative (cash outflow), so it
        # subtracts naturally; issuances would arrive as a positive account.
        activity = _local_sum(translated, entity, period, "CFS",
                              "cfs_debt_repayment")
        has_cfs = _has_rows(translated, entity, period, "CFS")

        expected, actual = end, begin + activity
        if not has_cfs and abs(actual - expected) > IDENTITY_TOLERANCE:
            results.append(_result(
                "C6", "Debt rolls forward through issuances/repayments",
                "DEBT", company, period, entity,
                expected=expected, actual=actual,
                tolerance=IDENTITY_TOLERANCE,
                comment=f"Debt moved {begin:g} -> {end:g} with no debt "
                        "activity rows to explain it (no CFS for this "
                        "entity/period)",
                source_ref="client_fs_raw.csv statement_type=BS,CFS",
                force_status="REVIEW",
            ))
        else:
            results.append(_result(
                "C6", "Debt rolls forward through issuances/repayments",
                "DEBT", company, period, entity,
                expected=expected, actual=actual,
                tolerance=IDENTITY_TOLERANCE,
                comment=f"begin {begin:g} + net debt activity {activity:g} "
                        f"vs ending {end:g} (local currency)",
                source_ref="client_fs_raw.csv statement_type=BS,CFS",
            ))
    return results


CONSOLIDATION_KEY = ["company_id", "period_id", "scenario", "standard_account_id"]

_BUCKET_TO_COLUMN = {
    "pre": "pre_elimination_amount",
    "ic": "intercompany_elimination",
    "fx": "fx_translation_adjustment",
}


def _recompute_consolidation(translated, entity_master):
    """
    Independent per-bucket roll-up of the translated rows: one row per
    (company, period, scenario, standard account) with pre / ic / fx
    columns and their total. Keyed the full way so a second company or
    scenario can never cross-contaminate the comparison.
    """
    elim = set(entity_master.loc[
        entity_master["elimination_entity_flag"] == "Y", "entity_id"
    ])
    src = translated.copy()
    src["bucket"] = "pre"
    src.loc[src["entity_id"].isin(elim), "bucket"] = "ic"
    src.loc[src["origin"] == "FX_ENGINE", "bucket"] = "fx"

    recomputed = src.pivot_table(
        index=CONSOLIDATION_KEY, columns="bucket",
        values="calculated_reporting_amount", aggfunc="sum", fill_value=0.0,
    )
    for bucket in _BUCKET_TO_COLUMN:
        if bucket not in recomputed.columns:
            recomputed[bucket] = 0.0
    recomputed["total"] = (
        recomputed["pre"] + recomputed["ic"] + recomputed["fx"]
    )
    return recomputed


def control_8_consolidation(translated, consolidated, entity_master):
    """
    C8: (a) over the UNION of account keys — never only the rows that
    survived consolidation — the independently recomputed pre-elimination /
    elimination / FX buckets AND their total match entity_consolidation's
    breakdown columns (an account dropped from the output, or an amount
    folded into the wrong column, both FAIL); (b) the consolidated balance
    sheet balances, CTA included.
    """
    results = []
    company = consolidated["company_id"].iloc[0]

    recomputed = _recompute_consolidation(translated, entity_master)
    cons = consolidated.set_index(CONSOLIDATION_KEY)

    all_keys = recomputed.index.union(cons.index)
    worst = {}
    missing = {}
    for key in all_keys:
        period = key[1]
        gaps = []
        for bucket, column in _BUCKET_TO_COLUMN.items():
            expected = recomputed[bucket].get(key, 0.0)
            actual = cons[column].get(key, 0.0)
            gaps.append(abs(actual - expected))
        gaps.append(abs(
            cons["consolidated_amount"].get(key, 0.0)
            - recomputed["total"].get(key, 0.0)
        ))
        worst[period] = max(worst.get(period, 0.0), max(gaps))
        if key not in cons.index:
            missing[period] = missing.get(period, 0) + 1

    for period in sorted(worst):
        dropped = missing.get(period, 0)
        results.append(_result(
            "C8", "Entity totals + eliminations + FX = consolidated",
            "CONSOLIDATION", company, period, "CONSOLIDATED",
            expected=0.0, actual=worst[period], tolerance=IDENTITY_TOLERANCE,
            comment="Largest gap across the union of account keys, compared "
                    "per bucket (pre-elimination / elimination / FX) and in "
                    "total, between an independent roll-up of the translated "
                    "rows and entity_consolidation.csv"
                    + (f" — {dropped} account(s) MISSING from the "
                       "consolidated output" if dropped else ""),
            source_ref="entity_consolidation.csv vs translate_statements()",
        ))

    from financials.consolidation import consolidated_balance_gap
    for period, gap in consolidated_balance_gap(consolidated).items():
        results.append(_result(
            "C8", "Consolidated balance sheet balances (CTA included)",
            "CONSOLIDATION", company, period, "CONSOLIDATED",
            expected=0.0, actual=float(gap), tolerance=IDENTITY_TOLERANCE,
            comment="Consolidated assets - liabilities - equity",
            source_ref="entity_consolidation.csv statement_type=BS",
        ))
    return results


def control_9_fx(translated):
    """
    C9: source-reported reporting amounts vs the engine's deterministic
    translation, per entity/period/statement, foreign-currency rows only.
    A variance beyond tolerance is REVIEW (not FAIL): the fixture's known
    cause is the source's closing-rate shortcut on equity (DECISIONS #15),
    which an analyst signs off rather than the pipeline silently fixing.
    """
    results = []
    rows = _source_rows(translated)
    foreign = rows[rows["local_currency"] != rows["reporting_currency"]]
    if foreign.empty:
        return results

    grouped = foreign.groupby(
        ["company_id", "entity_id", "period_id", "statement_type"]
    )["fx_translation_variance"].apply(lambda s: s.abs().sum())

    # The engine's own CTA per entity/period — quoted as the "known cause"
    # ONLY when the variance actually equals it; an arbitrary translation
    # error must never be mislabeled with a reassuring explanation.
    cta_rows = translated[translated["origin"] == "FX_ENGINE"]
    cta = cta_rows.groupby(["entity_id", "period_id"])[
        "calculated_reporting_amount"
    ].sum()

    for (company, entity, period, statement), total_var in grouped.items():
        beyond = total_var > FX_VARIANCE_TOLERANCE
        entity_cta = abs(cta.get((entity, period), 0.0))
        is_the_shortcut = (
            statement == "BS"
            and abs(total_var - entity_cta) <= IDENTITY_TOLERANCE
        )
        if beyond and is_the_shortcut:
            cause = (" - known cause: source translated equity at closing "
                     "rate; difference equals the CTA (docs/FX_AND_"
                     "CONSOLIDATION.md #4)")
        elif beyond:
            cause = (" - UNEXPLAINED: does not match the CTA; investigate "
                     "the source translation row by row")
        else:
            cause = ""
        results.append(_result(
            "C9", "Source FX translation matches engine translation", "FX",
            company, period, entity,
            expected=0.0, actual=float(total_var),
            tolerance=FX_VARIANCE_TOLERANCE,
            comment=(
                f"{statement}: sum of |source-reported - calculated| across "
                f"foreign-currency rows{cause}"
            ),
            source_ref=f"translate_statements() statement_type={statement}",
            force_status="REVIEW" if beyond else None,
        ))
    return results


def control_10_source_total(normalized, translated):
    """
    C10: the normalized layer reconciles 1:1 to the raw source layer -
    same row count and the same canonical totals per entity/period/
    statement, before any analyst adjustment exists.

    Evaluated over the UNION of group keys from BOTH layers: a statement
    group missing entirely from the normalized side (the exact failure
    Phase 8 adjustments could introduce) FAILs instead of vanishing.
    """
    results = []
    raw = _source_rows(translated)
    group_cols = ["company_id", "entity_id", "period_id", "statement_type"]

    norm_groups = dict(tuple(normalized.groupby(group_cols)))
    raw_groups = dict(tuple(raw.groupby(group_cols)))

    for key in sorted(set(norm_groups) | set(raw_groups)):
        company, entity, period, statement = key
        group = norm_groups.get(key)
        raw_rows = raw_groups.get(key)
        norm_count = 0 if group is None else len(group)
        raw_count = 0 if raw_rows is None else len(raw_rows)

        # Compare like-for-like: the normalized layer's canonical amounts
        # against the raw rows' canonicalized source-reported amounts.
        norm_total = 0.0 if group is None else group["amount_reporting"].sum()
        raw_total = (
            0.0 if raw_rows is None
            else raw_rows["source_reported_canonical"].sum()
        )
        count_gap = norm_count - raw_count
        results.append(_result(
            "C10", "Normalized layer reconciles to source statements",
            "SOURCE_INTEGRITY", company, period, entity,
            expected=float(raw_total), actual=float(norm_total),
            tolerance=IDENTITY_TOLERANCE,
            comment=f"{statement}: {norm_count} normalized rows vs "
                    f"{raw_count} raw rows"
                    + (f" (ROW COUNT MISMATCH {count_gap:+d})" if count_gap else ""),
            source_ref="client_fs_normalized.csv vs client_fs_raw.csv "
                       f"statement_type={statement}",
            force_status="FAIL" if count_gap else None,
        ))
    return results


# ------------------------------------------------
# ENGINE ENTRY POINTS
# ------------------------------------------------

def run_all_controls(tables, normalized, translated, consolidated):
    """Run every implemented control and return the ControlResult list."""
    period_master = tables["period_master"]
    entity_master = tables["entity_master"]
    return (
        control_1_balance_sheet(normalized)
        + control_2_cash_flow(translated, period_master)
        + control_3_net_income(translated)
        + control_4_retained_earnings(translated, period_master)
        + control_5_oci_aoci(translated)
        + control_6_debt(translated, period_master)
        + control_8_consolidation(translated, consolidated, entity_master)
        + control_9_fx(translated)
        + control_10_source_total(normalized, translated)
    )


def results_frame(results) -> pd.DataFrame:
    frame = pd.DataFrame([r.__dict__ for r in results])
    return frame[CONTROL_CHECKS.column_names()]


def apply_consolidation_status(consolidated, translated, entity_master):
    """
    Fill entity_consolidation.csv's control columns from the independent
    per-bucket recomputation (replaces the PENDING placeholder). A row
    passes only if its pre-elimination / elimination / FX breakdown AND
    its total all match the recomputation. Accounts missing from the
    consolidated frame entirely have no row to stamp — that direction is
    covered by C8, which checks the union of keys.
    """
    recomputed = _recompute_consolidation(translated, entity_master)
    out = consolidated.copy()

    variances, statuses = [], []
    for row in out.itertuples():
        key = (row.company_id, row.period_id, row.scenario,
               row.standard_account_id)
        headline = round(
            row.consolidated_amount - recomputed["total"].get(key, 0.0), 6
        )
        bucket_gaps = [
            abs(getattr(row, column) - recomputed[bucket].get(key, 0.0))
            for bucket, column in _BUCKET_TO_COLUMN.items()
        ]
        ok = (abs(headline) <= IDENTITY_TOLERANCE
              and max(bucket_gaps) <= IDENTITY_TOLERANCE)
        variances.append(headline)
        statuses.append("PASS" if ok else "FAIL")

    out["control_variance"] = variances
    out["control_status"] = statuses
    return out


def write_control_checks(frame: pd.DataFrame, path=None) -> Path:
    path = Path(path) if path else DEFAULT_OUTPUT
    frame.to_csv(path, index=False)
    return path

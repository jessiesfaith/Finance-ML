"""
Outlier / discrepancy engine — Phase 7 (spec sections 9 and 26).

Deterministic outlier tests that run BEFORE any LLM interpretation:

    POP_VARIANCE     period-over-period dollar AND percentage movement
                     per standard account (consolidated + per entity)
    MARGIN_VARIANCE  EBITDA / EBIT margin change in percentage points
    RATIO_VARIANCE   DSO, DIO, DPO, NWC % revenue, CapEx % revenue
    NEW_ITEM         an IS/BS account with material activity and no
                     prior-period counterpart (new entity, acquisition,
                     consolidation-scope change ...)
    ZSCORE           deviation vs own history — requires at least
                     MIN_HISTORY_FOR_ZSCORE periods; with less history
                     the method reports itself as not applicable rather
                     than pretending statistics it cannot support

AN OUTLIER IS NOT AN ERROR. Every flag carries deterministic
possible-cause candidates (growth, acquisition/divestiture,
restructuring, FX, accounting change, error) and a PENDING review
status; the Phase 10 agent interprets, a human concludes. Nothing is
ever auto-adjusted.

Thresholds live in one visible config (future: per-company profiles).
A movement must clear BOTH the percentage and the dollar bar to flag —
a big % on a tiny balance is noise, a big $ on a huge balance may be
routine.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from financials.nwc import nwc_components
from financials.schemas import OUTLIER_FLAGS
from financials.ufcf import income_walk

log = logging.getLogger("financials.outliers")

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = BASE_DIR / "data" / "client_fs" / OUTLIER_FLAGS.filename

THRESHOLDS = {
    "pop_pct": 15.0,          # % movement, AND
    "pop_amount": 25.0,       # $M movement (both must clear)
    "margin_pp": 2.0,         # percentage points
    "ratio_pct": 15.0,        # relative change of the ratio
    "new_item_amount": 25.0,  # $M for an account with no prior
    "zscore": 3.0,
    "min_history_for_zscore": 4,
}

GENERIC_CAUSES = (
    "growth, acquisition/divestiture, restructuring, FX, accounting "
    "change, or error - an outlier is not a conclusion (docs/OUTLIERS.md)"
)


@dataclass
class OutlierFlag:
    company_id: str
    level: str                # CONSOLIDATED | ENTITY
    entity_id: str
    period_id: str
    prior_period_id: str
    method: str
    metric_name: str
    statement_type: str
    baseline_value: float
    current_value: float
    variance_amount: float
    variance_pct: float       # % for POP/RATIO; percentage POINTS for MARGIN
    threshold_desc: str
    severity: str
    possible_causes: str
    review_status: str = "PENDING"


def _severity(magnitude, threshold):
    return "HIGH" if abs(magnitude) >= 2 * threshold else "MEDIUM"


def _flag(company, level, entity, period, prior, method, metric, statement,
          baseline, current, variance_pct_or_pp, threshold_desc, severity,
          causes):
    return OutlierFlag(
        company_id=company, level=level, entity_id=entity, period_id=period,
        prior_period_id=prior, method=method, metric_name=metric,
        statement_type=statement,
        baseline_value=round(baseline, 4), current_value=round(current, 4),
        variance_amount=round(current - baseline, 4),
        variance_pct=round(variance_pct_or_pp, 4),
        threshold_desc=threshold_desc, severity=severity,
        possible_causes=causes,
    )


def _account_frames(consolidated, translated):
    """(level, entity_id, frame) tuples of per-account amounts to scan."""
    cons = consolidated.rename(
        columns={"consolidated_amount": "amount"}
    ).assign(level="CONSOLIDATED", entity_id="CONSOLIDATED")

    src = translated[translated["origin"] == "SOURCE"].copy()
    ent = (
        src.groupby(["company_id", "entity_id", "period_id",
                     "statement_type", "standard_account_id"], as_index=False)
        ["calculated_reporting_amount"].sum()
        .rename(columns={"calculated_reporting_amount": "amount"})
        .assign(level="ENTITY")
    )
    return pd.concat(
        [cons[ent.columns.tolist()], ent], ignore_index=True
    )


def pop_and_new_item_flags(consolidated, translated, prior_of):
    """Period-over-period account movements + material new items (IS/BS)."""
    flags = []
    frame = _account_frames(consolidated, translated)
    frame = frame[frame["statement_type"].isin(["IS", "BS"])]

    indexed = frame.set_index(
        ["level", "entity_id", "period_id", "statement_type",
         "standard_account_id"]
    )["amount"]

    for key, current in indexed.items():
        level, entity, period, statement, account = key
        prior_period = prior_of.get(period)
        if prior_period is None:
            continue
        company = frame["company_id"].iloc[0]
        prior_key = (level, entity, prior_period, statement, account)

        if prior_key not in indexed.index:
            if abs(current) >= THRESHOLDS["new_item_amount"]:
                flags.append(_flag(
                    company, level, entity, period, prior_period,
                    "NEW_ITEM", account, statement,
                    baseline=0.0, current=current, variance_pct_or_pp=0.0,
                    threshold_desc=f"no prior activity and |amount| >= "
                                   f"{THRESHOLDS['new_item_amount']}",
                    severity="MEDIUM",
                    causes="new entity/eliminations, acquisition, "
                           "consolidation-scope or accounting change; "
                           + GENERIC_CAUSES,
                ))
            continue

        baseline = indexed[prior_key]
        variance = current - baseline
        if baseline == 0:
            continue
        pct = variance / abs(baseline) * 100
        if (abs(pct) >= THRESHOLDS["pop_pct"]
                and abs(variance) >= THRESHOLDS["pop_amount"]):
            flags.append(_flag(
                company, level, entity, period, prior_period,
                "POP_VARIANCE", account, statement,
                baseline, current, pct,
                threshold_desc=f"|%| >= {THRESHOLDS['pop_pct']} and "
                               f"|$| >= {THRESHOLDS['pop_amount']}",
                severity=_severity(pct, THRESHOLDS["pop_pct"]),
                causes=GENERIC_CAUSES,
            ))
    return flags


def margin_flags(consolidated, prior_of):
    """EBITDA / EBIT margin movement in percentage points (consolidated)."""
    flags = []
    walk = income_walk(consolidated).set_index("period_id")
    company = consolidated["company_id"].iloc[0]

    for period in walk.index:
        prior = prior_of.get(period)
        if prior is None or prior not in walk.index:
            continue
        for name, num in (("ebitda_margin", "ebitda"), ("ebit_margin", "ebit")):
            cur = walk.loc[period, num] / walk.loc[period, "revenue"] * 100
            base = walk.loc[prior, num] / walk.loc[prior, "revenue"] * 100
            delta_pp = cur - base
            if abs(delta_pp) >= THRESHOLDS["margin_pp"]:
                flags.append(_flag(
                    company, "CONSOLIDATED", "CONSOLIDATED", period, prior,
                    "MARGIN_VARIANCE", name, "IS",
                    base, cur, delta_pp,
                    threshold_desc=f"|pp| >= {THRESHOLDS['margin_pp']}",
                    severity=_severity(delta_pp, THRESHOLDS["margin_pp"]),
                    causes="mix shift, pricing, cost inflation/deflation, "
                           "one-time items, FX; " + GENERIC_CAUSES,
                ))
    return flags


def ratio_flags(consolidated, account_mapping, period_master, prior_of):
    """Working-capital and intensity ratios: DSO, DIO, DPO, NWC%rev, CapEx%rev."""
    flags = []
    walk = income_walk(consolidated).set_index("period_id")
    nwc = nwc_components(consolidated, account_mapping).set_index("period_id")
    days = dict(zip(period_master["period_id"],
                    pd.to_numeric(period_master["days_in_period"])))
    company = consolidated["company_id"].iloc[0]

    def capex(period):
        m = consolidated[
            (consolidated["period_id"] == period)
            & (consolidated["standard_account_id"] == "cfs_capex")
        ]
        return -float(m["consolidated_amount"].iloc[0]) if len(m) else None

    def ratios(period):
        # DIO/DPO use total operating costs as the cost base (COGS-only
        # refinement arrives when clients report COGS distinctly enough
        # to matter); DSO uses revenue. days_in_period from period_master.
        w, n, d = walk.loc[period], nwc.loc[period], days[period]
        out = {
            "dso_days": n["accounts_receivable"] / w["revenue"] * d,
            "dio_days": (n["inventory"] / (w["operating_costs"]) * d
                         if w["operating_costs"] else None),
            "dpo_days": (n["accounts_payable"] / (w["operating_costs"]) * d
                         if w["operating_costs"] else None),
            "nwc_pct_revenue": n["operating_nwc"] / w["revenue"] * 100,
        }
        cx = capex(period)
        out["capex_pct_revenue"] = (
            cx / w["revenue"] * 100 if cx is not None else None
        )
        return out

    for period in walk.index:
        prior = prior_of.get(period)
        if prior is None or prior not in walk.index:
            continue
        cur_r, base_r = ratios(period), ratios(prior)
        for name in cur_r:
            cur, base = cur_r[name], base_r[name]
            if cur is None or base is None or base == 0:
                continue   # a ratio without both sides is not evaluated
            rel_pct = (cur - base) / abs(base) * 100
            if abs(rel_pct) >= THRESHOLDS["ratio_pct"]:
                flags.append(_flag(
                    company, "CONSOLIDATED", "CONSOLIDATED", period, prior,
                    "RATIO_VARIANCE", name, "BS",
                    base, cur, rel_pct,
                    threshold_desc=f"|relative %| >= {THRESHOLDS['ratio_pct']}",
                    severity=_severity(rel_pct, THRESHOLDS["ratio_pct"]),
                    causes="collection/payment-term shifts, inventory "
                           "build, demand change, FX; " + GENERIC_CAUSES,
                ))
    return flags


def zscore_flags(consolidated, prior_of):
    """
    Z-score vs own history — honest about its precondition: with fewer
    than MIN_HISTORY_FOR_ZSCORE periods the method is NOT APPLICABLE and
    says so in the run log instead of inventing statistics.
    """
    periods = sorted(consolidated["period_id"].unique())
    if len(periods) < THRESHOLDS["min_history_for_zscore"]:
        log.info(
            "ZSCORE not applicable: %d period(s) on file, %d required",
            len(periods), THRESHOLDS["min_history_for_zscore"],
        )
        return []

    flags = []
    frame = consolidated[consolidated["statement_type"].isin(["IS", "BS"])]
    company = frame["company_id"].iloc[0]
    for account, group in frame.groupby("standard_account_id"):
        series = group.sort_values("period_id").set_index("period_id")[
            "consolidated_amount"
        ]
        if len(series) < THRESHOLDS["min_history_for_zscore"]:
            continue
        history, current = series.iloc[:-1], series.iloc[-1]
        std = history.std()
        if not std:
            continue
        z = (current - history.mean()) / std
        if abs(z) >= THRESHOLDS["zscore"]:
            flags.append(_flag(
                company, "CONSOLIDATED", "CONSOLIDATED", series.index[-1],
                series.index[-2], "ZSCORE", account,
                group["statement_type"].iloc[0],
                history.mean(), current, z,
                threshold_desc=f"|z| >= {THRESHOLDS['zscore']}",
                severity=_severity(z, THRESHOLDS["zscore"]),
                causes=GENERIC_CAUSES,
            ))
    return flags


def run_outlier_engine(tables, consolidated, translated):
    """All deterministic methods; returns the flag list."""
    from financials.controls import _prior_period_map
    prior_of = _prior_period_map(tables["period_master"])
    return (
        pop_and_new_item_flags(consolidated, translated, prior_of)
        + margin_flags(consolidated, prior_of)
        + ratio_flags(consolidated, tables["account_mapping"],
                      tables["period_master"], prior_of)
        + zscore_flags(consolidated, prior_of)
    )


def flags_frame(flags) -> pd.DataFrame:
    frame = pd.DataFrame([f.__dict__ for f in flags])
    if frame.empty:
        frame = pd.DataFrame(columns=OUTLIER_FLAGS.column_names())
    frame = frame[OUTLIER_FLAGS.column_names()]
    return frame.sort_values(
        ["level", "entity_id", "period_id", "method", "metric_name"]
    ).reset_index(drop=True)


def write_outlier_flags(frame: pd.DataFrame, path=None) -> Path:
    path = Path(path) if path else DEFAULT_OUTPUT
    frame.to_csv(path, index=False)
    log.info("wrote %s (%d flag(s))", path, len(frame))
    return path

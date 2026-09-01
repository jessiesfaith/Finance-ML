"""
Industry / peer benchmarking — Phase 14 (spec sections 19, 22).

Architecture-first, per the spec's rule: NO industry or peer values are
invented anywhere. What ships now:

  * benchmark_metric_master — ratio definitions with derivation rules
    that apply IDENTICALLY to the company and to future peers
    (comparability is the whole game);
  * peer_group_master — group definitions (membership arrives via the
    Phase 12 SEC pipeline);
  * benchmark_observations — COMPANY statistic rows computed from the
    internal pipeline through those same rules; PEER_MEDIAN /
    INDUSTRY_MEDIAN / P25 / P75 slots exist but stay EMPTY until real
    peer data lands.

The no-invented-data guard is structural: a non-COMPANY statistic row
whose source is INTERNAL_PIPELINE fails validation — peers can only
ever come from a cited external source. When peer bands exist, a
company assumption outside P25–P75 becomes a REVIEW flag (never an
auto-correction).
"""

import logging
from pathlib import Path

import pandas as pd

from financials import validator
from financials.loader import ClientFSValidationError, _coerce_types, _read_csv
from financials.schemas import (
    BENCHMARK_METRIC_MASTER,
    BENCHMARK_OBSERVATIONS,
    PEER_GROUP_MASTER,
)

log = logging.getLogger("financials.benchmarking")

BASE_DIR = Path(__file__).resolve().parents[2]
MARKET_DIR = BASE_DIR / "data" / "market"

INTERNAL_SOURCE = "INTERNAL_PIPELINE"
COMPANY_VINTAGE = "2026-08-31T00:00:00Z"   # deterministic fixture stamp


def build_company_benchmarks(income_walk_frame, nwc_frame, roic_frame,
                             consolidated, period_master,
                             peer_group_id) -> pd.DataFrame:
    """COMPANY statistic rows from the internal pipeline — same
    derivations the SEC pipeline will apply to peers."""
    walk = income_walk_frame.set_index("period_id")
    nwc = nwc_frame.set_index("period_id")
    roic = roic_frame.set_index("period_id")
    days = dict(zip(period_master["period_id"],
                    pd.to_numeric(period_master["days_in_period"])))

    def capex(period):
        match = consolidated[
            (consolidated["period_id"] == period)
            & (consolidated["standard_account_id"] == "cfs_capex")
        ]
        return -float(match["consolidated_amount"].iloc[0]) if len(match) else None

    rows = []
    periods = sorted(walk.index)
    for i, period in enumerate(periods):
        w, n, d = walk.loc[period], nwc.loc[period], days[period]
        values = {
            "revenue_growth_pct": (
                (w["revenue"] / walk.loc[periods[i - 1], "revenue"] - 1) * 100
                if i > 0 else None),
            "ebitda_margin_pct": w["ebitda"] / w["revenue"] * 100,
            "ebit_margin_pct": w["ebit"] / w["revenue"] * 100,
            "roic_pct": roic.loc[period, "roic_pct"],
            "nwc_pct_revenue": n["operating_nwc"] / w["revenue"] * 100,
            "capex_pct_revenue": (
                capex(period) / w["revenue"] * 100
                if capex(period) is not None else None),
            "dso_days": n["accounts_receivable"] / w["revenue"] * d,
            "dio_days": n["inventory"] / w["operating_costs"] * d,
            "dpo_days": n["accounts_payable"] / w["operating_costs"] * d,
        }
        unit_of = {"dso_days": "DAYS", "dio_days": "DAYS", "dpo_days": "DAYS"}
        for metric_id, value in values.items():
            if value is None:
                continue
            rows.append({
                "benchmark_metric_id": metric_id,
                "peer_group_id": peer_group_id,
                "statistic": "COMPANY",
                "period_id": period,
                "value": round(float(value), 4),
                "unit": unit_of.get(metric_id, "PCT"),
                "source": INTERNAL_SOURCE,
                "source_reference": "financials pipeline (consolidated)",
                "retrieval_timestamp": COMPANY_VINTAGE,
            })
    return pd.DataFrame(rows, columns=BENCHMARK_OBSERVATIONS.column_names())


def load_benchmarks(market_dir=None, strict=True):
    """Validate the benchmark layer, including the no-invented-data guard."""
    market_dir = Path(market_dir) if market_dir else MARKET_DIR
    issues, tables = [], {}
    for schema in (BENCHMARK_METRIC_MASTER, PEER_GROUP_MASTER,
                   BENCHMARK_OBSERVATIONS):
        path = market_dir / schema.filename
        if not path.exists():
            issues.append(validator.Issue(
                "ERROR", schema.table, "missing_file", f"not found: {path}"))
            continue
        raw = _read_csv(path)
        issues.extend(validator.validate_table(raw, schema))
        tables[schema.table] = _coerce_types(raw, schema)

    if "benchmark_observations" in tables:
        obs = tables["benchmark_observations"]
        known_metrics = set(
            tables.get("benchmark_metric_master",
                       pd.DataFrame({"benchmark_metric_id": []})
                       )["benchmark_metric_id"])
        bad = obs.index[~obs["benchmark_metric_id"].isin(known_metrics)].tolist()
        if bad:
            issues.append(validator.Issue(
                "ERROR", "benchmark_observations", "unknown_metric",
                f"benchmark_metric_id not in master — {validator._rows(bad)}"))

        invented = obs.index[
            (obs["statistic"] != "COMPANY")
            & (obs["source"] == INTERNAL_SOURCE)
        ].tolist()
        if invented:
            issues.append(validator.Issue(
                "ERROR", "benchmark_observations", "invented_peer_data",
                "peer/industry statistics may never come from "
                f"{INTERNAL_SOURCE} — cite the external source — "
                f"{validator._rows(invented)}"))

    if strict and any(i.severity == "ERROR" for i in issues):
        raise ClientFSValidationError(issues)
    return tables, issues

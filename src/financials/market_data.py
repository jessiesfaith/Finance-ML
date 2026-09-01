"""
Market-data layer — Phase 13 architecture (docs/MARKET_DATA_PROPOSAL.md).

A SEPARATE module with a separate lifecycle from the client-FS loader:
`load_market_data()` has its own required-file set, so a market refresh
never touches a client load and vice versa (refresh independence,
decision d). Facts are APPEND-ONLY: revisions become new rows with newer
retrieval_timestamps; "current" and "point-in-time" are computed views,
never stored files.

Today every observation is the seed-42 SYNTHETIC history, re-platformed
into canonical rows with honest metadata (source=SYNTHETIC,
revision_status=SYNTHETIC, lineage to the generator script). The FRED
adapter below is coded and tested for URL/parse behavior, but the LIVE
source flip (preferred_source SYNTHETIC -> FRED, per metric) waits for
the owner's "reporting tool finalized" gate (DECISIONS #5) — and FRED,
like EDGAR, blocks hosted IPs (403 verified), so live fetches run from
the analyst's machine.
"""

import logging
import urllib.request
from io import StringIO
from pathlib import Path

import pandas as pd

from financials import validator
from financials.loader import ClientFSValidationError, _coerce_types, _read_csv
from financials.schemas import (
    MARKET_METRIC_MASTER,
    MARKET_OBSERVATIONS,
)

log = logging.getLogger("financials.market")

BASE_DIR = Path(__file__).resolve().parents[2]
MARKET_DIR = BASE_DIR / "data" / "market"
SYNTHETIC_HISTORY = BASE_DIR / "data" / "raw" / "macro_history.csv"

# One fixed vintage stamp for the synthetic re-platform (deterministic;
# real fetches stamp their own retrieval time).
SYNTHETIC_RETRIEVAL = "2026-08-31T00:00:00Z"

_SYNTHETIC_COLUMNS = {
    "treasury_2y": "ust_2y",
    "treasury_10y": "ust_10y",
    "fed_funds": "fed_funds_eff",
    "cpi": "cpi_yoy",
    "unemployment": "unemployment_rate",
}


def replatform_synthetic_history() -> pd.DataFrame:
    """Seed-42 history -> canonical observation rows, honestly labeled."""
    wide = pd.read_csv(SYNTHETIC_HISTORY, parse_dates=["date"])
    rows = []
    for column, metric_id in _SYNTHETIC_COLUMNS.items():
        for _, r in wide.iterrows():
            rows.append({
                "metric_id": metric_id,
                "observation_date": r["date"].strftime("%Y-%m-%d"),
                "value": round(float(r[column]), 6),
                "unit": "PCT",
                "source": "SYNTHETIC",
                "source_reference": "src/generate_history.py seed 42",
                "retrieval_timestamp": SYNTHETIC_RETRIEVAL,
                "frequency": "MONTHLY",
                "revision_status": "SYNTHETIC",
            })
    frame = pd.DataFrame(rows, columns=MARKET_OBSERVATIONS.column_names())
    return frame.sort_values(
        ["metric_id", "observation_date"]).reset_index(drop=True)


def load_market_data(market_dir=None, strict=True):
    """Independent market loader: its own files, its own validation."""
    market_dir = Path(market_dir) if market_dir else MARKET_DIR
    issues, tables = [], {}

    for schema in (MARKET_METRIC_MASTER, MARKET_OBSERVATIONS):
        path = market_dir / schema.filename
        if not path.exists():
            issues.append(validator.Issue(
                "ERROR", schema.table, "missing_file",
                f"required file not found: {path}",
            ))
            continue
        raw = _read_csv(path)
        issues.extend(validator.validate_table(raw, schema))
        tables[schema.table] = _coerce_types(raw, schema)

    if "market_metric_master" in tables and "market_observations" in tables:
        master = tables["market_metric_master"]
        obs = tables["market_observations"]

        known = set(master["metric_id"])
        bad = obs.index[~obs["metric_id"].isin(known)].tolist()
        if bad:
            issues.append(validator.Issue(
                "ERROR", "market_observations", "unknown_metric",
                f"metric_id not in market_metric_master — "
                f"{validator._rows(bad)}",
            ))

        expected_unit = dict(zip(master["metric_id"], master["unit"]))
        mismatched = obs.index[[
            expected_unit.get(m) not in (None, u)
            for m, u in zip(obs["metric_id"], obs["unit"])
        ]].tolist()
        if mismatched:
            issues.append(validator.Issue(
                "ERROR", "market_observations", "unit_mismatch",
                f"unit differs from the metric master — values are stored "
                f"as published, never rescaled — {validator._rows(mismatched)}",
            ))

    if strict and any(i.severity == "ERROR" for i in issues):
        raise ClientFSValidationError(issues)
    log.info("market layer loaded: %d metric(s), %d observation(s)",
             len(tables.get("market_metric_master", [])),
             len(tables.get("market_observations", [])))
    return tables, issues


def current_view(observations: pd.DataFrame) -> pd.DataFrame:
    """Latest known value per metric (max date, then max retrieval)."""
    ordered = observations.sort_values(
        ["metric_id", "observation_date", "retrieval_timestamp"]
    )
    return ordered.groupby("metric_id", as_index=False).last()


def point_in_time_view(observations: pd.DataFrame,
                       as_of: str) -> pd.DataFrame:
    """What was known ON a date: observations retrieved by then."""
    known = observations[observations["retrieval_timestamp"] <= as_of]
    return current_view(known) if len(known) else known


# ------------------------------------------------
# FRED ADAPTER — coded and unit-tested; live flip gated (DECISIONS #5)
# ------------------------------------------------

def fred_csv_url(series_id: str) -> str:
    """Keyless official CSV endpoint (blocked from hosted IPs; run locally)."""
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"


def parse_fred_csv(csv_text: str, metric_id: str, unit: str,
                   frequency: str, retrieval_timestamp: str) -> pd.DataFrame:
    """FRED CSV ('DATE,<SERIES>' with '.' for missing) -> canonical rows."""
    frame = pd.read_csv(StringIO(csv_text))
    frame.columns = [c.strip().lower() for c in frame.columns]
    date_col, value_col = frame.columns[0], frame.columns[1]
    frame = frame[frame[value_col] != "."]        # gaps stay absent, never filled
    return pd.DataFrame({
        "metric_id": metric_id,
        "observation_date": frame[date_col],
        "value": pd.to_numeric(frame[value_col]),
        "unit": unit,
        "source": "FRED",
        "source_reference": f"fredgraph.csv id={value_col.upper()}",
        "retrieval_timestamp": retrieval_timestamp,
        "frequency": frequency,
        "revision_status": "FINAL",
    })[MARKET_OBSERVATIONS.column_names()]


def fetch_fred_series(series_id: str, timeout=60) -> str:
    request = urllib.request.Request(
        fred_csv_url(series_id),
        headers={"User-Agent": "Finance-ML-learning-project"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")

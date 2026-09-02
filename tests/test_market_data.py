"""
Tests for the Phase 13 market-data layer: canonical re-platform of the
synthetic history, independent validation, computed views, append-only
lock, and the FRED adapter's parse behavior (no network — FRED blocks
hosted IPs; live fetches run locally).
"""

import io

import pandas as pd
import pytest

from financials.loader import ClientFSValidationError
from financials.market_data import (
    MARKET_DIR,
    current_view,
    fred_csv_url,
    load_market_data,
    parse_fred_csv,
    point_in_time_view,
    replatform_synthetic_history,
)


@pytest.fixture(scope="module")
def tables():
    tables, issues = load_market_data(strict=True)
    assert issues == []
    return tables


def test_replatform_covers_the_full_synthetic_history(tables):
    obs = tables["market_observations"]
    assert len(obs) == 5150                      # 103 months x 50 stored metrics
    assert set(obs["source"]) == {"SYNTHETIC"}
    assert set(obs["revision_status"]) == {"SYNTHETIC"}
    assert set(obs["source_reference"]) == {
        "src/generate_history.py seed 42",
        "financials/market_data.py seed 42 (macro extensions)",
        "financials/market_data.py seed 4242 (headline indicators)",
        "financials/market_data.py seed 424242 (commodities & equities)",
        "financials/market_data.py seed 24242 (brent)",
        "financials/market_data.py seed 2424242 (index levels)",
    }


def test_replatform_values_match_the_source_history(tables):
    from financials.market_data import SYNTHETIC_HISTORY
    wide = pd.read_csv(SYNTHETIC_HISTORY, parse_dates=["date"])
    obs = tables["market_observations"]
    ust10 = obs[obs["metric_id"] == "ust_10y"].sort_values("observation_date")
    assert ust10.iloc[0]["value"] == pytest.approx(
        wide.iloc[0]["treasury_10y"], abs=1e-6)
    assert ust10.iloc[-1]["value"] == pytest.approx(
        wide.iloc[-1]["treasury_10y"], abs=1e-6)


def test_unknown_metric_and_unit_mismatch_fail_loudly(tmp_path, tables):
    master = tables["market_metric_master"]
    obs = tables["market_observations"].copy()
    obs.loc[0, "metric_id"] = "not_a_metric"
    obs.loc[1, "unit"] = "INDEX"                 # master says PCT
    master.to_csv(tmp_path / "market_metric_master.csv", index=False)
    obs.to_csv(tmp_path / "market_observations.csv", index=False)
    (tmp_path / "risk_free_policy.csv").touch()  # not required by loader

    with pytest.raises(ClientFSValidationError) as excinfo:
        load_market_data(market_dir=tmp_path, strict=True)
    message = str(excinfo.value)
    assert "unknown_metric" in message
    assert "unit_mismatch" in message


def test_current_and_point_in_time_views(tables):
    obs = tables["market_observations"]
    view = current_view(obs)
    assert len(view) == 50
    assert set(view["observation_date"].astype(str).str[:10]) == {"2026-07-31"}

    # Nothing was retrieved before the synthetic vintage stamp.
    empty = point_in_time_view(obs, "2020-01-01T00:00:00Z")
    assert len(empty) == 0
    full = point_in_time_view(obs, "2026-09-01T00:00:00Z")
    assert len(full) == 50


def test_committed_observations_match_a_fresh_replatform(tables):
    from conftest import assert_matches_committed
    assert_matches_committed(replatform_synthetic_history(),
                            MARKET_DIR / "market_observations.csv")


# ------------------------------------------------
# FRED ADAPTER (parse-level; no network)
# ------------------------------------------------

def test_fred_url_builder():
    assert fred_csv_url("DGS10") == (
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
    )


def test_fred_csv_parses_and_never_fills_gaps():
    csv_text = "DATE,DGS10\n2026-08-27,4.25\n2026-08-28,.\n2026-08-29,4.30\n"
    rows = parse_fred_csv(csv_text, "ust_10y", "PCT", "DAILY",
                          "2026-08-31T12:00:00Z")
    assert len(rows) == 2                        # the '.' gap stays absent
    assert rows.iloc[0]["value"] == pytest.approx(4.25)
    assert set(rows["source"]) == {"FRED"}
    assert set(rows["revision_status"]) == {"FINAL"}
    assert (rows["source_reference"] == "fredgraph.csv id=DGS10").all()


# ------------------------------------------------
# EQUITY ADAPTER (parse-level; live fetch runs on the owner's machine)
# ------------------------------------------------

def test_stooq_csv_parses_real_names_with_real_labels():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path("src").resolve()))
    from fetch_equity_prices import parse_stooq_csv, watchlist

    csv_text = ("Date,Open,High,Low,Close,Volume\n"
                "2026-06-30,210.0,231.0,205.0,220.5,123456\n"
                "2026-07-31,221.0,229.0,214.0,,0\n"        # no close -> absent
                "2026-08-29,222.0,240.0,220.0,227.3,98765\n")
    rows = parse_stooq_csv(csv_text, "nvda_px", "nvda.us",
                           "2026-09-02T12:00:00Z")
    assert len(rows) == 2
    assert rows.iloc[0]["value"] == pytest.approx(220.5)
    assert set(rows["source"]) == {"STOOQ"}
    assert set(rows["revision_status"]) == {"FINAL"}
    assert (rows["source_reference"] == "stooq.com nvda.us monthly close").all()

    symbols = dict(watchlist())
    assert symbols["nvda_px"] == "nvda.us"
    assert len(symbols) == 15                    # the owner's watchlist

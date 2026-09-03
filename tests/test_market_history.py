"""
Tests for the rolling 24-month macro-history export (Page 5).

Rolling means are recomputed by hand for one metric, the partial-window
months must stay blank (a 12-month mean labeled as a 24-month mean would
be a different statistic), the derived curve spread must honor its
registered derivation rule, and the committed CSV must match a fresh
rebuild.
"""

import pandas as pd
import pytest

from financials.market_data import (
    HISTORY_METRICS,
    HISTORY_OUTPUT,
    ROLLING_WINDOW,
    load_market_data,
    rolling_24m_history,
)


@pytest.fixture(scope="module")
def history():
    tables, issues = load_market_data(strict=True)
    assert issues == []
    return rolling_24m_history(tables["market_observations"])


def test_schema_is_locked(history):
    expected = ["observation_date"]
    for metric in HISTORY_METRICS:
        expected += [metric, f"{metric}_r24"]
    expected += ["source", "value_class"]
    assert list(history.columns) == expected
    assert len(history) == 103                       # one row per month
    assert set(history["source"]) == {"SYNTHETIC"}


def test_partial_windows_stay_blank(history):
    head = history["cpi_yoy_r24"].iloc[:ROLLING_WINDOW - 1]
    assert head.isna().all()
    assert history["cpi_yoy_r24"].iloc[ROLLING_WINDOW - 1:].notna().all()


def test_rolling_mean_recomputed_by_hand(history):
    first24 = history["cpi_yoy"].iloc[:ROLLING_WINDOW]
    expected = round(float(first24.mean()), 4)
    assert history["cpi_yoy_r24"].iloc[ROLLING_WINDOW - 1] == pytest.approx(
        expected, abs=2e-4)                          # both sides round to 4dp


def test_curve_spread_honors_its_derivation_rule(history):
    derived = history["ust_10y"] - history["ust_2y"]
    assert history["curve_spread_10y_2y"].values == pytest.approx(
        derived.values, abs=2e-4)


def test_committed_history_matches_a_fresh_rebuild(history):
    from conftest import assert_matches_committed
    assert_matches_committed(history, HISTORY_OUTPUT)


# ------------------------------------------------
# WINDOWED HISTORY (the Page 5 time-period toggle)
# ------------------------------------------------

def test_windowed_history_schema_and_coverage():
    from financials.market_data import (
        HISTORY_WINDOWS, WINDOWS_OUTPUT, load_market_data, windowed_history)
    tables, _ = load_market_data(strict=True)
    win = windowed_history(tables["market_observations"])
    assert list(win.columns) == (["observation_date", "window", "months_ago"]
                                 + list(HISTORY_METRICS)
                                 + ["source", "value_class"])
    latest = win[win["months_ago"] == 0]
    assert set(latest["observation_date"]) == {"2026-07-31"}
    assert win["months_ago"].max() == 102
    assert set(win["window"]) == set(HISTORY_WINDOWS) | {"YTD"}
    assert len(win) == 103 * 5

    # 3M window: hand-recompute one value; blanks until the window fills.
    w3 = win[win["window"] == "03M"].reset_index(drop=True)
    assert w3["cpi_yoy"].iloc[:2].isna().all()
    raw = win[win["window"] == "YTD"].reset_index(drop=True)  # Jan YTD = raw
    hand = (raw["cpi_yoy"].iloc[0] * 0 +  # placeholder, real check below
            0)
    # YTD: January equals the month itself; March = mean of Jan..Mar.
    tables_obs = tables["market_observations"]
    latest = (tables_obs.sort_values("retrieval_timestamp")
              .groupby(["metric_id", "observation_date"]).tail(1))
    cpi = (latest[latest["metric_id"] == "cpi_yoy"]
           .sort_values("observation_date")["value"].astype(float))
    ytd = win[win["window"] == "YTD"].reset_index(drop=True)
    assert ytd["cpi_yoy"].iloc[0] == pytest.approx(cpi.iloc[0], abs=2e-4)
    assert ytd["cpi_yoy"].iloc[2] == pytest.approx(
        cpi.iloc[:3].mean(), abs=2e-4)
    assert w3["cpi_yoy"].iloc[2] == pytest.approx(
        cpi.iloc[:3].mean(), abs=2e-4)

    # The 24M rows must agree with the legacy rolling-24 export.
    from financials.market_data import rolling_24m_history
    legacy = rolling_24m_history(tables["market_observations"])
    w24 = win[win["window"] == "24M"].reset_index(drop=True)
    assert w24["cpi_yoy"].dropna().values == pytest.approx(
        legacy["cpi_yoy_r24"].dropna().values)

    from conftest import assert_matches_committed
    assert_matches_committed(win, WINDOWS_OUTPUT)


def test_long_history_matches_the_wide_values():
    from financials.market_data import (
        LONG_OUTPUT, PAGE5_PANELS, load_market_data, long_history,
        rolling_24m_history)
    tables, _ = load_market_data(strict=True)
    long_frame = long_history(tables["market_observations"])
    total_series = sum(len(v) for v in PAGE5_PANELS.values())
    assert list(long_frame.columns) == ["observation_date", "panel",
                                        "series", "value", "recency_rank"]
    assert len(long_frame) == 103 * total_series
    # rank 0 = the newest month, so matrices open on the latest data
    newest = long_frame[long_frame["recency_rank"] == 0]
    assert set(newest["observation_date"]) == {"2026-07-31"}
    oldest = long_frame["recency_rank"].max()
    assert oldest == 102

    wide = rolling_24m_history(tables["market_observations"])
    spot = long_frame[(long_frame["panel"] == "inflation")
                      & (long_frame["series"] == "CPI")
                      & (long_frame["observation_date"] == "2026-07-31")]
    assert len(spot) == 1
    assert spot.iloc[0]["value"] == pytest.approx(
        wide["cpi_yoy"].iloc[-1], abs=2e-4)

    from conftest import assert_matches_committed
    assert_matches_committed(long_frame, LONG_OUTPUT)

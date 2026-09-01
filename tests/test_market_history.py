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

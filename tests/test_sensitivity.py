"""
Tests for the WACC x growth sensitivity grid (Page 4).

The grid center must equal the reported Base implied price (the grid
re-implements the DCF; drift = failure), prices must fall as WACC
rises and rise with growth, the impossible w <= g region must refuse
loudly, and the committed export must match a fresh rebuild.
"""

import pytest

from financials.sensitivity import (
    GROWTH_GRID,
    OUTPUT,
    OUTPUT_COLUMNS,
    build_sensitivity,
    growth_column,
    implied_price,
    load_inputs,
)


@pytest.fixture(scope="module")
def grid():
    base, ufcf_path = load_inputs()
    return base, build_sensitivity(base, ufcf_path)


def test_schema_is_locked(grid):
    _, frame = grid
    assert list(frame.columns) == OUTPUT_COLUMNS
    assert len(frame) == 5


def test_center_cell_equals_the_reported_base_price(grid):
    base, frame = grid
    center = frame[frame["wacc_delta_pts"] == 0.0].iloc[0][growth_column(2.5)]
    assert center == pytest.approx(
        float(base["implied_share_price"]), abs=1e-3)


def test_price_falls_with_wacc_and_rises_with_growth(grid):
    _, frame = grid
    mid = growth_column(2.5)
    assert frame[mid].is_monotonic_decreasing        # rows: rising WACC
    center_row = frame[frame["wacc_delta_pts"] == 0.0].iloc[0]
    prices = [center_row[growth_column(g)] for g in GROWTH_GRID]
    assert prices == sorted(prices)                  # cols: rising growth


def test_impossible_region_refuses_loudly():
    with pytest.raises(ValueError, match="must exceed terminal growth"):
        implied_price([100] * 5, 2.0, 2.5, 172.0, 103.6667)


def test_committed_grid_matches_a_fresh_rebuild(grid):
    _, frame = grid
    from conftest import assert_matches_committed
    assert_matches_committed(frame, OUTPUT)

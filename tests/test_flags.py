"""
Tests for the red/green flag engine (deterministic notification layer).

The engine reads only curated reports/*.csv and must mirror the tab-2
verdict-card thresholds and the tab-5 recommendations exactly.
"""

import pandas as pd
import pytest

from financials.flags import FLAG_COLUMNS, REPORTS, build_flags


@pytest.fixture(scope="module")
def flags():
    return build_flags()


def test_shape_and_columns(flags):
    assert list(flags.columns) == FLAG_COLUMNS
    assert set(flags["color"]) <= {"RED", "YELLOW", "GREEN"}
    assert set(flags["value_class"]) == {"MODEL_OUTPUT"}
    assert flags["flag_id"].is_unique
    # sorted RED first, then YELLOW, then GREEN
    order = {"RED": 0, "YELLOW": 1, "GREEN": 2}
    seq = flags["color"].map(order).tolist()
    assert seq == sorted(seq)


def test_fixture_health_flags_are_green(flags):
    """The fixture is a healthy company: core checks come back GREEN."""
    greens = flags[flags["color"] == "GREEN"]["headline"].str.cat(sep=" | ")
    assert "Quick ratio 3.03x >= 1.0x target" in greens
    assert "ROIC beats WACC by 14.4pts" in greens
    assert "self-funded" in greens
    # current ratio 3.98x is ABOVE the band -> lazy-capital YELLOW
    yellows = flags[flags["color"] == "YELLOW"]["headline"].str.cat(sep=" | ")
    assert "Current ratio 3.98x above the 3.0x band" in yellows


def test_headroom_quote_matches_tab2(flags):
    row = flags[flags["headline"].str.startswith("Net debt/EBITDA")]
    assert len(row) == 1
    assert "headroom $457.6M" in row.iloc[0]["detail"].lower() \
        or "$457.6M" in row.iloc[0]["detail"]


def test_option_recommendations_map_to_colors(flags):
    opts = flags[flags["area"] == "Options"]
    projects = pd.read_csv(REPORTS / "client_fs_projects.csv")
    projects = projects[projects["scenario"].str.upper() == "BASE"]
    for _, p in projects.iterrows():
        rows = opts[opts["headline"].str.startswith(
            f"{p['project_id']} {p['project_name']}:")]
        assert len(rows) == 1, p["project_id"]
        expected = {"APPROVE": "GREEN", "REVIEW": "YELLOW",
                    "REJECT": "RED"}[p["recommendation"]]
        assert rows.iloc[0]["color"] == expected


def test_rate_fragility_flag_for_proj001(flags):
    frag = flags[flags["headline"] == "PROJ-001 is rate-fragile"]
    assert len(frag) == 1
    assert frag.iloc[0]["color"] == "YELLOW"
    assert "REJECT" in frag.iloc[0]["detail"]


def test_every_open_review_finding_becomes_a_flag(flags):
    review = pd.read_csv(REPORTS / "client_fs_review.csv")
    controls = flags[flags["area"] == "Controls"]
    assert len(controls) == len(review)
    for _, r in review.iterrows():
        row = controls[controls["headline"].str.startswith(
            r["review_id"] + " ")]
        assert len(row) == 1
        if str(r["recommended_action"]).startswith("No action needed"):
            # the agent's own benign verdict is a confirmed-healthy note
            assert row.iloc[0]["color"] == "GREEN"
        else:
            assert row.iloc[0]["color"] in ("RED", "YELLOW")


def test_committed_export_matches_fresh_build(flags):
    committed = pd.read_csv(REPORTS / "client_fs_flags.csv")
    pd.testing.assert_frame_equal(
        committed, flags, check_dtype=False)

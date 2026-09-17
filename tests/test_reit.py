"""Tests for the Macro-tab REIT + business-cycle layer (owner
requests 2026-09-17). Same honesty rules as the NFP module: every
researched number is pinned, blanks are RESEARCH REQUIRED rather
than guesses, and owner-provided narrative is labeled as such."""

import pytest

from financials.reit import (build_reit_frames, cycle_indicators,
                             reit_returns, reit_returns_wide,
                             reit_summary)


def test_reit_returns_pinned_and_honest():
    r = reit_returns()
    assert len(r) == 50  # 5 tickers x (9 years + 2026 YTD)
    assert (r["confidence"] == "MEDIUM").all()
    assert (r["url"].str.startswith("https://")).all()

    def val(tk, yr):
        return r[(r["ticker"] == tk)
                 & (r["year_label"] == yr)].iloc[0]["return_pct"]

    assert float(val("WELL", "2025")) == 48.8
    assert float(val("PLD", "2021")) == 72.33
    assert float(val("AMT", "2019")) == 47.87
    assert float(val("DLR", "2022")) == -41.0
    assert float(val("EQIX", "2026 YTD")) == 38.19
    # the two unverifiable cells stay blank - never guessed
    for tk in ("EQIX", "DLR"):
        row = r[(r["ticker"] == tk) & (r["year_label"] == "2017")].iloc[0]
        assert row["return_pct"] == ""
        assert "RESEARCH REQUIRED" in row["note"]
    blanks = r[r["return_pct"] == ""]
    assert len(blanks) == 2


def test_reit_wide_is_a_faithful_pivot():
    r = reit_returns()
    w = reit_returns_wide(r).set_index("ticker")
    assert len(w) == 5
    assert float(w.loc["WELL", "y2026_ytd"]) == 28.45
    assert w.loc["EQIX", "y2017"] == ""
    assert "RESEARCH REQUIRED" in w.loc["EQIX", "note"]
    assert w.loc["WELL", "note"] == ""
    assert float(w.loc["PLD", "y2021"]) == 72.33


def test_reit_summary_is_owner_narrative():
    s = reit_summary()
    assert len(s) == 30
    assert (s["basis"] == "OWNER-PROVIDED SUMMARY").all()
    assert s.groupby("ticker").size().eq(6).all()
    dlr26 = s[(s["ticker"] == "DLR") & (s["period"] == "2026")].iloc[0]
    assert dlr26["driver"] == "AI/hyperscale rebound"
    pld22 = s[(s["ticker"] == "PLD") & (s["period"] == "2022")].iloc[0]
    assert "Duke Realty" in pld22["driver"]


def test_cycle_indicators_transcribed_faithfully():
    c = cycle_indicators()
    assert len(c) == 25
    counts = c.groupby("indicator_group").size().to_dict()
    assert counts == {"LEADING": 10, "COINCIDENT": 8, "LAGGING": 7}
    assert set(c["signal"]) == {"EXPANSION", "MIXED / WATCH",
                                "CONTRACTION"}
    assert (c["confidence"] == "MEDIUM").all()
    lei = c[c["indicator"].str.startswith("LEI")].iloc[0]
    assert lei["latest_reading"] == "99.5" and lei["signal"] == "EXPANSION"
    gdp = c[c["indicator"] == "Real GDP"].iloc[0]
    assert gdp["latest_reading"] == "+3.0%"
    assert gdp["signal"] == "MIXED / WATCH" and gdp["as_of"] == "Q2 2026"
    ur = c[c["indicator"] == "Unemployment Rate"].iloc[0]
    assert ur["latest_reading"] == "4.1%" and ur["signal"] == "CONTRACTION"
    # per-group signal counts quoted in the tab-4 caption
    sig = c.groupby(["indicator_group", "signal"]).size()
    assert sig["LEADING"]["EXPANSION"] == 5
    assert sig["LEADING"]["CONTRACTION"] == 3
    assert sig["COINCIDENT"]["EXPANSION"] == 4
    assert sig["LAGGING"]["CONTRACTION"] == 4


def test_reit_frames_have_row_ids():
    frames = build_reit_frames()
    assert set(frames) == {"market_reit_returns",
                           "market_reit_returns_wide",
                           "market_reit_summary",
                           "market_cycle_indicators"}
    for df in frames.values():
        assert df.columns[0] == "row_id"
        assert df["row_id"].iloc[0] == "R001"

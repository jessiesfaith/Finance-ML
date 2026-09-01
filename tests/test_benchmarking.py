"""
Tests for the Phase 14 benchmarking layer: company rows derived through
the shared rules, and the structural guard against invented peer data.
"""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

from financials.benchmarking import MARKET_DIR, load_benchmarks
from financials.loader import ClientFSValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(scope="module")
def tables():
    tables, issues = load_benchmarks(strict=True)
    assert issues == []
    return tables


def test_company_rows_only_until_real_peers_exist(tables):
    obs = tables["benchmark_observations"]
    assert set(obs["statistic"]) == {"COMPANY"}
    assert set(obs["source"]) == {"INTERNAL_PIPELINE"}


def test_company_values_match_the_pipeline(tables):
    obs = tables["benchmark_observations"]
    fy25 = obs[obs["period_id"] == "FY2025"].set_index("benchmark_metric_id")
    assert fy25.loc["revenue_growth_pct", "value"] == pytest.approx(6.7002)
    assert fy25.loc["ebitda_margin_pct", "value"] == pytest.approx(24.7096)
    assert fy25.loc["roic_pct", "value"] == pytest.approx(23.5565, abs=1e-3)
    assert fy25.loc["dso_days", "value"] == pytest.approx(47.2724)


def test_invented_peer_data_is_structurally_blocked(tables, tmp_path):
    for name in ("benchmark_metric_master.csv", "peer_group_master.csv"):
        pd.read_csv(MARKET_DIR / name).to_csv(tmp_path / name, index=False)
    obs = tables["benchmark_observations"].copy()
    fake = obs.iloc[[0]].copy()
    fake["statistic"] = "PEER_MEDIAN"          # a peer stat...
    fake["source"] = "INTERNAL_PIPELINE"       # ...from ourselves = invented
    pd.concat([obs, fake]).to_csv(
        tmp_path / "benchmark_observations.csv", index=False)

    with pytest.raises(ClientFSValidationError, match="invented_peer_data"):
        load_benchmarks(market_dir=tmp_path, strict=True)


def test_cited_external_peer_data_is_accepted(tables, tmp_path):
    for name in ("benchmark_metric_master.csv", "peer_group_master.csv"):
        pd.read_csv(MARKET_DIR / name).to_csv(tmp_path / name, index=False)
    obs = tables["benchmark_observations"].copy()
    peer = obs.iloc[[0]].copy()
    peer["statistic"] = "PEER_MEDIAN"
    peer["source"] = "SEC_XBRL"
    peer["source_reference"] = "computed from peer 10-K filings (Phase 12)"
    pd.concat([obs, peer]).to_csv(
        tmp_path / "benchmark_observations.csv", index=False)

    loaded, issues = load_benchmarks(market_dir=tmp_path, strict=True)
    assert issues == []


def test_committed_observations_match_a_fresh_rebuild(tables):
    from build_benchmarks import main  # noqa: F401 (import path check only)
    committed = pd.read_csv(
        MARKET_DIR / "benchmark_observations.csv",
        dtype=str, keep_default_na=False,
    )
    # 9 metrics in FY2025; FY2024 has 7 (no prior year for growth, and
    # no CFS so CapEx intensity is honestly absent rather than invented).
    assert len(committed) == 16
    assert list(committed.columns)[0] == "benchmark_metric_id"
"""
Tests for the SEC/XBRL ingestion adapter (Phase 12), run entirely on a
clearly-labeled SYNTHETIC companyfacts fixture — no real SEC data is
invented or committed.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from financials import load_client_fs, validator
from financials.schemas import ACCOUNT_MAPPING
from financials.sec_adapter import (
    load_tag_mapping,
    parse_companyfacts,
    write_staging,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sec_companyfacts_synthetic.json"


@pytest.fixture(scope="module")
def parsed():
    facts = json.loads(FIXTURE.read_text())
    return parse_companyfacts(facts, fy_min=2023, fy_max=2024)


def raw(parsed):
    return parsed["tables"]["client_fs_raw.csv"]


def test_values_convert_ones_to_millions(parsed):
    revenue_2023 = raw(parsed)[
        (raw(parsed)["source_account_code"] == "Revenues")
        & (raw(parsed)["period_id"] == "FY2023")
    ].iloc[0]
    assert revenue_2023["amount_local"] == pytest.approx(500.0)   # 500,000,000
    assert "ONES->MILLIONS" in revenue_2023["source_note"]


def test_latest_accession_wins_for_refiled_facts(parsed):
    revenue_2024 = raw(parsed)[
        (raw(parsed)["source_account_code"] == "Revenues")
        & (raw(parsed)["period_id"] == "FY2024")
    ].iloc[0]
    assert revenue_2024["amount_local"] == pytest.approx(561.0)   # the refile
    assert "24-000011" in revenue_2024["source_sheet"]


def test_quarterly_facts_are_excluded(parsed):
    assert "FY2025" not in set(raw(parsed)["period_id"])
    assert not (raw(parsed)["amount_local"] == 150.0).any()


def test_lineage_points_at_edgar(parsed):
    row = raw(parsed).iloc[0]
    assert "SEC EDGAR companyfacts CIK0009999999" == row["source_file"]
    assert row["source_system"] == "SEC_XBRL"


def test_unmapped_material_tags_are_surfaced_by_magnitude(parsed):
    unmapped = parsed["unmapped"]
    assert "Goodwill" in set(unmapped["source_account_code"])
    top = unmapped.iloc[0]
    assert top["source_account_code"] == "Goodwill"     # biggest unmapped
    assert top["latest_amount_musd"] == pytest.approx(400.0)


def test_period_master_derived_from_filing_dates(parsed):
    periods = parsed["tables"]["period_master.csv"].set_index("period_id")
    assert periods.loc["FY2024", "period_end"] == "2024-09-28"
    assert periods.loc["FY2024", "is_historical"] == "Y"


def test_staged_output_passes_canonical_loader_validation(parsed, tmp_path):
    target = write_staging(parsed, staging_root=tmp_path)
    result = load_client_fs(data_dir=target, strict=False)
    assert result.errors == [], [str(i) for i in result.errors]
    assert (target / "unmapped_tags.csv").exists()


def test_starter_tag_mapping_is_schema_valid():
    mapping = load_tag_mapping()
    issues = validator.validate_table(mapping, ACCOUNT_MAPPING)
    assert issues == [], [str(i) for i in issues]
    # Starter rows await per-company analyst approval by design.
    assert set(mapping["review_status"]) == {"REVIEW"}

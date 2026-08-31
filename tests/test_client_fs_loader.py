"""
End-to-end tests of the loader against the bundled COMP001 synthetic
fixture data in data/client_fs/ (clearly fictional Example Company data —
see docs/SCHEMAS.md).
"""

import shutil

import pandas as pd
import pytest

from financials.loader import (
    DEFAULT_DATA_DIR,
    ClientFSValidationError,
    load_client_fs,
)


def test_bundled_sample_data_loads_clean():
    """The shipped COMP001 fixture must pass every validation rule."""
    result = load_client_fs(strict=True)

    assert result.errors == []
    assert set(result.tables) == {
        "company_master", "entity_master", "period_master",
        "account_mapping", "fx_rates", "client_fs_raw",
    }
    assert len(result.tables["client_fs_raw"]) > 0


def test_amounts_are_numeric_after_load():
    result = load_client_fs(strict=True)
    raw = result.tables["client_fs_raw"]

    assert pd.api.types.is_numeric_dtype(raw["amount_local"])
    assert pd.api.types.is_numeric_dtype(raw["amount_reporting"])
    assert pd.api.types.is_numeric_dtype(raw["fx_rate_to_reporting"])


def test_source_lineage_survives_the_load():
    """
    Spec section 30: after loading, any number can still be traced to the
    exact file / sheet / row that produced it.
    """
    result = load_client_fs(strict=True)
    raw = result.tables["client_fs_raw"]

    revenue = raw[
        (raw["entity_id"] == "ENT_GMBH")
        & (raw["period_id"] == "FY2025")
        & (raw["source_account_code"] == "8400")
    ]
    assert len(revenue) == 1

    row = revenue.iloc[0]
    assert row["source_file"] == "beispiel_gmbh_fy2025.xlsx"
    assert row["source_sheet"] == "GuV"
    assert row["source_row"] == 10
    assert row["load_id"] == "L20260831-001"

    # Load IDs are surfaced on the result as the audit trail.
    assert result.load_ids == ("L20260831-001",)


def test_loader_never_modifies_source_files(tmp_path):
    """Raw source financials are immutable: loading twice reads identical bytes."""
    before = {
        p.name: p.read_bytes() for p in sorted(DEFAULT_DATA_DIR.glob("*.csv"))
    }
    load_client_fs(strict=True)
    after = {
        p.name: p.read_bytes() for p in sorted(DEFAULT_DATA_DIR.glob("*.csv"))
    }
    assert before == after


def test_missing_required_file_is_a_hard_stop(tmp_path):
    """A directory missing any required CSV must fail loudly, not partially load."""
    partial = tmp_path / "client_fs"
    partial.mkdir()
    for name in ("company_master.csv", "entity_master.csv"):
        shutil.copy(DEFAULT_DATA_DIR / name, partial / name)

    with pytest.raises(ClientFSValidationError) as excinfo:
        load_client_fs(data_dir=partial, strict=True)

    assert "missing_file" in str(excinfo.value)
    assert "client_fs_raw.csv" in str(excinfo.value)


def test_non_strict_mode_returns_issues_instead_of_raising(tmp_path):
    partial = tmp_path / "client_fs"
    partial.mkdir()

    result = load_client_fs(data_dir=partial, strict=False)

    assert result.errors, "expected missing-file errors"
    frame = result.issues_frame()
    assert list(frame.columns) == ["severity", "table", "rule", "message"]
    assert (frame["severity"] == "ERROR").any()

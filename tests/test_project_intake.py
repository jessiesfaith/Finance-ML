"""
Tests for the Excel intake form round-trip: a filled form parses, upserts
into the project files, and the result passes the same fail-loud
validation as hand-edited data; a bad form is rejected with the data
files left untouched.
"""

import shutil
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from financials.loader import ClientFSValidationError  # noqa: E402
from financials.projects import build_project_appraisal, load_projects, load_rates  # noqa: E402
from ingest_project_intake import DEFAULT_FORM, parse_intake, upsert  # noqa: E402


def filled_form(tmp_path, **overrides):
    form = tmp_path / "form.xlsx"
    shutil.copy(DEFAULT_FORM, form)
    wb = openpyxl.load_workbook(form)
    ws = wb["Project Intake"]
    values = {
        "B6": "PROJ-777", "B7": "Test line", "B8": "GROWTH_CAPEX",
        "B9": 60.0, "B10": "FY2026", "B11": 5, "B12": "REVIEW",
        "B13": "test rationale",
        "B17": 50.0, "B22": 3.0, "B23": 10.0,   # rev Y1, capex%, nwc%
    }
    values.update(overrides)
    for cell, value in values.items():
        ws[cell] = value
    wb.save(form)
    return form


def project_dir_copy(tmp_path):
    d = tmp_path / "projects"
    d.mkdir()
    for f in ("project_master.csv", "project_assumptions.csv"):
        shutil.copy(Path("data/projects") / f, d / f)
    return d


def test_round_trip_parses_upserts_and_appraises(tmp_path):
    form = filled_form(tmp_path)
    master_row, assumption_rows = parse_intake(form)
    assert master_row["project_id"] == "PROJ-777"
    assert len(assumption_rows) == 3

    d = project_dir_copy(tmp_path)
    replacing = upsert(master_row, assumption_rows, projects_dir=d)
    assert not replacing
    tables, issues = load_projects(projects_dir=d, strict=True)
    assert issues == []
    frame = build_project_appraisal(
        tables["project_master"], tables["project_assumptions"], load_rates())
    assert len(frame) == 15                      # 5 options x 3 scenarios
    assert "PROJ-777" in set(frame["project_id"])


def test_upsert_replaces_not_duplicates(tmp_path):
    form = filled_form(tmp_path, B6="PROJ-002", B7="Replacement")
    master_row, assumption_rows = parse_intake(form)
    d = project_dir_copy(tmp_path)
    assert upsert(master_row, assumption_rows, projects_dir=d)  # replacing
    tables, _ = load_projects(projects_dir=d, strict=True)
    master = tables["project_master"]
    assert (master["project_id"] == "PROJ-002").sum() == 1
    assert master[master["project_id"] == "PROJ-002"].iloc[0][
        "project_name"] == "Replacement"


def test_bad_form_rejected_and_files_untouched(tmp_path):
    form = filled_form(tmp_path, B11=9)          # horizon out of range
    master_row, assumption_rows = parse_intake(form)
    d = project_dir_copy(tmp_path)
    before = (d / "project_master.csv").read_text()
    with pytest.raises(ClientFSValidationError):
        upsert(master_row, assumption_rows, projects_dir=d)
    assert (d / "project_master.csv").read_text() == before


def test_empty_form_fails_loudly(tmp_path):
    form = tmp_path / "empty.xlsx"
    shutil.copy(DEFAULT_FORM, form)
    with pytest.raises(SystemExit, match="fill the yellow cells"):
        parse_intake(form)

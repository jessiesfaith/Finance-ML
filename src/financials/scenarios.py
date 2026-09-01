"""
Scenario / driver layer loader — kind C, analyst assumptions.

Lives in data/scenarios/, deliberately SEPARATE from client financial
data (data/client_fs/) and future market facts (data/market/). Scenarios
are diffable, reviewable rows — never edits to observations or client
statements.

A scenario carries two kinds of assumption rows (scenario_assumptions):
    COMPANY_DRIVER — feeds the UFCF forecast engine (revenue growth,
                     margins, CapEx intensity, normalized tax, ...)
    MARKET_METRIC  — will feed the WACC layer once the market-data
                     module lands (rates, spreads); accepted by the
                     schema now so one scenario can hit BOTH sides.
"""

import logging
from pathlib import Path

from financials import validator
from financials.loader import ClientFSValidationError, _coerce_types, _read_csv
from financials.schemas import SCENARIO_SCHEMAS

log = logging.getLogger("financials.scenarios")

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_DIR = BASE_DIR / "data" / "scenarios"


def load_scenarios(scenario_dir=None, strict=True):
    """Load + validate the scenario layer; returns {table: DataFrame}."""
    scenario_dir = Path(scenario_dir) if scenario_dir else DEFAULT_SCENARIO_DIR
    issues, raw = [], {}

    missing = [s.filename for s in SCENARIO_SCHEMAS
               if not (scenario_dir / s.filename).exists()]
    if missing:
        issues.append(validator.Issue(
            "ERROR", "scenarios", "missing_file",
            f"required file(s) not found in {scenario_dir}: {missing}",
        ))
        if strict:
            raise ClientFSValidationError(issues)
        return {}, issues

    for schema in SCENARIO_SCHEMAS:
        df = _read_csv(scenario_dir / schema.filename)
        raw[schema.table] = df
        issues.extend(validator.validate_table(df, schema))

    # Cross-table integrity: every assumption points at a real scenario,
    # and every COMPANY_DRIVER at a defined driver.
    known_scenarios = set(raw["scenario_master"]["scenario_id"])
    known_drivers = set(raw["driver_master"]["driver_id"])
    assumptions = raw["scenario_assumptions"]

    bad_scenario = assumptions.index[
        ~assumptions["scenario_id"].isin(known_scenarios)
    ].tolist()
    if bad_scenario:
        issues.append(validator.Issue(
            "ERROR", "scenario_assumptions", "unknown_scenario",
            f"scenario_id not in scenario_master — "
            f"{validator._rows(bad_scenario)}",
        ))

    driver_rows = assumptions[assumptions["target_type"] == "COMPANY_DRIVER"]
    bad_driver = driver_rows.index[
        ~driver_rows["target_id"].isin(known_drivers)
    ].tolist()
    if bad_driver:
        issues.append(validator.Issue(
            "ERROR", "scenario_assumptions", "unknown_driver",
            f"COMPANY_DRIVER target_id not in driver_master — "
            f"{validator._rows(bad_driver)}",
        ))

    if strict and any(i.severity == "ERROR" for i in issues):
        raise ClientFSValidationError(issues)

    tables = {
        s.table: _coerce_types(raw[s.table], s) for s in SCENARIO_SCHEMAS
    }
    log.info("loaded scenario layer: %d scenario(s), %d assumption row(s)",
             len(tables["scenario_master"]), len(tables["scenario_assumptions"]))
    return tables, issues


def company_drivers(scenario_tables, scenario_id, company_id):
    """
    Resolve COMPANY_DRIVER values for one scenario/company:
    {driver_id: value}. Company-specific rows override blank-company
    defaults (the account_mapping precedence pattern). Period-specific
    paths (period_id set) are a future refinement; blank-period rows
    apply to every forecast year.
    """
    a = scenario_tables["scenario_assumptions"]
    rows = a[
        (a["scenario_id"] == scenario_id)
        & (a["target_type"] == "COMPANY_DRIVER")
        & (a["period_id"] == "")
        & (a["company_id"].isin(["", company_id]))
    ]
    out = {}
    for row in rows.itertuples():   # defaults first, then overrides win
        if row.company_id == "":
            out[row.target_id] = row.value
    for row in rows.itertuples():
        if row.company_id == company_id:
            out[row.target_id] = row.value
    return out

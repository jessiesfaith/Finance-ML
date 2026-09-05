"""
Structural integrity of the hand-authored Power BI project.

Guards the failure modes Desktop has actually thrown at us:
  * every visual.json must be valid JSON with a unique name,
  * every measure/column a visual binds must exist in the model,
  * measure names must be unique model-wide (case-insensitive) and must
    not collide with a column in their own table - Desktop refuses the
    model outright on such a clash ("The 'X' measure cannot be created
    because a column with the same name already exists"),
  * every partition sourceColumn must exist in its CSV.
"""

import glob
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

SM = Path("reports/ML Tool.SemanticModel/definition")
PAGES = Path("reports/ML Tool.Report/definition/pages")

CSV_FOR_TABLE = {
    "finance_scenario_report": "finance_scenario_report",
    "client_fs_ufcf": "client_fs_ufcf",
    "client_fs_statements": "client_fs_statements",
    "client_fs_income_walk": "client_fs_income_walk",
    "client_fs_valuation_inputs": "client_fs_valuation_inputs",
    "client_fs_controls": "client_fs_controls",
    "client_fs_review": "client_fs_review",
    "client_fs_projects": "client_fs_projects",
    "client_fs_sensitivity": "client_fs_sensitivity",
    "market_rf_policy": "market_rf_policy",
    "market_history_rolling24": "market_history_rolling24",
    "market_history_windows": "market_history_windows",
    "market_history_long": "market_history_long",
    "client_fs_option_sensitivity": "client_fs_option_sensitivity",
    "client_fs_option_verdicts": "client_fs_option_verdicts",
    "client_fs_flags": "client_fs_flags",
    "nfp_settings": "nfp_settings",
    "nfp_alternatives": "nfp_alternatives",
    "nfp_programs": "nfp_programs",
    "nfp_solutions": "nfp_solutions",
    "nfp_grants": "nfp_grants",
    "nfp_funding_cliff": "nfp_funding_cliff",
    "nfp_pipeline": "nfp_pipeline",
    "nfp_calendar": "nfp_calendar",
    "nfp_campaign": "nfp_campaign",
    "nfp_pledges": "nfp_pledges",
    "nfp_project_cash": "nfp_project_cash",
    "nfp_financing": "nfp_financing",
    "nfp_debt_reserves": "nfp_debt_reserves",
    "nfp_risks": "nfp_risks",
    "nfp_scenarios": "nfp_scenarios",
    "nfp_sensitivity": "nfp_sensitivity",
    "nfp_exec_board": "nfp_exec_board",
    "nfp_controls": "nfp_controls",
    "nfp_ml_series": "nfp_ml_series",
    "nfp_ml_estimates": "nfp_ml_estimates",
    "nfp_ml_anomalies": "nfp_ml_anomalies",
    "nfp_public_financials": "nfp_public_financials",
    "nfp_role_matrix": "nfp_role_matrix",
    "nfp_ratio_990": "nfp_ratio_990",
    "client_fs_option_sizing": "client_fs_option_sizing",
}


def model_tables():
    tables = {}
    for f in sorted(glob.glob(str(SM / "tables" / "*.tmdl"))):
        text = Path(f).read_text(encoding="utf-8")
        name = re.search(r"^table (\S+)", text, re.M).group(1)
        tables[name] = {
            "measures": re.findall(r"measure '([^']+)'", text),
            "columns": re.findall(r"^\tcolumn (\S+)", text, re.M),
            "source_columns": re.findall(r"sourceColumn: (\S+)", text),
        }
    return tables


def test_measure_names_never_collide_with_columns():
    for table, info in model_tables().items():
        columns = {c.lower() for c in info["columns"]}
        clashes = [m for m in info["measures"] if m.lower() in columns]
        assert not clashes, f"{table}: measure/column name clash {clashes}"


def test_measure_names_unique_model_wide():
    everything = [m.lower() for info in model_tables().values()
                  for m in info["measures"]]
    dupes = [m for m, n in Counter(everything).items() if n > 1]
    assert not dupes, f"duplicate measure names: {dupes}"


def test_every_source_column_exists_in_its_csv():
    for table, info in model_tables().items():
        csv = CSV_FOR_TABLE.get(table)
        assert csv is not None, f"untracked model table {table}"
        cols = set(pd.read_csv(f"reports/{csv}.csv", nrows=0).columns)
        missing = [c for c in info["source_columns"] if c not in cols]
        assert not missing, f"{table}: sourceColumn not in CSV {missing}"


def test_every_visual_parses_resolves_and_is_unique():
    tables = model_tables()
    measures = {m for info in tables.values() for m in info["measures"]}
    columns = {c for info in tables.values() for c in info["columns"]}
    names = []
    for f in glob.glob(str(PAGES / "*" / "visuals" / "*" / "visual.json")):
        visual = json.loads(Path(f).read_text(encoding="utf-8"))
        names.append(visual["name"])
        bound = []

        def walk(node):
            if isinstance(node, dict):
                if "Measure" in node and isinstance(node["Measure"], dict):
                    bound.append(("m", node["Measure"].get("Property")))
                if "Column" in node and isinstance(node["Column"], dict):
                    bound.append(("c", node["Column"].get("Property")))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(visual["visual"].get("query", {}))
        for kind, prop in bound:
            pool = measures if kind == "m" else columns
            assert prop in pool, f"{f}: unresolved binding {prop}"
    assert len(names) == len(set(names)), "duplicate visual names"


def test_number_typed_partition_columns_parse_as_numbers():
    """Every column an M partition types as number (or Int64) must hold
    only parseable numerics in its CSV - non-blank text in a
    number-typed column fails the whole table's refresh in Desktop.
    (Bit us twice: is_recommended True/False, then date_verified dates
    landing in a column typed while it was still all-blank.)"""
    import re

    import pandas as pd

    for table, csv_name in CSV_FOR_TABLE.items():
        tmdl = (SM / "tables" / f"{table}.tmdl")
        if not tmdl.exists():
            continue
        text = tmdl.read_text(encoding="utf-8")
        cols = re.findall(r'\{"([^"]+)", (?:type number|Int64\.Type)\}',
                          text)
        if not cols:
            continue
        df = pd.read_csv(Path("reports") / f"{csv_name}.csv", dtype=str)
        for col in cols:
            assert col in df.columns, f"{table}.{col} missing from CSV"
            bad = [v for v in df[col].dropna() if v != ""
                   and not _parses_as_float(v)]
            assert not bad, (f"{table}.{col} is number-typed but holds "
                             f"non-numeric values, e.g. {bad[:3]}")


def _parses_as_float(value):
    try:
        float(value)
        return True
    except ValueError:
        return False

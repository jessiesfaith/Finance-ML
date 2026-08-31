"""
Client financial-statement loader.

Locates the CSV layer in data/client_fs/, verifies every required file and
column, runs all validation rules, and returns clean, type-coerced
DataFrames plus the full list of issues found.

Design rules (spec sections 24, 27, 30):
  * Raw source files are IMMUTABLE — this module only ever reads them.
  * Critical validation failures never pass silently: with strict=True
    (the default) any ERROR raises ClientFSValidationError listing every
    problem found.
  * Source lineage (source_file / source_sheet / source_row / load_id /
    load_timestamp) is carried through untouched so any number can be
    traced back to the exact cell it came from.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from financials import validator
from financials.schemas import ALL_SCHEMAS

log = logging.getLogger("financials.loader")

# Repo root is two levels up from this file (src/financials/loader.py).
BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = BASE_DIR / "data" / "client_fs"


class ClientFSValidationError(Exception):
    """Raised when a strict load finds ERROR-severity issues."""

    def __init__(self, issues):
        self.issues = issues
        errors = [i for i in issues if i.severity == "ERROR"]
        lines = "\n".join(f"  {i}" for i in errors[:20])
        more = "" if len(errors) <= 20 else f"\n  ... and {len(errors) - 20} more"
        super().__init__(
            f"client financial-statement load failed with "
            f"{len(errors)} error(s):\n{lines}{more}"
        )


@dataclass
class LoadResult:
    """Everything a load produced: the data, the issues, and the audit trail."""

    data_dir: Path
    tables: dict = field(default_factory=dict)   # table name -> DataFrame
    issues: list = field(default_factory=list)   # validator.Issue records
    load_ids: tuple = ()                         # distinct load_id values seen

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == "WARNING"]

    def issues_frame(self) -> pd.DataFrame:
        """Issues as a DataFrame — exportable for audit / Power BI later."""
        return pd.DataFrame(
            [
                {
                    "severity": i.severity,
                    "table": i.table,
                    "rule": i.rule,
                    "message": i.message,
                }
                for i in self.issues
            ],
            columns=["severity", "table", "rule", "message"],
        )

    def summary(self) -> str:
        lines = [f"Client FS load from {self.data_dir}"]
        for name, df in self.tables.items():
            lines.append(f"  {name:<18} {len(df):>5} rows")
        lines.append(
            f"  issues: {len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        )
        if self.load_ids:
            lines.append(f"  load_id(s): {', '.join(self.load_ids)}")
        return "\n".join(lines)


def _read_csv(path: Path) -> pd.DataFrame:
    """
    Read every cell as a string, with no NaN guessing.

    Validation needs to see the file exactly as written — pandas' automatic
    type inference would hide problems (e.g. turning a blank into NaN or an
    account code '0400' into the number 400). Types are applied later, only
    after the values have been checked.
    """
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def _coerce_types(df: pd.DataFrame, schema) -> pd.DataFrame:
    """Convert validated columns to their real dtypes for downstream use."""
    out = df.copy()
    for col in schema.columns:
        if col.name not in out.columns:
            continue
        values = out[col.name].astype(str).str.strip()
        if col.kind in ("number", "integer"):
            out[col.name] = pd.to_numeric(values.replace("", pd.NA), errors="coerce")
        elif col.kind == "date":
            out[col.name] = pd.to_datetime(
                values.replace("", pd.NA), format="%Y-%m-%d", errors="coerce"
            )
        else:
            out[col.name] = values
    return out


def load_client_fs(data_dir=None, strict=True) -> LoadResult:
    """
    Load and validate the full client financial-statement CSV layer.

    strict=True (default): raise ClientFSValidationError if any ERROR is
    found. strict=False: return the LoadResult with all issues attached,
    for tooling that wants to display problems rather than stop.
    """
    data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    result = LoadResult(data_dir=data_dir)

    log.info("loading client financial statements from %s", data_dir)

    # 1. Every required file must exist before anything else is checked.
    missing_files = [s.filename for s in ALL_SCHEMAS if not (data_dir / s.filename).exists()]
    if missing_files:
        result.issues.append(validator.Issue(
            "ERROR", "client_fs", "missing_file",
            f"required file(s) not found in {data_dir}: {missing_files}",
        ))
        if strict:
            raise ClientFSValidationError(result.issues)
        return result

    # 2. Read and validate each table on its own.
    raw_frames = {}
    for schema in ALL_SCHEMAS:
        path = data_dir / schema.filename
        df = _read_csv(path)
        raw_frames[schema.table] = df
        table_issues = validator.validate_table(df, schema)
        result.issues.extend(table_issues)
        log.info(
            "read %-18s %5d rows, %d issue(s)",
            schema.filename, len(df), len(table_issues),
        )

    # 3. Cross-table rules (IDs resolve, accounts mapped, FX present).
    result.issues.extend(validator.validate_cross_table(raw_frames))

    # 4. Coerce dtypes only on tables whose shape is usable.
    for schema in ALL_SCHEMAS:
        result.tables[schema.table] = _coerce_types(raw_frames[schema.table], schema)

    # 5. Record the audit trail.
    raw = raw_frames["client_fs_raw"]
    if "load_id" in raw.columns:
        result.load_ids = tuple(sorted(set(raw["load_id"]) - {""}))

    log.info(
        "load finished: %d error(s), %d warning(s)",
        len(result.errors), len(result.warnings),
    )

    if strict and result.errors:
        raise ClientFSValidationError(result.issues)

    return result

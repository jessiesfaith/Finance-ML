# Pytest configuration for the whole repository.
#
# Adds src/ to Python's import path so tests (and scripts) can do
# `from financials import loader` without installing a package.
# This keeps the project runnable with nothing but `pip install -r requirements.txt`.

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
# agents/ lives at the repo root (spec section 23 structure).
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def assert_matches_committed(frame, path):
    """
    Rebuild-lock assertion, portable across platforms: a committed CSV
    must match a fresh in-memory rebuild - text exactly, numbers to
    within float tolerance. (numpy/pandas versions format floating-point
    text slightly differently across OSes, so byte-exact comparison of
    unrounded sums is too brittle - discovered on Windows/Python 3.13.)
    """
    import io

    import pandas as pd

    committed = pd.read_csv(path, keep_default_na=False, na_values=[""])
    fresh = pd.read_csv(
        io.StringIO(frame.to_csv(index=False)),
        keep_default_na=False, na_values=[""],
    )
    pd.testing.assert_frame_equal(
        committed, fresh, check_exact=False, rtol=1e-9, atol=1e-9,
        obj=str(path),
    )

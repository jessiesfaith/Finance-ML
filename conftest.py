# Pytest configuration for the whole repository.
#
# Adds src/ to Python's import path so tests (and scripts) can do
# `from financials import loader` without installing a package.
# This keeps the project runnable with nothing but `pip install -r requirements.txt`.

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
financials — the client financial-statement pipeline.

Phase 1 modules:
    schemas    — the schema registry: what every client_fs CSV must look like
    validator  — deterministic validation rules that produce structured issues
    loader     — locates, reads, validates, and type-coerces the CSV layer

Later phases add: account mapping, sign normalization, FX translation,
consolidation, controls, adjustments, outliers, NWC/UFCF/ROIC, shares,
pro forma, and the analyst-review agent.
"""

from financials.loader import load_client_fs, ClientFSValidationError, LoadResult
from financials.validator import Issue

__all__ = [
    "load_client_fs",
    "ClientFSValidationError",
    "LoadResult",
    "Issue",
]

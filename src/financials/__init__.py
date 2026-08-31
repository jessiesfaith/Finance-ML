"""
financials — the client financial-statement pipeline.

Phase 1 modules:
    schemas         — the schema registry: what every client_fs CSV must look like
    validator       — deterministic validation rules that produce structured issues
    loader          — locates, reads, validates, and type-coerces the CSV layer
    sign_normalizer — the canonical sign convention policy

Phase 2 modules:
    account_mapper        — resolves raw rows to standard accounts
                            (company-specific overrides beat reusable defaults)
    normalized_statements — builds client_fs_normalized.csv (REPORTED view)
                            with canonical signs and a per-row audit trail

Later phases add: account mapping, sign normalization, FX translation,
consolidation, controls, adjustments, outliers, NWC/UFCF/ROIC, shares,
pro forma, and the analyst-review agent.
"""

from financials.account_mapper import resolve_mapping
from financials.loader import load_client_fs, ClientFSValidationError, LoadResult
from financials.normalized_statements import (
    build_normalized_statements,
    write_normalized_statements,
)
from financials.sign_normalizer import normalize_sign
from financials.validator import Issue

__all__ = [
    "load_client_fs",
    "ClientFSValidationError",
    "LoadResult",
    "Issue",
    "normalize_sign",
    "resolve_mapping",
    "build_normalized_statements",
    "write_normalized_statements",
]

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

Phase 3 modules:
    fx_translation — deterministic currency translation (correct rate type
                     per item, RE roll-forward, CTA plug, per-row variance
                     vs the source's own translation)
    consolidation  — entity roll-up with an explicit elimination layer,
                     written to entity_consolidation.csv

Phase 4 modules:
    controls — deterministic financial controls (balance sheet, cash walk,
               NI tie, RE/debt rolls, OCI coverage, consolidation, FX,
               source integrity) producing PASS/REVIEW/FAIL records in
               control_checks.csv; exceptions are surfaced, never fixed

Later phases add: account mapping, sign normalization, FX translation,
consolidation, controls, adjustments, outliers, NWC/UFCF/ROIC, shares,
pro forma, and the analyst-review agent.
"""

from financials.account_mapper import resolve_mapping
from financials.consolidation import consolidate, write_consolidation
from financials.controls import (
    apply_consolidation_status,
    results_frame,
    run_all_controls,
    write_control_checks,
)
from financials.fx_translation import translate_statements
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
    "translate_statements",
    "consolidate",
    "write_consolidation",
    "run_all_controls",
    "results_frame",
    "apply_consolidation_status",
    "write_control_checks",
]

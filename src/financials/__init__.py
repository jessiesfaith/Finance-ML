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

Phase 5 modules:
    nwc       — operating NWC components + delta (classification-driven,
                cash/debt excluded by design)
    ufcf      — income walk, NOPAT (normalized vs reported effective tax
                kept separate), driver-based UFCF forecast
    scenarios — analyst-assumption layer loader (data/scenarios/):
                scenario_master, driver_master, scenario_assumptions

Phase 6 modules:
    net_debt          — transparent net-debt build (classification-elected
                        membership; not every liability is debt)
    invested_capital  — invested-capital components + configurable-basis ROIC
    shares            — treasury-stock-method dilution; stated counts must
                        reproduce from their own inputs
    capital_structure — market-value E/D weights (derived, not assumed)

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
from financials.capital_structure import market_value_weights
from financials.invested_capital import invested_capital_components, roic
from financials.net_debt import net_debt_components
from financials.nwc import nwc_components
from financials.shares import load_shares_dilution, treasury_stock_method
from financials.scenarios import load_scenarios
from financials.sign_normalizer import normalize_sign
from financials.ufcf import (
    build_ufcf_forecast,
    income_walk,
    write_ufcf_forecast,
)
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
    "nwc_components",
    "load_scenarios",
    "income_walk",
    "build_ufcf_forecast",
    "write_ufcf_forecast",
    "net_debt_components",
    "invested_capital_components",
    "roic",
    "treasury_stock_method",
    "load_shares_dilution",
    "market_value_weights",
]

# Client Financial-Statement CSV Schemas

Plain-language reference for every file in `data/client_fs/`. The
machine-readable version of this document is `src/financials/schemas.py` —
the loader and validator enforce exactly what is written there.

All bundled data is **synthetic test data** for COMP001 "Example Company
Inc." — a fictional company invented to exercise the pipeline. No real
company's financials appear anywhere in this repository.

---

## The three kinds of information (kept separate by design)

| Kind | Lives in | Examples |
|---|---|---|
| A. Client/company financial data | `client_fs_raw.csv` | revenue, AR, debt, equity |
| B. External market data | `fx_rates.csv` (later: rates, ERP, beta) | FX rates, Treasury yields |
| C. Analyst assumptions | *(later phases: adjustments, forecasts)* | normalized tax rate, terminal growth |

Raw source tables never mix these.

---

## company_master.csv — one row per company

| Column | Meaning |
|---|---|
| company_id | unique ID (key), e.g. COMP001 |
| company_name | legal/display name |
| reporting_currency | currency the consolidated statements are presented in |
| fiscal_year_end | MM-DD |
| accounting_standard | US_GAAP, IFRS, … |
| source_system | where the data came from |
| industry / subindustry | classification for future benchmarking |
| country | domicile |
| active_flag | Y/N |

## entity_master.csv — one row per legal entity

Key: `entity_id`. Supports parents, subsidiaries with any functional
currency and ownership %, and **elimination entities** (rows that hold
intercompany eliminations rather than a real business).

Notable columns: `parent_entity_id` (blank for the top parent — this forms
the ownership tree), `ownership_pct`, `functional_currency`,
`consolidation_method` (FULL / EQUITY_METHOD / NOT_CONSOLIDATED /
ELIMINATION), `elimination_entity_flag`.

## period_master.csv — one row per reporting period

Key: `period_id`. `period_type` supports ANNUAL / QUARTERLY / MONTHLY;
`is_historical` and `is_forecast` flags let actuals and forecast periods
coexist (FY2026 in the fixture is a forecast shell). `days_in_period`
supports future per-day metrics (DSO, DIO, DPO).

## account_mapping.csv — the translation dictionary

Key: `(company_id, source_system, source_account_code)`. Maps every
account name a source system uses ("Accounts Receivable", "Trade
Receivables", "Forderungen aus Lieferungen und Leistungen") onto ONE
standardized concept (`accounts_receivable`), so no company's chart of
accounts is ever hard-coded into analysis logic.

**Why the compound key:** ERP systems, subsidiaries, and acquired
companies reuse account codes — the fixture itself has `4000` meaning
Revenue in NETSUITE and Other Operating Income in DATEV. An account is
therefore identified by system + code, never code alone. `company_id`
blank = a reusable default mapping that applies to any company; a row
with a `company_id` is company-specific and overrides the default for
that company (the validator honors applicability now; the Phase 2 mapper
implements the override resolution).

Each row also carries the account's sign treatment —
`normal_balance` (DR/CR), `sign_multiplier` (the account's canonical
sign), and `source_sign_convention` (MAGNITUDE/SIGNED — how this source
presents the number); see **docs/SIGN_CONVENTION.md** — plus
classification tags consumed by later phases: `nwc_classification`
(which accounts form operating NWC; cash and debt are explicitly
EXCLUDED), `ufcf_classification` (D&A add-back, CapEx),
`roic_classification` (invested-capital build-up), and share / cash-flow /
OCI classifications. `review_status` records that an analyst approved the
mapping.

## fx_rates.csv — currency translation inputs

Key: `(period_id, from_currency, to_currency, rate_type)`. Rate types:
**AVERAGE** (income statement), **CLOSING** (balance sheet), **HISTORICAL**
(certain equity accounts). The source is pluggable: today the rows are
fixture data; a live FX feed later just writes more rows with its own
`source` value. Rates are data, never constants in Python code.

## client_fs_raw.csv — the raw financial statements

Key: `(company_id, entity_id, period_id, statement_type,
source_account_code, scenario)`.

Statement types: IS, BS, CFS, OCI, EQUITY, SEGMENT, CONSOL — the list
lives in one place (`schemas.py`) so new types are one-line additions.
Each row also names its `source_system` (NETSUITE, DATEV, …) so accounts
resolve against the mapping per system.

**Raw vs computed amounts:** `amount_local` is the authoritative source
amount. `amount_reporting` is the *source-reported* reporting-currency
figure, preserved for reconciliation only — Phase 3's FX engine computes
its own `calculated_reporting_amount` from `amount_local` × the correct
rate type and reports an FX translation variance against the
source-reported figure; it never treats the client's own translation as
the answer.

Every row carries **full source lineage**: `source_file`, `source_sheet`,
`source_row`, `source_note`, `load_id`, `load_timestamp`. That is what
makes every downstream number traceable back to a cell in a source
workbook (spec section 30).

This file is **immutable**: the loader only reads it, the analyst agent
will never write to it, and adjustments live in their own tables (Phase 8).

---

## client_fs_normalized.csv — the normalized statements (OUTPUT, Phase 2)

Written by `python src/build_client_fs_normalized.py` — never edited by
hand and never a required input. One normalized row per raw row (no
aggregation), so lineage survives: `source_system` +
`source_account_code` + `load_id` lead straight back to the raw row and
from there to the source file / sheet / row.

Each row carries the standardized account (`standard_account_id`,
`statement_section`), the canonical-sign `amount_reporting`, and the full
sign audit trail: `amount_source` (as the source presented it) →
`sign_multiplier` + `source_sign_convention` (the rule) →
`amount_reporting` (the canonical result). Under the canonical convention
subtotals are sums: IS rows sum to net income, CFS rows sum to the change
in cash, and assets − liabilities − equity nets to zero — verified on
every build and in tests.

`reported_or_adjusted` is REPORTED for all rows today; the Phase 8
adjustment engine adds ADJUSTED rows referencing `adjustment_id`, and the
`include_in_normalized` / `include_in_proforma` flags (YES/NO/REVIEW)
drive the three views. Until the Phase 3 FX engine lands,
`amount_reporting` derives from the source-reported reporting-currency
amount (decision #19/#22).

## Canonical files vs future ingestion adapters

These CSVs are the **canonical internal layer**, so their required fields
are enforced strictly. Unexpected *additional* columns do **not** fail the
load — they raise a WARNING and are ignored downstream, so a file carrying
extra useful information still loads.

Real client files will not arrive in canonical shape, and clients will
never be asked to delete columns to make a load work. The eventual flow is:

```
arbitrary client/source file → ingestion adapter → canonical internal schema
```

Adapters (a future phase) translate whatever a client exports — extra
columns, different names, different layouts — into these canonical files,
preserving lineage. The canonical layer stays strict; the adapters absorb
the messiness.

## Validation rules (Phase 1)

Run on every load; any ERROR refuses the load (nothing fails silently):

| Rule | Severity | Meaning |
|---|---|---|
| missing_file | ERROR | a required CSV is absent |
| missing_columns | ERROR | a schema column is absent |
| unexpected_columns | WARNING | extra column (ignored downstream) |
| missing_value | ERROR | blank cell in a required column |
| bad_type | ERROR | value doesn't parse as number/integer/date |
| invalid_value | ERROR | value outside the allowed list (e.g. statement_type) |
| duplicate_key | ERROR | two rows share a table's unique key |
| unknown_company / unknown_entity / unknown_period | ERROR | ID not in its master table |
| unknown_parent_entity | ERROR | ownership tree points at a missing entity |
| unmapped_account | ERROR | raw account with no account_mapping row |
| missing_fx_rate | ERROR | foreign-currency row with no FX rate for its period |

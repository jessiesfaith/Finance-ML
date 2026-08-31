# Market / Macro Data Layer — Architecture Proposal (finance-ml)

**Scope:** architecture only. No code, no fabricated market values. Everything below follows the conventions already established in `/home/user/finance-ml`: a strict CSV canonical layer, a machine-readable schema registry (`src/financials/schemas.py`), loader + validator that surface every issue and never fix anything silently, append-only facts with full lineage, methodology in `docs/`, and Power BI reading only curated `reports/*.csv` (docs/POWERBI_CONTRACT.md).

**What this replaces:** today market data is `data/raw/macro_history.csv`, a 103-row synthetic random walk written by `src/generate_history.py` (seed 42) with five metrics baked into column names (`treasury_2y`, `treasury_10y`, `fed_funds`, `cpi`, `unemployment`) and two derived columns. The risk-free rate is *implicitly* an ML prediction of the synthetic 10Y (`models/train_treasury_model.py` → `models/export_finance_report.py`). Decision #5 in `docs/DECISIONS.md` already schedules live data for Phase 13; this proposal is the target architecture that phase lands on.

**Design principles inherited from the repo** (each maps to an existing decision):

| Principle | Precedent |
|---|---|
| Wide "one column per metric" files don't scale; facts are long/keyed rows | `client_fs_raw` vs the old wide `macro_history.csv` |
| Rates/values are **data with a source**, never constants in code | fx_rates.csv, DECISIONS #26 |
| Raw facts are immutable & append-only; derived layers are rebuilt OUTPUTs | client_fs_raw vs client_fs_normalized, DECISIONS #23 |
| Methodology lives in one documented place, selectable, not implicit | `rate_type_for`, docs/FX_AND_CONSOLIDATION.md |
| The three kinds of information stay separate: client data / external market data / analyst assumptions | docs/SCHEMAS.md "three kinds" table |
| Validation reads text first, ERRORs block, WARNINGs surface | loader/validator, DECISIONS #12–13 |

---

## 1. Core tables

New directory: **`data/market/`** (see §6 for why it is not `data/client_fs/` and not `data/raw/`).

### 1.1 `data/market/market_metric_master.csv` — one row per metric (reference data)

Key: `(metric_id)`.

| Column | Kind | Notes |
|---|---|---|
| `metric_id` | text, key | stable snake_case ID, domain-prefixed (`ust_10y`, `cpi_headline_idx`, `ig_oas`) — the join key everywhere; never renamed |
| `metric_name` | text | display name ("10-Year Treasury Constant Maturity Yield") |
| `category` | text, controlled vocab | `INFLATION, MONETARY_POLICY, TREASURY_CURVE, REAL_RATES_BREAKEVENS, GROWTH, LABOR, CREDIT, EQUITY_RISK, FX_MARKET, BENCHMARKING` — a `MARKET_CATEGORIES` tuple in the schema registry, same idiom as `STATEMENT_TYPES` |
| `unit` | text, controlled vocab | `PCT` (4.25 means 4.25%), `INDEX`, `THOUSANDS`, `PCT_POINTS` (spreads), `CCY_PER_CCY` — values stored **as the source publishes them**, never rescaled on ingest (mirrors "raw amounts are never modified", DECISIONS #18); percent→decimal conversion belongs to the calculation layer only |
| `frequency` | text, controlled vocab | `DAILY, WEEKLY, MONTHLY, QUARTERLY` — the metric's *native* frequency |
| `seasonal_adjustment` | text, controlled vocab | `SA, NSA, NA` — spec addition (justified: CPI/PCE/PAYEMS exist in both forms and mixing them is a classic error) |
| `preferred_source` | text | `FRED`, `SYNTHETIC`, later `TREASURY`, `SEC` — which source wins in the current view when several report the same metric |
| `source_series_id` | text | exact upstream ID (`DGS10`, `CPIAUCSL`); blank for derived metrics |
| `derived_flag` | flag Y/N | spec addition: `Y` = computed by us, not ingested |
| `derivation_rule` | text, required=False | for derived metrics: plain-language formula naming input `metric_id`s ("yoy_pct_change(cpi_headline_idx, 12)") — the machine version lives in one Python function per rule, like `rate_type_for` |
| `description` | text | teaching-style plain-language description (repo idiom) |
| `active_flag` | flag Y/N | matches company/entity masters |

### 1.2 `data/market/market_observations.csv` — append-only fact history

Key: `(metric_id, observation_date, source, retrieval_timestamp)`.

| Column | Kind | Notes |
|---|---|---|
| `metric_id` | text | must exist in metric master (cross-table rule `unknown_metric`, ERROR) |
| `observation_date` | date | the date the value is *for* (period-end for monthly/quarterly series) |
| `value` | number | as published; no rescaling |
| `unit` | text | must equal the master's unit (`unit_mismatch`, ERROR) |
| `source` | text | `FRED`, `SYNTHETIC`, … |
| `source_reference` | text | lineage: series ID + release/vintage reference, or for synthetic rows `src/generate_history.py seed 42` — the market-layer equivalent of `source_file/source_sheet/source_row` |
| `retrieval_timestamp` | text (ISO datetime) | when we fetched it — this is the **vintage axis** |
| `frequency` | text | echo of native frequency (guards against accidentally loading a resampled file; `frequency_mismatch`, ERROR) |
| `revision_status` | text, controlled vocab | `PRELIMINARY, REVISED, FINAL, SYNTHETIC` — a `REVISION_STATUSES` tuple |

**Append-only semantics (never overwrite):**
- Ingestion only ever *adds* rows. When a re-fetch finds that FRED revised a value (GDP and payrolls do this constantly), the loader writes a **new row** with a newer `retrieval_timestamp` and `revision_status=REVISED`. The old row stays forever.
- The **current view** ("latest known value per metric/date") is *computed*, never stored: for each `(metric_id, observation_date)` take the row from the `preferred_source` with the max `retrieval_timestamp`. A **point-in-time view** (what did we know on date X — exactly what an as-of valuation needs) filters `retrieval_timestamp <= X` first. Both are functions in the market package, not files.
- Enforcement follows the repo's existing lock-test idiom (DECISIONS #23): a test asserts every row of the previously committed file appears unchanged in the new file (`non_append_change`, ERROR). Nothing is ever silently corrected.

### 1.3 Derived analytics: `data/market/market_derived.csv` — an OUTPUT, mirroring `client_fs_normalized`

Derived metrics (YoY inflation, curve spreads, computed breakevens, policy-stance indicator) are **registered in the metric master** (`derived_flag=Y`) but stored in a separate rebuilt OUTPUT file — never mixed into `market_observations`, which holds only ingested facts. Same raw-vs-computed split as `amount_local` vs `calculated_reporting_amount` (DECISIONS #19). Columns: `metric_id, observation_date, value, unit, derivation_rule, input_metric_ids, input_retrieval_max, build_id, build_timestamp` — full lineage from derived number back to the exact input vintages. Rebuilt deterministically by a build script (like `build_client_fs_normalized.py`); locked to a fresh rebuild by a test.

### 1.4 How scenario overlays reference metrics *without touching observations*

Scenarios are **kind C — analyst assumptions** (docs/SCHEMAS.md's three-kinds table) and live in their own directory, `data/scenarios/` (§5). A scenario overlay row references a `metric_id` plus an override (`ABSOLUTE` value or `DELTA` vs baseline). The scenario engine resolves, at computation time: *baseline* = point-in-time current view of observations as of the scenario's `as_of_date`, *then* applies overlays in memory. `market_observations.csv` is read-only to the scenario engine — same immutability contract as `client_fs_raw.csv` (adjustments live in their own tables; facts are never edited).

---

## 2. Per-domain architecture

Legend for FRED IDs: **[OK]** = confident from knowledge; **[VERIFY]** = plausible but confirm against FRED at implementation time. All IDs below should be re-verified once during Phase 13 ingestion-adapter development anyway (the adapter should fail loudly on an unknown series, per repo philosophy).

### 2.1 Inflation

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `cpi_headline_idx` | CPI-U all items, SA | **CPIAUCSL** [OK] | monthly, INDEX |
| `cpi_core_idx` | CPI-U less food & energy, SA | **CPILFESL** [OK] | monthly, INDEX |
| `pce_headline_idx` | PCE price index, SA | **PCEPI** [OK] | monthly, INDEX |
| `pce_core_idx` | PCE less food & energy, SA | **PCEPILFE** [OK] | monthly, INDEX |

**Derived (Phase 13.2):** `cpi_headline_yoy`, `cpi_core_yoy`, `pce_headline_yoy`, `pce_core_yoy` = 12-month % change of the index — **YoY first** (robust, what the Fed talks about); MoM-annualized variants later (noisier, needs care with SA). Note the current synthetic `cpi` column is *already a rate*, so the migration is a semantic change: the new layer stores official *index levels* and derives rates, which is the auditable direction.

**Purpose:** shapes scenario narratives and **company operating assumptions** — pricing power (revenue growth), input-cost inflation (margin), nominal terminal-growth sanity. Core-vs-headline gap signals whether inflation is energy noise or broad. Cross-checks market breakevens (§2.4).
**Must NOT:** CPI/PCE never plug into WACC — not into the risk-free rate, not as an "inflation premium" added to anything. Nominal Treasury yields already embed expected inflation; adding CPI would double count. Inflation reaches the DCF only through (a) rate-path scenario design and (b) explicit operating-assumption deltas in `scenario_assumptions`.

### 2.2 Fed / monetary policy

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `fed_funds_eff` | Effective fed funds rate | **FEDFUNDS** [OK] (monthly avg; daily = **DFF** [OK]) | monthly, PCT |
| `fed_target_upper` / `fed_target_lower` | Target range bounds | **DFEDTARU / DFEDTARL** [OK] | daily, PCT |

**Derived:** `policy_stance` = classification of the **real policy rate** (`fed_funds_eff − pce_core_yoy`) vs a neutral band → `RESTRICTIVE / NEUTRAL / ACCOMMODATIVE`. The neutral band is a *documented methodology parameter* in docs/MARKET_DATA.md (approved by Jessica, revisable), not a hard-coded constant — there is no clean official FRED series for r-star [VERIFY if one is wanted].
**Purpose:** anchors the short end of the curve; the primary *driver variable* when designing Lower/Base/Higher-Rate scenarios; base rate for **floating-rate debt** in the Phase 6 debt schedule.
**Must NOT:** never a direct WACC input. Fed funds is an overnight rate; discounting long-dated UFCF at anything keyed to it is a maturity mismatch. It influences WACC only *through* the curve (§2.3).

### 2.3 Treasury curve — a structure, not scattered fields

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `ust_3m` | 3-month CM yield | **DGS3MO** [OK] | daily, PCT |
| `ust_2y` | 2-year CM yield | **DGS2** [OK] | daily, PCT |
| `ust_5y` | 5-year CM yield | **DGS5** [OK] | daily, PCT |
| `ust_10y` | 10-year CM yield | **DGS10** [OK] | daily, PCT |
| `ust_30y` | 30-year CM yield | **DGS30** [OK] | daily, PCT |

The 3-month tenor uses **DGS3MO** (constant-maturity basis, consistent with the other tenors) rather than the secondary-market bill rate (TB3MS monthly / DTB3 daily [OK]) — mixing bases inside one curve is a subtle error; the choice is recorded in docs/MARKET_DATA.md.

**Curve as structure:** tenors are separate metrics in the long fact table, but the market package exposes a **curve accessor**: `yield_curve(as_of)` returning ordered `(tenor_years, metric_id, value, observation_date)` — tenor is an ordered dimension. Consumers (rf policy, charts, classification) use the accessor; nothing downstream hard-codes "the 2y column" again.

**Derived:** `curve_spread_10y_2y` = `ust_10y − ust_2y`; `curve_spread_10y_3m` = `ust_10y − ust_3m` (FRED's pre-computed **T10Y2Y / T10Y3M** [OK] are ingested only as *cross-check controls* against our own derivation — variance beyond tolerance → REVIEW, never silently reconciled, per DECISIONS #24 philosophy). `curve_shape` classification → `NORMAL / FLAT / INVERTED`; the band thresholds are documented methodology parameters (e.g. the conventional "within ±X bp = flat" — X is set in docs, not invented here).
**Purpose:** **the home of the risk-free rate** (§3) — the one domain that legitimately feeds WACC directly. Curve shape drives scenario design (inversion → recession-flavored operating deltas) and the maturity-matching discussion in the learning guide.

### 2.4 Real rates & breakevens

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `tips_5y` | 5-year TIPS CM (real) | **DFII5** [OK] | daily, PCT |
| `tips_10y` | 10-year TIPS CM (real) | **DFII10** [OK] | daily, PCT |
| `breakeven_5y` | 5-year breakeven | **T5YIE** [OK] | daily, PCT |
| `breakeven_10y` | 10-year breakeven | **T10YIE** [OK] | daily, PCT |

**Derived:** `breakeven_10y_calc` = `ust_10y − tips_10y` (and 5y analog). Since T10YIE is constructed exactly this way, ingested-vs-derived must agree within a small tolerance — a free **integrity control** proving ingestion and derivation are both healthy (same spirit as the accounting identity tests). This *replaces* the synthetic `real_10y_proxy = treasury_10y − cpi`, which mixes a forward-looking yield with realized inflation; the docs entry should teach exactly that distinction.
**Purpose:** checks scenario coherence — a "Higher Rate" scenario's implied inflation vs breakevens; terminal-growth sanity (real growth vs real yields).
**Must NOT:** breakevens/real yields never modify WACC directly; the nominal curve already carries this information.

### 2.5 Economic growth (GDP)

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `real_gdp_level` | Real GDP (chained $) | **GDPC1** [OK] | quarterly, INDEX-like level |
| `real_gdp_growth` | Real GDP % chg, SAAR | **A191RL1Q225SBEA** [VERIFY — plausible BEA-style ID; alternatively derive from GDPC1] | quarterly, PCT |

Recommendation: ingest GDPC1 and **derive** the growth rate ourselves (annualized q/q and YoY variants) — derivations are reproducible and vintage-aware; ingest the official growth series only as a cross-check. GDP is the poster child for `revision_status` (advance → second → third estimate → annual revisions): the vintage axis in §1.2 exists largely for this series.
**Purpose:** volume/demand context for **revenue-growth assumptions**; ceiling control on terminal growth (terminal g materially above long-run nominal GDP growth → REVIEW flag in the Phase 5/6 valuation controls).
**Must NOT:** GDP never plugs into WACC.

### 2.6 Labor

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `unemployment_rate` | U-3 unemployment, SA | **UNRATE** [OK] | monthly, PCT |
| `payrolls_level` | Total nonfarm payrolls, SA | **PAYEMS** [OK] | monthly, THOUSANDS (a *level*) |

**Derived:** `payrolls_mom_chg` = monthly diff of PAYEMS (the headline "jobs number" is the *change*, not the level — worth a teaching note). Heavily revised → vintage rows again.
**Purpose:** demand-side and wage-cost context for operating assumptions; coherence check on scenarios (the current fixture scenarios in `models/export_finance_report.py` pair higher rates with *lower* unemployment — an overheating narrative; the scenario engine should force that narrative to be stated in `scenario_assumptions.rationale`).
**Must NOT:** never a WACC input (its role as an ML feature for the 10Y prediction is a *rates-path modeling* use, which is fine — see §3).

### 2.7 Credit markets

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `ig_oas` | ICE BofA US Corporate OAS | **BAMLC0A0CM** [OK] | daily, PCT |
| `hy_oas` | ICE BofA US High Yield OAS | **BAMLH0A0HYM2** [OK] | daily, PCT |

**Derived:** `credit_risk_appetite` = `hy_oas − ig_oas` (risk-appetite gauge); optional rating-bucket OAS series later (BAML per-rating IDs exist [VERIFY individual IDs when needed]).
**Purpose:** this domain **legitimately touches WACC — via one channel only**: it is the market **benchmark corridor for the company-specific credit spread**. The company's spread (today the constant `credit_spread = 0.020` in the models/ ladder; from Phase 6 an explicit assumption tied to its leverage/rating) is an *analyst assumption validated against* IG/HY OAS: outside the corridor for its bucket → REVIEW. Scenario spread-widening in Higher Rate flows through this channel into cost of debt.
**Must NOT:** market OAS is never *silently substituted* for the company spread, and index OAS never feeds cost of equity.

### 2.8 Equity risk

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `sp500_level` | S&P 500 index close | **SP500** [OK — caveat: FRED redistributes only ~10 years of history under license] | daily, INDEX |
| `vix_close` | CBOE VIX close | **VIXCLS** [OK] | daily, INDEX |

**Derived:** `equity_vol_regime` classification from VIX bands (`CALM / ELEVATED / STRESSED`; band edges = documented methodology parameters); index drawdown-from-peak later.
**Purpose:** context for the **ERP discussion** — the ERP remains an explicit, documented, *selectable* assumption (today 4.5% hard-coded). VIX regime may justify which ERP the analyst selects, with the rationale recorded.
**Must NOT (explicit rule):** **VIX never plugs into WACC**, and no formula mechanically maps VIX→ERP. S&P level never enters valuation math; it is chart/context only.

### 2.9 FX — market FX, distinct from accounting `fx_rates.csv`

| metric_id | Series | ID | Freq/Unit |
|---|---|---|---|
| `usd_broad_idx` | Nominal broad USD index | **DTWEXBGS** [OK — the current goods-and-services index; predecessor TWEXB is discontinued] | daily, INDEX |
| `eur_usd_spot` | USD per 1 EUR (H.10) | **DEXUSEU** [OK — quoted as USD per EUR; the quoting convention must be stated in the master's description] | daily, CCY_PER_CCY |

**Separation of duties:** `data/client_fs/fx_rates.csv` remains the **accounting translation layer** — period-keyed AVERAGE/CLOSING/HISTORICAL rates consumed by `rate_type_for` (DECISIONS #26). Market FX in `data/market/` is **analytical context and a scenario driver** (dollar-strength narrative → translated revenue mix). One permitted bridge, one direction only: a Phase 13 adapter may *derive candidate* `fx_rates.csv` rows (period average/closing computed from daily DEXUSEU) written through the existing fx_rates schema with a distinguishing `source` (e.g. `FRED_DERIVED`) and `source_reference` naming the input series and window — subject to the existing missing_fx_rate validation and analyst review. The market layer never reads the accounting layer.

### 2.10 Industry / peer benchmarking (architecture only — Phase 14)

No values, no peers invented now. Two tables, same pattern as §1:

- **`data/market/benchmark_metric_master.csv`** — key `(benchmark_metric_id)`: ratio definitions (`ebit_margin`, `revenue_growth`, `nwc_pct_revenue`, `capex_pct_revenue`, `beta_levered`, …), each with a `derivation_rule` naming the standard-account/UFCF inputs so definitions are identical for the company and for peers (comparability is the whole game).
- **`data/market/benchmark_observations.csv`** — key `(benchmark_metric_id, peer_group_id, statistic, period_id, source, retrieval_timestamp)`, where `statistic` is a controlled vocab: **`COMPANY, PEER_MEDIAN, INDUSTRY_MEDIAN, P25, P75`**. Plus `peer_group_master.csv` (peer_group_id → definition, membership source, `company_master.industry/subindustry` as the default grouping key — those columns were placed in company_master "for future benchmarking" exactly for this).

Source: the Phase 12 SEC pipeline computes peer statistics and appends here; COMPANY rows come from the internal pipeline (Phases 5–6 outputs) through the same derivation rules. Purpose: context for operating assumptions and beta selection; a company assumption outside its industry's P25–P75 band → REVIEW flag, never auto-corrected.

---

## 3. Risk-free rate methodology (explicit & selectable)

**Problem:** today the risk-free rate is whatever `treasury_10y_model.joblib` (linear regression on synthetic seed-42 data) predicts, divided by 100, with no source, maturity, or date recorded. That is an *implicit* methodology.

**Proposal — `data/market/risk_free_policy.csv`**, key `(rf_methodology_id)`:

| Column | Notes |
|---|---|
| `rf_methodology_id` | e.g. `UST_10Y_SPOT` (default), `UST_10Y_AVG_3M`, `UST_30Y_SPOT`, `SYNTHETIC_ML_10Y` (legacy) |
| `source_metric_id` | → metric master (`ust_10y`, …); for the legacy method, the model file path in `source_reference` style |
| `maturity_years` | 10, 30, … — makes maturity-vs-forecast-horizon matching explicit and teachable |
| `observation_rule` | `SPOT_AS_OF` (latest observation ≤ valuation date, from the point-in-time view) or `TRAILING_AVG` + window |
| `description` / `active_flag` | repo idiom |

**Consumption contract:** every place a risk-free rate is used (scenario engine → WACC → report) records five fields: `rf_methodology_id, rf_source_metric_id, rf_maturity_years, rf_observation_date, rf_value` — so every WACC in every report row is reproducible down to a dated, sourced observation. This is the CAPM analog of the FX layer's "rate policy is code, rates are data" (#26).

- **Default for DCF:** `UST_10Y_SPOT` — 10Y constant maturity (DGS10) as of the valuation/scenario `as_of_date`, matching the 5-year explicit forecast + terminal value duration; the docs entry explains the 10Y-vs-30Y trade-off.
- **The ML model is repositioned, not deleted** (it's a learning-ladder artifact, protected by CLAUDE.md): it becomes a **rates-path scenario tool** — given a macro scenario (fed funds, 2y, CPI, unemployment) it *proposes* a 10Y level that an analyst may adopt as a scenario override in `scenario_assumptions` (with `source = SYNTHETIC_ML_10Y` until retrained on real history in Phase 13). It is never again the silent default source of rf. `SYNTHETIC_ML_10Y` stays in the policy table, clearly labeled, so "what changed when we went live" is demonstrable — a deliberate, visible switch, exactly like the #22→#25 FX cutover.
- Backward compatibility: `reports/finance_scenario_report.csv` keeps its locked 32 columns; the new rf lineage fields appear only in **new** report files (POWERBI_CONTRACT rule 3).

---

## 4. Dependency maps

Rule being enforced: **macro variables are context** — they influence *market variables* and *operating assumptions*; they are never direct WACC inputs. Structurally enforced by a `WACC_INPUT_CONTRACT` in the schema registry: the closed list of metric_ids/channels allowed to bind to WACC fields. A scenario row binding anything else to a WACC field fails validation (`invalid_wacc_binding`, ERROR).

### 4.1 MACRO → WACC

```
CONTEXT LAYER (never touches WACC directly)
  cpi_*_yoy, pce_*_yoy   gdp growth   unemployment, payrolls   vix, sp500
        │                    │                 │                   │
        └──────── shape scenario narratives & the rate path ───────┘
                              │  (analyst judgment, recorded in
                              ▼   scenario_assumptions.rationale)
MARKET VARIABLE LAYER (the only WACC feeders)
  UST curve (ust_3m … ust_30y)      ig_oas / hy_oas          fed_funds path
        │                                │                        │
        ▼ (risk_free_policy §3)          ▼ (benchmark corridor)   ▼ (floating base,
WACC INPUT LAYER                                                    Phase 6 debt schedule)
  risk_free_rate ────────────► cost_of_equity = rf + beta × ERP
  company credit spread ─────► cost_of_debt  = rf + company_spread
  beta ──────── assumption (Phase 14: peer-informed)
  ERP ───────── explicit documented assumption (VIX = context only)
  tax rate, E/D weights ── company data (Phase 6 capital structure)
        │
        ▼
  WACC = w_e·CoE + w_d·CoD·(1 − tax)

FORBIDDEN (validation ERROR): cpi/pce/gdp/unrate/payems/vix/sp500 → any WACC field.
```

### 4.2 MACRO → UFCF

```
  inflation (cpi/pce yoy) ──► pricing & input-cost deltas ──► revenue growth %, EBIT margin
  gdp growth ──────────────► volume/demand delta ─────────► revenue growth %
  labor (unrate, payrolls) ─► wage cost & demand deltas ───► EBIT margin, revenue growth %
  rates (curve, fed funds) ─► investment appetite,
                              customer payment behavior ───► CapEx % rev, NWC intensity
  market fx (usd idx, eurusd) ─► translated revenue/cost mix (multi-currency entities, via Phase 3 FX layer)
        │
        ▼   ALL of the above land as explicit rows in scenario_assumptions
            (COMPANY_DRIVER deltas) — macro never bypasses them to edit numbers
        ▼
  UFCF = NOPAT + D&A − ΔNWC − CapEx        (Phase 5 engine, driven by the drivers above)

  NOTE: interest expense is NOT in UFCF (unlevered by definition); rates reach the
  valuation twice, by different doors — operating drivers into UFCF (this map),
  and discounting via WACC (map 4.1). Keeping the doors separate is the point.
```

---

## 5. Scenario engine target

Directory `data/scenarios/` — analyst assumptions (kind C), separate from client data (A) and market facts (B).

### `scenario_master.csv` — key `(scenario_id)`

| Column | Notes |
|---|---|
| `scenario_id`, `scenario_name`, `description` | `Lower Rate` / `Base` / `Higher Rate` survive as the first three rows for PBI continuity |
| `scenario_type` | controlled vocab `BASE / UPSIDE / DOWNSIDE / STRESS / CUSTOM` |
| `as_of_date` | the market-data vintage: baseline values = point-in-time current view as of this date (§1.2) |
| `rf_methodology_id` | → risk_free_policy (§3) — the scenario picks its rf methodology explicitly |
| `narrative` | the macro story ("overheating: higher rates, tight labor") — makes coherence reviewable |
| `status` | `DRAFT / APPROVED / REJECTED` (mirrors `review_status`) |
| `scenario_sort` | replaces the hard-coded sort map in `export_finance_report.py` |
| `created_by`, `approved_by`, `created_timestamp` | audit trail idiom |

### `scenario_assumptions.csv` — key `(scenario_id, target_type, target_id, company_id, period_id)`

| Column | Notes |
|---|---|
| `scenario_id` | → master |
| `target_type` | **`MARKET_METRIC`** or **`COMPANY_DRIVER`** — one scenario coherently sets both |
| `target_id` | MARKET_METRIC → a `metric_id` (`ust_10y`, `ig_oas`, `fed_funds_eff`); COMPANY_DRIVER → a `driver_id` from a small `driver_master.csv` controlled vocabulary: `REVENUE_GROWTH_PCT, EBIT_MARGIN_PCT, NWC_PCT_REVENUE, CAPEX_PCT_REVENUE, DA_PCT_REVENUE, TAX_RATE_PCT, TERMINAL_GROWTH_PCT, CREDIT_SPREAD_PCT` |
| `company_id` | blank = applies to all companies (default-vs-override pattern from account_mapping) |
| `period_id` | blank = all forecast periods; set = period-specific path |
| `override_type` | `ABSOLUTE` (set the value) or `DELTA` (shift vs baseline) — DELTA is what keeps scenarios meaningful as market data refreshes |
| `value`, `unit` | the analyst's number (assumption, not fabricated market data) |
| `rationale`, `source` | why — e.g. "10Y path proposed by SYNTHETIC_ML_10Y", "margin −Xbp per rate-shock playbook" |

**How "Higher Rate" hits both WACC and UFCF:** one `scenario_id` carries MARKET_METRIC rows (10Y up → rf up → CoE and CoD up via §4.1; IG/HY OAS wider → company spread benchmark shifts; fed funds path up → floating debt cost) **and** COMPANY_DRIVER rows (revenue growth down, EBIT margin compressed, NWC intensity up as customers pay slower, CapEx trimmed → UFCF down via §4.2). Engine order: resolve baseline from observations at `as_of_date` → apply MARKET_METRIC overlays → resolve rf via policy → build WACC (contract-checked) → apply COMPANY_DRIVER overlays to the Phase 5 driver set → UFCF → DCF. Observations and client_fs files are never written — overlays live only in these two tables, so scenarios are diffable, reviewable rows, replacing the hard-coded dicts duplicated across the `models/` ladder (which stays untouched as the learning ladder, per DECISIONS #4).

---

## 6. Refresh independence

Market ingestion is a **separate module with a separate lifecycle** — market data refreshes daily/monthly; client FS loads happen per engagement. Neither may break the other.

- **Separate directory:** `data/market/` (facts + masters + derived output). `data/client_fs/` remains exclusively the client layer. `data/raw/macro_history.csv` is frozen as a legacy artifact until Phase 13 retires it.
- **Separate package:** `src/market_data/` with its own `schemas.py` (registry), `loader.py` (`load_market_data()` → `MarketLoadResult`, `MarketValidationError`), `views.py` (current/point-in-time/curve accessors), `build_market_derived.py`, and later `ingest/fred_adapter.py`. It **imports the building blocks** — `Column`, `TableSchema`, the validator's generic single-table rules and `Issue` — from `financials.schemas` / `financials.validator` (shared mechanics), but keeps its **own registry** (`MARKET_SCHEMAS`, `MARKET_OUTPUT_SCHEMAS`). Rationale for not stuffing market tables into `financials/schemas.py`'s `ALL_SCHEMAS`: that tuple defines what a *client FS load requires*; adding market files there would make every client load fail when `data/market/` is absent — exactly the coupling to avoid. (If Jessica prefers one registry file, the same independence is achievable with two registry tuples in one module — the non-negotiable is two loaders and two required-file sets.)
- **No load-time coupling, either direction:** `load_client_fs()` never reads `data/market/`; `load_market_data()` never reads `data/client_fs/`. The only meeting points are *downstream consumers* (scenario engine, WACC/DCF engine, curated report builders) which call both loaders and fail with their own clear errors if either layer is missing.
- **Separate audit trail:** market loads get their own `load_id`/`retrieval_timestamp` lineage and their own issues frame; a market WARNING (e.g. `stale_series` — newest observation older than its frequency implies) never blocks a client FS load, and an unmapped account never blocks a market refresh.
- **Ingestion adapters absorb messiness** (SCHEMAS.md pattern): `FRED → fred_adapter → canonical market_observations rows (append-only)`. The canonical schema stays strict; adapters normalize source quirks (missing daily values on holidays, revision vintages, licensing-truncated SP500 history). Until Phase 13, a `synthetic_adapter` re-encodes the existing seed-42 history into canonical rows with `source=SYNTHETIC`, `revision_status=SYNTHETIC`, `source_reference=src/generate_history.py seed 42` — the architecture is exercised end-to-end today **without inventing a single market number**.
- **Power BI:** unchanged contract — PBI reads only curated `reports/*.csv`; it never points at `data/market/*` (extend the POWERBI_CONTRACT.md "never reads" list explicitly).

**New market-layer validation rules** (same ERROR/WARNING discipline): `unknown_metric` (E), `unit_mismatch` vs master (E), `frequency_mismatch` (E), `duplicate_key` (E), `non_append_change` — a previously committed observation row changed or vanished (E), `bad_type`/`bad_date`/`missing_value` (E, inherited generics), `stale_series` (W), `implausible_value` — outside a per-metric plausibility band from the master, flagged for review, never auto-clamped (W).

**New docs:** `docs/MARKET_DATA.md` (this architecture: tables, vintage semantics, per-domain purpose and the must-not rules, curve/stance/VIX classification parameters) and `docs/RISK_FREE_RATE.md` (§3 methodology + the maturity-matching teaching note); update `docs/SCHEMAS.md` (kind-B row now points at `data/market/`), `docs/DECISIONS.md` (new numbered decisions: market layer lands, rf methodology explicit, ML repositioned — superseding the implicit part of #5), `docs/LEARNING_GUIDE.md` (new section, per CLAUDE.md), `docs/POWERBI_CONTRACT.md` (new curated file when it exists). **Tests mirror the existing suite:** schema-lock tests for the new masters, append-only lock test for observations, derivation identity tests (breakeven ingested-vs-calculated, spread cross-checks vs T10Y2Y/T10Y3M), rf-policy resolution test, WACC-contract violation test.

---

## 7. Phasing (relative to the existing plan: controls=4, NWC/UFCF=5, net debt/IC/shares=6, outliers=7, adjustments=8, M&A=9, agent=10, PBI page 2=11, SEC=12, live market=13, benchmarking=14)

| Slice | Lands | What ships | Why then |
|---|---|---|---|
| **13-prep A: market schema layer** | alongside **Phase 5** (any time after 4) | `data/market/` masters + observations schema, `src/market_data/` loader/validator/views, synthetic adapter re-platforms seed-42 history as `source=SYNTHETIC` rows, docs/MARKET_DATA.md, lock + append-only tests | Zero new facts, pure architecture; Phase 5's UFCF drivers and Phase 6's WACC engine then consume a real interface instead of a wide CSV, avoiding a rewrite at 13 |
| **13-prep B: scenario engine (company side)** | with **Phase 5** | `data/scenarios/` scenario_master + scenario_assumptions + driver_master, COMPANY_DRIVER path wired into the UFCF engine; the three legacy scenarios become rows | Phase 5 needs driver-based forecasting anyway; hard-coding drivers again would create the fourth copy of the scenario dict |
| **13-prep C: explicit rf + WACC contract** | with **Phase 6** | risk_free_policy table, rf lineage fields, `WACC_INPUT_CONTRACT` + `invalid_wacc_binding` rule; `SYNTHETIC_ML_10Y` becomes the *labeled* default until live data | Phase 6 (net debt/capital structure) is where WACC becomes data-driven; the rf source must be explicit before that, even while its value is still synthetic |
| **Phase 11 (PBI page 2)** | as planned | new curated `reports/market_context.csv` (+ schema-lock test + POWERBI_CONTRACT entry) exposing current-view metrics, curve shape, stance, rf lineage | New file, never a change to the locked 32-column report |
| **Phase 13 (live market)** | as planned — now a cutover, not a build-from-scratch | `fred_adapter` (IDs from §2, verified on first fetch), revision/vintage handling, `build_market_derived` (YoY, spreads, classifications, breakeven checks), MARKET_METRIC overlays wired into scenarios, `preferred_source` flips SYNTHETIC→FRED per metric (visible, per-metric switch), ML model retrained on real history or formally retired to teaching status (decision logged), `data/raw/macro_history.csv` + `sql/macro.sql` path retired | Decision #5's "live data once the reporting tool is finalized" — the switch is one column flip per metric with both histories retained, the architecture's payoff |
| **Phase 14 (benchmarking)** | as planned | benchmark_metric_master / peer_group_master / benchmark_observations (§2.10) populated by the Phase 12 SEC pipeline; assumption-vs-band REVIEW flags | Depends on SEC data (12) and on internal metrics being final (5–8) |

Phases 7–10 and 12 are untouched by this design except that the Phase 10 agent gets the same read-only relationship to `market_observations` that it has to `client_fs_raw`.

---

## Appendix A — FRED series ID verification summary

Confident from knowledge: CPIAUCSL, CPILFESL, PCEPI, PCEPILFE, FEDFUNDS, DFF, DFEDTARU, DFEDTARL, DGS3MO, DGS2, DGS5, DGS10, DGS30, DFII5, DFII10, T5YIE, T10YIE, T10Y2Y, T10Y3M, GDPC1, UNRATE, PAYEMS, BAMLC0A0CM, BAMLH0A0HYM2, SP500, VIXCLS, DTWEXBGS, DEXUSEU. Verify at implementation: **A191RL1Q225SBEA** (real GDP growth SAAR — prefer deriving from GDPC1 regardless), any per-rating OAS series, any r-star series. Operational caveats to encode in adapter + docs: SP500 history limited to ~10 years on FRED (licensing); DEXUSEU quoted USD-per-EUR; DGS3MO chosen over TB3MS/DTB3 for constant-maturity curve consistency; daily series have holiday gaps (store gaps as absent rows, never forward-fill into the fact table — fills, if ever needed, are a derived-layer decision).

## Appendix B — audit note on existing code

No genuine mathematical or logic errors were found in the existing computation code reviewed (`models/export_finance_report.py`, `models/valuation_model.py`, `models/finance_scenario_model.py`, `models/scenario_analysis.py`, `models/train_treasury_model.py`, `src/generate_history.py`): percent-vs-decimal conversions are applied consistently, the WACC/DCF/terminal-value formulas are standard, and the IRR bisection brackets and updates correctly. The known limitations — synthetic training data, hard-coded assumptions, constant ROIC across scenarios, the target-leakage flavor of training the 10Y model on the variables that generated it — are documented, accepted decisions (docs/DECISIONS.md #4–#5), which this proposal addresses architecturally rather than as bugs.

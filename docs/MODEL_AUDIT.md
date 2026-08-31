# Finance-ML Model Audit — Valuation, ML, Reporting & Market Architecture

**Date:** 2026-08-31 · **Status: audit only — nothing changed.** No Python, DAX, PBIX/PBIP,
or CSV output was modified. The only files this audit adds are this document and
`docs/MARKET_DATA_PROPOSAL.md` (the full market-data architecture design).

**Method.** Four parallel read-only audits (Python valuation math, PBIX internals,
Phase 1–3 conformance vs the new directives, market architecture), followed by adversarial
verification of every claimed error. The PBIX was inspected on a **copy** in scratch space:
it is a PBIR-format container whose report-layer JSON (180 visuals) is fully readable, and
whose compressed DataModel was decompressed (XPress9) far enough to extract the actual
**DAX measure definitions**, which are quoted below verbatim. Every quoted number was
independently recomputed and reconciles with the committed
`reports/finance_scenario_report.csv` and with the values visible on the rendered report
(Hurdle 10.42%, spread 7.11 pts, NPV $99.17M).

**Headline verdicts**

1. **Python: zero mathematical errors.** Every formula is the standard textbook form and
   every committed CSV value reproduces to full float precision.
2. **Report layer: two genuine defects confirmed** (Section D): a comparison-glyph measure
   bound to the wrong pairs (the screen currently renders two false inequalities), and a
   Project-IRR section whose displayed inputs do not produce its displayed IRR (17.54%
   shown; the shown inputs imply 12.03%).
3. **Units:** Python is uniformly $M; the $M/$B confusion is created entirely in DAX by
   `($B)` twin measures (`DIVIDE(x, 1000)`) that exist for some measures but not for
   `PV FCF Years 1-5` (Section B).
4. **The scenario engine varies WACC only — never cash flows.** All company fundamentals
   are byte-identical across Lower/Base/Higher (Section Y).
5. **Phases 1–3 conform well** to the new directives on lineage, immutability, and variance
   visibility; the gaps are metadata (units column, value-class taxonomy, LTM, forecast
   provenance) and are catalogued with minimal fixes (Section “Phase 1–3 conformance”).

---

# A. Complete dependency / source map

Classes: **CFS** = client F/S (placeholder today) · **MD** = market data · **IP** = industry/peer ·
**AA** = analyst assumption · **CALC** = calculated · **MO** = model output.

| Metric | Class | Current value | Defined in | Formula | Units | Depends on | Feeds | Hard-coded? | Future source |
|---|---|---|---|---|---|---|---|---|---|
| Macro scenario inputs (2Y, FF, CPI, U) | AA (scenario design; Base should be MD) | 3 sets, e.g. Base 4.25/4.25/2.80/4.40 | export_finance_report.py:27–51 | direct | % | — | ML 10Y prediction | Yes | Base = observed FRED values w/ as-of date; shocks stay AA as deltas |
| Risk-free rate | MO | 3.95/4.53/5.28% | model.predict()/100, export:59–61 | LinearRegression(2Y,FF,CPI,U) on synthetic seed-42 10Y | decimal (×100 for `_pct`) | scenario inputs, treasury_10y_model.joblib | CoE, CoD → WACC | Model yes; trained on synthetic data | Observed **10Y Treasury (DGS10)** via explicit rf methodology (Section L) |
| Beta | AA → IP | 1.20 | export:71 | direct | ratio | — | Cost of equity | Yes | Peer-derived (levered / bottom-up), Phase 14 |
| Equity risk premium | AA | 4.5% | export:72 | direct | decimal | — | Cost of equity | Yes | Stays AA, sourced + dated; VIX is context only |
| Equity weight | **AA** (not calculated) | 70% | export:74 | direct | decimal | — | WACC | Yes | Market-value capital structure (CFS+MD) or peer target (IP) |
| Debt weight | **AA** | 30% | export:75 | direct | decimal | — | WACC | Yes | Same (complement) |
| Credit spread | AA | 2.0% | export:77 | direct | decimal | — | Cost of debt | Yes | Company-specific spread (CFS debt schedule) benchmarked vs IG/HY OAS (MD) |
| Tax rate | AA/CFS | 25% | export:78 | direct | decimal | — | WACC shield; NOPAT | Yes | CFS effective + analyst normalized rate (kept separate, Phase 5) |
| Cost of equity | CALC | 9.35/9.93/10.68% | export:99–102 | rf + β·ERP | decimal | rf, β, ERP | WACC | — | — |
| Cost of debt | CALC | 5.95/6.53/7.28% | export:104–107 | rf + spread | decimal | rf, spread | WACC | — | — |
| WACC | CALC | 7.88/8.42/9.12% | export:109–115 | wₑ·CoE + w_d·CoD·(1−t) | decimal | above | Discounting, TV, ROIC spread, DAX hurdle | — | — |
| FCF Y1–Y5 | CFS | 100/110/121/133/146 | export:84–90 | direct list | $M | — | PV FCF, TV, EV, IRR CFs | Yes | **UFCF engine** (Phase 5): NOPAT + D&A − CapEx − ΔNWC from client statements |
| Terminal growth | AA | 2.5% | export:92 | direct | decimal | — | TV (DCF + project) | Yes | Stays AA, sanity-bounded vs long-run nominal GDP |
| Terminal Value | CALC | Base $2,525.83M | export:131–135 | FCF₅(1+g)/(WACC−g) | $M | FCF₅, g, WACC | PV TV → EV | — | — |
| PV FCF 1–5 / PV TV / EV | CALC | Base 474.40 / 1,685.63 / 2,160.03 | export:123–148 | Σ discounting; sum | $M | FCF, WACC, TV | Equity value | — | — |
| Debt / Cash / Net debt | CFS | 500 / 150 / 350 | export:80–81 | net = debt − cash | $M | — | Equity bridge | Yes | Net-debt component build (Phase 6) |
| Equity value | CALC | Base $1,810.03M | export:150–154 | EV − debt + cash | $M | EV, net debt | Share price | — | — |
| Shares outstanding | CFS | 100 | export:82 | direct | M shares | — | Implied price | Yes | **Diluted** shares w/ treasury-stock method (Phase 6) — today it isn’t even labeled diluted |
| Implied share price | MO | Base $18.10 | export:156–159 | equity / shares | $/share | above | Decision | — | — |
| Revenue / EBIT margin / EBIT | CFS | 1000 / 20% / 200 | export:250–255 | EBIT = rev·margin | $M, %, $M | — | NOPAT | Yes | Normalized income statement (Phases 2–5) |
| NOPAT | CALC | 150 | export:258 | EBIT·(1−t) | $M | EBIT, tax | ROIC | — | — |
| Invested capital | CFS | 1,500 | export:252 | direct | $M | — | ROIC | Yes | Component build: NWC + net PP&E + other operating (Phase 6) |
| ROIC | CALC | 10.0% (scenario-invariant) | export:261 | NOPAT/IC | % | NOPAT, IC | ROIC−WACC spread → decision | — | — |
| Initial investment | AA | 1,500 (numerically = IC — decouple) | export:274 | direct | $M | — | Project IRR/NPV | Yes | Real project budget |
| Project IRR | CALC | 17.54% (single value broadcast to all rows) | export:279–336 | bisection on [−1500, FCF₁..₄, FCF₅+TV(WACC_base)] | % | FCF, TV @ Base WACC, initial inv. | IRR vs hurdle decision | — | Per-scenario IRR; TV basis reconciled with DAX (Section D2) |
| **Hurdle Rate (%)** | **AA — DAX only** | WACC + 2.00 pts (9.88/10.42/11.12) | **PBIX DataModel measure** | `[Calculated WACC] + 2.00` | pts | WACC | IRR decision, project NPV discounting, project TV | Yes — invisible to Python/git | Promote into the Python layer/CSV contract as an explicit assumption |
| Project NPV ($M) | CALC — DAX | $99.17M (Base) | DAX | PV(CFs @ hurdle) + PV(TV @ hurdle) − 1500 | $M | FCF, hurdle | NPV decision | — | Python once hurdle is promoted |
| Investment recommendation | MO — DAX | APPROVE | DAX | NPV>0 && IRR>hurdle && ROIC>WACC | text | all three tests | Final verdict | — | — |

DAX also **re-derives** WACC, PV FCF by year, TV, EV, equity value, and share price from the
CSV’s input columns (`Calculated WACC`, `PV FCF Year N`, `Terminal Value`, `DCF Enterprise
Value`, `DCF Implied Share Price`), in parallel with the Python-computed output columns the
CSV already carries (`wacc_pct`, `enterprise_value`, …). Both paths agree today (a
`DCF EV Reconciliation` measure exists), but this is a **dual calculation lineage** — the
single-source-of-truth decision belongs to Phase 11 (recommendation: Python computes,
DAX presents).

# B. Unit audit

**Python: uniformly $M** — fcf, debt 500, cash 150, revenue 1000, IC 1500, EV/equity
outputs; shares in millions so `equity/shares` = $/share. Rates: decimals internally,
`*_pct` columns ×100 exactly once at the reporting edge. **No $M→$B conversion exists
anywhere in Python** (verified by grep + recomputation). One cross-script hazard: the same
column name `risk_free_rate` holds *percent* in `finance_scenario_model.py` but *decimal*
in the other ladder scripts — each internally consistent, but a copy-paste trap.

**DAX: where $B comes from.** Extracted definitions show `($B)` twin measures:
`Terminal Value ($B) = DIVIDE([Terminal Value], 1000)`, same for PV TV, DCF EV, DCF Equity
— while `PV FCF Years 1-5 = [PV FCF Year 1] + … + [PV FCF Year 5]` **has no ($B) twin**.
No visual sets `displayUnits` (zero hits across all 180 visuals). Hence the EV build-up row
renders **474.40 ($M) + 1.69 ($B) = 2.16 ($B)** — the exact confusion you flagged. The TV
rows have the same cross-scale reads ($M FCF Year 5 and decimal `WACC Less Growth` feeding
a $B result).

**Recommended canonical display convention:** valuation build-ups in **$B with two
decimals** (`PV FCF Years 1-5 = $0.474B`, `PV Terminal Value = $1.690B`,
`Enterprise Value = $2.164B`) via a `PV FCF Years 1-5 ($B)` twin — one new measure, no
formula changes; project appraisal stays all-$M (it already is, consistently). Pipeline
convention (also closes conformance gap g): monetary amounts in **millions of stated
currency** everywhere, declared via a future `company_master.amount_scale` column
(`MILLIONS`); shares in millions; rates as decimals internally, percent only in `_pct`
display columns. To be written into `docs/SCHEMAS.md` once approved.

# C. Formula verification

All standard forms, all recomputed from the committed CSV’s WACC values (scripts not run):

| Scenario | WACC | PV FCF 1–5 | Terminal Value | PV TV | EV | vs committed |
|---|---|---|---|---|---|---|
| Lower | 7.8803% | 481.70 | 2,781.45 | 1,903.54 | 2,385.23 | exact (diff 0.0) |
| Base | 8.4248% | 474.40 | **2,525.83** | 1,685.63 | 2,160.03 | exact |
| Higher | 9.1201% | 465.32 | 2,260.54 | 1,461.13 | 1,926.45 | exact |

**Section-5 check confirmed:** Base TV = 146 × 1.025 / (0.0842478 − 0.025) = 149.65 /
0.0592478 = **$2,525.83M ≈ $2.53B** — the formula is correct; the “$2.53B” on screen is the
DAX `($B)` twin (÷1000), not a Python conversion. CAPM, cost of debt, WACC, equity bridge
(`EV − debt + cash` ≡ `EV − net_debt`), NOPAT, ROIC, and the bisection IRR (17.53658200%,
residual 2.7e-07) all verified exact.

# D. Actual mathematical errors discovered

**Python: none.** (Adversarial panel: all claims against Python refuted.)

### D1 — CONFIRMED: comparison glyph bound to the wrong pairs (report displays two false inequalities)

- **Existing formula** (extracted DAX):
  `Hurdle vs IRR Symbol = SWITCH(TRUE(), [Hurdle Rate (%)] < [Project IRR (%)], "<", [Hurdle Rate (%)] > [Project IRR (%)], ">", "=")` — **Hurdle-first** phrasing, so with
  Hurdle 10.42 < IRR 17.54 it returns **"<"**.
- **Why it is wrong:** the measure is bound as the glyph in **two rows where the left/right
  operands are different**: the ROIC row (`ROIC 10.00 [glyph] WACC 8.42`) and the
  IRR row (`… IRR 17.54 [glyph] Hurdle 10.42`). Both rows therefore render "<" where the
  true relation is ">": the screen literally states **"10.00 < 8.42"** and
  **"17.54 < 10.42"**, contradicting the adjacent (correct) status cards "Value Creating"
  and "PASS — Clears Hurdle". The stale binding `queryRef =
  "finance_scenario_report.ROIC Comparison Symbol"` shows the measure was renamed from a
  ROIC comparison and re-purposed; a correctly phrased `IRR Comparison Symbol`
  (IRR-first) exists but is used only in the bottom “Project IRR Calculation” row.
- **Corrected form:** rebind the ROIC row’s glyph card (`da893f14…`) to a ROIC-vs-WACC
  symbol measure (`SWITCH(TRUE(), [ROIC Check (%)] > [Calculated WACC], ">", …)` — to be
  created) and the IRR row’s glyph card (`d5dabcf9…`) to the existing
  `IRR Comparison Symbol`. No Python change.
- **Numerical impact:** none on values; **two false mathematical statements on screen**.
- **Downstream impact:** none (decision measures compare correctly in DAX); credibility only.

### D2 — CONFIRMED: Project IRR section’s displayed inputs don’t produce the displayed IRR

- **Existing formulas:** the DAX project section discounts at the **hurdle rate**:
  `Project Terminal Value ($M) = FCF₅(1+g)/(hurdle−g)` = **1,888.38**, and
  `Project NPV ($M)` = PV(CFs @ hurdle) + PV(TV @ hurdle) − 1,500 = **99.17** ✓ (matches
  your screen). But `Project IRR (%)` = **17.54%** comes from Python
  (`export_finance_report.py:309–317`), whose cash-flow set used the **Base-WACC-based TV
  = 2,525.83**.
- **Why it is wrong:** the “Project IRR Calculation” section displays the IRR equation with
  the hurdle-based TV card (1,888.38) as an input; solving that displayed equation gives
  **IRR = 12.03%**, not the 17.54% shown beside it. Two internally-valid methodologies
  (NPV at hurdle; IRR on WACC-based TV) are mixed in one visual walk-through, so the
  displayed inputs and displayed output don’t reconcile (a 5.5-point gap).
- **Corrected form (choose one, on approval):** (i) make Python compute the project TV on
  the same basis DAX displays (hurdle-based) — Base IRR becomes ≈12.03%, still clearing the
  10.42% hurdle; or (ii) add a `Project Terminal Value (WACC basis)` display so the IRR
  panel shows the inputs actually used. Either way, promote **Hurdle Rate = WACC + 2.00pts**
  (extracted DAX: `VAR ProjectRiskPremium = 2.00 RETURN WACC + ProjectRiskPremium`) into
  the Python layer/CSV so one engine owns the project cash-flow set.
- **Numerical impact:** displayed IRR is right for one methodology, wrong for the one on
  screen; NPV/decision unaffected (99.17 > 0 and both IRRs clear the hurdle).
- **Downstream impact:** decision unchanged today; the inconsistency would matter the day
  IRR is near the hurdle. Note also the Python comment “Calculate IRR separately for each
  rate scenario” — a single Base-WACC IRR is actually broadcast to all three rows.

**Note:** `Initial Investment ($M)` extracts as literal `1500` (a trailing “0” in the raw
extraction is format-string bleed; NPV = 99.17 reconciles only with 1,500 — confirmed).

# E. Visual-only issues (no math impact)

1. **Duplicated leading card in the IRR row** (y≈1474): two cards bound to the same
   `Hurdle Rate (%)` measure; row reads “Hurdle | IRR | ⋛ | Hurdle | Spread” and is missing
   the “=” card its sibling row (y≈2469) has. Fix: delete/rebind card `0d068804…`.
2. **Mixed $M/$B rows** (Section B) — EV build-up and TV rows.
3. **No visual titles anywhere** — all 180 visuals untitled; sections are free-floating
   textboxes (fragile for accessibility/tooltips; fine visually).
4. **Probable color mis-binding:** both `NPV Comparison Symbol` cards bind `fontColor` to
   the symbol measure itself instead of `NPV Decision Color` — conditional color silently
   defaults.
5. **Stale renames:** `Invested Capital ($M)` → `Invested Capital Display` (3 cards) and
   `ROIC Comparison Symbol` → `Hurdle vs IRR Symbol` (2 cards) carry old queryRefs.
6. **Interaction inconsistency:** the scenario table is NoFilter toward the four line
   charts (deliberate) but DataFilter toward one lone assumptions card.
7. ~35 cards exist solely to render operator glyphs from constant measures ("×", "=", "+").

# F. Duplicate measures / visuals

**Duplicate visuals, not duplicate measures.** “Terminal Value Calculation” (y≈1089)
repeats, measure-for-measure, the two TV rows already inside “DCF Valuation Calc” (y≈600,
703). `Terminal Value ($B)` and `PV Terminal Value ($B)` are each bound to 4 cards; no
duplicate measure names exist. Recommendation: one clean sequence — keep the TV rows inside
the DCF walk-through, delete the standalone repeat section (visual deletion only, later,
with approval).

# G–K. Sources of record

- **G. Equity/Debt weight:** ANALYST ASSUMPTIONS, `export_finance_report.py:74–75`
  (0.70/0.30). Not calculated anywhere; the report should label them as assumptions.
- **H. Every WACC input:** rf = MO (ML-predicted 10Y, synthetic training); β 1.20 = AA
  (:71); ERP 4.5% = AA (:72); weights = AA (:74–75); credit spread 2.0% = AA (:77); tax 25%
  = AA (:78). WACC itself CALC (:109–115). The DAX `Calculated WACC` re-derives it from the
  same CSV inputs (dual lineage, Section A).
- **I. FCF values:** hard-coded list `[100, 110, 121, 133, 146]` (:84–90), identical in
  every scenario; duplicated in `valuation_model.py`/`dcf_scenario_model.py`.
- **J. Net debt:** `debt 500 − cash 150 = 350`, both hard-coded (:80–81).
- **K. Diluted shares:** `shares = 100` (:82) — a **basic** constant; nothing in the model
  is actually diluted. Replacement path: `shares_dilution.csv` + treasury-stock method
  (Phase 6).

# L. Risk-free rate — source and methodology

Fully traced provenance: synthetic macro history (`generate_history.py`, seed 42; series
actually ends **2026-07-31**, not 2026-08-28 — `freq="ME"` drops the partial month) →
SQLite → `LinearRegression` on [2Y, FF, CPI, U] targeting the synthetic 10Y, fit on the
**first 80% only** (82 of 103 rows) → `model.predict(scenarios)/100`. The fitted
coefficients essentially re-learn the hand-written generating rule. **It represents a
10-year maturity; there is no observation date anywhere; it is a model output, not an
observation.** Meanwhile the report displays `treasury_2y` prominently as an input — a
reader can easily conflate the two. **Proposed methodology** (detail in
`docs/MARKET_DATA_PROPOSAL.md` §3): an explicit, selectable `risk_free_policy` —
default `UST_10Y_SPOT` (DGS10, spot as of valuation date, maturity-matched to the 5-year
explicit + terminal horizon), with every WACC recording
`rf_methodology_id / source / maturity / observation_date / value`. The ML model is
repositioned as a **rates-path scenario tool** (labeled `SYNTHETIC_ML_10Y`), never again
the silent rf source.

# M. Existing Treasury maturity coverage

**2Y** (scenario input only) and **10Y** (model-predicted). No 3M, 5Y, or 30Y; no curve
structure; no spreads; no real rates. The `yield_spread_10y_2y` column exists only in the
synthetic history CSV and is unused by the valuation chain.

# N. Missing market/macro datasets

Core CPI, PCE + Core PCE (headline CPI exists only as a synthetic scenario input); Fed
target range + policy-path expectations; Treasury 3M/5Y/30Y + curve spreads; TIPS real
yields + breakevens; real GDP; payrolls (unemployment exists only as a synthetic input);
IG/HY credit spreads; broad equity index + VIX; market FX (accounting FX exists,
market FX does not); all industry/peer data (class entirely absent from the repo).

# O–V. Proposed architectures (full detail: `docs/MARKET_DATA_PROPOSAL.md`)

Everything lands in a new, **independent** `data/market/` layer: `market_metric_master`
(metric_id, unit, native frequency, seasonal adjustment, source series, derivation rule) +
**append-only** `market_observations` (metric_id, observation_date, value, unit, source,
source_reference, **retrieval_timestamp**, frequency, revision_status — history never
overwritten; “current” and “point-in-time” are computed views) + `market_derived` (rebuilt
OUTPUT for YoY/spreads/classifications, with input-vintage lineage). All FRED (free,
official); no invented observations; a synthetic adapter re-platforms the seed-42 history
as clearly-labeled `SYNTHETIC` rows so the architecture runs end-to-end today.

- **O. CPI/PCE:** CPIAUCSL/CPILFESL/PCEPI/PCEPILFE as **index levels**, YoY derived
  (headline vs core explicit; Core PCE first-class as the Fed’s measure). Never enters WACC.
- **P. Yield curve:** DGS3MO/DGS2/DGS5/DGS10/DGS30 as an ordered curve structure with a
  `yield_curve(as_of)` accessor; derived 10Y−2Y and 10Y−3M spreads (FRED T10Y2Y/T10Y3M
  ingested as cross-checks only) + NORMAL/FLAT/INVERTED classification. The one domain that
  legitimately feeds WACC (via the rf policy).
- **Q. Real rates/breakevens:** DFII5/DFII10 + T5YIE/T10YIE, with `10Y − TIPS10Y` derived
  as an integrity control against the ingested breakeven; replaces the synthetic
  `real_10y_proxy` (which wrongly mixes a forward yield with realized inflation).
- **R. GDP/labor:** GDPC1 (growth derived, vintage-aware — GDP is why `revision_status`
  exists), UNRATE, PAYEMS (+ MoM change derived). Context for revenue/margin assumptions
  and terminal-growth sanity; never WACC inputs.
- **S. Credit:** BAMLC0A0CM (IG OAS) + BAMLH0A0HYM2 (HY OAS). The **one legitimate WACC
  channel besides the curve**: a benchmark corridor for the company-specific spread
  (assumption validated against it → REVIEW if outside; never silently substituted).
- **T. Equity risk:** SP500 (context only; ~10y history on FRED) + VIXCLS with a
  CALM/ELEVATED/STRESSED regime classification. **VIX never mechanically maps to ERP/WACC**
  — it informs which documented ERP the analyst selects.
- **U. FX:** market FX (DTWEXBGS broad dollar, DEXUSEU) in `data/market/`, strictly
  separate from accounting `fx_rates.csv`; one one-way bridge — an adapter may *derive
  candidate* accounting rows (period avg/close from daily fixes) marked `FRED_DERIVED`,
  subject to existing validation and review.
- **V. Industry/peer:** `benchmark_metric_master` (shared derivation rules so company and
  peer metrics are computed identically) + `benchmark_observations` keyed by
  (metric, peer_group, statistic ∈ COMPANY/PEER_MEDIAN/INDUSTRY_MEDIAN/P25/P75, period,
  source, retrieval_timestamp); populated by the Phase 12 SEC pipeline; company assumptions
  outside the P25–P75 band → REVIEW, never auto-corrected.

# W. Macro → WACC dependency map

```
CONTEXT (never touches WACC):  CPI/PCE YoY · GDP · labor · VIX/SP500
        └── shape scenario narratives & the rate path (analyst judgment,
            recorded in scenario_assumptions.rationale) ──┐
MARKET VARIABLES (the only WACC feeders):                 ▼
  Treasury curve ──(rf policy)──► risk-free rate ─► Cost of Equity = rf + β·ERP
  IG/HY OAS ──(benchmark corridor)─► company credit spread ─► Cost of Debt = rf + spread
  Fed funds path ──► floating-rate debt cost (Phase 6 debt schedule)
ASSUMPTIONS: β (Phase 14 peer-informed) · ERP (documented, VIX = context)
COMPANY DATA: tax rate, E/D weights (Phase 6 capital structure)
        ▼
  WACC = wₑ·CoE + w_d·CoD·(1−tax)  ─►  DCF
FORBIDDEN (future validation ERROR): CPI/PCE/GDP/UNRATE/PAYEMS/VIX/SP500 → any WACC field.
```

# X. Macro → UFCF dependency map

```
inflation (CPI/PCE YoY) ─► pricing & input costs ─► revenue growth %, EBIT margin
GDP growth ─────────────► volume/demand ─────────► revenue growth %
labor ──────────────────► wages & demand ────────► EBIT margin, revenue growth %
rates (curve/Fed) ──────► investment appetite,
                          customer payment speed ─► CapEx %, NWC intensity
market FX ──────────────► translated revenue/cost mix (via the Phase 3 FX layer)
        ▼  all land as explicit COMPANY_DRIVER rows in scenario_assumptions —
           macro never edits numbers directly
UFCF = NOPAT + D&A − CapEx − ΔNWC   (Phase 5 engine)
```
Macro reaches valuation through **two separate doors** — operating drivers into UFCF, and
discounting via WACC — and keeping the doors separate is the design rule.

# Y. Scenario-engine assessment

Today: scenarios change **only** the 4 macro inputs → predicted 10Y → WACC → discounting.
Everything else — FCF, revenue, margin, ROIC (10.0% in every row), tax, weights, spread,
net debt, shares, even `project_irr_pct` (17.54% broadcast) — is byte-identical across
Lower/Base/Higher. A 124bp WACC swing moves EV ±~10.5% on frozen operations: the
“Higher Rate” world (4.0% CPI, 5.25% FF) carries exactly the same revenue and margins as
the easy-rates world. **Target design** (`docs/MARKET_DATA_PROPOSAL.md` §5):
`scenario_master` (as-of date, rf methodology, narrative, approval status) +
`scenario_assumptions` with `target_type ∈ {MARKET_METRIC, COMPANY_DRIVER}` and
ABSOLUTE/DELTA overrides — one scenario coherently hits **both** WACC and UFCF; the three
existing scenarios become the first three rows; observations and client files are never
written by the engine.

# Z. Recommended left→right / top→bottom flow

- **Page 1 — Market & Cost of Capital** (top→bottom): market-regime summary strip →
  Inflation + Fed (headline vs core, stance) → Treasury curve + real rates/breakevens
  (curve chart, spreads, shape) → Credit + equity risk (OAS corridor, VIX regime) →
  **Cost of Equity | Cost of Debt | WACC** as the culminating left→right equation, each
  input carrying its source label; side/lower panel: GDP, labor, FX trends. No wall of
  cards — trend charts, small multiples, tooltips.
- **Page 2 — Client Financials & Forecast:** Reported → Normalized → (Pro Forma) views →
  operating walk (Revenue − OpCosts = EBITDA − D&A = EBIT ×(1−t) = NOPAT) → NWC build and
  ΔNWC → UFCF bridge (NOPAT + D&A − CapEx − ΔNWC) → right rail: net-debt components,
  diluted-share build, control status, outlier flags, consolidation/FX status.
- **Page 3 — Valuation & Capital Allocation:** UFCF row (from Page 2) → discounting row →
  TV row (once, not twice) → EV = PV FCF + PV TV (single scale, $B) → − Net Debt → Equity →
  ÷ Diluted Shares → Implied Price; then the three decision rows, each `left op right =
  spread → verdict` with correctly-paired symbols: ROIC vs WACC, IRR vs Hurdle, NPV vs 0 →
  final recommendation.

# AA. Human-readable descriptions / tooltip metadata

(Ready to load as measure descriptions in the pbip phase.)

- **Risk-Free Rate** — *Market data (currently model output).* “Government-yield benchmark
  for discounting. Today: a 10-year yield predicted by an internal model trained on
  synthetic data, with no observation date. Target: observed 10Y Treasury (DGS10) with
  source, maturity, and as-of date displayed.”
- **Equity Weight / Debt Weight** — *Analyst assumption.* “Share of capital funded by
  equity/debt in WACC. Model assumption (70/30) — not calculated. Future: market-value or
  peer-target capital structure.”
- **Beta** — *Analyst assumption → peer data.* “Sensitivity of the company’s equity to
  market moves; scales the ERP in CAPM. Currently assumed 1.20; future: peer-derived.”
- **Equity Risk Premium** — *Market assumption.* “Extra return demanded over the risk-free
  rate for equities (4.5%). Stays an explicit, dated assumption; VIX is context, never a
  formula input.”
- **Credit Spread** — *Assumption, benchmarked by market.* “Company borrowing premium over
  the risk-free benchmark (2.0%). Future: company-specific, sanity-checked against IG/HY
  index spreads.”
- **WACC** — *Calculated.* “Weighted average required return of debt and equity used to
  discount unlevered free cash flow.”
- **Terminal Value** — *Calculated.* “Value of cash flows beyond year 5 via Gordon growth:
  FCF₅ × (1+g) ÷ (WACC − g).”
- **PV Terminal Value** — *Calculated.* “Terminal value discounted back 5 years at WACC.”
- **Enterprise Value** — *Calculated.* “PV of forecast FCF plus PV of terminal value —
  the value of the whole business.”
- **Net Debt** — *Client F/S + calculated.* “Debt and debt-like items less eligible cash;
  bridges enterprise to equity value. Currently hard-coded 500 − 150.”
- **Diluted Shares** — *Client F/S + calculated.* “Fully diluted count converting equity
  value to per-share value (treasury-stock method for options). Currently a 100M basic
  constant.”
- **UFCF** — *Client F/S + forecast calculation.* “NOPAT + D&A − CapEx − ΔNWC; cash
  available to all capital providers. Currently a hard-coded list.”
- **ROIC** — *Calculated.* “NOPAT ÷ invested capital; value is created only when ROIC
  exceeds WACC.”
- **Hurdle Rate** — *Analyst assumption (DAX).* “Minimum acceptable project return —
  currently WACC + 2.00pts, defined only in the Power BI model; to be promoted into the
  versioned pipeline.”
- **Project IRR / NPV** — *Calculated.* “Return that zeroes project NPV / PV of project
  cash flows at the hurdle rate minus the investment.” (Bases to be reconciled — D2.)
- **Core PCE (future)** — *Market/economic data.* “Inflation excluding food and energy —
  the Fed’s preferred gauge of underlying pressure. Context for policy expectations; never
  a direct WACC input.”
- **Implied Share Price / Investment Recommendation** — *Model output.* “End of the chain:
  equity ÷ diluted shares; APPROVE only if NPV>0 and IRR>hurdle and ROIC>WACC.”

# AB. Recommended semantic-model metadata changes (pbip phase, on approval)

1. Source-class labels on every measure/field: `MARKET / MACRO / CLIENT F/S /
   INDUSTRY-PEER / ASSUMPTION / CALCULATED / MODEL OUTPUT` (description prefix + display
   folders per the eleven-folder plan in `docs/POWERBI_CONTRACT.md`).
2. Fix the two D-defects: dedicated ROIC-vs-WACC symbol measure; rebind the IRR row to
   `IRR Comparison Symbol`; delete the stray leading Hurdle card; add the missing “=” card.
3. Add `PV FCF Years 1-5 ($B)` twin (or displayUnits) — kills the mixed-scale row.
4. Rebind the two `NPV Comparison Symbol` fontColor properties to `NPV Decision Color`.
5. Clean the two stale renames (re-save bindings for `Invested Capital Display`,
   `Hurdle vs IRR Symbol`).
6. Remove the duplicate “Terminal Value Calculation” section (visuals only).
7. Promote `Hurdle Rate` into the CSV contract (new column in a *new* curated file — the
   32-column contract stays frozen); add measure descriptions from AA; align the one
   inconsistent visual-interaction setting.

# AC–AF. Field dispositions

- **AC. Eventually LIVE (market feed):** Treasury 3M/2/5/10/30Y; Fed funds + target range;
  CPI/Core CPI/PCE/Core PCE; TIPS 5/10Y; breakevens 5/10Y; real GDP; unemployment;
  payrolls; IG/HY OAS; SP500; VIX; broad-dollar index; EURUSD (+ pairs as needed);
  Base-scenario macro anchors (today’s hard-coded 4.25/4.25/2.80/4.40).
- **AD. Remain ANALYST ASSUMPTIONS (explicit, dated, documented):** ERP; terminal growth;
  hurdle premium (+2.00pts over WACC); scenario shock deltas; normalized tax rate; target
  capital structure (until market-value structure lands); beta until peer-derived;
  ML-proposed 10Y as a labeled scenario override.
- **AE. Ultimately from CLIENT F/S:** FCF/UFCF (via NOPAT + D&A − CapEx − ΔNWC); revenue;
  EBITDA/EBIT margins; D&A; CapEx; NWC components (AR, inventory, AP, other operating);
  effective tax rate; debt & cash → net debt components; basic + diluted shares (options,
  RSUs, converts); invested capital build; actual borrowing cost; market-value equity
  (with share price) for capital structure.
- **AF. From INDUSTRY / PEER data:** beta; peer capital structure; margin/growth/ROIC
  benchmarks; Debt/EBITDA, interest coverage; CapEx/Revenue, NWC/Revenue, DSO/DIO/DPO;
  EV/Revenue, EV/EBITDA, P/E, FCF yield — company vs peer-median/industry-median/P25/P75.

# AG. Recommended implementation sequence after this audit

1. **Phase 4 — Control engine** (already approved; unchanged next step).
2. **Phase 5 + market-prep A/B:** NWC/NOPAT/UFCF engine; land the `data/market/` schema
   layer with the synthetic adapter (zero new facts) and the scenario tables
   (COMPANY_DRIVER side) so drivers aren’t hard-coded a fourth time.
3. **Phase 6 + market-prep C:** net debt / invested capital / diluted shares; explicit
   rf-policy table + WACC input contract; promote the hurdle rate into the versioned layer;
   resolve D2’s TV-basis choice (Python-side, additive).
4. **Phases 7–10** as planned (outliers, adjustments, M&A, agent).
5. **Phase 11 — Power BI:** convert to `.pbip`; apply the AB metadata/fix list (including
   both D-defects, which live in the report/model layer); new curated
   `reports/client_fs_*.csv` + `reports/market_context.csv` files each schema-locked;
   Pages 1–3 per Z. Visual QA stays human, in Desktop.
6. **Phase 12 — SEC test** → feeds **Phase 14 benchmarking**.
7. **Phase 13 — live market data:** FRED adapter; per-metric SYNTHETIC→FRED source flips
   (visible, reversible); ML model retrained on real history or formally retired to
   teaching status (decision logged).

---

## Phase 1–3 conformance vs the new directives (the “go back through phases” check)

| Directive | Status | Evidence / gap |
|---|---|---|
| (a) Six-class value taxonomy | PARTIAL | Three-kind separation (client/market/assumption) is designed-in at file level (SCHEMAS.md) but no machine-readable `value_class` exists; `finance_scenario_report.csv` blends classes in one row (frozen contract — class column goes in future curated files) |
| (b) Market FX vs accounting FX separation | PARTIAL | Accounting half fully conforms (`rate_type_for`, DECISIONS #26); market-FX half doesn’t exist yet and no doc draws the boundary — MARKET_DATA_PROPOSAL §2.9 supplies it |
| (c) Observation metadata + never overwrite | GAP | `fx_rates.csv` has 4 of 9 fields; macro history has none and `if_exists="replace"` overwrites the DB on every load (tolerable only while synthetic — to be forbidden at Phase 13); `macro_sample.csv` carries a BOM (same class of bug as decision #1) |
| (d) Refresh independence | PARTIAL | The two stacks share no code/files/DB (good); one coupling: `fx_rates.csv` is a required client-load input — split `ALL_SCHEMAS` into client vs reference sets when market work lands |
| (e) Scenarios hit WACC **and** fundamentals | GAP (hooks exist) | Today WACC-only (Section Y); `scenario` column is already in every FS key; scenario_master/assumptions design ready |
| (f) Historical/LTM/forecast/consensus/proforma metadata | PARTIAL | `is_historical`/`is_forecast`, scenario-in-key, `reported_or_adjusted`, `include_in_proforma` all in place; missing: LTM period type, forecast provenance (`data_basis` column), controlled scenario vocabulary |
| (g) Unit discipline documented | GAP | No unit/scale column anywhere; amounts implicitly $M; convention proposed in Section B — needs a doc + `company_master.amount_scale` |
| (h) Every number answers where-from/type/formula/units/depends/decides | PARTIAL | Lineage (“where from”) is genuinely end-to-end and is the pipeline’s strongest asset; type partially (`origin`, `reported_or_adjusted`); units absent; depends/decides needs the Phase 11 metric registry |

**Strengths already in place:** end-to-end cell-level lineage; raw immutability + rebuild-locked
derived files (the “never overwritten” ethos directive (c) wants, already lived on the client
side); variance visibility with no silent substitution (FX variance = CTA proof, pinned by
test); scenario/forecast metadata pre-wired in the keys; policies as single documented
functions; structural independence of the two stacks.

**Gap fixes are catalogued with owners/phases above; none were applied — awaiting approval.**

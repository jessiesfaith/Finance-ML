# How Finance-ML Works — A Learning Guide

A plain-language walkthrough of every moving part in this repository: how the
data is made, how the machine learning works, how the finance math builds on
it, and how the results reach Power BI. Read it top to bottom with the code
open next to it — each section names the exact file it explains.

---

## 1. The big picture

The whole project is one pipeline. Data flows left to right:

```
generate_history.py          train_treasury_model.py        export_finance_report.py
   (make macro data)    →       (learn the pattern)     →      (apply it to finance)

macro_history.csv  →  finance_ml.db  →  treasury_10y_model.joblib  →  finance_scenario_report.csv
                                                                            ↓
                                                                       ML Tool.pbix
                                                                       (Power BI)
```

In words: we create a history of macroeconomic data, teach a model the
relationship between short-term conditions and the 10-year Treasury yield,
then use that predicted yield as the **risk-free rate** that drives WACC,
DCF valuation, ROIC, and IRR — and export it all to one CSV that Power BI
visualizes.

---

## 2. Step 1 — Creating the data (`src/generate_history.py`)

Real Treasury data isn't wired in yet (it will be, once the reporting tool is
finalized — see docs/DECISIONS.md #5), so this script *synthesizes* a
plausible monthly history from 2018 to 2026.

Key ideas to notice in the code:

- **`np.random.seed(42)`** — random number generators are actually
  deterministic sequences; the seed picks the starting point. Fixing it means
  every run produces *identical* "random" data, so results are reproducible.
- **`np.cumsum(np.random.normal(...))`** — a *random walk*: each month's Fed
  Funds rate is last month's plus a small random step. That mimics how rates
  drift rather than jump around independently.
- **`np.clip(x, low, high)`** — keeps values inside realistic bounds (e.g.
  unemployment can't go below 2.5%).
- The 10Y Treasury is built as a formula of the other variables **plus
  noise**. That's deliberate: it plants a real relationship in the data for
  the ML model to discover, while the noise keeps discovery non-trivial.

## 3. Step 2 — Storing it in SQL (`src/build_history_database.py`, `sql/`)

`build_history_database.py` loads the CSV into a **SQLite** database
(`data/finance_ml.db`) — a full SQL database that lives in a single file, no
server needed. `df.to_sql("macro_history", conn, if_exists="replace")` turns
a DataFrame into a table in one line.

`src/query_database.py` shows the other direction: it reads a `.sql` file
(`sql/macro.sql`), runs it with `pd.read_sql_query`, and gets a DataFrame
back. Keeping SQL in its own files (rather than strings inside Python) is a
good habit — analysts can read and edit the query without touching code.
`src/ml.features.sql` does the same for the ML feature set, renaming
`treasury_10y` to `target_treasury_10y` to make the prediction target obvious.

## 4. Step 3 — Training the model (`models/train_treasury_model.py`)

This is the machine-learning heart of the project. The steps, in order:

1. **Define the problem.** Predict `treasury_10y` (the *target*, `y`) from
   four *features* (`X`): `treasury_2y`, `fed_funds`, `cpi`, `unemployment`.
   In ML terms this is *supervised regression*: we have historical examples
   where the right answer is known, and we want a rule that generalizes.

2. **Split by time, not randomly.** `split = int(len(df) * 0.80)` trains on
   the first 80% of months and tests on the last 20%. For time-series data
   you must never shuffle: a random split would let the model "train on the
   future" and score unrealistically well.

3. **Fit `LinearRegression`.** The model learns one number per feature
   (a *coefficient*) plus an *intercept*:

   ```
   predicted_10y = b0 + b1·treasury_2y + b2·fed_funds + b3·cpi + b4·unemployment
   ```

   "Learning" means choosing the b's that minimize squared prediction error
   on the training rows. The script prints the coefficients — read them! A
   coefficient of, say, 0.55 on `treasury_2y` means: holding everything else
   constant, a 1-point rise in the 2Y is associated with a 0.55-point rise
   in the 10Y.

4. **Score on the held-out test set.**
   - **MAE** (mean absolute error): "on average the prediction misses by X
     percentage points" — in the same units as the target, easy to judge.
   - **R²**: fraction of the target's variance the model explains; 1.0 is
     perfect, 0.0 means no better than always guessing the average.

5. **Save with `joblib.dump`.** The trained model *and* its feature list are
   saved together in `treasury_10y_model.joblib`, so every downstream script
   loads a matched pair and can't feed columns in the wrong order.

`models/predict_treasury_10yr.py` is the minimal "hello world" of using the
saved model: build a one-row DataFrame of inputs, call `model.predict`.

## 5. Step 4 — The scenario ladder (`models/`)

Five scripts intentionally repeat themselves, each adding one finance concept.
Study them in this order:

| Script | Adds |
|---|---|
| `scenario_analysis.py` | 3 macro scenarios → predicted 10Y for each |
| `finance_scenario_model.py` | CAPM cost of equity, cost of debt, **WACC** |
| `dcf_scenario_model.py` | **DCF** → enterprise value |
| `valuation_model.py` | net debt → **equity value → implied share price**, plus a WACC × growth sensitivity grid |
| `export_finance_report.py` | ROIC, NPV/IRR, and the Power BI export — the production script |

## 6. The finance math (as coded in `models/export_finance_report.py`)

- **Risk-free rate**: the ML model's predicted 10Y Treasury, divided by 100
  to convert percent → decimal.
- **Cost of equity (CAPM)**: `risk_free + beta × equity_risk_premium`.
  Beta scales the market-wide risk premium up or down for this company's
  riskiness (beta 1.2 = 20% more volatile than the market).
- **Cost of debt**: `risk_free + credit_spread` — what lenders charge over
  the safe rate.
- **WACC**: `70% × cost_of_equity + 30% × cost_of_debt × (1 − tax_rate)`.
  The `(1 − tax)` is there because interest is tax-deductible, so debt's
  true cost is lower than its stated rate.
- **DCF**: each forecast free cash flow is discounted:
  `FCF_t / (1 + WACC)^t`. After year 5 a **terminal value** captures all
  later years using the Gordon growth formula
  `FCF₅ × (1 + g) / (WACC − g)`, itself discounted back 5 years.
  The sum is **enterprise value** (value of the whole business).
- **Equity bridge**: `equity value = EV − debt + cash`, then
  `implied share price = equity value / shares outstanding`.
- **ROIC**: `NOPAT / invested capital`, where
  `NOPAT = EBIT × (1 − tax)` — after-tax operating profit. The report also
  shows `ROIC − WACC`: a business creates value only when this spread is
  positive (it earns more on capital than the capital costs).
- **IRR**: the discount rate at which a project's NPV equals zero. There is
  no algebraic formula, so `calculate_irr` finds it by **bisection**: keep a
  low guess (NPV positive) and a high guess (NPV negative), test the
  midpoint, keep the half that still brackets zero, repeat until the answer
  stops moving. A classic numerical-search algorithm worth understanding.

Note which numbers are currently **hard-coded placeholders** — the FCF list
`[100, 110, 121, 133, 146]`, debt 500, cash 150, 100M shares, $1.5B invested
capital, revenue 1000. The financial-statement layer being built next exists
to *derive* these from real client financial statements instead.

## 7. Step 5 — The Power BI handoff

`export_finance_report.py` writes everything to
`reports/finance_scenario_report.csv`: 3 rows (Lower Rate / Base / Higher
Rate) × 32 columns (inputs, assumptions, and outputs side by side, rates
pre-converted to percentages so Power BI needs no math). `ML Tool.pbix`
reads that CSV. The CSV is therefore a **contract**: rename a column in
Python and a Power BI visual silently breaks. `tests/test_report_schema.py`
locks the schema so that can't happen unnoticed.

## 8. Running everything

```bash
pip install -r requirements.txt

python src/generate_history.py           # 1. synthesize macro history
python src/build_history_database.py     # 2. load it into SQLite
python models/train_treasury_model.py    # 3. train + save the ML model
python models/export_finance_report.py   # 4. scenarios → valuation → CSV
pytest                                   # 5. verify nothing broke
```

Then open `reports/ML Tool.pbix` and refresh to pull the new CSV.

## 9. The financial-statement layer (Phase 1: `src/financials/`)

This is the start of the pipeline that will eventually replace the
hard-coded valuation inputs with numbers derived from real client
financial statements. Phase 1 is the foundation: schemas, loading, and
validation. Python concepts worth studying in it:

- **`schemas.py` — data as code.** Every CSV's shape is declared once as
  frozen `@dataclass` objects (`Column`, `TableSchema`). "Frozen" makes
  them immutable — nothing can quietly change a schema at runtime. The
  loader and validator both read this registry, so there is exactly one
  definition of "valid" in the whole codebase.
- **`validator.py` — collect, don't crash.** Each rule returns a list of
  `Issue` records instead of raising on the first problem. One load
  surfaces *every* error at once (an analyst fixes the file in one pass),
  and the same records can later be exported for audit. Compare
  `check_duplicate_keys` (pandas `duplicated()`) and `_missing_refs`
  (set membership with `isin`) — two patterns you'll reuse constantly.
- **`loader.py` — validate as text, then type.** CSVs are read with
  `dtype=str, keep_default_na=False` so validation sees the file exactly
  as written; pandas' type guessing would hide problems (an account code
  `0400` becoming the number 400). Only after checks pass are columns
  coerced to real numbers and dates.
- **Fail loudly.** With `strict=True`, any ERROR raises
  `ClientFSValidationError` listing every problem with CSV line numbers.
  Silent bad data is the one thing a financial pipeline can never allow.
- **Lineage.** Every raw row carries source_file / sheet / row / load_id.
  Run `python src/load_client_fs.py` to watch one number (Beispiel GmbH
  FY2025 revenue, €300) get traced from standardized concept back to the
  workbook cell it came from.
- **Tests as specification.** `tests/test_client_fs_validator.py` breaks
  the fixture data one way per test and asserts the exact rule fires.
  Read it as the executable list of what the loader guarantees.
- **`sign_normalizer.py` — policy as a pure function.** Two sources can
  present the same expense as `+50` or `-50`; both must normalize to the
  same canonical value without ever double-flipping a sign. The whole
  policy is one small deterministic function plus a documented convention
  (docs/SIGN_CONVENTION.md) — worth studying as an example of separating
  a business rule from the pipeline that will apply it (Phase 2).

The fixture company (COMP001, a USD parent with a EUR subsidiary and an
elimination entity) is small enough to check by hand: both balance sheets
balance, retained earnings roll forward (170 + 135 − 45 = 260), and the
cash-flow statement walks beginning cash to ending cash (120 + 30 = 150).
Later phases (controls, FX, consolidation) will verify those same
identities in code.

## 10. Account mapping + sign normalization (Phase 2)

Run `python src/build_client_fs_normalized.py` and study these ideas:

- **Resolution with precedence (`account_mapper.py`).** Two pandas
  left-merges — one keyed by (company, system, code), one by (system,
  code) — line up row-for-row with the raw data because mapping keys are
  unique. `Series.where(~use_specific, by_company[col])` then picks the
  company-specific answer wherever one exists and the reusable default
  otherwise. That's the override pattern databases call "most specific
  wins," in four lines of pandas.
- **Policy stays in one function.** The builder applies signs by calling
  `normalize_sign` in a plain loop rather than re-implementing the rule
  vectorized — 67 rows don't need speed, and one implementation of a
  business rule beats two that can drift apart.
- **Identities as sums.** The payoff of the canonical convention: net
  income is `groupby(entity, period).sum()` over IS rows — no special
  cases, no sign juggling. Same for the cash walk and the balance-sheet
  gap. The Phase 4 control engine will formalize exactly these checks.
- **Derived data locked by a test.** The committed
  `client_fs_normalized.csv` must byte-match a fresh rebuild
  (`test_committed_output_matches_a_fresh_rebuild`), the same trick that
  protects the Power BI report CSV — generated files in a repo either
  stay verifiably current or the suite goes red.

## 11. FX translation + consolidation (Phase 3)

Run `python src/build_consolidation.py` alongside
docs/FX_AND_CONSOLIDATION.md. The finance ideas are the stars here:

- **Different items, different rates.** IS at average, BS at closing,
  common stock at historical, retained earnings rolled forward — and the
  balance sheet *deliberately* stops balancing. The gap is the CTA, an
  equity line the engine emits explicitly rather than hiding.
- **The variance teaches.** The fixture's source file used the common
  shortcut of translating everything at closing rate. The engine never
  overwrites it — it shows, row by row, that the shortcut's equity
  variances (8.00 + 2.75) are *exactly* the CTA (10.75). A test pins it.
- **Eliminations change mix, not profit.** IC revenue −50 and IC COGS +50
  net to zero income; IC AR/AP knock 10 off both sides of the balance
  sheet. Consolidation = Σ entities + eliminations + CTA, each in its own
  column so a reviewer sees what was removed and why.
- **Python-wise**, study the roll-forward loop (state carried across
  periods in order — a pattern vectorization handles badly and a plain
  loop handles clearly) and the `groupby(...).sum()` + `merge(how="outer")`
  assembly of the consolidation columns.

## 12. The control engine (Phase 4)

Run `python src/run_controls.py` next to docs/CONTROLS.md. What to study:

- **Identities become code.** Every check we'd been doing ad hoc —
  balance sheet, cash walk, NI tie, RE and debt rolls, consolidation,
  FX, source integrity — is now one small function returning structured
  `ControlResult` records with expected/actual/variance/tolerance/status.
  Accounting identities are the *original* unit tests; here they run
  against data instead of code.
- **Three statuses, one philosophy.** PASS within tolerance; FAIL beyond
  it; and REVIEW for "cannot be tested with the data on hand" or "known,
  documented cause needing sign-off." The engine never adjusts a number
  to make a control pass — exceptions go to a human.
- **Local currency for roll-forwards.** C2/C4/C6 test each entity in its
  own currency so FX can't mask (or fake) a broken roll; C9 separately
  compares the source's translation against the engine's — and the
  fixture's variance is *exactly* the CTA, the identity the audit proved.
- **Independent recomputation.** C8 rebuilds the consolidation roll-up
  through its own groupby rather than trusting the builder's arithmetic —
  the difference between re-running code and actually checking it.
- **Corruption tests.** `tests/test_controls.py` breaks the data one way
  per test and asserts exactly the right control flips to FAIL with
  exactly the right variance — the executable proof the controls detect
  what they claim to.

## 13. NWC → NOPAT → UFCF (Phase 5)

Run `python src/build_ufcf.py` next to docs/PAGE_FLOW.md — the output IS
the walk the client page will render. What to study:

- **The chain is one calculation.** Revenue − operating costs = EBITDA;
  − D&A = EBIT; × (1 − tax) = NOPAT; + D&A − CapEx − ΔNWC = UFCF. Each
  step is a few lines in `ufcf.py`/`nwc.py` reading the consolidated
  statements — the first statement-derived UFCF in this project
  (FY2025: 156.35), sitting where a hard-coded list used to be.
- **Classification beats hard-coding.** NWC membership comes from
  `account_mapping.nwc_classification` — cash and debt are excluded
  because the mapping says so, not because code names them. A test
  proves deleting cash/debt rows changes nothing.
- **Two tax rates on purpose.** The reported effective rate (24.70% in
  FY2024!) is what happened; the normalized driver rate (25%) is what
  you forecast with. The walk shows both; NOPAT uses the driver.
- **Drivers are data.** The forecast reads growth/margin/CapEx/tax from
  `data/scenarios/scenario_assumptions.csv`, each with a rationale and
  source — driver-based forecasting (volume × price thinking), never
  "revenue grows 10% because I typed 10".
- **Honest blanks.** FY2024 has no cash-flow statement, so its CapEx and
  UFCF are blank — the engine never invents a number to finish a row.

## 14. Net debt, invested capital, diluted shares (Phase 6)

Run `python src/build_valuation_inputs.py`. The concepts:

- **Not every liability is debt.** Net-debt membership is an explicit
  election in the mapping (`netdebt_classification`); AP never counts,
  and restricted cash is shown but not netted by default. Result:
  net debt 172.0, where the DCF still assumes 350.
- **Invested capital is a build, not a number.** NWC 159.5 + net PP&E
  632.0 = 791.5 — so real ROIC is 23.56%, not the 10% the report shows
  from the manual $1.5B. Basis is configurable (ENDING vs AVERAGE)
  because project ROIC differs from company ROIC.
- **The treasury-stock method in one line.** Exercising 5M options at
  $12 raises $60M, which buys back 60/18 = 3.33M shares, so only
  5 × (1 − 12/18) = 1.6667M net shares appear. Anti-dilutive options
  (strike ≥ price) are excluded, never allowed to shrink the count. And
  the file must reproduce from its own inputs — a tampered share count
  refuses to load.
- **Weights should be derived.** E = price × diluted shares = 1,866.0 →
  E/(D+E) = 83.6% vs the assumed 70/30. The comparison is displayed;
  switching WACC to it is a deliberate approval, like every cutover.
- **Methodology as data.** `data/market/risk_free_policy.csv` makes the
  risk-free-rate choice a selectable, documented row instead of an
  implicit model call.

## 15. The outlier engine (Phase 7)

Run `python src/run_outliers.py` next to docs/OUTLIERS.md:

- **Two bars, not one.** A movement flags only when BOTH the percent and
  the dollar thresholds clear — revenue +$80M (6.7%) stays quiet, cash
  +$41.6M (27.3%) flags. One bar alone drowns you in noise.
- **The loudest flags are innocent.** Retained earnings jumps 53–89%
  and flags HIGH — and Control C4 already proved those rolls tie
  exactly. That's the design lesson: an outlier is a question; the
  controls, the agent, and finally a human answer it.
- **NEW_ITEM is the M&A hook.** The elimination entity's ±$50M
  intercompany activity appearing from nowhere is exactly the
  "goodwill/revenue jumped — was there a deal?" pattern Phase 9 pairs
  with transaction events.
- **Statistical honesty.** Z-score refuses to run on 2 periods of
  history (needs 4) and says so, instead of inventing a standard
  deviation from nothing.

## 16. What comes next

Adjustments and the reported/normalized/pro-forma views (Phase 8), M&A
events (Phase 9), the analyst agent (Phase 10) — then the Power BI
pages render the walks these layers already produce, and the four
staged cutovers (UFCF, net debt, shares, weights) re-price the DCF one
approved switch at a time.

## 17. Page 2 — reading a report page as a proof (2026-09-01)

Page 2 of the .pbip ("Page 2 — Client Financials") is the on-screen
version of section 5's math, built so you can audit it by reading:

- **Every card is a measure, every measure reads a curated CSV.** The
  DAX pattern is always the same two steps: find the anchor row
  (`Latest Actual Period` = MAX period where forecast_method = ACTUAL),
  then pick one column from that row with
  `CALCULATE(MAX(column), FILTER(ALL(table), period = anchor))`. The
  `ALL()` matters: it makes the walk immune to accidental
  cross-filtering when someone clicks the tables on the page.
- **Equation rows are cards + operator cards.** The +, −, ×, ÷, =
  glyphs are themselves tiny measures (`"+"` literals) on cards with
  labels hidden — so a row like `NOPAT + D&A − CapEx − ΔNWC = UFCF`
  is data-driven end to end, and `UFCF Bridge Check ($M)` recomputes
  it and must show 0.00 (same trick as Page 1's DCF Model Check).
- **Nothing is hard-coded to a year or a company.** Change the client,
  rerun the pipeline, refresh — the page re-anchors itself.
- **The gate comes first.** Controls PASS/REVIEW/FAIL cards sit above
  the statements because that is the ICFR logic: numbers are only
  usable because the controls say so.

## 18. Rolling windows — smoothing as an honest trade-off (2026-09-01)

Page 5 charts every macro series as a trailing 24-month mean
(`rolling(24, min_periods=24).mean()`). Three lessons baked in:

- **min_periods equals the window.** The first 23 months stay blank.
  Pandas would happily average whatever it has, but a 12-month mean
  labeled "24-month average" is a different statistic wearing the
  wrong name — the same honesty rule as the z-score refusing to run
  on 2 periods.
- **Smoothing trades noise for lag.** The 24-month line shows regime
  shape (the 2020 shock, the hiking cycle) but turns well after the
  raw series does. Both columns ship in the export so the report can
  overlay raw vs smoothed and show the lag rather than hide it.
- **Derived synthetic series are labeled and formulaic.** The four
  added series (PCE, GDP growth, IG/HY spreads) come from documented
  formulas over the existing seed-42 history — coherent co-movement
  for teaching, never passed off as market data, replaced by FRED at
  the live cutover.

## 19. Appraising your own project (2026-09-01)

Page 6 turns the report into a tool you can feed. The workflow:

1. Describe the project in `data/projects/project_master.csv` — name,
   initial investment, horizon — and what it CHANGES in
   `project_assumptions.csv` (incremental revenue and/or cost savings,
   margin, maintenance capex, NWC intensity). Levers you omit are zero.
2. `python src/build_project_appraisal.py` — the engine builds the
   incremental UFCF path (working capital comes back when the project
   ends) and judges it per scenario with the SAME tax, WACC and hurdle
   as the company DCF.
3. Refresh the report; Page 6 shows the verdicts.

The lesson baked into the fixtures: the big expansion earns an
accounting return above WACC yet fails NPV and IRR — a return computed
over a depreciating book value flatters late years while the cash
never repays the hurdle. When the tests disagree, cash rules; that is
why the recommendation logic never lets ROIC alone approve a project.

## 20. Sensitivity: the error bar around one number (2026-09-02)

A DCF outputs one price, but that precision is borrowed. Page 4's grid
re-runs the identical math at +/-1pt of WACC and +/-1pt of terminal
growth: $28.86 sits at the center of a ~$22-$41 range. Two lessons:
(1) the two most powerful assumptions in any DCF are the discount rate
and the terminal growth rate - everything else moves the price by
cents; (2) pin recomputations to their source - the suite forces the
grid's center cell to equal the reported price, so the page can never
show a grid from one model version beside a price from another.

## 21. Intake forms beat raw files (2026-09-02)

Page 6's project intake went from "edit two CSVs" to "fill a form":
the Excel template names every field, shows a worked example, and the
ingest script validates with the same fail-loud rules as every other
loader - and rolls back untouched if anything is rejected. The
pipeline itself never changed; only the on-ramp did. That separation
(strict core, friendly edge) is how production finance systems stay
both usable and trustworthy.

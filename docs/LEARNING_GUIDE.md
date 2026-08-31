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

## 11. What comes next

FX translation and consolidation (Phase 3), the control engine
(Phase 4), then NWC → NOPAT → UFCF (Phase 5) — each phase upgrading one
placeholder in the valuation model into a number traced all the way back
to a source statement.

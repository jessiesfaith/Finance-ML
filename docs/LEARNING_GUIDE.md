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

## 9. What comes next

The financial-statement layer (see the phased plan) adds ingestion,
account mapping, FX translation, consolidation, controls, and forecasting
*upstream* of step 6, so the placeholder inputs become numbers traced all the
way back to a client's actual statements — file, sheet, and row.

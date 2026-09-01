# Finance-ML

A finance and data-science learning project that connects **machine learning to
corporate finance**: an ML model predicts the 10-year Treasury yield from
macroeconomic conditions, and that prediction drives WACC, DCF valuation,
ROIC, and IRR analysis across rate scenarios — reported in Power BI.

A client **financial-statement ingestion, normalization, controls, and
forecasting layer** is being built upstream, so valuation inputs that are
placeholders today will be derived from real financial statements.

## How it works

```
macro data → SQLite → ML model (predict 10Y Treasury = risk-free rate)
          → CAPM / WACC → DCF → enterprise & equity value → implied share price
          → ROIC vs WACC, project IRR
          → reports/finance_scenario_report.csv → Power BI (ML Tool.pbix)
```

New to the codebase? Start with **[docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md)**
— a plain-language walkthrough of every file, the ML mechanics, and the
finance math. Design decisions live in **[docs/DECISIONS.md](docs/DECISIONS.md)**.

## Repository layout

| Folder | Contents |
|---|---|
| `data/` | SQLite database + raw macro CSVs (synthetic for now, seed 42) |
| `data/client_fs/` | client financial-statement CSV layer (synthetic COMP001 fixture) |
| `src/` | data generation, database build, loaders, shared calcs |
| `src/financials/` | financial-statement pipeline: schemas, loader, validation |
| `sql/` | SQL queries kept in their own files |
| `models/` | ML training + the scenario/valuation "learning ladder" scripts |
| `reports/` | `finance_scenario_report.csv` (Power BI contract) + `ML Tool.pbix` |
| `tests/` | pytest suite, including the Power BI schema lock |
| `docs/` | learning guide, schemas, Power BI contract, decisions log |

## Quick start

```bash
pip install -r requirements.txt

python src/generate_history.py           # synthesize macro history
python src/build_history_database.py     # load it into SQLite
python models/train_treasury_model.py    # train + save the Treasury model
python models/export_finance_report.py   # scenarios → valuation → report CSV
python src/load_client_fs.py             # load + validate client financial statements
python src/build_client_fs_normalized.py # map accounts, normalize signs, apply adjustments -> 3 views
python src/build_consolidation.py        # FX translation + eliminations -> consolidation CSV
python src/run_controls.py               # deterministic controls -> control_checks.csv
python src/build_ufcf.py                 # NWC/NOPAT/UFCF walk + driver forecast -> ufcf_forecast.csv
python src/build_valuation_inputs.py     # net debt, invested capital/ROIC, diluted shares
python src/run_outliers.py               # deterministic outlier flags -> outlier_flags.csv
python src/run_agent.py                  # analyst-review agent -> agent_review_log.csv
python src/build_powerbi_exports.py      # curated, schema-locked Power BI report tables
python src/ingest_sec.py --cik N --from Y1 --to Y2   # stage a public company from SEC EDGAR
python src/build_market_data.py          # append-only market observations (synthetic until cutover)
pytest                                   # verify nothing broke
```

Then open `reports/ML Tool.pbix` in Power BI Desktop and refresh.

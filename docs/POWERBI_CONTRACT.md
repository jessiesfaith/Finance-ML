# Power BI Interface Contract

The rule that keeps the reporting layer stable while the financial-data
architecture underneath it keeps evolving.

## The contract

**Power BI only ever reads curated output tables in `reports/`.**
It never reads `data/client_fs/*` (raw or normalized financial-statement
files), `data/*.db`, or any intermediate calculation. Python is the only
thing that writes to `reports/`.

```
data/client_fs/*  →  src/financials pipeline  →  reports/*.csv  →  Power BI
   (volatile — schemas may change            (STABLE — schema-locked
    during Phases 1–10)                       by tests, append-only)
```

Why: during Phases 1–10 the internal table structures WILL change
repeatedly (NWC, consolidation, controls, M&A, shares…). If Power BI read
them directly, every internal change would break the report and force
rework. The curated layer absorbs all of that churn.

## Current contract tables

| File | Consumed by | Locked by |
|---|---|---|
| `reports/finance_scenario_report.csv` | ML Tool.pbix (Pages 1/3 today) | `tests/test_report_schema.py` (33 columns, 3 scenario rows) |
| `reports/client_fs_statements.csv` | Page 2 — statement detail, three views via flags | `tests/test_powerbi_exports.py` |
| `reports/client_fs_income_walk.csv` | Page 2 — Reported/Normalized/Pro Forma income summary | same |
| `reports/client_fs_ufcf.csv` | Page 2 — NWC/NOPAT/UFCF walk + driver forecast | same |
| `reports/client_fs_valuation_inputs.csv` | Pages 2/3 — net debt, invested capital/ROIC, shares | same |
| `reports/client_fs_controls.csv` | Page 2/6 — control status strip + exceptions | same |
| `reports/client_fs_review.csv` | Page 2/6 — agent findings with confidence | same |
| `reports/market_rf_policy.csv` | Page 1 — risk-free methodology lineage | same |
| `reports/market_history_rolling24.csv` | Page 5 — macro history, rolling 24-month averages | `tests/test_market_history.py` |
| `reports/client_fs_projects.csv` | Page 6 — project appraisal per scenario | `tests/test_projects.py` |
| `reports/client_fs_sensitivity.csv` | Page 4 — WACC × growth sensitivity grid | `tests/test_sensitivity.py` |
| `reports/market_history_windows.csv` | Page 5 — windowed history behind the 03M/06M/12M/24M/YTD toggle | `tests/test_market_history.py` |
| `reports/client_fs_option_sensitivity.csv` | Options page — per-option NPV grid (rates × delivery) | `tests/test_projects.py` |
| `reports/client_fs_option_verdicts.csv` | Options page — APPROVE/REJECT strip under each grid | `tests/test_projects.py` |
| `reports/client_fs_option_sizing.csv` | Options page — amount scenarios per option (price grid for M&A) | `tests/test_projects.py` |

Every curated file is written only by `python src/build_powerbi_exports.py`,
carries a `value_class` column (the six-class taxonomy) so the report can
label every number's nature, and follows the same rule as the legacy file:
columns are only ever ADDED, never renamed or removed.

## Rules during the financial-statement build (Phases 1–10)

1. `reports/ML Tool.pbix` and the existing semantic model are **read-only
   downstream artifacts**. No phase modifies them.
2. `finance_scenario_report.csv` keeps full backward compatibility —
   columns are never renamed, removed, or reordered. The schema-lock test
   enforces this on every `pytest` run.
3. New pipeline outputs become **new** files (`reports/client_fs_*.csv`),
   each added to this document and given its own schema-lock test the day
   it is created (Phase 11).

## Later: the Power BI Project (.pbip) phase

Once the output tables are stable, `ML Tool.pbix` stays as the backup and
a Power BI Project copy is saved at `reports/ML Tool/` (`ML Tool.pbip` +
`ML Tool.Report` + `ML Tool.SemanticModel`), moving report development
into source control. The manual copy/paste-DAX workflow disappears:
measures, relationships, calculated columns, Power Query/M, display
folders, and formatting metadata get authored directly in the project
files, then reviewed on refresh in Power BI Desktop.

Planned measure organization (display folders), so the Fields pane scales
past 100 measures: Client Financials · Working Capital · Free Cash Flow ·
Cost of Capital · Valuation · ROIC · Capital Allocation · Controls · FX ·
M&A · Industry Benchmarking.

Division of labor:

- **Code-first (Claude authors directly):** Power Query/M, the TMDL
  semantic model — tables, relationships, field definitions, DAX measures —
  and report-page scaffolding where practical.
- **Code-assisted + visual QA (Power BI Desktop, human-owned):** the
  report-layout JSON is treated as fragile — more fragile than DAX/TMDL —
  so chart rendering, spacing/alignment, card sizing, slicer behavior,
  conditional formatting, and visual polish are verified and finished
  visually in Power BI Desktop.

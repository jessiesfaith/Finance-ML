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
| `reports/finance_scenario_report.csv` | ML Tool.pbix | `tests/test_report_schema.py` (32 columns, 3 scenario rows) |

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

Once the output tables are stable, `ML Tool.pbix` gets a `.pbip` copy in
the repo and report development moves into source control, with this
division of labor:

- **Code-first (Claude authors directly):** Power Query/M, the TMDL
  semantic model — tables, relationships, field definitions, DAX measures —
  and report-page scaffolding where practical.
- **Code-assisted + visual QA (Power BI Desktop, human-owned):** the
  report-layout JSON is treated as fragile — more fragile than DAX/TMDL —
  so chart rendering, spacing/alignment, card sizing, slicer behavior,
  conditional formatting, and visual polish are verified and finished
  visually in Power BI Desktop.

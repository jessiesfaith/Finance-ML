# Finance-ML — repo notes for Claude

Finance/data-science learning project: Treasury-rate ML → WACC/DCF/ROIC/IRR
valuation → Power BI, with a client financial-statement ingestion layer being
built upstream in phases. The owner is building this **to learn Python, ML,
and report building** — write teaching-style code and docs (see the existing
idiom: heavily sectioned scripts, plain-language comments), and keep
`docs/LEARNING_GUIDE.md` updated when adding modules.

## Facts
- Python 3.11+, deps in `requirements.txt` (pandas, numpy, scikit-learn,
  joblib, pytest). No lint config; no packaging — `conftest.py` puts `src/`
  on the import path (`from financials import ...`).
- Test: `pytest` from repo root. Demo load: `python src/load_client_fs.py`.
- Existing ML/valuation scripts in `models/` are a deliberate learning
  ladder with duplicated code — do NOT refactor them.
  `models/export_finance_report.py` is the production export.
- Macro history is synthetic (`src/generate_history.py`, seed 42). Live
  market data arrives in a later phase (docs/DECISIONS.md #5).
- Phase plan + status: docs/DECISIONS.md. Schemas: docs/SCHEMAS.md.

## Hard rules
- `reports/ML Tool.pbix` and the Power BI semantic model are downstream and
  **read-only** until the financial-statement pipeline is stable
  (docs/POWERBI_CONTRACT.md). Power BI consumes only curated
  `reports/*.csv`; never point it at `data/client_fs/*`.
- `reports/finance_scenario_report.csv` keeps backward compatibility —
  its 32-column schema is locked by `tests/test_report_schema.py`.
- `data/client_fs/client_fs_raw.csv` (raw source financials) is immutable:
  code only reads it; adjustments live in separate tables.
- Never silently fix failed validations/controls — surface exceptions.
- All fixture data is fictional (COMP001 Example Company). Never add real
  company financials, invented market data, or secrets.
- Work on `claude/…` branches; the owner reviews before merge to `main`.

# Next session — owner's list (queued 2026-09-01, night)

1. **Calc-flow legibility.** Make the math easier to read straight off
   the cards - the equation rows should read as one visible calculation
   flow (grouping/spacing/arrow treatment; confirm exact preference
   with the owner before building).

2. **Abbreviation legend on every page.** A compact legend per page:
   EBITDA, D&A, NOPAT, NWC, UFCF, WACC, CAPM, ERP, TSM, EV, IRR, NPV,
   ROIC, OAS, U-3/U-6, SAAR, PCE, PPI, JOLTS, PMI - each one line,
   plain English.

3. **Page 6 intake process.** The owner wants a fill-out process, not
   raw CSVs: build a form-style intake (e.g. an Excel template with
   labeled fields + a converter script into data/projects/*.csv, or an
   interactive prompt script), so adding a project feels like filling
   out a form. The engine and validation already exist - this is the
   on-ramp.

4. **Sensitivity analysis.** Recommendation (per PAGE_FLOW: the
   decision page "ends with sensitivity"): a WACC x terminal-growth
   grid on implied share price / EV belongs on PAGE 4 beside the DCF
   result; a second grid (hurdle x growth on project NPV) can join
   Page 6 later. Pipeline computes the grid into a curated export;
   a matrix visual renders it.

State at close: main = 28ce652, suite 195/195, six pages live and
refreshing (owner mid review-and-reconcile of Pages 1-5; Page 6 fixture
verdicts confirmed on screen). Desktop-vs-git rule in force: never both
at once.

## Status 2026-09-02: ALL FOUR DONE (plus mid-session additions)

1. Calc flow -> THE FLOW strips on pages 2/3/4 (one line under each
   title; further styling is Desktop polish, owner's lane).
2. Legends -> on pages 2, 3, 4, 5, 6.
3. Page 6 intake -> templates/project_intake.xlsx +
   src/ingest_project_intake.py (upsert + rollback; round-trip tested).
4. Sensitivity -> Page 4 grid, center-pinned to the Base price.
Also: Page 5 widened to 21 panels (commodities, tech/AI indices,
placeholder firm slots - DECISIONS #71); 'Selected ' prefix retired
from 15 measures (#72). Owner still owes Desktop QA of all of today's
changes; requires a full Refresh (partitions changed).

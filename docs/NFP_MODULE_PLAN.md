# NFP CFO Decision Intelligence Module — Build Plan

Response to the master build prompt (owner, 2026-09-05). Section 3 of
that prompt requires this plan before implementation.

## 1. Existing architecture (inspected)
Deterministic Python engine (`src/financials/*`) → curated CSV exports
(`reports/*.csv`, add-only columns) → Power BI semantic model (TMDL
tables, one per export) → PBIR report pages (generated visual.json,
chained left→right top→bottom rows) → pytest suite (240 tests incl. a
whole-.pbip integrity parser) → provenance discipline (value_class on
every row; SYNTHETIC/ASSUMPTION/MODEL_OUTPUT labels; append-only market
data) → flags engine (red/yellow/green notification feed) → chat
assistant + local chat page grounded in the same CSVs.

## 2. Existing data model
Corporate fixture: client_fs_* statements/UFCF/projects/options,
finance_scenario_report, market history, flags, review/controls.
Nothing NFP-specific exists; nothing needs to change for it.

## 3. Existing calculation engine
NPV/IRR/payback + three-tests appraisal (projects.py), scenario &
sensitivity grids, WACC/DCF/P/E chain, funding_capacity(), review
agent, flags rulebook. NPV/discounting and the
scenario/sensitivity/appraisal PATTERNS are reused; NFP math is a new
module because the drivers differ (mission, restricted funds, grants).

## 4. Reused components
CSV-export architecture + integrity tests; page-generator idiom
(deterministic visual ids `finance-ml-nfp:<label>`); provenance
columns; flags feed pattern; chat assistant (new exports join its
bundle); docs discipline (DECISIONS.md); test patterns incl.
committed-export-matches-fresh-build.

## 5. New components
- `data/nfp/` seed inputs (org-agnostic schemas; JSV as first case)
- `src/financials/nfp.py` deterministic engine (all module math)
- `src/build_nfp.py` builder → 16 `reports/nfp_*.csv` exports
- `tests/test_nfp.py` (formula pins from the prompt's worked examples,
  controls, schema, committed-match)
- 16 TMDL tables + 4 report tabs (9 Capital Allocation · 10 Programs &
  Funding · 11 Campaign & Financing · 12 Executive Decision)

## 6. Database changes
None destructive. New tables only; existing tables untouched.

## 7. Navigation
New tabs appended after "8 · Calc Build-Out", before Legacy.

## 8. Page layouts
Chain-rule rows, tables for portfolios/comparisons (not card walls):
- Tab 9: capital & global inputs → five aligned alternatives →
  financial/mission/risk metrics → scoring → scenarios → sensitivity →
  recommendation → board practice
- Tab 10: program portfolio → economics → classification → grants →
  funding cliff → pipeline → calendar → solutions → board practice
- Tab 11: campaign → sources & uses (control) → pledges → project cash
  → financing alternatives → liquidity → debt & reserves → scenarios
- Tab 12: executive decision page — position, risks, actions, the ten
  board questions answered, CFO 60–90s script, debate mode Q&A,
  controls status

## 9. Missing data (seeded SYNTHETIC until client data arrives)
Program-level P&L, donor detail, pipeline, pledge schedules, monthly
project cash, current grant terms. All such rows carry
value_class=SYNTHETIC — same placeholder discipline as T1–T5 tickers.

## 10. Assumptions requiring user input
Available capital ($250K default, globally editable in
data/nfp/nfp_settings.csv), analysis period (5y), board discount rate,
inflation, expected return, cash yield, months-cash policy, risk
appetite, decision weights (must total 100 — enforced), scalability &
strategic-alignment ratings, mission scores. All ASSUMPTION-labeled.

## 11. External research requirements (JSV)
Seeded from the owner-provided prompt only, labeled
PUBLIC_RESEARCH/HISTORICAL with source+confidence, never invented:
Chai House (2024 Santa Clara County Jewish Community Study; AMOUNT NOT
PUBLICLY CONFIRMED), Second Pool campaign (~$7.0M goal, ~$2.34M
restricted at 2023-06-30 → ~$4.66M remaining, HISTORICAL — CURRENT
STATUS REQUIRES UPDATE), JCRIF loan (~$600K original, ~$200K at
2023-06-30, since repaid — HISTORICAL DEBT NOT CURRENT DEBT),
board-designated reserve (~$697K at 2023-06-30). Grant prospects (Cal
OES NSGP, SVCF, Jewish LearningWorks, Koret, Jim Joseph, Federation,
local government, corporate) enter as status=RESEARCH with amounts =
RESEARCH REQUIRED. A dedicated research pass (990s, Cal OES cycles,
foundation portals) is Phase 2 — no unverified number ships as
actionable.

## 12. Calculation controls (§61, enforced in code + tests + report)
Sources=Uses · weights=100 · restricted never counted as unrestricted
liquidity · program totals reconcile to org totals · pledges reconcile
(collected+outstanding=pledged) · cash/debt rolls (beginning+activity=
ending) · scenarios flow through · division-by-zero guards · no
RESEARCH-status funder shown with a confirmed amount · no current debt
from historical-only data.

## 13. Testing approach
Pin the prompt's own worked examples ($200K×70%=$140K → $110K gap;
$250K×7%=$17.5K interest avoided; $1M×7%×6/12=$35K LOC; $7.0M−$2.34M=
$4.66M), controls raise on violation, schema checks, committed-export
match, integrity suite over the enlarged .pbip.

## 14. Implementation sequence (prompt §63 order)
Phase 1 (this build): data → validation → deterministic calcs →
program analysis → funding/grants → capital allocation → campaign/
financing → risk → scenarios → sensitivity → solution engine → board
practice → tabs → chat-bundle wiring.
Phase 2: JSV public-research pass with URLs; fundraising cash forecast
feeding a rolling org forecast; calendar 30/60/90 views; flags-engine
NFP rules.
Phase 3: "Board asks a question" interactive mode (assumption toggle →
re-run → CFO response); tornado visual.
Phase 4: ML scaffolding (schemas already carry the labels: ACTUAL /
HISTORICAL / MANAGEMENT ASSUMPTION / MODEL ESTIMATE / PUBLIC_RESEARCH)
— models remain out until the owner approves each one.

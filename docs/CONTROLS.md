# Financial Controls (Phase 4)

Deterministic checks that run BEFORE any AI/agent analysis. Each control is
a reusable Python function (`src/financials/controls.py`) producing
structured results written to `data/client_fs/control_checks.csv`, so
controls can be tested, logged, reviewed, and displayed in Power BI —
never embedded only in report visuals.

Run everything with:

```bash
python src/run_controls.py
```

## Statuses — and the no-silent-fix rule

| Status | Meaning |
|---|---|
| PASS | the identity holds within tolerance |
| REVIEW | the identity cannot be (fully) tested with the data on hand, or the variance has a known documented cause needing human sign-off |
| FAIL | the identity is violated beyond tolerance |

A REVIEW or FAIL is an **exception for a human**. The pipeline never
adjusts a number to make a control pass — the same philosophy as unmapped
accounts and statement-type mismatches. `review_status`
(PENDING/APPROVED/REJECTED) records the human's verdict;
`agent_comment` carries the engine's explanation today and, from
Phase 10, the analyst agent's *appended* interpretation (the agent can
never change `status`).

Tolerances: identities use ±0.01 (rounding noise only); the FX
source-vs-engine comparison (C9) uses ±0.50 because a source's
translation shortcut is an expected, documented difference until Phase 3
methodology is adopted upstream.

## The controls

Roll-forward controls (C2/C4/C6) run in **local currency**: an entity's
books must roll in their own currency regardless of translation, so FX
can neither mask nor fake a broken roll. Translation quality is C9's job.

| # | Control | Identity tested | Fixture result |
|---|---|---|---|
| C1 | Balance sheet | Assets − Liabilities − Equity = 0, per entity/period (REPORTED view, canonical signs) | PASS ×5 |
| C2 | Cash flow | beginning cash + CFS total = ending cash | PASS (parent); **REVIEW** for the GmbH — its cash moved €30→€40 with no cash-flow statement on file |
| C3 | Net income | IS net income = CFS net income | PASS |
| C4 | Retained earnings | begin RE + NI + dividends = end RE (dividends canonically negative) | PASS both entities (170+135−45=260; 35+30=65 EUR) |
| C5 | OCI/AOCI | begin AOCI + OCI = end AOCI | **REVIEW** — OCI is reported but no `aoci` balance account is mapped yet, so the roll can't be verified (surfaced, not skipped) |
| C6 | Debt | begin debt + issuances − repayments = end debt | PASS (parent: 320−20=300); **REVIEW** for the GmbH — debt moved €65→€60 with nothing explaining it |
| C7 | Shares | begin shares + issued − repurchased + comp = end shares | **NOT YET IMPLEMENTABLE** — share data arrives with `shares_dilution.csv` in Phase 6; documented here rather than silently absent |
| C8 | Consolidation | translated entity totals + eliminations + FX adjustment = consolidated, via an **independent recomputation** (not the builder's own arithmetic); plus the consolidated balance sheet balances with CTA included | PASS ×4 |
| C9 | FX | source-reported reporting amounts vs the engine's deterministic translation, foreign-currency rows, per statement | PASS on the income statement (average-rate translation matches exactly); **REVIEW** on the balance sheet — the source's closing-rate shortcut on equity differs by exactly the CTA (7.85 / 10.75), the audit-proven identity |
| C10 | Source total | the normalized layer reconciles 1:1 to the raw statements: same row counts, same canonical totals, per entity/period/statement — the guard that Phase 8 adjustments never mutate REPORTED rows | PASS |

Fixture landscape: **34 evaluated — 29 PASS, 5 REVIEW, 0 FAIL.** Every
REVIEW is a genuine, explained data-coverage or methodology exception —
exactly the behavior the spec demands ("generate exceptions").

## Where results land

- `data/client_fs/control_checks.csv` — the full record (spec section 8
  columns), rebuild-locked by `tests/test_controls.py` so the committed
  file can never go stale.
- `entity_consolidation.csv.control_status` / `control_variance` — filled
  by `apply_consolidation_status()` from the independent per-account
  recomputation (replaces the Phase 3 `PENDING` placeholder).

## Extending

Each control is a plain function over the pipeline's frames; a new
control = one function + one line in `run_all_controls` + tests. Coming
later: C7 (Phase 6), the AOCI roll in C5 once an `aoci` account is
mapped, the C2 FX-effect line for foreign entities' cash walks, and
tolerance profiles per company (`control_checks` already records
tolerance per row).

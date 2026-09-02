# Page Flow — How the Math Starts, Flows, and Ends on Each Page

The design contract for the report pages (built at Phase 11 on the .pbip):
every page reads **left → right, then top → bottom**, each page **starts**
with labeled inputs (CLIENT F/S · MARKET · ASSUMPTION), **flows** through
visible equation rows, and **ends** by handing exactly one thing to the
next page. One connected decision chain, no orphaned numbers:

```
Historical financials → Forecast → UFCF          (Page 2)
Market data → Cost of Equity / Debt → WACC       (Page 1)
UFCF + WACC → DCF → EV → Equity → Share price
            → NPV / IRR / ROIC → Capital allocation decision   (Page 3)
```

Fixture numbers below are the pipeline's real outputs (run
`python src/build_ufcf.py`), so the walk is demonstrable today.

---

## PAGE 1 — Market, Macro & Cost of Capital

**Starts with (all labeled by source):** Treasury curve → risk-free rate
(source, maturity, observation date shown — MARKET); beta (ASSUMPTION →
peer data); ERP (ASSUMPTION, dated); credit spread (ASSUMPTION,
benchmarked vs IG/HY OAS); tax rate and E/D weights (today ASSUMPTION
70/30 — Phase 6 derives them: E/(D+E) from market capitalization and
debt, e.g. 700/(700+300) = 70%).

**Flows (equation rows):**
```
Risk-Free Rate + Beta × ERP                       = Cost of Equity
Risk-Free Rate + Credit Spread                    = Cost of Debt
Equity Weight × Cost of Equity                    = Equity contribution
Debt Weight × Cost of Debt × (1 − Tax)            = Debt contribution
Equity contribution + Debt contribution           = WACC
```
Above the equations: the macro context (inflation, Fed, curve shape,
credit, VIX) that *informs* the scenario and the assumptions — never
plugged into WACC directly (docs/MODEL_AUDIT.md map W).

**Ends with:** **WACC per scenario** (and the hurdle rate = WACC +
2.00pts) → handed to Page 3.

## PAGE 2 — Client Financials & Forecast  ← the walk to DCF

**Starts with (CLIENT F/S, every number traceable to file/sheet/row):**
the consolidated statements — after mapping, sign normalization, FX
translation, eliminations — with the control status strip
(29 PASS / 5 REVIEW / 0 FAIL) beside them: the numbers are only usable
because the controls say so.

**Flows (top → bottom, each row left → right):**

```
1  INCOME WALK          Revenue 1,274.0 − Operating costs 959.2 = EBITDA 314.8
                        EBITDA 314.8 − D&A 66.2 = EBIT 248.6
                        [reported effective tax 25.00% | analyst normalized 25.00%]
                        EBIT 248.6 × (1 − 25%) = NOPAT 186.5

2  NWC BUILD            AR 165.0 + Inventory 113.0 (+ other op. CA)
                        − AP 118.5 (− other op. CL) = Operating NWC 159.5
                        ΔNWC = 159.5 − 133.2 (prior) = +26.3   (increase = use of cash)

3  UFCF BRIDGE          NOPAT 186.5 + D&A 66.2 − CapEx 70.0 − ΔNWC 26.3 = UFCF 156.4

4  FORECAST             drivers (ASSUMPTION, from data/scenarios/): growth 6.0%,
                        EBITDA margin 25.0%, D&A 5.0% rev, CapEx 5.5% rev, tax 25.0%
                        → same walk repeated per year, FY2026–FY2030:
                        UFCF 186.2 / 197.4 / 209.3 / 221.8 / 235.1

5  RIGHT RAIL           net-debt components · diluted-share build (Phase 6) ·
                        control status · outlier flags (Phase 7) ·
                        reported vs normalized vs pro forma (Phase 8)
```

Forecasts are **driver-based, never arbitrary**: volume/price/margin
drivers with a stated rationale and source, resolved from
`scenario_assumptions.csv` — and a scenario carries BOTH company drivers
(this page) and market metrics (Page 1), so "Higher Rate" can hit UFCF
and WACC coherently.

**Ends with:** the **UFCF path** (and later net debt + diluted shares) →
handed to Page 3. This is what replaces `fcf = [100, 110, 121, 133, 146]`.

## PAGE 3 — Valuation & Capital Allocation

**Starts with:** UFCF path (Page 2) + WACC and hurdle (Page 1) + terminal
growth (ASSUMPTION).

**Flows:**
```
UFCF_t ÷ (1 + WACC)^t                       = PV of each year        (Σ = PV FCF 1–5)
UFCF₅ × (1 + g) ÷ (WACC − g)                = Terminal Value
Terminal Value ÷ (1 + WACC)^5               = PV Terminal Value
PV FCF 1–5 + PV Terminal Value              = Enterprise Value        (one scale: $B)
Enterprise Value − Net Debt                 = Equity Value
Equity Value ÷ Diluted Shares               = Implied Share Price
```
Then the three decision rows, symbols correctly paired
(docs/POWERBI_FIX_CHECKLIST.md):
```
ROIC  vs WACC   → spread → Value creating / destroying
IRR   vs Hurdle → spread → Pass / fail          (same cash-flow basis as NPV)
NPV   vs 0      →        → Accept / reject
ROIC ✓ + IRR ✓ + NPV ✓                     = Investment recommendation
```

**Ends with:** the **capital-allocation decision** — with sensitivity
(WACC × growth grid) and scenarios showing which assumptions the
recommendation depends on. NPV and IRR are read together (a small
project can have a huge IRR and little absolute value); alternatives are
compared as full business cases (investment → cash flows → risk → NPV →
IRR → ROIC → strategic fit), risk-adjusted, not ranked by headline IRR.

## Project appraisal (future layer, same discipline)

For a project/M&A case the chain starts one step earlier — **what does
the project change?** Operating drivers (headcount × cost, transactions ×
minutes saved, capacity × utilization × price) sourced from GL/HR/ops
systems, challenged, then: with-project vs without-project →
**incremental** EBITDA → EBIT → NOPAT → FCF → the same Page 3 math, run
as downside/base/upside. The scenario tables and driver registry built in
Phase 5 are exactly the mechanism this layer will reuse; purchase price
vs DCF value, synergies, and integration costs join at Phase 9 (M&A).

## Where every number class lives

| On-page label | Source | Where it lives |
|---|---|---|
| CLIENT F/S | statements, full lineage | `data/client_fs/` → consolidated |
| MARKET | dated, sourced observations | `data/market/` (Phase 6+/13) |
| ASSUMPTION | drivers with rationale + approval | `data/scenarios/` |
| CALCULATED | one formula, shown on the page | pipeline modules |
| MODEL OUTPUT | end of a visible chain | report edge |

## Engagement flow (owner redesign, 2026-09-02)

The tabs now follow a consulting engagement, not the build order:

| Step | Tab | Question it answers |
|---|---|---|
| 1 | Client Financials | what do the books say? |
| 2 | Current Position | how healthy is the client; how much capacity? |
| 3 | Market & Cost of Capital | what world are they in; what does money cost? |
| 4 | Macro History (Reference) | the context behind Step 3, on demand |
| 5 | Options | grow / automate / launch / pay down debt - what could they do? |
| 6 | Valuation & Recommendation | what is it worth; what should they do? |

Design rule (owner's, enforced everywhere): every equation row begins
with either the visible RESULT of the row above - repeated even if
redundant - or a number wearing its source label. No orphan numbers.

## Physical pages in ML Tool.pbip (superseded mapping, 2026-09-01)

The design pages above map onto the report file as:

| Report tab | Content | This doc's design |
|---|---|---|
| Page 1 | the original combined market + valuation page, kept as-is | (legacy; predates this doc) |
| Page 2 — Client Financials | statements → walk → forecast → handoff | PAGE 2 |
| Page 3 — Market & Cost of Capital | macro context → labeled inputs → CAPM → WACC → hurdle | PAGE 1 |
| Page 4 — Valuation & Decision | discounting → TV → EV → equity → per share → three tests → scenarios | PAGE 3 |

Pages 3 and 4 are the clean, single-purpose versions of what legacy
Page 1 does in one canvas; once they pass Desktop QA the owner can
retire Page 1 (delete the tab) whenever they choose — nothing binds to
it.

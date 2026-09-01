# Power BI Fix Checklist — from the 2026-08-31 Model Audit

These fixes live in the report/model layer of `reports/ML Tool.pbix`, which cannot be
edited from the pipeline. Apply them either **by hand in Power BI Desktop now** (steps
below, ~15 minutes) or **in code at the .pbip conversion phase** (each item maps to a
TMDL/report-JSON change). Until applied, the two D-defects from docs/MODEL_AUDIT.md remain
visible on the report.

The Python side of audit finding D2 is already fixed in the pipeline: the CSV now carries
`hurdle_rate_pct` (= WACC + 2.00pts, per scenario) and a per-scenario, hurdle-basis
`project_irr_pct` — so after a simple **data refresh**, the IRR shown on the report
reconciles with the Terminal Value and NPV shown beside it. Expect the numbers to change:
IRR becomes 13.34% / 12.03% / 10.53% (was a single 17.54% in every scenario), and in the
Higher Rate scenario the project now honestly **fails** its hurdle — which finally agrees
with the NPV panel, which already showed a negative NPV in that scenario.

## 1. Fix the comparison glyphs (audit D1) — REQUIRED

The measure `Hurdle vs IRR Symbol` compares Hurdle vs IRR (Hurdle-first) but is used as
the glyph in two rows with different operands, producing false inequalities on screen.

a. **Create a new measure** for the ROIC row:

```dax
ROIC vs WACC Symbol =
VAR ROICValue = [ROIC Check (%)]
VAR WACCValue = [Calculated WACC]
RETURN
    SWITCH(
        TRUE(),
        ROICValue > WACCValue, ">",
        ROICValue < WACCValue, "<",
        "="
    )
```

b. **Capital Allocation Decision → ROIC row** (y≈1360): rebind the symbol card (visual id
   `da893f146a5b34ec2c2a`, currently `Hurdle vs IRR Symbol`) to the new
   `ROIC vs WACC Symbol`.

c. **Capital Allocation Decision → IRR row** (y≈1474):
   - Delete the stray leading card bound to `Hurdle Rate (%)` (visual id
     `0d0688046eae2d32a80f`, x≈176) — the row should start with `Project IRR (%)`.
   - Rebind the symbol card (`d5dabcf9607412064c9a`) from `Hurdle vs IRR Symbol` to the
     existing, correctly-phrased `IRR Comparison Symbol`.
   - Add the missing "=" card (`WACC Equals Symbol`) before the spread card, matching the
     correctly-assembled row at y≈2469.
   - After this, `Hurdle vs IRR Symbol` is unused and can be deleted (its binding still
     carries the stale internal name "ROIC Comparison Symbol" from a rename).

## 2. Fix the mixed $M/$B rows (audit B/E2)

a. **Create the missing ($B) twin:**

```dax
PV FCF Years 1-5 ($B) =
DIVIDE(
    [PV FCF Years 1-5],
    1000
)
```

b. In the **DCF Valuation Calc → EV row** (y≈795), rebind the first card from
   `PV FCF Years 1-5` to `PV FCF Years 1-5 ($B)`. The row then reads
   `$0.47B + $1.69B = $2.16B` — one scale.
c. Optionally do the same for the TV rows' `FCF Year 5` input (or retitle those cards
   `($M)`) so no row mixes scales.

## 3. Remove the duplicate Terminal Value section (audit F)

The "Terminal Value Calculation" section (textbox y≈1089 + the two card rows under it,
y≈1127 and y≈1217) repeats, measure-for-measure, the rows already inside "DCF Valuation
Calc". Delete the section's textbox and its cards — visuals only, no measures are removed.

## 4. Conditional-color rebinds (audit E4)

Both `NPV Comparison Symbol` cards (`46ea242c…` at y≈1604 and `bb7f7069…` at y≈2031) bind
their **font color** to the symbol measure itself. Rebind fontColor → `NPV Decision Color`.

## 5. Housekeeping (audit E5–E7, optional)

- Re-save bindings for the two renamed measures so stale queryRefs clear:
  `Invested Capital Display` (3 cards), and whatever replaces `Hurdle vs IRR Symbol`.
- Align the one inconsistent visual interaction: the scenario table is NoFilter toward the
  four line charts but DataFilter toward the lone `After Tax Debt Factor` card.
- Consider replacing the DAX literals that duplicate pipeline values with references to
  the CSV columns now available: `Hurdle Rate (%)` (= column `hurdle_rate_pct`),
  `Equity Weight` (literal 0.70 vs column `equity_weight_pct`), `FCF Year 1..5` and
  `Initial Investment ($M)` literals. Single source of truth = the pipeline; DAX presents.

## Verification after applying

1. ROIC row reads `10.00 > 8.42 = 1.58 → Value Creating` (Base).
2. IRR row reads `IRR 12.03 > Hurdle 10.42 = 1.61pts → PASS` (Base); in Higher Rate it
   reads `10.53 < 11.12 → FAIL`, consistent with the negative NPV that panel shows.
3. EV row reads in one scale: `$0.47B + $1.69B = $2.16B`.
4. Terminal Value appears exactly once, inside the DCF walk-through.

## 6. POST-CUTOVER (2026-08-31): retire the stale DAX input literals — NOW ACTIVE

The DCF cutover (DECISIONS #62) re-priced the report from derived
inputs: Base EV is now ~$3.16B and the implied price ~$28.86. The CSV
columns update on refresh, BUT several DAX measures are LITERALS that
now disagree with the pipeline:

- `FCF Year 1..5` (literals 100/110/121/133/146) vs the derived UFCF
  path 186.2 / 197.4 / 209.3 / 221.8 / 235.1 — the DCF walk-through
  rows and the `DCF EV Reconciliation` measure will visibly disagree
  with `enterprise_value` until these read the new
  `reports/client_fs_ufcf.csv` (or columns added to the legacy CSV).
- `Equity Weight` (literal 0.70) and its Debt twin vs the derived
  83.6 / 16.4 in `equity_weight_pct` / `debt_weight_pct`.
- `Initial Investment ($M)` literal 1500 still matches the explicit
  assumption — no change needed.

Until the pbip phase replaces these literals with column references,
treat the DAX-rebuilt walk-through numbers as stale; the CSV columns
(`enterprise_value`, `equity_value`, `implied_share_price`, weights)
are the pipeline-true values.

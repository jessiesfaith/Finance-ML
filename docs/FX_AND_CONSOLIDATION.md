# Currency Translation & Consolidation Methodology

How entity financial statements in different functional currencies become
one set of consolidated statements in the reporting currency — the
deterministic Phase 3 layer between normalized statements and controls.

## 1. The rate policy (current-rate method)

One rate does not fit all statement items:

| Item | Rate | Why |
|---|---|---|
| Income statement, cash flow, OCI, equity movements | **AVERAGE** for the period | activity happens throughout the period |
| Balance sheet (except equity) | **CLOSING** at period end | balances are measured at a point in time |
| Common stock | **HISTORICAL** (rate when contributed) | capital keeps its original translated value |
| Retained earnings | **ROLL-FORWARD** | see below |
| CTA | the **plug** | see below |

The policy lives in one function (`rate_type_for` in
`src/financials/fx_translation.py`); rates themselves are data in
`fx_rates.csv`, never constants in code.

## 2. Retained earnings roll-forward

RE is accumulated income from many periods, each earned at that period's
average rate — so it cannot be translated at any single rate. Instead:

```
RE(reporting ccy) = beginning RE  (historical rate, earliest period on file,
                                   derived as ending RE − NI − dividends in
                                   local currency)
                  + each period's net income  × that period's AVERAGE rate
                  + each period's dividends   × that period's AVERAGE rate
```

Fixture: EUR beginning RE 8 × 1.00 = 8; + FY2024 NI 27 × 1.05 → **36.35**;
+ FY2025 NI 30 × 1.08 → **68.75**.

## 3. CTA — the plug that is not a fudge

Translating assets/liabilities at closing but equity at historical/rolled
rates makes the translated balance sheet stop balancing — by design. The
gap is the **cumulative translation adjustment (CTA)**, an equity/AOCI
line the engine emits as its own row (`cta_aoci`, origin `FX_ENGINE`):

```
CTA = translated assets − translated liabilities − translated equity-so-far
```

Fixture: FY2024 CTA **7.85**, FY2025 CTA **10.75**.

## 4. Source-reported vs calculated — the variance is visible

The fixture's source file translated its whole balance sheet at the
closing rate (a common client shortcut, flagged in `source_note`). The
engine never overwrites the source figure; it reports, per row:

```
source_reported_canonical − calculated_reporting_amount = fx_translation_variance
```

The FY2025 result: common stock variance **8.00** + retained earnings
variance **2.75** = **10.75** = the CTA. The shortcut and the "missing"
CTA are exactly the same money — proven by a test. Phase 4's Control 9
monitors these variances against tolerances.

## 5. Consolidation with an explicit elimination layer

```
  Σ translated non-elimination entities     pre_elimination_amount
+ Σ elimination-entity rows                 intercompany_elimination
+ other consolidation adjustments           (none yet — later phases)
+ CTA rows from the FX engine               fx_translation_adjustment
= consolidated_amount
```

Subsidiaries are never simply added together. The fixture's elimination
entity (ENT_ELIM) reverses intercompany revenue/COGS (±50 — changes the
mix, nets to zero profit) and intercompany AR/AP (−10 each side). Output:
`data/client_fs/entity_consolidation.csv`, one row per standard account
and period, `entity_id = CONSOLIDATED` (per-entity detail stays in the
translated layer). `control_status` is PENDING until Phase 4.

Consolidated fixture results: revenue 1324 − 50 = **1274**; net income
FY2025 **167.40**; balance sheet gap **0.00** both years, CTA included.

## 6. Known limitations (deliberate, documented)

- The fixture parent carries no investment-in-subsidiary account, so the
  investment-vs-subsidiary-equity elimination (spec section 7's "eventually
  support" list) is not yet exercised. The elimination-layer architecture
  is where it will land.
- Ownership below 100% (minority interest) and the equity method are
  future enhancements; `entity_master` already carries the fields.
- CTA is computed as a period-end balance; splitting the period-over-period
  CTA movement into OCI arrives with the Phase 4 OCI control.

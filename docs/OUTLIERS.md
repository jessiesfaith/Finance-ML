# Outlier / Discrepancy Engine (Phase 7)

Deterministic outlier tests that run BEFORE any AI interpretation
(`python src/run_outliers.py` → `data/client_fs/outlier_flags.csv`).

## The methods

| Method | Test | Threshold (config in `financials/outliers.py`) |
|---|---|---|
| POP_VARIANCE | period-over-period account movement, consolidated AND per entity | BOTH ±15% and ±$25M must clear |
| MARGIN_VARIANCE | EBITDA / EBIT margin change | ±2.0 percentage points |
| RATIO_VARIANCE | DSO, DIO, DPO, NWC % revenue, CapEx % revenue | ±15% relative |
| NEW_ITEM | material IS/BS activity with no prior-period counterpart | ≥ $25M |
| ZSCORE | deviation vs own history | \|z\| ≥ 3, **requires ≥ 4 periods** — self-reports "not applicable" below that |

Severity: HIGH at ≥ 2× threshold, else MEDIUM. Industry-deviation
methods join at Phase 14 when benchmark data exists.

## An outlier is not an error

Each flag lists candidate causes — real growth, acquisition/divestiture,
restructuring, FX, accounting change, or error — and starts
`review_status = PENDING`. The engine never adjusts anything. The
fixture makes the point: its three loudest flags (retained earnings
+53–89%) are provably clean — Control C4 shows the rolls tie exactly —
they are large because the company is profitable and retains earnings.
The elimination entity's new ±$50M intercompany activity flags as
NEW_ITEM: exactly the "goodwill/revenue jumped — was there a deal?"
pattern the M&A layer (Phase 9) will pair with transaction events.

## Hand-off

Phase 10's analyst agent reads these flags (and the control exceptions)
to search narratives, explain, and propose adjustments — READ / ANALYZE
/ FLAG / EXPLAIN / PROPOSE, never DELETE / OVERWRITE / APPROVE.

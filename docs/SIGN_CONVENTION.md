# Sign Normalization Policy

Every downstream number — EBIT, NOPAT, NWC, UFCF, ROIC, and ultimately the
DCF — depends on signs being deterministic and auditable. This document
defines the one canonical convention, how source presentations map into
it, and why a sign can never be flipped twice.

## The problem

Different source systems present the same economics differently:

```
Source A (magnitudes):   Revenue 100     Expense  50
Source B (signed):       Revenue 100     Expense -50
```

Both describe the same P&L. Naively multiplying every expense by −1 would
turn Source B's −50 into +50 and silently overstate profit.

## The canonical analytical convention

Normalized amounts are signed so that **subtotals are sums**:

| Item | Canonical sign | So that |
|---|---|---|
| Revenue | + | IS lines sum to net income |
| COGS | − | |
| Operating expenses | − | |
| D&A (income statement) | − | |
| Interest expense | − | |
| Tax expense | − | |
| Assets | + | positive in natural (debit) direction |
| Liabilities | + | positive in natural (credit) direction |
| Equity | + | positive in natural (credit) direction |
| Cash-flow inflows | + | CFS lines sum to the change in cash |
| Cash-flow outflows (CapEx, dividends, repayments) | − | |
| OCI, income-increasing | + | |
| Equity-statement increases / decreases | + / − | RE rolls forward by summing |

The balance-sheet identity is then checked as Assets = Liabilities +
Equity (Control 1), not by making liabilities negative.

## The mechanism (no double-flips possible)

Each `account_mapping` row carries two independent facts:

- **`sign_multiplier`** — the account's canonical sign (+1 or −1). A
  property of the *standard account* (an expense is always −1).
- **`source_sign_convention`** — how *this source* presents *this
  account*: `MAGNITUDE` (size only, direction implied — expense +50) or
  `SIGNED` (already economically signed — expense −50).

```
normalized = raw × sign_multiplier    when source_sign_convention = MAGNITUDE
normalized = raw                      when source_sign_convention = SIGNED
```

The multiplier is only ever applied to MAGNITUDE presentations; a SIGNED
source is already canonical and passes through untouched — so
`-50 × -1 = +50` can never happen. A negative value in a MAGNITUDE source
remains meaningful: an expense line of −5 is a credit/refund and
normalizes to +5 (income-increasing), which is correct.

Implementation: `src/financials/sign_normalizer.py` (`normalize_sign`).
Tests: `tests/test_sign_normalizer.py` — including the property that a
positive-presented and a negative-presented expense normalize to the same
value, and the CapEx equivalence for both presentation styles.

## Auditability

Raw source data is never modified. Phase 2's normalized layer stores, per
row: the raw amount, the rule that was applied (`sign_multiplier`,
`source_sign_convention` — both visible in `account_mapping`), and the
normalized amount, so every transformation is reproducible and reviewable.

## Fixture examples

The COMP001 fixture deliberately mixes conventions the way real exports
do: income-statement and balance-sheet files present magnitudes
(`MAGNITUDE` — Expense 50), while the cash-flow, OCI, and equity
schedules are already signed (`SIGNED` — CapEx −70, Dividends −45).

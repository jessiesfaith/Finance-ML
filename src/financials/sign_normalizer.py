"""
Sign normalization policy — the deterministic core of Phase 2.

THE PROBLEM
Different source systems present the same economics differently:

    Source A (magnitudes):   Revenue 100    Expense  50
    Source B (signed):       Revenue 100    Expense -50

Both mean the same thing. Every downstream number (EBIT, NOPAT, NWC, UFCF,
ROIC, DCF) depends on turning both into ONE canonical convention — without
ever flipping a sign twice (-50 x -1 = +50 would silently overstate profit).

THE CANONICAL CONVENTION (full table: docs/SIGN_CONVENTION.md)
Income-statement items are signed so they SUM to net income (revenue +,
expenses -). Balance-sheet items are positive in their natural direction
(assets +, liabilities +, equity +). Cash-flow items are signed by cash
direction (inflows +, outflows - : CapEx -, dividends -). OCI: income-
increasing +. Equity-statement movements: increases +, decreases -.

THE MECHANISM
Each account_mapping row carries two facts:

    sign_multiplier          the account's canonical sign (+1 or -1)
    source_sign_convention   how the source presents the number:
        MAGNITUDE  size only, direction implied by the account (expense +50)
        SIGNED     already signed economically (expense -50)

    normalized = raw x sign_multiplier    if MAGNITUDE
    normalized = raw                      if SIGNED

Why this cannot double-flip: the multiplier is only ever applied to
MAGNITUDE presentations. A SIGNED source is already canonical and passes
through untouched. A negative value in a MAGNITUDE source is meaningful
too: an expense line of -5 (a credit/refund) normalizes to +5 — genuinely
income-increasing.

AUDITABILITY
Raw amounts are never modified. Phase 2 stores, per row:
raw amount -> (sign_multiplier, source_sign_convention) -> normalized
amount, so every transformation is reproducible and reviewable.
"""

from financials.schemas import SIGN_CONVENTIONS


def normalize_sign(amount, sign_multiplier, source_sign_convention):
    """
    Convert one source-presented amount to the canonical sign convention.

    amount                  the raw value exactly as the source reported it
    sign_multiplier         the account's canonical sign, +1 or -1
    source_sign_convention  "MAGNITUDE" or "SIGNED"
    """
    if source_sign_convention not in SIGN_CONVENTIONS:
        raise ValueError(
            f"unknown source_sign_convention {source_sign_convention!r}; "
            f"expected one of {list(SIGN_CONVENTIONS)}"
        )
    if sign_multiplier not in (1, -1):
        raise ValueError(
            f"sign_multiplier must be +1 or -1, got {sign_multiplier!r}"
        )

    if source_sign_convention == "SIGNED":
        return amount

    return amount * sign_multiplier

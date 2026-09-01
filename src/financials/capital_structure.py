"""
Market-value capital structure — Phase 6 (spec / audit item 24).

Where the WACC weights SHOULD come from, instead of an assumption:

    Market value of equity = share price × diluted shares
    Market value of debt   ≈ total debt (book proxy until traded levels exist)

    Equity weight = E ÷ (D + E)      Debt weight = D ÷ (D + E)

This module DERIVES the market-value weights and compares them against
the current 70/30 assumption in models/export_finance_report.py — it
does NOT switch the WACC over (that cutover re-prices the valuation and
is a separate explicit approval; a peer/target structure is the Phase 14
alternative).
"""


def market_value_weights(share_price: float, diluted_shares_m: float,
                         total_debt_m: float) -> dict:
    equity_mv = share_price * diluted_shares_m
    total = equity_mv + total_debt_m
    if total <= 0:
        raise ValueError("total capitalization must be positive")
    return {
        "equity_market_value_m": round(equity_mv, 4),
        "debt_value_m": round(total_debt_m, 4),
        "total_capitalization_m": round(total, 4),
        "equity_weight_pct": round(equity_mv / total * 100, 4),
        "debt_weight_pct": round(total_debt_m / total * 100, 4),
    }

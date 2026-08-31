SELECT
    date,
    treasury_2y,
    fed_funds,
    cpi,
    unemployment,
    yield_spread_10y_2y,
    real_10y_proxy,
    treasury_10y AS target_treasury_10y
FROM macro_history
ORDER BY date;
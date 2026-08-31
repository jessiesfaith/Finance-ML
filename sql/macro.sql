SELECT
    date,
    treasury_2y,
    treasury_10y,
    fed_funds,
    cpi,
    unemployment,
    treasury_10y - treasury_2y AS yield_spread_10y_2y,
    treasury_10y - cpi AS real_10y_proxy
FROM macro_data
ORDER BY date;
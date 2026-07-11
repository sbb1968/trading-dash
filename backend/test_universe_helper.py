"""
test_universe_helper.py
───────────────────────
Test af de delte volatility-univers-helpers (Hoisting Batch A + Del 1-instrumentering).
Mocker _query_intraday_volatility på modulet → ingen netværk.

Kør:  python test_universe_helper.py
"""

import asyncio
from strategies.shared import tv_scanner


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


def _row(sym, price):
    """Minimal række-dict som _query_intraday_volatility ville returnere."""
    return {
        "symbol": sym, "price": price, "change": 1.0, "volume": 100,
        "avg_vol_30d": 1.0, "market_cap": 1.0, "exchange": "NASDAQ",
        "atrp_1w": 6.0, "atrp_1d": 3.0, "rvol": 1.5, "volatility_d": 2.0,
    }


def _run(**kw):
    return asyncio.run(tv_scanner.build_volatility_universe(
        top_n=2, price_min=5, price_max=50, mkt_cap_min=1, mkt_cap_max=2,
        min_avg_vol=1, atr_pct_min=1, exchanges=["NASDAQ"], **kw))


def _run_rows(**kw):
    return asyncio.run(tv_scanner.build_volatility_universe_rows(
        top_n=2, price_min=5, price_max=50, mkt_cap_min=1, mkt_cap_max=2,
        min_avg_vol=1, atr_pct_min=1, exchanges=["NASDAQ"], **kw))


def main():
    print("Test: build_volatility_universe(_rows) (delte TV-scan-wrappere)")
    _orig = tv_scanner._query_intraday_volatility
    try:
        # Normal: (pool_size, rows) → kun symboler ud, rækkefølge bevaret.
        tv_scanner._query_intraday_volatility = \
            lambda **kw: (31, [_row("AAPL", 1.0), _row("MSFT", 4.0)])
        check("normal → kun symboler, rækkefølge bevaret", _run() == ["AAPL", "MSFT"], _run())

        # Rows-wrapperen returnerer pool_size + fulde rækker.
        pool, rows = _run_rows()
        check("rows-wrapper → pool_size", pool == 31, pool)
        check("rows-wrapper → fulde rækker m. atrp_1d", rows[0]["atrp_1d"] == 3.0, rows)

        # Fejl → (0, []) → tom liste (kalderen falder tilbage til retry/fallback).
        def _boom(**kw):
            raise RuntimeError("boom")
        tv_scanner._query_intraday_volatility = _boom
        check("screener-fejl → tom liste", _run() == [], "ikke tom")
        check("screener-fejl → (0, [])", _run_rows() == (0, []), "ikke (0, [])")

        # Timeout → tom liste (lille timeout + langsom screener).
        import time
        tv_scanner._query_intraday_volatility = \
            lambda **kw: (time.sleep(0.3) or (1, [_row("X", 1.0)]))
        check("timeout → tom liste", _run(timeout=0.05) == [], "ikke tom")
    finally:
        tv_scanner._query_intraday_volatility = _orig

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()

"""
test_universe_helper.py
───────────────────────
Test af den delte build_volatility_universe-helper (Hoisting Batch A, trin 1).
Mocker fetch_tv_intraday_volatility på modulet → ingen netværk.

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


def _run(**kw):
    return asyncio.run(tv_scanner.build_volatility_universe(
        top_n=2, price_min=5, price_max=50, mkt_cap_min=1, mkt_cap_max=2,
        min_avg_vol=1, atr_pct_min=1, exchanges=["NASDAQ"], **kw))


def main():
    print("Test: build_volatility_universe (delt TV-scan-wrapper)")
    _orig = tv_scanner.fetch_tv_intraday_volatility
    try:
        # Normal: tuples → kun symboler ud, rækkefølge bevaret.
        tv_scanner.fetch_tv_intraday_volatility = \
            lambda **kw: [("AAPL", 1.0, 2.0, 3), ("MSFT", 4.0, 5.0, 6)]
        check("normal → kun symboler, rækkefølge bevaret", _run() == ["AAPL", "MSFT"], _run())

        # Fejl → tom liste (kalderen falder tilbage til retry/fallback).
        def _boom(**kw):
            raise RuntimeError("boom")
        tv_scanner.fetch_tv_intraday_volatility = _boom
        check("screener-fejl → tom liste", _run() == [], "ikke tom")

        # Timeout → tom liste (lille timeout + langsom screener).
        import time
        tv_scanner.fetch_tv_intraday_volatility = lambda **kw: (time.sleep(0.3) or [("X", 1, 2, 3)])
        check("timeout → tom liste", _run(timeout=0.05) == [], "ikke tom")
    finally:
        tv_scanner.fetch_tv_intraday_volatility = _orig

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()

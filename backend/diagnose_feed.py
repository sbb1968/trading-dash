"""
diagnose_feed.py — Isoleret datafeed-diagnose for Error 162.

Tester TRADES vs MIDPOINT side om side paa den aktuelle TWS-konto, saa vi
kan se PRAECIS hvad der virker. Bruger samme IBKRConnection-klasse som ORB,
saa resultatet er repraesentativt for hvad ORB selv ville opleve.

Koer paa algoserveren mens Ibens TWS (DUO509856) er logget ind:
    cd C:\\projects\\trading_dash\\backend
    venv\\Scripts\\activate
    python diagnose_feed.py

Hvad vi leder efter:
    - TRADES tom + MIDPOINT virker  -> bar-type-problem (kodeloesning mulig,
                                        men MIDPOINT mangler volumen)
    - Begge tomme                   -> subscription-problem (ikke kode)
    - Begge virker                  -> datafeed er faktisk OK nu
"""

import asyncio
from ibkr_connect import IBKRConnection

# Test-tickers: AAPL (stor, altid likvid) + et par small caps som ORB handler
TEST_TICKERS = ["AAPL", "GME", "SNDL"]


async def test_one(conn, ticker, what):
    """Hent bars for én ticker med én bar-type. Returnér antal bars."""
    try:
        bars = await conn.get_historical_bars(
            ticker, duration="1 D", bar_size="5 mins", what_to_show=what
        )
        n = len(bars)
        vol = bars[0]["volume"] if bars else None
        return n, vol
    except Exception as e:
        return f"FEJL: {e}", None


async def main():
    print("\n" + "=" * 60)
    print("  DATAFEED DIAGNOSE — TRADES vs MIDPOINT")
    print("=" * 60)

    conn = IBKRConnection(paper_trading=True)
    print("\nForbinder til TWS (port 7497)...")
    ok = await conn.connect()
    if not ok:
        print("❌ Kunne ikke forbinde til TWS. Er Iben logget ind?")
        return

    # Vis hvilken konto vi faktisk er forbundet til
    try:
        accounts = conn.ib.managedAccounts()
        print(f"✅ Forbundet. Konto(er): {accounts}")
    except Exception as e:
        print(f"✅ Forbundet (kunne ikke laese konto: {e})")

    print("\n" + "-" * 60)
    print(f"{'Ticker':<8} {'TRADES':<22} {'MIDPOINT':<22}")
    print("-" * 60)

    for ticker in TEST_TICKERS:
        trades_n, trades_vol = await test_one(conn, ticker, "TRADES")
        await asyncio.sleep(0.5)
        mid_n, mid_vol = await test_one(conn, ticker, "MIDPOINT")
        await asyncio.sleep(0.5)

        def fmt(n, vol):
            if isinstance(n, str):       # fejlbesked
                return n[:20]
            if n == 0:
                return "TOM (0 bars)"
            return f"{n} bars (vol={vol})"

        print(f"{ticker:<8} {fmt(trades_n, trades_vol):<22} {fmt(mid_n, mid_vol):<22}")

    print("-" * 60)
    print("\nFortolkning:")
    print("  TRADES tom + MIDPOINT virker = bar-type-problem")
    print("  Begge tomme                  = subscription-problem")
    print("  Begge virker                 = datafeed OK nu")
    print()

    conn.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
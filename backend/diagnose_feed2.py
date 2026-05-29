"""
diagnose_feed2.py — Udvidet datafeed-diagnose med market-data-type test.

Forrige diagnose viste at TRADES og MIDPOINT begge fejler med Error 162 selv
naar backenden er stoppet (ingen client-konflikt). Naeste hypotese: kontoen
mangler gyldig REALTIME market data, saa alle realtime-forespoergsler timer ud.

Denne test proever fire market-data-typer:
    1 = Live (realtime)      <- default, det vi har proevet (fejler)
    2 = Frozen               <- sidste realtime-vaerdi
    3 = Delayed              <- 15-20 min forsinket, GRATIS, ingen subscription
    4 = Delayed-frozen

Hvis DELAYED (3) virker hvor LIVE (1) fejler -> kontoen mangler realtime-adgang.
Det er en subscription/rettigheds-sag hos IBKR, ikke en kodefejl.

Koer paa algoserveren med BACKENDEN STOPPET (kun een forbindelse):
    cd C:\\projects\\trading_dash\\backend
    venv\\Scripts\\activate
    python diagnose_feed2.py
"""

import asyncio
from ib_async import IB, Stock

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TICKER   = "AAPL"   # stor, altid likvid — bedste testcase

MKT_TYPES = {
    1: "Live (realtime)",
    3: "Delayed (gratis)",
    2: "Frozen",
}


async def try_bars(ib, label):
    """Hent bars for AAPL med TRADES. Returnér resultat-streng."""
    contract = Stock(TICKER, "SMART", "USD")
    try:
        await ib.qualifyContractsAsync(contract)
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr="2 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            ),
            timeout=20.0,
        )
        if bars:
            return f"✅ {len(bars)} bars (sidste close: {bars[-1].close})"
        return "TOM (0 bars)"
    except asyncio.TimeoutError:
        return "❌ TIMEOUT (20s)"
    except Exception as e:
        return f"❌ {str(e)[:50]}"


async def main():
    print("\n" + "=" * 60)
    print("  MARKET-DATA-TYPE DIAGNOSE")
    print("=" * 60)

    import random
    ib = IB()
    print(f"\nForbinder til TWS (port {TWS_PORT})...")
    try:
        await ib.connectAsync(host=TWS_HOST, port=TWS_PORT,
                              clientId=random.randint(50, 99), timeout=15)
    except Exception as e:
        print(f"❌ Kunne ikke forbinde: {e}")
        return

    print(f"✅ Forbundet. Konto: {ib.managedAccounts()}")
    print("\n" + "-" * 60)

    for code, label in MKT_TYPES.items():
        ib.reqMarketDataType(code)
        await asyncio.sleep(1)
        result = await try_bars(ib, label)
        print(f"  Type {code} — {label:<20} {result}")
        await asyncio.sleep(1)

    print("-" * 60)
    print("\nFortolkning:")
    print("  Delayed (3) virker, Live (1) fejler = kontoen mangler realtime-adgang")
    print("  Alle fejler                         = dybere problem (forbindelse/konto)")
    print("  Live (1) virker nu                  = realtime kom online (marked aabnet?)")
    print()

    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
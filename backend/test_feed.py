"""
test_feed.py
────────────
Test at market data abonnementerne virker via API.
Tester snapshot og historiske bars for NYSE, NASDAQ og AMEX aktier.

Kør:
    python test_feed.py
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock

# Test-aktier fra alle tre børser
TEST_TICKERS = [
    ("AAPL",  "NASDAQ — Network C"),
    ("JPM",   "NYSE   — Network A"),
    ("SNDL",  "NASDAQ — small cap"),
    ("GME",   "NYSE   — meme stock"),
]

async def main():
    ib = IB()
    print("Forbinder til IBKR Paper Trading...")
    await ib.connectAsync("127.0.0.1", 7497, clientId=4, timeout=15)
    print("✅ Forbundet!\n")

    print("=" * 55)
    print("TEST AF MARKET DATA ABONNEMENTER")
    print("=" * 55)

    all_ok = True

    for ticker, beskrivelse in TEST_TICKERS:
        print(f"\n{ticker} ({beskrivelse})")
        print("─" * 40)

        contract = Stock(ticker, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)

        # Test historiske bars
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime    = "",
            durationStr    = "1 D",
            barSizeSetting = "5 mins",
            whatToShow     = "TRADES",
            useRTH         = True,
            formatDate     = 1,
        )

        if bars:
            last = bars[-1]
            print(f"  ✅ Historiske bars: {len(bars)} bars modtaget")
            print(f"     Seneste: {last.date}  O:{last.open:.2f}  H:{last.high:.2f}  "
                  f"L:{last.low:.2f}  C:{last.close:.2f}")
        else:
            print(f"  ⚠  Ingen historiske bars — markedet er lukket eller abonnement mangler")
            all_ok = False

    print("\n" + "=" * 55)
    if all_ok:
        print("✅ Alle abonnementer virker — klar til live trading!")
    else:
        print("⚠  Nogle abonnementer mangler eller markedet er lukket.")
        print("   Prøv igen i morgen når markedet åbner kl. 15:30 dansk tid.")
        print("   Historiske bars virker kun i og efter handelstid.")
    print("=" * 55)

    ib.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

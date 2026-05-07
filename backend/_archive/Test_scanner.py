"""
test_scanner.py
───────────────
Tester IBKR scanner direkte og viser resultatet.

Kør: python test_scanner.py
"""

import asyncio
import logging

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

# Fix Python 3.14 event loop
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, ScannerSubscription

HOST      = "127.0.0.1"
PORT      = 7497
CLIENT_ID = 50


async def test_scanner():
    ib = IB()

    print("\n🔌 Forbinder til IBKR TWS...")
    try:
        await ib.connectAsync(host=HOST, port=PORT, clientId=CLIENT_ID, timeout=15)
        print(f"✅ Forbundet — Konto: {ib.managedAccounts()}\n")
    except Exception as e:
        print(f"❌ Forbindelsesfejl: {e}")
        return

    # ── Test 1: TOP_PERC_GAIN med manuel filtrering ───────────
    print("📡 Test 1: TOP_PERC_GAIN — top gainers (manuel filtrering)...")
    try:
        sub = ScannerSubscription(
            instrument   = "STK",
            locationCode = "STK.US.MAJOR",
            scanCode     = "TOP_PERC_GAIN",
        )
        data = await asyncio.wait_for(
            ib.reqScannerDataAsync(sub),
            timeout=15.0
        )
        print(f"  Rådata: {len(data)} resultater")

        # Manuel filtrering — symbol max 5 tegn
        tickers = []
        for item in data:
            symbol = item.contractDetails.contract.symbol
            if len(symbol) <= 5:
                tickers.append(symbol)
            if len(tickers) >= 25:
                break

        print(f"  Efter filtrering: {len(tickers)} tickers")
        print(f"  Universe: {', '.join(tickers)}")

    except asyncio.TimeoutError:
        print("❌ Timeout efter 15 sekunder")
    except Exception as e:
        print(f"❌ Fejl: {e}")

    print()

    # ── Test 2: TOP_VOLUME_RATE ───────────────────────────────
    print("📡 Test 2: TOP_VOLUME_RATE — højest volumen...")
    try:
        sub2 = ScannerSubscription(
            instrument   = "STK",
            locationCode = "STK.US.MAJOR",
            scanCode     = "TOP_VOLUME_RATE",
        )
        data2 = await asyncio.wait_for(
            ib.reqScannerDataAsync(sub2),
            timeout=15.0
        )
        tickers2 = []
        for item in data2:
            symbol = item.contractDetails.contract.symbol
            if len(symbol) <= 5:
                tickers2.append(symbol)
            if len(tickers2) >= 25:
                break

        print(f"  Efter filtrering: {len(tickers2)} tickers")
        print(f"  Universe: {', '.join(tickers2)}")

    except asyncio.TimeoutError:
        print("❌ Timeout efter 15 sekunder")
    except Exception as e:
        print(f"❌ Fejl: {e}")

    ib.disconnect()
    print("\n✅ Test afsluttet")


if __name__ == "__main__":
    asyncio.run(test_scanner())

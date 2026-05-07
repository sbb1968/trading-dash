"""
test_bars.py  v4
────────────────
Bruger KUN async metoder — ingen synkrone wrappers der kalder run_until_complete.
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock

async def main():
    ib = IB()

    print("Forbinder...")
    await ib.connectAsync("127.0.0.1", 7497, clientId=3, timeout=15)
    print("✅ Forbundet!\n")

    # Konto — accountValues er ikke async, men den bruger cached data fra connect
    values  = ib.accountValues()
    summary = {v.tag: v.value for v in values}
    print("Konto-oversigt:")
    print(f"  Net Liquidation: ${float(summary.get('NetLiquidation', 0)):,.2f}")
    print(f"  Cash Balance:    ${float(summary.get('CashBalance', 0)):,.2f}")

    # Historiske bars — brug ASYNC versioner af ALLE kald
    print("\nHenter AAPL MIDPOINT bars...")
    contract = Stock("AAPL", "SMART", "USD")

    # qualifyContractsAsync i stedet for qualifyContracts
    await ib.qualifyContractsAsync(contract)

    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime    = "",
        durationStr    = "1 D",
        barSizeSetting = "5 mins",
        whatToShow     = "MIDPOINT",
        useRTH         = True,
        formatDate     = 1,
    )

    if bars:
        print(f"  ✅ {len(bars)} bars modtaget")
        print(f"  Første: {bars[0].date}  O:{bars[0].open:.2f}  H:{bars[0].high:.2f}  C:{bars[0].close:.2f}")
        print(f"  Sidste: {bars[-1].date}  O:{bars[-1].open:.2f}  H:{bars[-1].high:.2f}  C:{bars[-1].close:.2f}")
    else:
        print("  ⚠ Ingen bars returneret")

    # Positioner
    print("\nÅbne positioner:")
    for p in ib.positions():
        print(f"  {p.contract.symbol:8s} {p.position:+.0f} stk @ ${p.avgCost:.4f}")

    ib.disconnect()
    print("\n✅ Test fuldført!")

if __name__ == "__main__":
    asyncio.run(main())

"""
test_top25.py
─────────────
Henter TOP 25 daglige stigere fra IBKR scanner.
Viser pris, change%, volumen og gap% sorteret efter change%.

Krav: TWS skal køre og være logget ind (port 7497)

Kør: python test_top25.py
"""

import asyncio
import logging

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, ScannerSubscription, Stock

HOST      = "127.0.0.1"
PORT      = 7497
CLIENT_ID = 55


async def get_bars(ib: IB, symbol: str) -> dict | None:
    """Hent historiske bars og beregn pris, change%, volumen og gap%."""
    for what_to_show in ["TRADES", "MIDPOINT"]:
        try:
            contract = Stock(symbol, "SMART", "USD")
            await ib.qualifyContractsAsync(contract)
            bars = await asyncio.wait_for(
                ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime    = "",
                    durationStr    = "3 D",
                    barSizeSetting = "1 day",
                    whatToShow     = what_to_show,
                    useRTH         = True,
                    formatDate     = 1,
                ),
                timeout=10.0
            )
            if bars and len(bars) >= 2:
                last       = bars[-1]
                prev       = bars[-2]
                price      = last.close
                prev_close = prev.close
                open_price = last.open or price
                volume     = last.volume or 0

                if prev_close <= 0:
                    continue

                change_pct = round((price - prev_close) / prev_close * 100, 2)
                gap_pct    = round((open_price - prev_close) / prev_close * 100, 2)

                return {
                    "symbol":     symbol,
                    "price":      round(price, 2),
                    "change_pct": change_pct,
                    "volume":     int(volume),
                    "gap_pct":    gap_pct,
                }
        except asyncio.TimeoutError:
            continue
        except Exception:
            continue
    return None


async def main():
    ib = IB()

    print("\n" + "═" * 70)
    print("  IBKR Scanner Test — TOP 25 Daglige Stigere med Markedsdata")
    print("═" * 70)
    print(f"  Forbinder til TWS på {HOST}:{PORT}...")

    try:
        await ib.connectAsync(host=HOST, port=PORT, clientId=CLIENT_ID, timeout=15)
        print(f"  ✅ Forbundet — Konto: {ib.managedAccounts()}\n")
    except Exception as e:
        print(f"  ❌ Forbindelsesfejl: {e}")
        print("\n  Tjekliste:")
        print("  1. Er TWS åben og logget ind?")
        print("  2. Edit → Global Configuration → API → Settings")
        print("  3. Enable ActiveX and Socket Clients: ✓")
        print("  4. Read-Only API: ✗")
        print("  5. Port: 7497")
        return

    # ── Hent scanner resultater ───────────────────────────────
    print("  Henter TOP 25 fra IBKR scanner...")
    try:
        sub = ScannerSubscription(
            instrument   = "STK",
            locationCode = "STK.US.MAJOR",
            scanCode     = "TOP_PERC_GAIN",
        )
        data = await asyncio.wait_for(
            ib.reqScannerDataAsync(sub),
            timeout=20.0
        )
    except asyncio.TimeoutError:
        print("  ❌ Scanner timeout")
        ib.disconnect()
        return
    except Exception as e:
        print(f"  ❌ Scanner fejl: {e}")
        ib.disconnect()
        return

    # Filtrer til max 5 tegn
    tickers = []
    for item in data:
        symbol = item.contractDetails.contract.symbol
        if len(symbol) <= 5:
            tickers.append(symbol)
        if len(tickers) >= 25:
            break

    print(f"  ✅ {len(tickers)} tickers fundet — henter historiske data...\n")

    # ── Hent bars for alle tickers ────────────────────────────
    all_results = []
    failed      = []

    for symbol in tickers:
        result = await get_bars(ib, symbol)
        if result:
            all_results.append(result)
        else:
            failed.append(symbol)

    # Sorter efter change% faldende (højest stigning øverst)
    all_results.sort(key=lambda x: x["change_pct"], reverse=True)

    # ── Udskriv tabel ─────────────────────────────────────────
    print("─" * 70)
    print(f"  {'#':<4} {'TICKER':<8} {'PRIS':>9} {'CHANGE %':>10} {'VOLUMEN':>12} {'GAP %':>8}")
    print("─" * 70)

    for i, r in enumerate(all_results, 1):
        indicator = "▲" if r["change_pct"] > 0 else "▼"
        price     = f"${r['price']:.2f}"
        change    = f"{r['change_pct']:+.2f}%"
        volume    = f"{r['volume']:,}"
        gap       = f"{r['gap_pct']:+.2f}%"
        print(f"  {i:<4} {r['symbol']:<8} {price:>9} {indicator} {change:>8} {volume:>12} {gap:>8}")

    if failed:
        print("─" * 70)
        print(f"  Ingen data: {', '.join(failed)}")

    print("─" * 70)
    print(f"\n  ✅ {len(all_results)} tickers med data, {len(failed)} uden data")
    gainers = [r for r in all_results if r["change_pct"] > 0]
    losers  = [r for r in all_results if r["change_pct"] < 0]
    print(f"  Stigere: {len(gainers)}  |  Faldere: {len(losers)}")
    print(f"\n  Universe til algoritmen: {', '.join(r['symbol'] for r in all_results[:8])}...")
    print()

    ib.disconnect()
    print("  ✅ Test afsluttet\n")


if __name__ == "__main__":
    asyncio.run(main())

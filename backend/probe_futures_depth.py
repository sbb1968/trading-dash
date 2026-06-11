"""
probe_futures_depth.py
──────────────────────
Fikset dybde-probe for futures: henter den KONKRETE front-måned-kontrakt
(ikke ContFuture, som forbyder endDateTime → fejl 10339) og chunker 1-min
bagud for at måle backtestbar dybde for MES/MNQ/MGC.

Kør fra backend/ med TWS oppe (paper 7497):
    python probe_futures_depth.py
"""

import asyncio
from datetime import datetime

# ── Python 3.14 event loop fix ──────────────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Future

PORT, CLIENT_ID = 7497, 26
CHUNK, BAR      = "5 D", "1 min"
MAX_CHUNKS      = 12          # ~60 dage — vi måler dybde, bygger ikke cache endnu
SLEEP           = 11          # ≥10s mellem requests → under 60 req/10 min

CANDIDATES = [
    ("MES  S&P500", "MES", "CME"),
    ("MNQ  Nasdaq", "MNQ", "CME"),
    ("MGC  guld",   "MGC", "COMEX"),
]


async def front_month(ib: IB, symbol: str, exch: str):
    """Find nærmeste ikke-udløbne kontrakt-måned (ikke continuous)."""
    details = await ib.reqContractDetailsAsync(
        Future(symbol=symbol, exchange=exch, currency="USD")
    )
    if not details:
        return None
    contracts = sorted(
        (d.contract for d in details),
        key=lambda c: c.lastTradeDateOrContractMonth,
    )
    today = datetime.now().strftime("%Y%m%d")
    for c in contracts:
        ltd = c.lastTradeDateOrContractMonth
        ltd_cmp = ltd if len(ltd) == 8 else ltd + "31"   # YYYYMM → slut-på-måned
        if ltd_cmp >= today:
            return c
    return contracts[-1]


async def probe(ib: IB, label: str, symbol: str, exch: str):
    c = await front_month(ib, symbol, exch)
    if c is None:
        print(f"  {label}: ingen kontrakt fundet"); return
    print(f"  {label}: front-måned = {c.localSymbol} "
          f"(udløb {c.lastTradeDateOrContractMonth})")

    total_bars, days, earliest = 0, set(), None
    end = ""   # tom = nu
    for i in range(MAX_CHUNKS):
        try:
            bars = await ib.reqHistoricalDataAsync(
                c,
                endDateTime    = end,
                durationStr    = CHUNK,
                barSizeSetting = BAR,
                whatToShow     = "TRADES",
                useRTH         = False,    # futures handler ~23t — tag det hele
                formatDate     = 2,
            )
        except Exception as e:
            msg = str(e)
            if "different IP" in msg or "session is connected" in msg:
                print("    TWS-session optaget fra anden IP — luk Client Portal "
                      "/ Mobile-app og genstart TWS."); return
            print(f"    chunk {i}: fejl {e}"); break
        if not bars:
            print(f"    chunk {i}: tomt → kontraktens begyndelse nået"); break
        total_bars += len(bars)
        for b in bars:
            days.add(b.date.date() if hasattr(b.date, "date") else b.date)
        earliest = bars[0].date
        end = bars[0].date        # datetime — ib_async accepterer det direkte
        await asyncio.sleep(SLEEP)

    print(f"    → {total_bars} bars · {len(days)} unikke dage · ældste = {earliest}\n")


async def main():
    ib = IB()
    await ib.connectAsync("127.0.0.1", PORT, clientId=CLIENT_ID)
    print(f"Forbundet (paper {PORT}). Prober futures-dybde...\n")
    for label, symbol, exch in CANDIDATES:
        await probe(ib, label, symbol, exch)
    ib.disconnect()
    print("Frakoblet.")


if __name__ == "__main__":
    asyncio.run(main())
    
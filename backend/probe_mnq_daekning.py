#!/usr/bin/env python3
r"""
probe_mnq_daekning.py — hvor langt tilbage kan MNQ 1-min overhovedet hentes?
════════════════════════════════════════════════════════════════════════════════
⚠ DENNE FIL FINDES FOR AT UNDGÅ EN FLERE TIMER LANG HØST DER IKKE KAN LYKKES.

Ønsket var "samme mængde 1-min candles for MNQ som for MES". MES rækker tilbage
til **2024-06-21** — men de gamle MES-kontrakter blev høstet dengang de var
aktuelle. `vol_futures_retention_test.py` målte 4. august 2026 at IBKR i dag kun
udleverer udløbne ES/M2K-kontrakter tilbage til **202412**; `202406` findes ikke
længere. Historikken kan altså ikke uden videre genskabes bagud.

Kørt for MNQ svarede **ingen** kontrakt — heller ikke 202509 og 202506, som
virkede for ES og M2K. ⚠ Men den sonde spørger kun om UDLØBNE kontrakter, så den
kan ikke skelne mellem:

    a) MNQ's udløbne kontrakter er væk hos IBKR
    b) MNQ kan slet ikke kvalificeres med den kontrakt-spec vi bruger

Forskellen er afgørende: (a) betyder "høst det der er", (b) betyder "ret specen
først". Vi VED at MNQ kan kvalificeres — multiplikatoren 2 blev aflæst fra
`reqContractDetails` på front-kontrakten 11-08-2026 (se futures_katalog.py).

Denne sonde spørger derfor om BEGGE dele: front-kontrakten (skal virke) og de
seneste udløbne, én kvartalsmåned ad gangen bagud. Den første måned der svarer
med barer, er den ældste vi realistisk kan høste.

Read-only. Sender ingen ordrer. Egen client-id (default 72).

KØRSEL (Sørens workstation, TWS/Gateway åbent):
    python probe_mnq_daekning.py
    python probe_mnq_daekning.py --symbol MES --client-id 73    # sammenlign
    python probe_mnq_daekning.py --maaneder 12

Placering: C:\Projects\trading_dash\backend\probe_mnq_daekning.py
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import sys
from datetime import datetime, timedelta

from ib_async import IB, Future

HOST = "127.0.0.1"


def kvartalsmaaneder(antal: int) -> list[str]:
    """De seneste `antal` kvartals-kontraktmåneder, nyeste først.

    ⚠ MES/M2K/MNQ er mar/jun/sep/dec. Vi starter ved den FØRSTE kvartalsmåned
    der ligger frem i tiden (front-kontrakten) og går bagud derfra — ellers
    ville sonden kun spørge om udløbne og aldrig få en positiv kontrol.
    """
    nu = datetime.now()
    m = ((nu.month - 1) // 3 + 1) * 3          # næste kvartalsslut, 3/6/9/12
    aar = nu.year
    if m > 12:
        m -= 12
        aar += 1
    ud = []
    for _ in range(antal):
        ud.append(f"{aar}{m:02d}")
        m -= 3
        if m < 1:
            m += 12
            aar -= 1
    return ud


async def proev(ib, symbol: str, ym: str, boers: str):
    """Returnerer (status, antal_barer, foerste, sidste)."""
    k = Future(symbol=symbol, lastTradeDateOrContractMonth=ym,
               exchange=boers, currency="USD", includeExpired=True)
    try:
        q = await ib.qualifyContractsAsync(k)
    except Exception as e:
        return f"kvalificering kastede: {type(e).__name__}", 0, None, None
    # ⚠ qualifyContractsAsync KASTER IKKE paa en ukendt kontrakt — den
    # returnerer [None]. Uden None-vagten braser sonden med AttributeError
    # midt i en maaling, og man mister resten af raekken.
    if not q or q[0] is None or not getattr(q[0], "conId", None):
        return "kontrakten findes ikke", 0, None, None

    # ⚠ Spørg om en dag MIDT i kontraktens levetid, ikke om "nu": en udløbet
    # kontrakt har ingen bars i dag, og et tomt svar ville så betyde noget helt
    # andet end at data mangler.
    try:
        udloeb = datetime.strptime(q[0].lastTradeDateOrContractMonth[:8], "%Y%m%d")
    except ValueError:
        udloeb = datetime.strptime(ym, "%Y%m") + timedelta(days=14)
    midt = udloeb - timedelta(days=30)
    slut = min(midt, datetime.now() - timedelta(days=1))

    try:
        bars = await ib.reqHistoricalDataAsync(
            q[0], endDateTime=slut.strftime("%Y%m%d %H:%M:%S"),
            durationStr="1 D", barSizeSetting="1 min", whatToShow="TRADES",
            useRTH=False, formatDate=1)
    except Exception as e:
        return f"data kastede: {type(e).__name__}", 0, None, None
    if not bars:
        return "kvalificeret, men INGEN barer", 0, None, None
    return "OK", len(bars), str(bars[0].date), str(bars[-1].date)


async def main_async(args) -> int:
    maaneder = kvartalsmaaneder(args.maaneder)
    print("=" * 78)
    print(f"  {args.symbol} 1-min DÆKNING — kan kontrakten kvalificeres, og er der barer?")
    print(f"  boers={args.boers}  ·  {len(maaneder)} kvartalsmaaneder: "
          f"{maaneder[0]} ned til {maaneder[-1]}")
    print("=" * 78)

    ib = IB()
    try:
        await ib.connectAsync(HOST, args.port, clientId=args.client_id, timeout=20)
    except Exception as e:
        print(f"FEJL: kan ikke forbinde til TWS {HOST}:{args.port} — {e}")
        return 2

    raekker = []
    try:
        for ym in maaneder:
            status, n, f, s = await proev(ib, args.symbol, ym, args.boers)
            raekker.append((ym, status, n, f, s))
            mark = "OK  " if status == "OK" else "    "
            print(f"  {mark}{ym}  {status:<32} {n:>5} barer"
                  + (f"   {f[:16]} .. {s[:16]}" if f else ""))
            await asyncio.sleep(0.4)          # venlig mod pacing-graensen
    finally:
        ib.disconnect()

    med = [r for r in raekker if r[1] == "OK"]
    print()
    print("─" * 78)
    if not med:
        print(f"  ⚠ INGEN {args.symbol}-kontrakt svarede med barer.")
        print("    Enten er kontrakt-specen forkert (boers/valuta/format), eller")
        print("    IBKR udleverer ikke intradag for dette produkt. Proev --boers")
        print("    eller sammenlign med --symbol MES paa samme boers.")
    else:
        aeldste = med[-1][0]
        print(f"  {args.symbol}: barer fra {len(med)} af {len(raekker)} kontrakter.")
        print(f"  ⚠ AELDSTE KONTRAKT MED DATA: {aeldste}")
        print(f"    En hoest kan realistisk daekke fra ca. {aeldste[:4]}-{aeldste[4:]} og frem.")
        uden = [r[0] for r in raekker if r[1] != "OK"]
        if uden:
            print(f"    Uden data: {', '.join(uden)}")
    print("─" * 78)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Maal hvor langt tilbage 1-min futures-data kan hentes.")
    ap.add_argument("--symbol", default="MNQ")
    ap.add_argument("--boers", default="CME")
    ap.add_argument("--maaneder", type=int, default=10,
                    help="antal kvartalsmaaneder bagud (default 10 = 2,5 aar)")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", dest="client_id", type=int, default=72)
    args = ap.parse_args()
    try:
        return asyncio.get_event_loop().run_until_complete(main_async(args))
    except KeyboardInterrupt:
        print("\nafbrudt")
        return 130


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
maal_futures_margin.py — hvad binder ÉN kontrakt, målt på en flad konto?
════════════════════════════════════════════════════════════════════════════════
Nævneren i `Afkast %` for futures. Skrivebeskyttet: der sendes ingen ordrer, kun
whatIf-forespørgsler, som IBKR beregner uden at lægge noget i bogen.

⚠ KONTOEN SKAL VÆRE FLAD, OG DET ER IKKE EN FORMALITET.

`initMarginChange` er porteføljens ÆNDRING, ikke instrumentets margin. Målt
11-08-2026 på en konto med MES −1 åben:

    MES x1 BUY  ->  -2756,85    lukker shorten, frigiver margin
    MES x2 BUY  ->   +734,59    lukker og åbner modsat
    M2K x1 BUY  ->   -516,78    modregner MES-shorten
    MNQ x1 BUY  ->  +1918,87    mindre end MNQ alene, pga. modregning

Ingen af de fire tal er instrumentets egen margin. Kun på en **flad** konto er
ændringen lig med hvad instrumentet selv binder — og derfor nægter scriptet at
måle hvis der ligger noget.

⚠ Det er samme fejlklasse som resten af projektet: et tal der ser målt ud og er
forurenet. Uden vagten ville tallene se rigtige ud og være forkerte for altid,
fordi ingen kunne se hvad de var målt oven på.

BRUG — på en maskine hvor TWS/Gateway kører mod en FLAD konto:

    python maal_futures_margin.py --konto DUQ441063 --port 4002
    python maal_futures_margin.py --konto DUO509856             # TWS 7497

Resultatet skrives IKKE automatisk ind i katalogets `margin_est`. Det er et
menneskes beslutning at flytte et estimat.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, MarketOrder

import futures_katalog as fk
import ibkr_client_ids


async def hoved(args) -> int:
    ib = IB()
    fejl: list[tuple] = []
    ib.errorEvent += lambda r, k, t, c: fejl.append((k, t[:70]))

    konto = args.konto.strip().upper()
    print("=" * 78)
    print(f"FUTURES-MARGIN  ·  {args.host}:{args.port}  ·  konto {konto}")
    print("=" * 78)

    await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=25)
    styrede = [a.strip().upper() for a in (ib.managedAccounts() or [])]
    if konto not in styrede:
        print(f"\n⚠ {konto} er ikke blandt {styrede}. Intet maalt.")
        ib.disconnect()
        return 1

    # ── ⚠ VAGTEN: kontoen SKAL vaere flad ───────────────────────────────────
    pos = await asyncio.wait_for(ib.reqPositionsAsync(), timeout=30)
    aabne = [p for p in pos
             if (p.account or "").upper() == konto and float(p.position or 0) != 0]
    if aabne:
        print(f"\n⚠ KONTOEN ER IKKE FLAD — {len(aabne)} position(er):")
        for p in aabne:
            print(f"     {p.contract.symbol:8} {float(p.position):+g}")
        print("\n  Maalingen ville blive forurenet: initMarginChange er")
        print("  portefoeljens AENDRING, og en aaben position modregner.")
        print("  Maal paa en flad konto, ellers er tallene forkerte for altid.")
        ib.disconnect()
        return 1
    print(f"\nkontoen er flad — maalingen er ren")

    print(f"\n{'':6} {'mult':>5} {'1 kontrakt':>13} {'2 kontrakter':>14}  lineaer?")
    resultat = {}
    for sym in sorted(fk.symboler()):
        try:
            k = await asyncio.wait_for(_kontrakt(ib, sym), timeout=25)
        except Exception as e:
            print(f"{sym:6} ⚠ kontrakt: {type(e).__name__}")
            continue

        maal = {}
        for antal in (1, 2):
            o = MarketOrder("BUY", antal)
            o.account = konto        # ⚠ begge felter — ellers tomt svar
            o.tif = "DAY"
            try:
                st = await asyncio.wait_for(ib.whatIfOrderAsync(k, o), timeout=15)
                raa = getattr(st, "initMarginChange", None)
                maal[antal] = float(raa) if raa not in (None, "") else None
            except Exception:
                maal[antal] = None
            await asyncio.sleep(0.5)

        en, to = maal.get(1), maal.get(2)
        # ⚠ Lineariteten er kontrollen. Binder 2 kontrakter praecis det dobbelte,
        # er tallet instrumentets egen margin. Goer den ikke, er der noget vi
        # ikke forstaar — og saa skal tallet ikke bruges som naevner.
        lin = "—"
        if en and to:
            afvig = abs(to - 2 * en) / (2 * en) * 100
            lin = f"{'JA' if afvig < 2 else '⚠ NEJ'} ({afvig:.1f}% afvig)"
        print(f"{sym:6} {fk.multiplikator(sym):>5.0f} "
              f"{('$'+format(en,',.2f')) if en else '—':>13} "
              f"{('$'+format(to,',.2f')) if to else '—':>14}  {lin}")
        if en:
            resultat[sym] = round(en, 2)

    ib.disconnect()

    if resultat:
        print("\n" + "=" * 78)
        print("TIL futures_katalog.py — indsaet som margin_est:")
        for s, v in sorted(resultat.items()):
            print(f'    "{s}": {v},')
        print("\n⚠ Skriv ogsaa DATOEN ind. Margin aendrer sig med volatiliteten,")
        print("  og et estimat uden dato er et taxameter uden ur.")
        print("=" * 78)

    if fejl:
        print("\nIBKR-fejl:")
        for k, t in dict(fejl).items():
            print(f"  {k:>6}  {t}")
    return 0


async def _kontrakt(ib: IB, sym: str):
    from ibkr_connect import IBKRConnection
    c = IBKRConnection.__new__(IBKRConnection)
    c.ib, c.connected = ib, True
    return await c._resolve_contract(sym)


def main() -> int:
    ap = argparse.ArgumentParser(description="Instrumentets egen margin, maalt fladt")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--konto", required=True)
    ap.add_argument("--client-id", dest="client_id", type=int,
                    default=ibkr_client_ids.for_script("maal_futures_margin.py"))
    return asyncio.run(hoved(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

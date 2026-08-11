#!/usr/bin/env python3
"""
flatten_alt.py — luk ALT, med mængden læst fra kontoen selv
════════════════════════════════════════════════════════════════════════════════
Bruges før en genstart hvor hukommelsen skal være tom. Efter en genstart er
`self._positions`, `_open_positions` og `_exposure_by_strategy` alle tomme mens
IBKR stadig holder positionerne — risikogrænserne tror eksponeringen er nul, og
en strategi kan gå ind i en ticker den allerede er i. Tom hukommelse er kun
korrekt hvis kontoen også er flad.

⚠ HVORFOR IKKE STRATEGIERNES EGNE LUKKEVEJE

De kører den gamle kode indtil genstarten, og på en DELT ticker er det netop dér
over-salget opstår: `_ibkr_still_holds` læser kontoens netto og kan ikke skelne
"mine aktier" fra "den anden strategis". IOVA har to strategier på sig, og der
kan være flere.

Her er hverken strategihukommelse eller den vagt involveret. **Mængden kommer fra
kontoen selv**, og der findes derfor ingen vej til at sælge for meget:

    netto +78  ->  SELL 78      netto -19  ->  BUY 19

⚠ OG ORDREN SENDES MOD DEN KONTRAKT IBKR SELV RAPPORTERER. Ikke mod en
nykvalificeret. For en future betyder det at udløbsmåneden er præcis den der
ligger i positionen — en gen-kvalificering kunne ramme den nye frontmåned og
åbne en position i stedet for at lukke en.

    ÉN ordre pr. ticker. Ingen genforsøg. Ingen undtagelser.

BRUG — fra backend/ på den maskine hvor Gatewayen kører:

    python flatten_alt.py                     # PREVIEW, rører intet
    python flatten_alt.py --udfoer            # sender lukkeordrer

SIKKERHED
  · preview er default
  · kontoen skal begynde med D (paper). En live-konto afbryder
  · --konto skal matche det Gatewayen melder
  · ÉN ordre pr. ticker, aldrig gen-afgivet
  · verificerer bagefter mod IBKR — ikke mod journalen
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, MarketOrder

import ibkr_client_ids

IBKR_FEJL: list[tuple] = []


def rute_for(kontrakt) -> str:
    """Hvilken boers ordren skal rutes til.

    ⚠ reqPositions giver kontrakten med dens PRIMAERE boers (NASDAQ, NYSE). En
    ordre mod den boers er "direct routed", og det er spaerret af Precautionary
    Settings i Global Configuration. Maalt 11-08-2026: alle syv lukkeordrer
    afvist med 10311 -> 201 "Order was discarded". Intet blev udfoert, men intet
    blev lukket heller.

    ⚠ FUTURES ROERES IKKE. Hele grunden til at bruge IBKR's egen kontrakt var at
    en gen-kvalificering kunne ramme den nye frontmaaned og AABNE en position i
    stedet for at lukke en. Det hensyn gaelder stadig; det er kun aktiernes boers
    der aendres, og conId foelger med, saa SMART ikke kan rute til et andet papir.
    """
    ex = (kontrakt.exchange or "").upper()
    if (kontrakt.secType or "").upper() == "STK" and ex not in ("", "SMART"):
        return "SMART"
    return ex or "SMART"


async def hent_positioner(ib: IB, konto: str) -> list:
    pos = await asyncio.wait_for(ib.reqPositionsAsync(), timeout=30)
    return [p for p in pos
            if (p.account or "").upper() == konto and float(p.position or 0) != 0]


async def hoved(args) -> int:
    ib = IB()

    def paa_fejl(reqId, kode, tekst, kontrakt):
        IBKR_FEJL.append((reqId, kode, tekst))
        print(f"     ↳ IBKR {kode} (reqId {reqId}): {tekst}")
    ib.errorEvent += paa_fejl

    konto = args.konto.strip().upper()
    print("=" * 78)
    print(f"FLATTEN ALT  ·  {args.host}:{args.port}  ·  konto {konto}"
          + ("" if args.udfoer else "  ·  PREVIEW"))
    print("=" * 78)

    await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=25)
    styrede = [a.strip().upper() for a in (ib.managedAccounts() or [])]
    print(f"\nstyrede konti: {styrede}")

    if konto not in styrede:
        print(f"\n⚠ {konto} er ikke blandt de styrede konti. Intet gjort.")
        ib.disconnect()
        return 1
    if not konto.startswith("D") and not args.tillad_live:
        print(f"\n⚠ {konto} ligner IKKE en paper-konto (D-præfiks). Intet gjort.")
        ib.disconnect()
        return 1

    poser = await hent_positioner(ib, konto)
    print(f"\n{len(poser)} åbne positioner:")
    for p in poser:
        c = p.contract
        udl = f" {c.lastTradeDateOrContractMonth}" if c.lastTradeDateOrContractMonth else ""
        print(f"   {c.symbol:8} {c.secType:5}{udl:10} {float(p.position):+g}")

    if not poser:
        print("\n  Kontoen er allerede flad. Intet at gøre.")
        ib.disconnect()
        return 0

    if not args.udfoer:
        print("\nPREVIEW — disse ordrer VILLE blive sendt:")
        for p in poser:
            n = float(p.position)
            # ⚠ Vis den boers ordren FAKTISK rutes til. Foerste udgave viste kun
            # symbolet, saa previewet saa fint ud mens alle syv blev afvist paa
            # netop routingen. Et preview der udelader det felt der faejler, er
            # ikke et preview.
            ex = (p.contract.exchange or "").upper()
            rute = rute_for(p.contract)
            print(f"   {'SELL' if n > 0 else 'BUY ':4} {abs(n):g} "
                  f"{p.contract.symbol} (MKT via {rute}"
                  + (f", var {ex}" if rute != ex and ex else "") + ")")
        print("\n  Intet sendt. Kør igen med --udfoer.")
        ib.disconnect()
        return 0

    print("\nSENDER LUKKEORDRER — én pr. ticker, ingen genforsøg:")
    handler = []
    for p in poser:
        n = float(p.position)
        ordre = MarketOrder("SELL" if n > 0 else "BUY", abs(n))
        ordre.account = konto
        ordre.tif = "DAY"                    # aldrig GTC
        ordre.orderRef = "flatten_alt"

        # ⚠ IBKR's EGEN kontrakt — men med den rute `rute_for` bestemmer.
        # conId foelger med, saa SMART ikke kan ramme et andet papir.
        # Reglen og begrundelsen staar ÉT sted: rute_for. Previewet bruger samme
        # funktion, saa det der vises, er det der sendes.
        kontrakt = copy.copy(p.contract)
        kontrakt.exchange = rute_for(p.contract)

        t = ib.placeOrder(kontrakt, ordre)
        handler.append((p.contract.symbol, n, t))
        print(f"   {ordre.action:4} {ordre.totalQuantity:g} {p.contract.symbol}")
        await asyncio.sleep(0.4)

    print("\nventer på fills …")
    for _ in range(20):
        await asyncio.sleep(1.5)
        if all((t.orderStatus.filled or 0) >= abs(n) or
               t.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled")
               for _s, n, t in handler):
            break

    print("\nordre-status:")
    for sym, n, t in handler:
        st = t.orderStatus
        print(f"   {sym:8} {st.status:12} filled={st.filled:g}/{abs(n):g} "
              f"@ {st.avgFillPrice or '—'}")

    # ── ⚠ VERIFICÉR MOD IBKR, IKKE MOD JOURNALEN ────────────────────────────
    # Journalen har allerede vist sig uenig med kontoen i halvdelen af
    # tilfaeldene. Det er kontoen der skal sige fladt.
    await asyncio.sleep(3)
    rest = await hent_positioner(ib, konto)
    print(f"\nEFTER — IBKR's egen opgørelse: {len(rest)} positioner tilbage")
    for p in rest:
        print(f"   ⚠ {p.contract.symbol:8} {float(p.position):+g}")

    ib.disconnect()
    if rest:
        print("\n⚠ IKKE FLAD. Gen-afgiv IKKE automatisk — se hvorfor hver enkelt")
        print("  hænger (halt, lukket marked, tynd likviditet) før du gør noget.")
        return 1
    print("\n  FLAD ✓ — hukommelsen må nu gerne tømmes ved genstart.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Luk ALT paa en konto. Maengden laeses fra kontoen selv.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497,
                    help="TWS paper 7497 · IB Gateway paper 4002")
    ap.add_argument("--konto", required=True, help="fx DUO509856")
    ap.add_argument("--udfoer", action="store_true",
                    help="send ordrer. Uden denne: preview")
    ap.add_argument("--tillad-live", dest="tillad_live", action="store_true")
    ap.add_argument("--client-id", dest="client_id", type=int,
                    default=ibkr_client_ids.SCRIPTS.get("flatten_alt.py", 8))
    args = ap.parse_args()

    kode = asyncio.run(hoved(args))

    print("\n" + "=" * 78)
    print("IBKR-FEJL, ORDRET")
    for reqId, k, tekst in IBKR_FEJL:
        print(f"  {k:>5}  (reqId {reqId})  {tekst}")
    if not IBKR_FEJL:
        print("  (ingen)")
    print("=" * 78)
    return kode


if __name__ == "__main__":
    sys.exit(main())

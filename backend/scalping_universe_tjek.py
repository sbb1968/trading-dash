#!/usr/bin/env python3
"""
scalping_universe_tjek.py — hvad af screener-listen kan faktisk handles her?
════════════════════════════════════════════════════════════════════════════════
Et TradingView-screenerudtræk siger om et instrument er interessant. Det siger
**intet** om hvorvidt din IBKR-konto kan prissætte eller handle det, og de to
spørgsmål blandes let sammen: symbolet står jo på skærmen.

Scriptet spørger IBKR direkte om tre ting pr. ticker, i rækkefølge:

    1  KONTRAKT      kan symbolet overhovedet kvalificeres? Hvilken børs?
    2  MARKEDSDATA   kommer der en kurs, eller mangler abonnementet?
    3  HANDEL        er der handelstilladelse for den børs?

⚠ ET SYMBOL UDEN BØRS ER TVETYDIGT. Screeneren giver bare `VAR`, `JD.`, `NHY`.
Kvalificeres de mod SMART/USD, kan de ramme et helt andet instrument med samme
bogstaver på en amerikansk børs — og så ville rapporten sige "kan handles" om
noget ganske andet end det Søren så i screeneren. Derfor bruges valutakolonnen
til at binde opslaget: NOK → Oslo, SEK → Stockholm, GBX → London, EUR → Europa.

⚠ GBX ER IKKE EN VALUTA HOS IBKR. London kvoterer i pence (GBX = 1/100 GBP);
IBKR bruger GBP. Uden den oversættelse fejler alle 93 London-tickere med en
besked om ukendt valuta, og det ville ligne et symbolproblem.

BRUG — kræver at TWS/Gateway kører på denne maskine:

    python scalping_universe_tjek.py --csv "...\\Soeren Scalping ....csv"
    python scalping_universe_tjek.py --csv ... --uden-markedsdata   # hurtigere

Skrivebeskyttet. Der sendes ingen ordrer.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import io
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock

import ibkr_client_ids

# Screener-valuta -> (IBKR-valuta, hvad vi kalder markedet)
VALUTA = {
    "NOK": ("NOK", "Oslo"),
    "SEK": ("SEK", "Stockholm"),
    "DKK": ("DKK", "København"),
    "EUR": ("EUR", "Euronext/Xetra"),
    # ⚠ pence, ikke pund. IBKR handler LSE i GBP.
    "GBX": ("GBP", "London"),
    "GBP": ("GBP", "London"),
    "USD": ("USD", "USA"),
}

IBKR_FEJL: list[tuple] = []


def ibkr_symbol(sym: str) -> str:
    """Screener-notation -> IBKR-notation.

    ⚠ AKTIEKLASSER SKRIVES FORSKELLIGT. TradingView bruger understreg
    (`SSAB_B`), IBKR bruger punktum (`SSAB.B`). Uden oversættelsen fejlede 11 af
    23 Stockholm-tickere med "No security definition" — og det ville have set ud
    som om instrumenterne ikke fandtes hos IBKR, frem for som en stavemåde.

    Målt 11-08: SSAB.B, NIBE.B, HUSQ.B m.fl. findes alle på SFB.
    """
    return sym.replace("_", ".")


def laes_csv(sti: str) -> list[dict]:
    raa = Path(sti).read_text(encoding="utf-8-sig")
    ud = []
    for r in csv.DictReader(io.StringIO(raa)):
        sym = (r.get("Symbol") or "").strip()
        if not sym:
            continue
        ud.append({
            "symbol":   sym,
            "navn":     (r.get("Description") or "").strip(),
            "valuta":   (r.get("Price - Currency") or "").strip().upper(),
            "pris":     r.get("Price") or "",
            "volumen":  r.get("Volume, 1 day") or "",
        })
    return ud


async def hoved(args) -> int:
    raekker = laes_csv(args.csv)
    print("=" * 78)
    print(f"SCALPING-UNIVERS  ·  {len(raekker)} tickere  ·  {args.host}:{args.port}")
    print("=" * 78)

    ukendt_valuta = [r for r in raekker if r["valuta"] not in VALUTA]
    if ukendt_valuta:
        print(f"\n⚠ {len(ukendt_valuta)} med ukendt valuta — springes over:")
        for r in ukendt_valuta[:5]:
            print(f"   {r['symbol']:12} {r['valuta']}")
    raekker = [r for r in raekker if r["valuta"] in VALUTA]

    ib = IB()

    def paa_fejl(reqId, kode, tekst, kontrakt):
        IBKR_FEJL.append((reqId, kode, tekst,
                          getattr(kontrakt, "symbol", "") if kontrakt else ""))
    ib.errorEvent += paa_fejl

    try:
        await asyncio.wait_for(
            ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=20),
            timeout=30)
    except Exception as e:
        print(f"\n⚠ Kunne ikke forbinde: {type(e).__name__}: {e}")
        print("   Koerer TWS/Gateway paa den port, og er API'et slaaet til?")
        return 1
    print(f"\nforbundet · konti: {ib.managedAccounts()}")

    # ── 1. Kvalificering ────────────────────────────────────────────────────
    print(f"\n1. Kontrakter — kan IBKR overhovedet finde dem?")
    resultat: dict[str, dict] = {}
    portion = 20
    for i in range(0, len(raekker), portion):
        gruppe = raekker[i:i + portion]
        kontrakter = []
        for r in gruppe:
            ibval, marked = VALUTA[r["valuta"]]
            k = Stock(ibkr_symbol(r["symbol"]), "SMART", ibval)
            kontrakter.append(k)
            resultat[r["symbol"]] = {**r, "marked": marked, "ibkr_valuta": ibval,
                                     "conid": 0, "boers": "", "kurs": None,
                                     "fejl": ""}
        try:
            await asyncio.wait_for(ib.qualifyContractsAsync(*kontrakter), timeout=60)
        except Exception as e:
            print(f"   ⚠ portion {i//portion + 1}: {type(e).__name__}: {e}")
        for r, k in zip(gruppe, kontrakter):
            cid = getattr(k, "conId", 0) or 0
            resultat[r["symbol"]]["conid"] = cid
            resultat[r["symbol"]]["boers"] = getattr(k, "primaryExchange", "") or ""
        print(f"   {min(i + portion, len(raekker)):3}/{len(raekker)} …", end="\r")
        await asyncio.sleep(0.4)

    fundet = [v for v in resultat.values() if v["conid"]]
    print(f"   {len(fundet)}/{len(raekker)} kunne kvalificeres" + " " * 20)

    # ── 2. Markedsdata ──────────────────────────────────────────────────────
    if not args.uden_markedsdata and fundet:
        print(f"\n2. Markedsdata — kommer der en kurs?")
        # ⚠ Kun en stikproeve pr. marked. 152 samtidige abonnementer ville sprænge
        # linjegraensen, og svaret er alligevel det samme for hele boersen: enten
        # har kontoen abonnementet, eller ogsaa har den ikke.
        pr_marked: dict[str, list] = defaultdict(list)
        for v in fundet:
            pr_marked[v["marked"]].append(v)

        for marked, poster in sorted(pr_marked.items()):
            proeve = poster[:4]
            for v in proeve:
                k = Stock(ibkr_symbol(v["symbol"]), "SMART", v["ibkr_valuta"])
                await ib.qualifyContractsAsync(k)
                t = ib.reqMktData(k, "", True, False)
                await asyncio.sleep(3.5)
                pris = next((x for x in (t.last, t.close, t.marketPrice())
                             if x and x == x and x > 0), None)
                v["kurs"] = pris
                ib.cancelMktData(k)
            haves = [v for v in proeve if v["kurs"]]
            print(f"   {marked:16} {len(haves)}/{len(proeve)} stikproever gav kurs"
                  + ("" if haves else "   ⚠ intet abonnement?"))

    ib.disconnect()

    # ── Rapport ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("RESULTAT PR. MARKED")
    print("=" * 78)
    pr_marked: dict[str, list] = defaultdict(list)
    for v in resultat.values():
        pr_marked[v["marked"]].append(v)

    for marked, poster in sorted(pr_marked.items()):
        ok_ = [v for v in poster if v["conid"]]
        kurs = [v for v in poster if v["kurs"]]
        print(f"\n{marked}  ({len(poster)} tickere)")
        print(f"   kontrakt fundet : {len(ok_)}/{len(poster)}")
        if not args.uden_markedsdata:
            print(f"   kurs i stikproeve: {'JA' if kurs else 'NEJ'}")
        mangler = [v['symbol'] for v in poster if not v["conid"]]
        if mangler:
            print(f"   ⚠ ikke fundet: {', '.join(mangler[:12])}"
                  + (f" … (+{len(mangler)-12})" if len(mangler) > 12 else ""))

    print("\n" + "=" * 78)
    print("IBKR-FEJLKODER, ORDRET")
    print("=" * 78)
    for kode, antal in Counter(k for _r, k, _t, _s in IBKR_FEJL).most_common():
        tekst = next(t for _r, k2, t, _s in IBKR_FEJL if k2 == kode)
        print(f"  {kode:>5}  ×{antal:<4} {tekst[:88]}")
    if not IBKR_FEJL:
        print("  (ingen)")

    print("\n⚠ HANDELSTILLADELSE er IKKE afproevet her — det kraever en rigtig")
    print("  ordre. En kontrakt der kan kvalificeres og prissaettes, kan stadig")
    print("  afvises paa tilladelse (fejlkode 200/201/2150).")
    print("=" * 78)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Hvad af screener-listen kan handles her?")
    ap.add_argument("--csv", default="")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--uden-markedsdata", dest="uden_markedsdata",
                    action="store_true")
    ap.add_argument("--client-id", dest="client_id", type=int,
                    default=ibkr_client_ids.for_script("scalping_universe_tjek.py"))
    args = ap.parse_args()

    if not args.csv:
        k = sorted(glob.glob(str(Path.home() / "Downloads" / "*Scalping*.csv")))
        if not k:
            print("Ingen --csv angivet, og ingen *Scalping*.csv i Downloads.")
            return 1
        args.csv = k[-1]
        print(f"(bruger {args.csv})")

    return asyncio.run(hoved(args))


if __name__ == "__main__":
    sys.exit(main())

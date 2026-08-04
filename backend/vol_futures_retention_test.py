#!/usr/bin/env python3
"""
vol_futures_retention_test.py — punkt B1 i Revision B
═══════════════════════════════════════════════════════════════════════════════════
Paastanden "IBKR's intradag-retention topper omkring to aar" blev udledt af ES, RTY,
NQ og VX. Men de er ALLE ogsaa underlagt kontraktlevetid — en ContFuture kan kun
levere den nuvaerende front-kontrakts historik. Der har aldrig vaeret en gyldig
kontrolgruppe bag konklusionen, og den kan lige saa godt skyldes kontraktlevetid
eller en harvest-parameter som retention.

DEN RENE TEST: bed om 1-min data for UDLOEBNE kontrakter, én ad gangen, bagud i
tid. Kontrakten eksisterede beviseligt, saa kontraktlevetid er elimineret som
forklaring. Svarer IBKR med barer, er retention ikke bindende dér. Den dato hvor
udloebne kontrakter holder op med at svare ER retention-graensen — nu renset.

Konsekvensen er reel: raekker futures-intradag fem aar tilbage i stedet for to,
faar spor 2 en aegte udviklingsperiode, og proxy-omvejen over SPY/IWM bliver
valgfri i stedet for noedvendig.

KOERSEL (workstation, TWS aabent):
    python vol_futures_retention_test.py
    python vol_futures_retention_test.py --symboler ES --client-id 66

Output: vol_probe_output/vol_futures_retention.{json,md}
"""
from __future__ import annotations

import asyncio

# Python 3.14: skal staa FOER ib_async importeres
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

IBKR_HOST, IBKR_PORT, CLIENT_ID = "127.0.0.1", 7497, 65
OUT_DIRNAME = "vol_probe_output"
SLEEP_BETWEEN = 2.0
REQ_TIMEOUT = 60

# Kvartalskontrakter bagud. For hver: (kontraktmaaned, en dato MIDT i dens
# aktive liv). ES/RTY ruller kvartalsvist (H/M/U/Z), udloeb 3. fredag.
# Vi spoerger en maaned foer udloeb, hvor kontrakten var front-maaned og likvid.
KONTRAKTER = [
    ("202509", datetime(2025, 8, 20, 20, 0)),
    ("202506", datetime(2025, 5, 20, 20, 0)),
    ("202412", datetime(2024, 11, 20, 20, 0)),
    ("202406", datetime(2024, 5, 20, 20, 0)),
    ("202312", datetime(2023, 11, 20, 20, 0)),
    ("202306", datetime(2023, 5, 22, 20, 0)),
    ("202212", datetime(2022, 11, 21, 20, 0)),
    ("202206", datetime(2022, 5, 20, 20, 0)),
    ("202112", datetime(2021, 11, 19, 20, 0)),
    ("202106", datetime(2021, 5, 20, 20, 0)),
    ("202006", datetime(2020, 5, 20, 20, 0)),
    ("201906", datetime(2019, 5, 20, 20, 0)),
]

# MES/M2K er dem vi faktisk handler; ES tages med som stor-kontrakt-kontrol.
# Micro-kontrakterne blev lanceret maj 2019 — aeldre kontrakter findes ikke.
SYMBOLER = {"MES": "CME", "M2K": "CME", "ES": "CME"}
BARSTOERRELSE = "1 min"
VARIGHED = "1 D"


async def probe_kontrakt(ib, sym: str, boers: str, maaned: str, midt: datetime, emit):
    """Kvalificér ÉN udloebet kontrakt og bed om 1-min barer midt i dens levetid."""
    from ib_async import Future
    # includeExpired=True er OBLIGATORISK for udloebne kontrakter — uden den svarer
    # IBKR "No security definition" selv for kontrakter der beviseligt har eksisteret.
    # Foerste koersel manglede den og gav 0 af 24 kvalificerede; det ville have set ud
    # som et datafund og ikke som en API-detalje. Samme moenster som harvest_futures_1min.
    c = Future(symbol=sym, lastTradeDateOrContractMonth=maaned,
               exchange=boers, currency="USD", includeExpired=True)
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=25)
        # conId-tjek — se ibkr_kvalificer. Uden det er en purget kontrakt ikke til
        # at skelne fra en levende, og hele testen giver falske positiver.
        c = q[0] if (q and getattr(q[0], "conId", 0)) else None
    except Exception as e:
        return dict(symbol=sym, maaned=maaned, kvalificeret=False,
                    fejl=f"kvalificering: {type(e).__name__}: {str(e)[:80]}", n=0)
    if c is None:
        return dict(symbol=sym, maaned=maaned, kvalificeret=False,
                    fejl="kontrakt findes ikke hos IBKR", n=0)
    c.includeExpired = True   # ogsaa paa den KVALIFICEREDE, ellers afvises historik-kaldet

    try:
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                c, endDateTime=midt.replace(tzinfo=timezone.utc),
                durationStr=VARIGHED, barSizeSetting=BARSTOERRELSE,
                whatToShow="TRADES", useRTH=False, formatDate=2),
            timeout=REQ_TIMEOUT)
    except asyncio.TimeoutError:
        return dict(symbol=sym, maaned=maaned, kvalificeret=True, fejl="timeout", n=0)
    except Exception as e:
        return dict(symbol=sym, maaned=maaned, kvalificeret=True,
                    fejl=f"{type(e).__name__}: {str(e)[:80]}", n=0)

    def dt(b):
        d = getattr(b, "date", None)
        if isinstance(d, datetime):
            return d.replace(tzinfo=None) if d.tzinfo else d
        try:
            return datetime.fromisoformat(str(d)[:19])
        except Exception:
            return None

    n = len(bars)
    return dict(symbol=sym, maaned=maaned, kvalificeret=True, fejl=None, n=n,
                conid=getattr(c, "conId", None),
                foerste=dt(bars[0]).isoformat() if n else None,
                sidste=dt(bars[-1]).isoformat() if n else None,
                spurgt_om=midt.date().isoformat())



# ── D1/D2: kortlaeg hvilke kontrakter der STADIG kan kvalificeres ──────────────
def kvartalsmaaneder(fra_aar: int, til_aar: int) -> list[str]:
    """H/M/U/Z — de kvartalsmaaneder ES/MES/RTY/M2K ruller i."""
    ud = []
    for a in range(fra_aar, til_aar + 1):
        for m in ("03", "06", "09", "12"):
            ud.append(f"{a}{m}")
    return ud


async def kortlaeg_overlevende(ib, symboler, emit):
    """Hvilke kontrakter lever endnu i IBKR's database?

    Kun kvalificering — ingen barhentning. Det er hurtigt og giver baade
    purge-graensen OG den liste der skal reddes (Revision D, punkt D1).

    D2-KONTROL: den nyeste kontrakt SKAL kunne kvalificeres. Kan den ikke, er
    noget galt med opsaetningen, og et nulresultat laengere tilbage betyder
    ingenting. Et nulresultat er ikke et fund foer det er holdt op mod et
    tilfaelde der beviseligt burde virke.
    """
    from ib_async import Future
    from datetime import date as _d
    i_aar = _d.today().year
    maaneder = kvartalsmaaneder(i_aar - 4, i_aar + 1)
    ud = {}
    for sym in symboler:
        boers = SYMBOLER.get(sym, "CME")
        levende, doede = [], []
        for ym in maaneder:
            c = Future(symbol=sym, lastTradeDateOrContractMonth=ym,
                       exchange=boers, currency="USD", includeExpired=True)
            # ⚠ qualifyContractsAsync returnerer en TRUTHY liste OGSAA naar
            # kvalificeringen mislykkedes — den lægger bare den ukvalificerede
            # kontrakt i den (conId forbliver 0). En test paa `if q:` giver derfor
            # "alle lever" for samtlige kontrakter. Fanget 4/8-2026 da en
            # kortlaegning paastod at kontrakter tilbage til 2022 var i live,
            # mens en rigtig barhentning for 202406 fejlede med
            # "No security definition". conId er det eneste paalidelige signal.
            try:
                q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=20)
                cc = q[0] if q else None
                (levende if (cc is not None and getattr(cc, "conId", 0)) else doede).append(ym)
            except Exception:
                doede.append(ym)
            await asyncio.sleep(0.4)
        ud[sym] = dict(levende=levende, doede=doede)
        if levende:
            emit(f"  {sym:<5} lever: {levende[0]} .. {levende[-1]}  ({len(levende)} kontrakter)")
            emit(f"        doede: {len([d for d in doede if d < levende[0]])} aeldre")
        else:
            emit(f"  {sym:<5} INGEN kontrakter kunne kvalificeres")
    # D2-KONTROL, begge veje. Et NULresultat skal holdes op mod noget der
    # beviseligt burde virke — og et POSITIVT resultat mod noget der beviseligt
    # IKKE burde. Uden den anden retning saa kortlaegningen "alle lever" ud som
    # et fund frem for som en API-detalje.
    from ib_async import Future as _F
    _neg = _F(symbol=symboler[0], lastTradeDateOrContractMonth="201503",
              exchange=SYMBOLER.get(symboler[0], "CME"), currency="USD",
              includeExpired=True)
    try:
        _q = await asyncio.wait_for(ib.qualifyContractsAsync(_neg), timeout=20)
        _cc = _q[0] if _q else None
        _neg_lever = bool(_cc is not None and getattr(_cc, "conId", 0))
    except Exception:
        _neg_lever = False
    if _neg_lever:
        emit("\n  ⚠ KENDT-NEGATIV KONTROL FEJLEDE: en kontrakt fra 2015 blev")
        emit("    kvalificeret. Metoden skelner ikke, og resultatet kasseres.")
        return None
    emit("  kendt-negativ kontrol OK: 201503 afvist som forventet")

    alle_levende = [v for s in ud.values() for v in s["levende"]]
    if not alle_levende:
        emit("\n  ⚠ D2-KONTROL FEJLEDE: ikke én kontrakt kunne kvalificeres paa noget")
        emit("    symbol. Det er en opsaetningsfejl, ikke et datafund. Resultatet")
        emit("    kasseres.")
        return None
    emit(f"\n  D2-kontrol OK: {len(alle_levende)} kontrakter kvalificeret i alt —")
    emit("  nulresultaterne laengere tilbage er derfor aegte purge, ikke opsaetning.")
    return ud


async def koer(args, emit):
    from ib_async import IB
    ud_dir = Path(__file__).resolve().parent / OUT_DIRNAME
    ud_dir.mkdir(exist_ok=True)
    symboler = [s.strip().upper() for s in args.symboler.split(",") if s.strip()]

    ib = IB()
    emit(f"Forbinder TWS {IBKR_HOST}:{args.port} (clientId={args.client_id}) ...")
    try:
        await ib.connectAsync(IBKR_HOST, args.port, clientId=args.client_id, timeout=20)
    except Exception as e:
        emit(f"KUNNE IKKE FORBINDE: {e}")
        return 1
    emit("Forbundet.\n")
    emit("Kontraktlevetid er ELIMINERET som forklaring: hver kontrakt eksisterede")
    emit("beviseligt paa den dato vi spoerger om.\n")

    if args.kortlaeg:
        emit("KORTLAEGNING — hvilke kontrakter lever endnu? (kun kvalificering)\n")
        try:
            ud = await kortlaeg_overlevende(ib, symboler, emit)
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
        if ud is None:
            return 1
        sti = ud_dir / "vol_futures_overlevende.json"
        sti.write_text(json.dumps(ud, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(f"\nSkrevet: {sti}")
        return 0

    res = []
    try:
        for sym in symboler:
            boers = SYMBOLER.get(sym, "CME")
            emit(f"[{sym}]")
            for maaned, midt in KONTRAKTER:
                r = await probe_kontrakt(ib, sym, boers, maaned, midt, emit)
                res.append(r)
                await asyncio.sleep(SLEEP_BETWEEN)
                if not r["kvalificeret"]:
                    emit(f"   {maaned}  KONTRAKT IKKE KVALIFICERET — {r['fejl']}")
                elif r["n"] == 0:
                    emit(f"   {maaned}  0 barer  (spurgt {r['spurgt_om']})"
                         + (f"  [{r['fejl']}]" if r.get("fejl") else ""))
                else:
                    emit(f"   {maaned}  {r['n']:>5} barer  "
                         f"{str(r['foerste'])[:16]} .. {str(r['sidste'])[:16]}")
            emit("")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    # ── dom ──
    L = ["# Futures-retention — testet med UDLOEBNE kontrakter\n",
         f"\nKoert: {datetime.now().isoformat(timespec='seconds')}  ·  "
         f"{BARSTOERRELSE}, {VARIGHED} pr. kontrakt, useRTH=False\n",
         "\nPunkt B1 i Revision B. Kontraktlevetid er elimineret som forklaring: hver "
         "kontrakt eksisterede beviseligt paa den dato der spoerges om. Kommer der "
         "barer, er retention ikke bindende dér.\n\n",
         "| symbol | kontrakt | spurgt om | barer | periode | note |\n",
         "|---|---|---|---|---|---|\n"]
    for r in res:
        note = r.get("fejl") or ("**INGEN DATA**" if r["n"] == 0 else "")
        per = (f"{str(r.get('foerste'))[:16]} .. {str(r.get('sidste'))[:16]}"
               if r.get("foerste") else "—")
        L.append(f"| {r['symbol']} | {r['maaned']} | {r.get('spurgt_om','—')} | "
                 f"{r['n']} | {per} | {note} |\n")

    L.append("\n## Dom\n\n")
    for sym in symboler:
        r_sym = [r for r in res if r["symbol"] == sym]
        med = [r for r in r_sym if r["n"] > 0]
        uden = [r for r in r_sym if r["n"] == 0]
        if med:
            aeldste = min(r["maaned"] for r in med)
            L.append(f"- **{sym}:** data helt tilbage til kontrakt **{aeldste}**. "
                     f"{len(med)} af {len(r_sym)} probede kontrakter svarede.\n")
            if uden:
                graense = max(r["maaned"] for r in uden if r["maaned"] < aeldste) \
                    if any(r["maaned"] < aeldste for r in uden) else None
                if graense:
                    L.append(f"  Foerste kontrakt UDEN data: {graense}. "
                             f"Retention-graensen ligger mellem {graense} og {aeldste}.\n")
        else:
            L.append(f"- **{sym}:** ingen udloebne kontrakter svarede. "
                     f"Retention er bindende og haard.\n")
    L.append("\n**Konsekvens for spor 2:** raekker futures-intradag laengere tilbage end "
             "de ~2 aar ContFuture kunne levere, faar spor 2 en aegte udviklingsperiode, "
             "og proxy-omvejen over SPY/IWM bliver valgfri frem for noedvendig.\n")

    (ud_dir / "vol_futures_retention.md").write_text("".join(L), encoding="utf-8")
    (ud_dir / "vol_futures_retention.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(f"Skrevet:\n  {ud_dir/'vol_futures_retention.md'}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="B1: futures-retention testet paa udloebne kontrakter")
    ap.add_argument("--symboler", default="MES,M2K,ES")
    ap.add_argument("--kortlaeg", action="store_true",
                    help="kortlaeg hvilke kontrakter der stadig kan kvalificeres "
                         "(kun kvalificering, ingen barhentning) — Revision D, D1")
    ap.add_argument("--port", type=int, default=IBKR_PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(koer(args, lambda s="": print(s, flush=True))))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

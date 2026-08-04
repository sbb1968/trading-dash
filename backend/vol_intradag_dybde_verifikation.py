#!/usr/bin/env python3
"""
vol_intradag_dybde_verifikation.py — punkt A9 i Revision A (BLOKERENDE)
═══════════════════════════════════════════════════════════════════════════════════
Hele spor 1 i specens dataopdeling hviler paa én paastand: at 1-min-historikken for
SPY, IWM og VIX raekker 15-20 aar tilbage. Den paastand kommer fra reqHeadTimeStamp
— og headstamp OVERLOVER systematisk paa intradag. I V0-koerslen kom SPY's
1-min-dybde netop fra en soegestige, og kvalitetstraekket for SPY og VIX TIMEDE UD.
Dybdetallet er altsaa ikke verificeret med rigtige barer.

Konsekvensen hvis paastanden ikke holder: raekker SPY 1-min kun ~2 aar tilbage,
findes der ingen udviklingsperiode for lag 3 overhovedet, og hele spor 1 falder.

Denne probe henter DERFOR FAKTISKE 1-min-barer i korte vinduer i udvalgte aar og
rapporterer for hvert aar: kom der barer, hvor mange, hvilken periode de daekker,
og ser dagen komplet ud mod en normal RTH-session (390 minutter).

Ingen headstamp bruges. Kun hentede barer taeller.

KOERSEL (workstation, TWS aabent):
    python vol_intradag_dybde_verifikation.py
    python vol_intradag_dybde_verifikation.py --symboler SPY,IWM,VIX --aar 2012,2018,2025

Output: vol_probe_output/vol_intradag_dybde.{json,md}
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

IBKR_HOST, IBKR_PORT, CLIENT_ID = "127.0.0.1", 7497, 64
OUT_DIRNAME = "vol_probe_output"
SLEEP_BETWEEN = 2.0
REQ_TIMEOUT = 60

# Aar der probes. Spaender specens tre perioder: udvikling (<=2023),
# design-validering (2024) og holdout (2025+).
AAR_DEFAULT = [2012, 2015, 2018, 2021, 2023, 2024, 2025]

# Instrumenter. SPY/IWM = ETF'er (udviklingsproxyer for MES/M2K), VIX = indeks.
INSTRUMENTER = {
    "SPY": dict(art="stk", boers="SMART", rolle="udviklingsproxy for MES"),
    "IWM": dict(art="stk", boers="SMART", rolle="udviklingsproxy for M2K (Revision A, A8)"),
    "VIX": dict(art="ind", boers="CBOE",  rolle="implicit vol, alle tre lag"),
}

# En normal RTH-session = 390 minutter. Vi henter 2 dage og forventer derfor
# ~780 barer hvis useRTH=True og begge dage er hele handelsdage.
FORVENTET_PR_DAG = 390
VARIGHED = "2 D"
# Midt i aaret, en onsdag-agtig dato uden helligdage taet paa.
MAANED_DAG = (6, 18)


def byg_kontrakt(sym: str, spec: dict):
    from ib_async import Index, Stock
    if spec["art"] == "ind":
        return Index(sym, spec["boers"], "USD")
    return Stock(sym, spec["boers"], "USD")


async def hent(ib, contract, slut: datetime, what: str):
    """Faktisk hentning. Returnerer (n, foerste, sidste, fejl)."""
    try:
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract, endDateTime=slut.replace(tzinfo=timezone.utc),
                durationStr=VARIGHED, barSizeSetting="1 min",
                whatToShow=what, useRTH=True, formatDate=2),
            timeout=REQ_TIMEOUT)
    except asyncio.TimeoutError:
        return 0, None, None, "timeout"
    except Exception as e:
        return 0, None, None, f"{type(e).__name__}: {str(e)[:90]}"
    if not bars:
        return 0, None, None, None

    def dt(b):
        d = getattr(b, "date", None)
        if isinstance(d, datetime):
            return d.replace(tzinfo=None) if d.tzinfo else d
        try:
            return datetime.fromisoformat(str(d)[:19])
        except Exception:
            return None
    return len(bars), dt(bars[0]), dt(bars[-1]), None


async def koer(args, emit):
    from ib_async import IB
    ud_dir = Path(__file__).resolve().parent / OUT_DIRNAME
    ud_dir.mkdir(exist_ok=True)

    symboler = [s.strip().upper() for s in args.symboler.split(",") if s.strip()]
    aar = [int(a) for a in str(args.aar).split(",") if str(a).strip()]

    ib = IB()
    emit(f"Forbinder TWS {IBKR_HOST}:{args.port} (clientId={args.client_id}) ...")
    try:
        await ib.connectAsync(IBKR_HOST, args.port, clientId=args.client_id, timeout=20)
    except Exception as e:
        emit(f"KUNNE IKKE FORBINDE: {e}")
        return 1
    emit("Forbundet.\n")
    emit(f"{len(symboler)} symboler x {len(aar)} aar = {len(symboler)*len(aar)} hentninger "
         f"(~{len(symboler)*len(aar)*SLEEP_BETWEEN/60:.0f} min)\n")

    resultater = []
    try:
        for sym in symboler:
            spec = INSTRUMENTER.get(sym, dict(art="stk", boers="SMART", rolle=""))
            c = byg_kontrakt(sym, spec)
            try:
                q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=20)
                c = q[0] if q else None
            except Exception as e:
                emit(f"  {sym}: kunne ikke kvalificeres — {e}")
                c = None
            if c is None:
                resultater.append(dict(symbol=sym, aar=None, fejl="ikke kvalificeret"))
                continue

            what = "TRADES"
            emit(f"[{sym}]  {spec.get('rolle','')}")
            for a in aar:
                slut = datetime(a, MAANED_DAG[0], MAANED_DAG[1], 21, 0, 0)
                n, f, l, fejl = await hent(ib, c, slut, what)
                await asyncio.sleep(SLEEP_BETWEEN)
                andel = (n / (2 * FORVENTET_PR_DAG) * 100.0) if n else 0.0
                resultater.append(dict(
                    symbol=sym, aar=a, n=n,
                    foerste=f.isoformat() if f else None,
                    sidste=l.isoformat() if l else None,
                    andel_af_forventet=round(andel, 1), fejl=fejl))
                status = ("INGEN DATA" if n == 0 else
                          f"{n:>4} barer  {str(f)[:16]} .. {str(l)[:16]}  "
                          f"({andel:.0f} % af 2 hele sessioner)")
                emit(f"   {a}  {status}" + (f"   [{fejl}]" if fejl else ""))
            emit("")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    # ── rapport ──
    L = ["# Intradag-dybde — VERIFICERET med faktiske hentninger\n",
         f"\nKoert: {datetime.now().isoformat(timespec='seconds')}  ·  "
         f"1-min barer, useRTH=True, {VARIGHED} pr. hentning\n",
         "\nPunkt A9 i Revision A. Ingen headstamp-paastande — kun barer der faktisk kom "
         "retur. En hel RTH-session er 390 minutter, saa en komplet 2-dages hentning er "
         "~780 barer.\n\n"]
    L.append("| symbol | aar | barer | periode | % af 2 sessioner | note |\n")
    L.append("|---|---|---|---|---|---|\n")
    for r in resultater:
        if r.get("aar") is None:
            L.append(f"| {r['symbol']} | — | — | — | — | {r.get('fejl')} |\n")
            continue
        per = (f"{str(r['foerste'])[:16]} .. {str(r['sidste'])[:16]}"
               if r.get("foerste") else "—")
        note = r.get("fejl") or ("**INGEN DATA**" if r["n"] == 0 else "")
        L.append(f"| {r['symbol']} | {r['aar']} | {r['n']} | {per} | "
                 f"{r['andel_af_forventet']} % | {note} |\n")

    (ud_dir / "vol_intradag_dybde.md").write_text("".join(L), encoding="utf-8")
    (ud_dir / "vol_intradag_dybde.json").write_text(
        json.dumps(resultater, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(f"Skrevet:\n  {ud_dir/'vol_intradag_dybde.md'}\n  {ud_dir/'vol_intradag_dybde.json'}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="A9: verificér 1-min-dybde med faktiske hentninger")
    ap.add_argument("--symboler", default="SPY,IWM,VIX")
    ap.add_argument("--aar", default=",".join(str(a) for a in AAR_DEFAULT))
    ap.add_argument("--port", type=int, default=IBKR_PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(koer(args, lambda s="": print(s, flush=True))))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

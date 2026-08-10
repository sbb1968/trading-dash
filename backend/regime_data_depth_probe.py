"""
regime_data_depth_probe.py - hvor langt tilbage raekker data REELT?
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497
CLIENT_ID = 53

OUTPUT_DIRNAME = "regime_data_depth_output"
MANIFEST_PATH  = Path("data_harvest") / "_manifest.json"
UNIVERSE_GLOB  = "historical_universe_*.json"

SLEEP_BETWEEN  = 1.2
PACING_WAIT    = 60
REQ_TIMEOUT    = 30
HEAD_TIMEOUT   = 20

DYBDE_STIGE    = [3, 6, 12, 24, 36, 48, 72]

SKIVE = {
    "1 day":   "5 D",
    "1 hour":  "2 D",
    "15 mins": "2 D",
    "5 mins":  "1 D",
    "1 min":   "1 D",
}

SMALLCAP_FALLBACK = ["SNDL", "OCGN", "TLRY"]


def byg_maal(inkl_aktier: bool, smallcaps: list[str]) -> list[dict]:
    maal = []
    for sym in ["ES", "NQ", "RTY", "MES", "MNQ", "M2K"]:
        maal.append({
            "label": sym,
            "art": "contfut",
            "kwargs": {"symbol": sym, "exchange": "CME", "currency": "USD"},
            "bars": ["1 day", "1 hour", "15 mins", "5 mins"],
            "what": "TRADES",
            "rth": False,
            "manifest_noegler": [f"{sym}|1day", f"{sym}|15min"],
        })

    if inkl_aktier:
        for sym in ["SPY", "AAPL"]:
            maal.append({
                "label": f"{sym} (kontrol)",
                "art": "stk",
                "kwargs": {"symbol": sym, "exchange": "SMART", "currency": "USD"},
                "bars": ["1 day", "1 hour", "15 mins", "5 mins", "1 min"],
                "what": "TRADES",
                "rth": True,
                "manifest_noegler": [],
            })
        for sym in smallcaps:
            maal.append({
                "label": f"{sym} (small-cap)",
                "art": "stk",
                "kwargs": {"symbol": sym, "exchange": "SMART", "currency": "USD"},
                "bars": ["1 day", "1 min"],
                "what": "TRADES",
                "rth": True,
                "manifest_noegler": [],
            })

    return maal


def find_smallcaps(backend: Path, antal: int = 3) -> list[str]:
    filer = sorted(backend.glob(UNIVERSE_GLOB))
    if not filer:
        return SMALLCAP_FALLBACK[:antal]
    try:
        data = json.loads(filer[-1].read_text(encoding="utf-8"))
        set_navne: list[str] = []
        for _dag, tickers in sorted(data.items()):
            for t in tickers:
                if t not in set_navne:
                    set_navne.append(t)
                if len(set_navne) >= antal:
                    return set_navne
        return set_navne or SMALLCAP_FALLBACK[:antal]
    except Exception:
        return SMALLCAP_FALLBACK[:antal]


def laes_manifest(backend: Path) -> dict:
    p = backend / MANIFEST_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def som_dato(vaerdi) -> datetime | None:
    if not vaerdi:
        return None
    s = str(vaerdi)
    for parse in (lambda x: datetime.fromisoformat(x),
                  lambda x: datetime.strptime(x[:10], "%Y-%m-%d")):
        try:
            d = parse(s)
            return d.replace(tzinfo=None) if d.tzinfo else d
        except Exception:
            continue
    return None


def aar_mellem(tidlig: datetime | None, sen: datetime | None) -> float | None:
    if not tidlig or not sen:
        return None
    return (sen - tidlig).days / 365.25


def klassificer(label: str, bar: str, head: datetime | None,
                dybest_bekraeftet: datetime | None,
                manifest_aeldste: datetime | None) -> str:
    if head is None and dybest_bekraeftet is None:
        return "INGEN DATA - tjek kontrakt/rettighed (se fejlkolonnen)"
    dyb = dybest_bekraeftet or head
    if manifest_aeldste and dyb and dyb < manifest_aeldste - timedelta(days=45):
        return "HARVEST-PARAMETER - IBKR har mere end vi har hentet (GRATIS at rette)"
    if "min" in bar or "hour" in bar:
        if dyb and (datetime.now() - dyb).days < 400:
            return "IBKR-RETENTION eller KONTRAKTLEVETID - intradag naar sjaeldent laengere"
    if manifest_aeldste and dyb and abs((dyb - manifest_aeldste).days) <= 45:
        return "VI HAR ALT - manifest matcher IBKR's dybde"
    return "IBKR-RETENTION - det er graensen"


async def kvalificer(ib, maal):
    from ib_async import ContFuture, Stock
    art, kw = maal["art"], maal["kwargs"]
    c = ContFuture(**kw) if art == "contfut" else Stock(**kw)
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=15)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        return q[0] if (q and getattr(q[0], "conId", 0)) else None
    except Exception:
        return None


async def hent_head(ib, contract, what: str):
    try:
        ts = await asyncio.wait_for(
            ib.reqHeadTimeStampAsync(contract, whatToShow=what,
                                     useRTH=False, formatDate=1),
            timeout=HEAD_TIMEOUT)
        if isinstance(ts, datetime):
            return ts.replace(tzinfo=None) if ts.tzinfo else ts
        return som_dato(ts)
    except Exception:
        return None


async def hent_skive(ib, contract, bar: str, slut: datetime, what: str, rth: bool):
    varighed = SKIVE.get(bar, "2 D")
    slut_utc = slut.replace(tzinfo=timezone.utc) if slut.tzinfo is None else slut
    try:
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract,
                endDateTime=slut_utc,
                durationStr=varighed,
                barSizeSetting=bar,
                whatToShow=what,
                useRTH=rth,
                formatDate=2,
            ),
            timeout=REQ_TIMEOUT)
        return (len(bars) if bars else 0), None
    except asyncio.TimeoutError:
        return 0, "timeout"
    except Exception as e:
        return 0, str(e)[:120]


async def maal_en(ib, maal, manifest: dict, deep: bool, fejl_log: list, emit):
    contract = await kvalificer(ib, maal)
    if contract is None:
        emit(f"  {maal['label']:20} KUNNE IKKE KVALIFICERES - springes over")
        return [{"label": maal["label"], "bar": b, "fejl": "kontrakt ikke kvalificeret"}
                for b in maal["bars"]]

    raekker = []
    for bar in maal["bars"]:
        MANIFEST_BAR = {"1day": "1 day", "15min": "15 mins", "5min": "5 mins", "1min": "1 min"}
        noegle = next((n for n in maal.get("manifest_noegler", [])
                       if MANIFEST_BAR.get(n.split("|")[-1]) == bar), None)
        man = manifest.get(noegle, {}) if noegle else {}
        man_aeldste = som_dato(man.get("oldest"))

        head = await hent_head(ib, contract, maal["what"])
        await asyncio.sleep(SLEEP_BETWEEN)

        bekraeftet = None
        tjek = []
        kandidater = []
        if head:
            kandidater.append(("indenfor head", head + timedelta(days=7)))
            kandidater.append(("udenfor head",  head - timedelta(days=30)))
        if deep:
            nu = datetime.now(timezone.utc).replace(tzinfo=None)
            for mdr in DYBDE_STIGE:
                kandidater.append((f"-{mdr} mdr", nu - timedelta(days=int(mdr * 30.44))))

        for navn, slut in kandidater:
            n, fejl = await hent_skive(ib, contract, bar, slut, maal["what"], maal["rth"])
            tjek.append({"punkt": navn, "slut": slut.strftime("%Y-%m-%d"),
                         "bars": n, "fejl": fejl})
            if fejl and "pacing" in fejl.lower():
                emit(f"    pacing-advarsel - venter {PACING_WAIT}s")
                await asyncio.sleep(PACING_WAIT)
            if fejl:
                fejl_log.append(f"{maal['label']} {bar} @{navn}: {fejl}")
            if n > 0 and (bekraeftet is None or slut < bekraeftet):
                bekraeftet = slut
            await asyncio.sleep(SLEEP_BETWEEN)

        dom = klassificer(maal["label"], bar, head, bekraeftet, man_aeldste)
        raekke = {
            "label": maal["label"],
            "bar": bar,
            "head_ibkr": head.strftime("%Y-%m-%d") if head else None,
            "bekraeftet_dybde": bekraeftet.strftime("%Y-%m-%d") if bekraeftet else None,
            "manifest_aeldste": man_aeldste.strftime("%Y-%m-%d") if man_aeldste else None,
            "manifest_noegle": noegle,
            "aar_ibkr": round(aar_mellem(head, datetime.now()) or 0, 2) if head else None,
            "aar_har": round(aar_mellem(man_aeldste, datetime.now()) or 0, 2) if man_aeldste else None,
            "dom": dom,
            "tjek": tjek,
        }
        if raekke["aar_ibkr"] and raekke["aar_har"]:
            raekke["gevinst_aar"] = round(raekke["aar_ibkr"] - raekke["aar_har"], 2)
        raekker.append(raekke)

        emit(f"  {maal['label']:20} {bar:8}  IBKR: {raekke['head_ibkr'] or '-':12}"
             f"  vi har: {raekke['manifest_aeldste'] or '-':12}  {dom}")

    return raekker


def skriv_rapport(raekker: list[dict], fejl_log: list[str], emit):
    emit("")
    emit("=" * 100)
    emit("  RESULTAT - HAR vs. TILBYDER")
    emit("=" * 100)
    emit(f"{'serie':22} {'bar':9} {'vi har':12} {'IBKR tilbyder':14} {'gevinst':>9}  dom")
    emit("-" * 100)
    samlet_gevinst = []
    for r in raekker:
        if r.get("fejl"):
            emit(f"{r['label']:22} {r['bar']:9} {'-':12} {'-':14} {'-':>9}  {r['fejl']}")
            continue
        g = r.get("gevinst_aar")
        g_txt = f"+{g:.1f} aar" if g and g > 0.25 else ("-" if g is None else "ingen")
        if g and g > 0.25:
            samlet_gevinst.append((r["label"], r["bar"], g))
        emit(f"{r['label']:22} {r['bar']:9} {r['manifest_aeldste'] or '-':12} "
             f"{r['head_ibkr'] or '-':14} {g_txt:>9}  {r['dom']}")

    emit("")
    emit("=" * 100)
    emit("  KONKLUSION")
    emit("=" * 100)
    if samlet_gevinst:
        emit("  Gratis historik vi IKKE har hentet endnu:")
        for label, bar, g in sorted(samlet_gevinst, key=lambda x: -x[2]):
            emit(f"    {label:22} {bar:9} +{g:.1f} aar")
        emit("")
        emit("  -> Udvid harvest-parametrene for disse serier FOER fase 1 koeres.")
        emit("     Percentil-referencen i fase 2 bliver praecis saa dyb som dette.")
    else:
        emit("  Ingen aabenlys gevinst - vi har stort set alt IBKR vil give os.")
        emit("  -> Percentil-referencens dybde er dermed en haard graense, ikke et valg.")
    emit("")
    emit("  HUSK ved intradag-futures: en enkelt expiry lever kun nogle faa maaneder.")
    emit("  Dybere 15-min-historik faas ved at stitche flere expiries (som")
    emit("  mes_m2k_stitched/ allerede goer) - ikke ved at bede IBKR om mere.")
    emit("")
    emit("  KONTROLGRUPPEN (SPY/AAPL) viser IBKR's aegte retention-graense.")
    emit("  Stopper en small-cap tidligere, er det aktiens egen levetid - ikke IBKR.")

    if fejl_log:
        emit("")
        emit("  FEJL UNDERVEJS (forventeligt ved 'udenfor head'-tjek):")
        for f in fejl_log[:40]:
            emit(f"    {f}")
        if len(fejl_log) > 40:
            emit(f"    ... og {len(fejl_log) - 40} mere")


async def koer(args, emit):
    from ib_async import IB

    backend = Path.cwd()
    manifest = laes_manifest(backend)
    smallcaps = find_smallcaps(backend)
    maal_liste = byg_maal(inkl_aktier=not args.no_stocks, smallcaps=smallcaps)

    if args.only:
        oensket = {s.strip().upper() for s in args.only.split(",")}
        maal_liste = [m for m in maal_liste
                      if m["label"].split()[0].upper() in oensket]

    emit(f"Manifest: {len(manifest)} serier laest" if manifest
         else "Manifest: IKKE fundet - koerer uden HAR-kolonne")
    emit(f"Small-caps til test: {', '.join(smallcaps)}")
    emit(f"Maal: {len(maal_liste)}   Deep-stige: {'JA' if args.deep else 'nej'}")
    emit("")

    ib = IB()
    fejl_log: list[str] = []

    def on_error(reqId, code, msg, *_):
        if code in (162, 200, 354, 165, 366):
            fejl_log.append(f"IBKR {code}: {msg[:100]}")

    ib.errorEvent += on_error

    try:
        await ib.connectAsync(IBKR_HOST, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"KUNNE IKKE FORBINDE til {IBKR_HOST}:{args.port} - {e}")
        emit("Er TWS aaben og logget ind? API aktiveret paa den port?")
        return 1

    emit(f"Forbundet. Konto: {ib.managedAccounts()}")
    emit("")
    emit("Maaler (dette tager nogle minutter - pacing-venligt med vilje)...")
    emit("")

    alle: list[dict] = []
    try:
        for m in maal_liste:
            alle.extend(await maal_en(ib, m, manifest, args.deep, fejl_log, emit))
    finally:
        ib.disconnect()

    skriv_rapport(alle, fejl_log, emit)

    out = backend / OUTPUT_DIRNAME
    out.mkdir(exist_ok=True)
    (out / "depth.json").write_text(
        json.dumps({"koert": datetime.now().isoformat(timespec="seconds"),
                    "deep": args.deep, "raekker": alle}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(
        description="Maaler hvor langt tilbage IBKR reelt serverer data (koeber intet, handler intet)")
    ap.add_argument("--port", type=int, default=IBKR_PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--only", default=None,
                    help="kommasepareret liste, fx ES,NQ,SPY")
    ap.add_argument("--deep", action="store_true",
                    help="fuld dybde-stige (3/6/12/24/36/48/72 mdr) - langsommere, mere praecist")
    ap.add_argument("--no-stocks", action="store_true",
                    help="spring aktier over (kun futures)")
    args = ap.parse_args()

    lines: list[str] = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 100)
    emit("  REGIME-MOTOR: DATA-DYBDE-PROBE   (laes-only, koeber intet, handler intet)")
    emit("=" * 100)
    emit(f"Tid: {datetime.now():%Y-%m-%d %H:%M}   Gateway: {IBKR_HOST}:{args.port}   "
         f"client-id {args.client_id}")
    emit("Spoergsmaal: hvor dybt kan percentil-referencen i regime-motor v2 naa?")
    emit("")

    try:
        kode = asyncio.get_event_loop().run_until_complete(koer(args, emit))
    except KeyboardInterrupt:
        emit("")
        emit("AFBRUDT af bruger - delvist resultat gemmes.")
        kode = 130

    out = Path.cwd() / OUTPUT_DIRNAME
    out.mkdir(exist_ok=True)
    (out / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit("")
    emit(f"Fil: {out / 'summary.txt'}")
    return kode


if __name__ == "__main__":
    sys.exit(main())

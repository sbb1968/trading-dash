r"""
vol_harvest.py — FASE V1: byg vol_cache for spor 1 (ETF og indeks)
═══════════════════════════════════════════════════════════════════════════════════
Henter det laaste instrumentsaet fra Revision A og laegger det i `vol_cache/`.
Futures-delen (MES/M2K) er allerede i hus i `data_harvest/` og roeres ikke herfra —
se vol_harvest_plan.md afsnit 0 for hvor data ligger.

SPECENS KRAV TIL V1, og hvor de er loest:

  · Inkrementel opdatering — et dagligt kald henter kun det nye  ->  `siden_sidst()`
  · Manifest pr. serie med IBKR-parametrene der blev brugt      ->  `skriv_manifest()`
  · Idempotent — koer to gange, faa samme resultat               ->  dedup paa bar-tid
  · Noegles paa DATA-dato, aldrig paa koerselsdato               ->  `by_ts`-noeglen

Det sidste er ikke bogholderi. Det forrige projekt noeglede paa koerselsdato og fik
nye raekker med identiske maalinger ved hver genkoersel — historikken saa ud til at
vokse uden at der kom ny viden.

KONTROLFIKSTUR I BEGGE RETNINGER (E2), permanent og foerst i koerslen:
  · kendt-POSITIV : SPY dagligt de sidste dage SKAL give barer. Goer den ikke det,
    er intet af det der foelger et datafund — det er en doed forbindelse.
  · kendt-NEGATIV : et symbol der umuligt kan findes maa IKKE kvalificere. Goer det
    det, svarer IBKR ja til alt, og hele koerslen kasseres.

Uden begge retninger er "vi fik ingen data" og "der er ingen data" ikke til at skelne
— og den forveksling har allerede kostet os tid én gang i dette projekt.

AFSLUTNING: fuldstaendighedsrevision mod NYSE-kalenderen (B3), saa den effektive
udviklingsstart fastlaegges af hvor sammenhaengen faktisk begynder — ikke af hvor den
foerste bar tilfaeldigvis ligger.

KOERSEL (workstation, TWS aabent):
    python vol_harvest.py --hvad dagligt         # alle dagsserier — minutter
    python vol_harvest.py --hvad 1min            # SPY/IWM/VIX 1-min — TIMER
    python vol_harvest.py --hvad alt
    python vol_harvest.py --hvad dagligt --toerloeb   # vis plan, hent intet

⚠ 1-MIN-DELEN ER LANG. Tre instrumenter × fjorten aar. Den er resumerbar pr. serie —
afbryd trygt, koer igen. Start med `--hvad dagligt`; den aabner lag 1 og 2 med det
samme, mens 1-min kun bruges af lag 3.
"""
from __future__ import annotations

import asyncio

# Python 3.14: skal staa FOER ib_async importeres
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 79
CACHE = "vol_cache"
SLEEP = 1.2
REQ_TIMEOUT = 90

# ── Det laaste saet (Revision A, afsnit 2.0) ──────────────────────────────────
# `start` er den dybeste dato det giver mening at bede om. For lag 1's serier er
# den sat af percentilreferencens start (B4: 2009-08-17, bundet af VIX3M), for
# 1-min-serierne af hvad A9 verificerede med rigtige barer (2012).
REFERENCE_START = date(2009, 8, 17)
INTRADAG_START = date(2012, 1, 1)

SERIER = [
    # navn, art, boers, barstoerrelser, hvorfor
    dict(navn="SPY",   art="stk", boers="SMART", bars=["1 day", "1 min"],
         hvorfor="lag 1 realiseret vol (ingen roll-gap) + lag 3 udviklingsproxy for MES"),
    dict(navn="IWM",   art="stk", boers="SMART", bars=["1 day", "1 min"],
         hvorfor="lag 3 udviklingsproxy for M2K (A8) — Russell-benet"),
    dict(navn="VIX",   art="ind", boers="CBOE",  bars=["1 day", "1 min"],
         hvorfor="lag 1 implicit vol-niveau + lag 3"),
    dict(navn="VIX3M", art="ind", boers="CBOE",  bars=["1 day"],
         hvorfor="lag 1 terminsstruktur sammen med VIX"),
    dict(navn="VIX9D", art="ind", boers="CBOE",  bars=["1 day"],
         hvorfor="lag 2 kort ende — begivenhedsrisiko"),
    dict(navn="RVX",   art="ind", boers="CBOE",  bars=["1 day"],
         hvorfor="lag 1 small-cap-vol (A2) — den population vi handler i"),
    dict(navn="VX",    art="contfut", sym="VIX", boers="CFE", bars=["1 day"],
         hvorfor="lag 2 FRISK implicit vol — spot-VIX opdaterer kun i RTH"),
]

# whatToShow pr. art. Indeks har ingen volumen; TRADES virker for CBOE-indeksene
# (verificeret i V0), ellers falder vi tilbage paa MIDPOINT.
WHAT = {"stk": ["TRADES"], "ind": ["TRADES", "MIDPOINT"], "contfut": ["TRADES"]}

# Hentevinduer. IBKR's graenser er strammest paa 1-min.
CHUNK = {"1 day": "1 Y", "1 min": "2 D"}

# ── Kontrolfikstur, begge retninger (E2) ──────────────────────────────────────
KENDT_POSITIV = dict(navn="SPY", art="stk", boers="SMART", bar="1 day")
KENDT_NEGATIV_SYM = "ZZQQXXNOTREAL"   # kan ikke findes hos nogen boers


def barkode(bar: str) -> str:
    return bar.replace(" ", "").replace("day", "dag").replace("mins", "min")


def sti(rod: Path, navn: str, bar: str) -> Path:
    return rod / CACHE / f"{navn}_{barkode(bar)}.csv"


# ═══════════════════════════════════════════════════════════════════════════════
# Cache-IO — noeglet paa DATA-dato, aldrig paa koerselsdato
# ═══════════════════════════════════════════════════════════════════════════════
def laes_cache(p: Path) -> dict[str, list]:
    """{iso-tidsstempel: raekke}. Tidsstemplet ER noeglen — derfor er genkoersler gratis."""
    ud: dict[str, list] = {}
    if not p.exists():
        return ud
    try:
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.reader(f):
                if r and r[0] != "timestamp":
                    ud[r[0]] = r
    except Exception:
        return {}
    return ud


def skriv_cache(p: Path, by_ts: dict[str, list]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for k in sorted(by_ts):
            w.writerow(by_ts[k])
    tmp.replace(p)


def sidste_bar(by_ts: dict) -> datetime | None:
    if not by_ts:
        return None
    try:
        return datetime.fromisoformat(max(by_ts))
    except ValueError:
        return None


def siden_sidst(by_ts: dict, dybeste: date) -> date:
    """Hvorfra skal der hentes? Inkrementel hvis der er noget, ellers helt tilbage.

    Overlap paa nogle dage er med vilje: dedup paa tidsstempel goer det gratis, og
    det fjerner risikoen for et hul i soemmen mellem to koersler.
    """
    s = sidste_bar(by_ts)
    if s is None:
        return dybeste
    return max(dybeste, s.date() - timedelta(days=3))


# ═══════════════════════════════════════════════════════════════════════════════
# IBKR
# ═══════════════════════════════════════════════════════════════════════════════
def byg_kontrakt(spec: dict):
    from ib_async import ContFuture, Index, Stock
    art = spec["art"]
    sym = spec.get("sym", spec["navn"])
    if art == "ind":
        return Index(sym, spec["boers"], "USD")
    if art == "contfut":
        return ContFuture(sym, spec["boers"], "USD")
    return Stock(sym, spec["boers"], "USD")


async def hent_skive(ib, contract, slut: datetime | str, bar: str, what: str,
                     varighed: str) -> list:
    from ib_async import util  # noqa: F401  (sikrer at ib_async er initialiseret)
    try:
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract,
                endDateTime=slut if isinstance(slut, str) else slut.replace(tzinfo=timezone.utc),
                durationStr=varighed, barSizeSetting=bar, whatToShow=what,
                useRTH=(bar == "1 min"), formatDate=2),
            timeout=REQ_TIMEOUT)
    except asyncio.TimeoutError:
        return []
    except Exception:
        return []
    return list(bars or [])


def som_raekke(b) -> tuple[str, list] | None:
    d = getattr(b, "date", None)
    if isinstance(d, datetime):
        ts = (d.replace(tzinfo=None) if d.tzinfo else d).isoformat()
    elif isinstance(d, date):
        ts = d.isoformat()
    else:
        try:
            ts = datetime.fromisoformat(str(d)[:19]).isoformat()
        except ValueError:
            return None
    return ts, [ts, b.open, b.high, b.low, b.close, int(getattr(b, "volume", 0) or 0)]


async def kontrolfikstur(ib, emit) -> bool:
    """Begge retninger, foer noget hentes. Fejler én af dem, er koerslen vaerdiloes."""
    from ibkr_kvalificer import kvalificer_eller_none
    from ib_async import Stock

    # KENDT-POSITIV: SPY dagligt SKAL give barer.
    c = await kvalificer_eller_none(ib, byg_kontrakt(KENDT_POSITIV))
    bars = await hent_skive(ib, c, "", "1 day", "TRADES", "5 D") if c else []
    if len(bars) < 2:
        emit("KONTROLFIKSTUR DUMPET (positiv): SPY dagligt gav ingen barer.")
        emit("  Alt der foelger ville vaere 'ingen data' — men det er forbindelsen,")
        emit("  ikke markedet. Koerslen kasseres.")
        return False
    emit(f"Kontrolfikstur OK (positiv): SPY dagligt gav {len(bars)} barer.")

    # KENDT-NEGATIV: et symbol der umuligt findes maa IKKE kvalificere.
    c = await kvalificer_eller_none(ib, Stock(KENDT_NEGATIV_SYM, "SMART", "USD"))
    if c is not None:
        emit(f"KONTROLFIKSTUR DUMPET (negativ): {KENDT_NEGATIV_SYM} kvalificerede "
             f"(conId {getattr(c, 'conId', '?')}). IBKR svarer ja til alt — kasseres.")
        return False
    emit(f"Kontrolfikstur OK (negativ): {KENDT_NEGATIV_SYM} kvalificerer ikke.\n")
    return True


async def hent_serie(ib, spec: dict, bar: str, rod: Path, emit,
                     toerloeb: bool = False) -> dict:
    """Hent én serie inkrementelt. Returnerer manifestposten."""
    from ibkr_kvalificer import kvalificer_eller_none

    p = sti(rod, spec["navn"], bar)
    by_ts = laes_cache(p)
    foer = len(by_ts)
    dybeste = INTRADAG_START if bar == "1 min" else REFERENCE_START
    fra = siden_sidst(by_ts, dybeste)
    i_dag = date.today()

    post = {"instrument": spec["navn"], "barstoerrelse": bar,
            "hvorfor": spec["hvorfor"], "fil": str(p.relative_to(rod)),
            "ibkr": {"art": spec["art"], "boers": spec["boers"],
                     "useRTH": bar == "1 min", "formatDate": 2,
                     "varighed_pr_kald": CHUNK.get(bar, "1 Y")}}

    if toerloeb:
        emit(f"   [{spec['navn']} {bar}] {foer} barer i cache · ville hente fra {fra}")
        post.update(barer_foer=foer, toerloeb=True)
        return post

    c = await kvalificer_eller_none(ib, byg_kontrakt(spec))
    if c is None:
        emit(f"   [{spec['navn']} {bar}] KUNNE IKKE KVALIFICERES — springes over")
        post.update(fejl="ikke kvalificeret", barer=foer)
        return post
    post["ibkr"]["conId"] = getattr(c, "conId", 0)

    varighed = CHUNK.get(bar, "1 Y")
    what_valgt = None
    slut = ""            # tom = 'nu'; vi gaar BAGUD indtil vi rammer `fra`
    tomme_i_traek = 0
    hentede = 0

    while True:
        got = []
        for what in ([what_valgt] if what_valgt else WHAT[spec["art"]]):
            got = await hent_skive(ib, c, slut, bar, what, varighed)
            if got:
                what_valgt = what
                break
        await asyncio.sleep(SLEEP)
        if not got:
            tomme_i_traek += 1
            if tomme_i_traek >= 2:
                break
            # Spring et vindue laengere tilbage og proev igen — enkelte tomme
            # svar er normale omkring helligdagsklynger.
            if isinstance(slut, str) and not slut:
                break
            slut = slut - timedelta(days=30)
            continue
        tomme_i_traek = 0

        aeldste = None
        for b in got:
            r = som_raekke(b)
            if r is None:
                continue
            if r[0] not in by_ts:
                hentede += 1
            by_ts[r[0]] = r[1]
            t = datetime.fromisoformat(r[0])
            aeldste = t if aeldste is None or t < aeldste else aeldste
        if aeldste is None:
            break
        if aeldste.date() <= fra:
            break
        slut = aeldste - timedelta(minutes=1)
        if hentede and hentede % 20000 == 0:
            skriv_cache(p, by_ts)      # loebende, saa en afbrydelse ikke koster alt

    skriv_cache(p, by_ts)
    s0, s1 = (min(by_ts) if by_ts else None), (max(by_ts) if by_ts else None)
    post.update(barer=len(by_ts), barer_foer=foer, nye_barer=len(by_ts) - foer,
                hentede_svar=hentede, foerste=s0, sidste=s1,
                what_to_show=what_valgt,
                hentet=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    emit(f"   [{spec['navn']} {bar}] {len(by_ts)} barer "
         f"(+{len(by_ts) - foer} nye) · {str(s0)[:10]} .. {str(s1)[:10]}")
    return post


def skriv_manifest(rod: Path, poster: list[dict]) -> Path:
    p = rod / CACHE / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tidligere = {}
    if p.exists():
        try:
            for x in json.loads(p.read_text(encoding="utf-8")).get("serier", []):
                tidligere[(x["instrument"], x["barstoerrelse"])] = x
        except Exception:
            pass
    for x in poster:
        tidligere[(x["instrument"], x["barstoerrelse"])] = x
    man = {"skema_version": "1.0",
           "opdateret": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "reference_start": REFERENCE_START.isoformat(),
           "intradag_start": INTRADAG_START.isoformat(),
           "serier": [tidligere[k] for k in sorted(tidligere)]}
    p.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


async def koer(args, emit) -> int:
    from ib_async import IB
    rod = Path(__file__).resolve().parent

    valgte = []
    for s in SERIER:
        for bar in s["bars"]:
            if args.hvad == "dagligt" and bar != "1 day":
                continue
            if args.hvad == "1min" and bar != "1 min":
                continue
            if args.kun and s["navn"] not in args.kun.upper().split(","):
                continue
            valgte.append((s, bar))
    if not valgte:
        emit("Intet valgt.")
        return 2

    emit(f"{len(valgte)} serier: " + ", ".join(f"{s['navn']}/{b}" for s, b in valgte))
    if args.hvad in ("1min", "alt"):
        emit("⚠ 1-min-delen tager TIMER. Den er resumerbar — afbryd trygt.")
    emit("")

    if args.toerloeb:
        poster = [await hent_serie(None, s, b, rod, emit, toerloeb=True) for s, b in valgte]
        emit("\nToerloeb — intet hentet, intet skrevet.")
        return 0

    ib = IB()
    emit(f"Forbinder TWS {HOST}:{args.port} (clientId={args.client_id}) …")
    try:
        await ib.connectAsync(HOST, args.port, clientId=args.client_id, timeout=20)
    except Exception as e:
        emit(f"KUNNE IKKE FORBINDE: {e}")
        return 1
    emit("Forbundet.\n")

    poster = []
    try:
        if not await kontrolfikstur(ib, emit):
            return 1
        for s, bar in valgte:
            poster.append(await hent_serie(ib, s, bar, rod, emit))
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    p = skriv_manifest(rod, poster)
    emit(f"\nManifest: {p}")
    nye = sum(x.get("nye_barer", 0) for x in poster)
    emit(f"{nye} nye barer i alt denne koersel.")
    emit("\nNaeste skridt — fuldstaendighedsrevision (B3):")
    emit("   python sessions_revision.py --mappe vol_cache --moenster \"*_1min.csv\" --streng")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FASE V1: byg vol_cache for spor 1")
    ap.add_argument("--hvad", choices=["dagligt", "1min", "alt"], default="dagligt")
    ap.add_argument("--kun", default=None, help="kommasepareret, fx SPY,IWM")
    ap.add_argument("--toerloeb", action="store_true", help="vis plan, hent intet")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", dest="client_id", type=int, default=CLIENT_ID)
    args = ap.parse_args()

    def emit(s=""):
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(s.encode(enc, "replace").decode(enc), flush=True)

    try:
        return asyncio.run(koer(args, emit))
    except KeyboardInterrupt:
        emit("\nAfbrudt. Cachen er skrevet loebende — koer igen for at fortsaette.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

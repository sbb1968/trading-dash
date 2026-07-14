#!/usr/bin/env python3
r"""
harvest_futures_1min.py — host 1-min bars for MES/M2K i et FAST datointerval
════════════════════════════════════════════════════════════════════════════
Henter 1-minuts OHLCV for CME-micro-futures (MES, M2K) for et datointerval du selv
angiver — bygget til at hoste april/maj/juni 2026.

KONTRAKT-ROLL HAANDTERES PR. DAG. MES/M2K er kvartals-kontrakter (mar/jun/sep/dec).
April-maj + juni indtil ~19. juni ligger paa JUNI-kontrakten; efter udloeb ruller
front-maaneden til SEPTEMBER — en ANDEN pris-serie (roll-gap). Scriptet resolver derfor
den kontrakt der var front-maaned PAA hver dato (eksplicit kvartalsmaaned + includeExpired,
= samme logik som ibkr_connect.qualify_future_asof) og skriver ÉN CSV pr. kontrakt-maaned,
saa juni- og september-bars ALDRIG blandes i samme serie.

Output (i --out, default data_harvest/):
    MES_202606_1min.csv   MES_202609_1min.csv   M2K_202606_1min.csv   ...
Kolonner: timestamp (ISO-ET), open, high, low, close, volume.

Read-only: handler ikke, sender ingen ordrer. Egen client-id (default 48; distinkt fra
backend og asian_harvest=47). useRTH=False (fuld ~23t futures-session), whatToShow=TRADES
(rigtig volumen). Resumerbar pr. fil (dedup paa bar-timestamp — afbrudt? koer igen).
Python 3.14 event-loop-fix. ib_async + stdlib.

Brug (paa Soerens workstation, fra backend/, med TWS/Gateway forbundet):
    python harvest_futures_1min.py                                   # default: MES,M2K · 2026-04-01..2026-06-30
    python harvest_futures_1min.py --start 2026-04-01 --end 2026-06-30
    python harvest_futures_1min.py --symbols MES --end 2026-06-18    # kun juni-kontrakten, kun MES
    python harvest_futures_1min.py --chunk-days 1 --client-id 49

Placering: C:\Projects\trading_dash\backend\harvest_futures_1min.py
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import statistics
import sys
from datetime import datetime, timedelta, timezone, date as datecls
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

from ib_async import IB, Future

# ── Defaults ──────────────────────────────────────────────────
HOST          = "127.0.0.1"
PORT          = 7497
CLIENT_ID     = 48                     # distinkt fra backend + asian_harvest (47)
BAR_SIZE      = "1 min"
WHAT_TO_SHOW  = "TRADES"
CHUNK_DAYS    = 2                      # 1-min bars pr. request (IBKR-graense; saenk til 1 ved pacing/tomme)
SLEEP_BETWEEN = 0.8
PACING_WAIT   = 60
HARVEST_DIR   = Path("data_harvest")

# CME-micro-boerser (samme kilde som ibkr_connect.FUTURES_EXCHANGE)
EXCHANGE = {"MES": "CME", "M2K": "CME"}
QUARTERLY_MONTHS = (3, 6, 9, 12)       # mar/jun/sep/dec
FRONT_MONTH_MAX_DAYS = 95              # en kontrakt er front-maaned ~ét kvartal foer udloeb;
                                       # datoer laengere foer dens udloeb hoerer til en TIDLIGERE
                                       # kontrakt (og pulles IKKE som back-month herfra)


# ═══════════════════════════════════════════════════════════════════
# Hjaelpere
# ═══════════════════════════════════════════════════════════════════
def _et(dt) -> datetime:
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(ET) if ET else dt
    return dt.replace(tzinfo=ET) if ET is not None else dt


def _parse_exp(s) -> "datecls | None":
    """'YYYYMMDD' eller 'YYYYMM' -> date (maaneds-slut for 6-cifret)."""
    s = (s or "").strip()
    try:
        if len(s) >= 8:
            return datetime.strptime(s[:8], "%Y%m%d").date()
        if len(s) == 6:
            return datetime.strptime(s + "28", "%Y%m%d").date()
    except ValueError:
        return None
    return None


def out_path(out_dir: Path, symbol: str, ym: str) -> Path:
    p = out_dir / f"{symbol}_{ym}_1min.csv"
    return p if p.is_absolute() else (Path.cwd() / p)


def load_existing(path: Path) -> dict:
    by_ts = {}
    if not path.exists():
        return by_ts
    try:
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                try:
                    dt = datetime.fromisoformat(r["timestamp"])
                except (ValueError, KeyError):
                    continue
                dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                by_ts[dt_utc] = {
                    "et": _et(dt), "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "volume": int(float(r["volume"])) if r.get("volume") else 0,
                }
    except Exception:
        return {}
    return by_ts


def write_csv(path: Path, by_ts: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for k in sorted(by_ts):
            b = by_ts[k]
            w.writerow([b["et"].isoformat(), b["open"], b["high"], b["low"], b["close"], b["volume"]])
    tmp.replace(path)


def expected_trading_days(s0: datecls, s1: datecls) -> list:
    """Man-fre i [s0, s1] (grov: helligdage ignoreres — CME-lukkedage giver bare tomme dage)."""
    out, d = [], s0
    while d <= s1:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def covered_days(by_ts: dict) -> list:
    """Distinkte ET-datoer der har mindst én bar (= dage vi allerede har hentet)."""
    return sorted({b["et"].date() for b in by_ts.values()})


def _fmt_days(days: list, head: int = 8, tail: int = 4) -> str:
    """Kompakt dato-liste: vis head foerste + tail sidste hvis lang."""
    ds = [f"{d:%m-%d}" for d in days]
    if len(ds) <= head + tail:
        return ", ".join(ds) or "(ingen)"
    return ", ".join(ds[:head]) + f"  … [{len(ds)-head-tail} mere] …  " + ", ".join(ds[-tail:])


def log_coverage(emit, symbol: str, ym: str, by_ts: dict, s0: datecls, s1: datecls) -> None:
    """Live-status: hvilke handelsdage er hentet, og hvilke mangler i [s0, s1]."""
    exp = expected_trading_days(s0, s1)
    have = [d for d in covered_days(by_ts) if s0 <= d <= s1]
    have_set = set(have)
    missing = [d for d in exp if d not in have_set]
    emit(f"   [{symbol} {ym}] dag-daekning: {len(have)}/{len(exp)} handelsdage hentet")
    emit(f"       hentet : {_fmt_days(have)}")
    emit(f"       mangler: {_fmt_days(missing)}")


async def reconnect(ib, host, port, client_id, emit, attempts=3, delay=8) -> bool:
    for a in range(1, attempts + 1):
        if ib.isConnected():
            return True
        try:
            ib.disconnect()
        except Exception:
            pass
        try:
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id, timeout=15), timeout=20)
            if ib.isConnected():
                emit(f"   genforbundet (forsoeg {a}/{attempts})")
                return True
        except Exception:
            pass
        await asyncio.sleep(delay)
    return False


# ═══════════════════════════════════════════════════════════════════
# Kontrakt-opdeling: hvilken kvartals-kontrakt var front-maaned pr. dato?
# ═══════════════════════════════════════════════════════════════════
async def quarterly_contracts(ib, symbol: str, start: datecls, end: datecls, emit):
    """Kvalificér de kvartals-kontrakter der kan vaere front-maaned i [start, end].

    Returnerer en sorteret liste af (expiry_date, ym 'YYYYMM', qualified_contract).
    Bruger includeExpired=True saa nyligt udloebne (fx juni set fra juli) stadig kan
    kvalificeres og levere historik.
    """
    exch = EXCHANGE.get(symbol, "CME")
    # Kandidat-kvartalsmaaneder: fra kvartalet der indeholder start, til ~2 kvartaler efter end.
    cand = []
    y = start.year
    while y <= end.year + 1:
        for m in QUARTERLY_MONTHS:
            # Kun kvartaler der kan vaere front-maaned for en dato i [start, end]:
            # udloeb (~den 20.) >= start OG maaneds-start <= end + ~100 dage. Undgaar at
            # probe endnu-ikke-noterede kontrakter (fx dec 2027) -> ingen Error 200-stoej.
            if datecls(y, m, 20) >= start and datecls(y, m, 1) <= end + timedelta(days=100):
                cand.append((y, m))
        y += 1
    out = []
    seen = set()
    for (yy, mm) in cand:
        ym = f"{yy}{mm:02d}"
        try:
            base = Future(symbol=symbol, exchange=exch, currency="USD",
                          lastTradeDateOrContractMonth=ym, includeExpired=True)
            details = await asyncio.wait_for(ib.reqContractDetailsAsync(base), timeout=15)
        except Exception as e:
            emit(f"   (kvartalsmaaned {ym}: {e})")
            continue
        for cd in (details or []):
            c = cd.contract
            if c.conId in seen:
                continue
            exp = _parse_exp(c.lastTradeDateOrContractMonth)
            if exp is None:
                continue
            c.includeExpired = True   # saa reqHistoricalData accepterer udloebet kontrakt
            seen.add(c.conId)
            out.append((exp, (c.lastTradeDateOrContractMonth or ym)[:6], c))
    out.sort(key=lambda t: t[0])
    return out


def build_segments(start: datecls, end: datecls, contracts):
    """Del [start, end] i sammenhaengende segmenter pr. front-maaned-kontrakt.

    En dato D hoerer til kontrakten med den NAERMESTE expiry >= D (front-maaned den dag) —
    MEN kun hvis D ligger inden for kontraktens front-maaned-vindue (<= FRONT_MONTH_MAX_DAYS
    foer dens udloeb). Ellers ville en dato hvis egentlige front-maaned-kontrakt er SLETTET
    hos IBKR (fx alt i 2023, hvor kun sep-2024-kontrakten stadig findes) blive tildelt en
    fjern kontrakt og hive dens TOMME back-month-data (volumen ~0) i stedet for reel front-
    maaned-likviditet. Saadanne datoer bliver i stedet et aabent hul (findes ikke).
    Returnerer liste af (ym, contract, seg_start, seg_end).
    """
    if not contracts:
        return []

    def contract_for(d: datecls):
        for exp, ym, c in contracts:      # sorteret stigende
            if exp >= d and (exp - d).days <= FRONT_MONTH_MAX_DAYS:
                return ym, c
        return None

    segs = []
    d = start
    cur = None            # (ym, contract, seg_start)
    while d <= end:
        cf = contract_for(d)
        if cf is None:
            d += timedelta(days=1)
            continue
        ym, c = cf
        if cur is None:
            cur = (ym, c, d)
        elif ym != cur[0]:
            segs.append((cur[0], cur[1], cur[2], d - timedelta(days=1)))
            cur = (ym, c, d)
        d += timedelta(days=1)
    if cur is not None:
        segs.append((cur[0], cur[1], cur[2], end))
    return segs


# ═══════════════════════════════════════════════════════════════════
# Hoest ét segment (én kontrakt, ét datointerval) — walk bagud i chunks
# ═══════════════════════════════════════════════════════════════════
async def pull_segment(ib, contract, seg_start: datecls, seg_end: datecls, what, bar_size,
                       chunk_days, by_ts, emit, write_cb, reconnect_cb, symbol="", ym=""):
    """1-min OHLCV fra seg_end tilbage til seg_start. Dedup paa timestamp, skriv pr. chunk.

    RESUME: harvesten gaar bagud, saa de allerede-hentede bars er den NYESTE del af
    segmentet. Vi genoptager derfor fra den AELDSTE eksisterende bar og fortsaetter nedad —
    ingen af de allerede-hentede dage genhentes."""
    floor_utc = datetime(seg_start.year, seg_start.month, seg_start.day, tzinfo=timezone.utc)

    existing_oldest = min(by_ts) if by_ts else None
    if existing_oldest is not None and existing_oldest <= floor_utc:
        emit(f"   [{symbol} {ym}] allerede hentet ned til segment-start — springer over.")
        return True
    if existing_oldest is not None:
        # Genoptag fra frontlinjen (aeldste eksisterende bar).
        end_dt = existing_oldest - timedelta(seconds=1)
        emit(f"   [{symbol} {ym}] RESUME: fortsaetter bagud fra {existing_oldest.astimezone(ET):%Y-%m-%d %H:%M} ET")
    else:
        # Frisk: endDateTime = dagen EFTER seg_end kl. 00:00 UTC (faar hele seg_end-dagen med).
        end_dt = datetime(seg_end.year, seg_end.month, seg_end.day, tzinfo=timezone.utc) + timedelta(days=1)
    end_str = end_dt.strftime("%Y%m%d %H:%M:%S") + " UTC"

    n_exp = len(expected_trading_days(seg_start, seg_end))
    total_chunks = (seg_end - seg_start).days // max(1, chunk_days) + 4
    reached_bottom = False
    for ci in range(total_chunks):
        bars = None
        for attempt in range(3):
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{chunk_days} D",
                    barSizeSetting=bar_size, whatToShow=what, useRTH=False, formatDate=1), timeout=90)
                break
            except Exception as e:
                msg = str(e).lower()
                if "pacing" in msg:
                    emit(f"   (pacing - venter {PACING_WAIT}s)"); await asyncio.sleep(PACING_WAIT); continue
                if "not connected" in msg or "peer closed" in msg or not ib.isConnected():
                    emit("   forbindelse tabt — proever at genforbinde...")
                    if await reconnect_cb():
                        continue
                    raise ConnectionError("TWS-forbindelse tabt (varig)")
                if attempt < 2:
                    await asyncio.sleep(3); continue
                emit(f"   reqHistoricalData fejl: {e}"); bars = None
        if not bars:
            emit(f"   chunk {ci + 1}: tom -> bunden naaet for {contract.localSymbol}.")
            reached_bottom = True; break

        chunk_oldest = None
        added = 0
        for b in bars:
            dt = _et(b.date)
            dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt
            if dt_utc < floor_utc:
                continue                       # foer segment-start — hoerer til forrige kontrakt
            if dt_utc not in by_ts:
                added += 1
            by_ts[dt_utc] = {"et": dt, "open": float(b.open), "high": float(b.high),
                             "low": float(b.low), "close": float(b.close),
                             "volume": int(b.volume) if b.volume else 0}
            if chunk_oldest is None or dt_utc < chunk_oldest:
                chunk_oldest = dt_utc
        write_cb(by_ts)
        if chunk_oldest is None:
            emit(f"   chunk {ci + 1}: kun bars foer segment-start -> stopper.")
            reached_bottom = True; break
        n_have = len([d for d in covered_days(by_ts) if seg_start <= d <= seg_end])
        emit(f"   chunk {ci + 1}: +{added} nye bars · frontlinje {chunk_oldest.astimezone(ET):%Y-%m-%d %H:%M} ET"
             f" · dag-daekning {n_have}/{n_exp} · total {len(by_ts)} bars")
        if chunk_oldest <= floor_utc or len(bars) < 5:
            reached_bottom = True; break
        end_str = (chunk_oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
        await asyncio.sleep(SLEEP_BETWEEN)
    return reached_bottom


async def harvest_symbol(ib, symbol, start, end, args, reconnect_cb, emit):
    emit("=" * 74)
    emit(f"  {symbol}   {start} -> {end}   ({args.what}, {args.bar_size}, useRTH=False)")
    emit("=" * 74)
    contracts = await quarterly_contracts(ib, symbol, start, end, emit)
    if not contracts:
        emit(f"   FEJL: ingen kvartals-kontrakter kunne kvalificeres for {symbol}.")
        return False
    segs = build_segments(start, end, contracts)
    if not segs:
        emit(f"   FEJL: ingen kontrakt daekker intervallet for {symbol}.")
        return False
    emit("   Kontrakt-opdeling (front-maaned pr. dato):")
    for ym, c, s0, s1 in segs:
        emit(f"     {ym}  ({c.localSymbol}, conId={c.conId}, udloeb {c.lastTradeDateOrContractMonth})"
             f"  ->  {s0} .. {s1}")
    # AERLIG frontier-note: bad om data foer den aeldste tilgaengelige kontrakts front-maaned?
    earliest = segs[0][2]
    if earliest > start:
        emit(f"   ℹ️ IBKR har ingen front-maaned-data foer {earliest} for {symbol} "
             f"(aeldre kontrakter er slettet). {start} .. {earliest - timedelta(days=1)} "
             f"findes ikke og springes over — IKKE en fejl.")

    out_dir = Path(args.out)
    all_done = True
    for ym, contract, s0, s1 in segs:
        path = out_path(out_dir, symbol, ym)
        by_ts = load_existing(path)
        emit(f"\n   ── {symbol} {ym}  ({s0} .. {s1})  →  {path.name}")
        if by_ts:
            emit(f"   RESUME: {len(by_ts)} eksisterende bars i {path.name}")
            log_coverage(emit, symbol, ym, by_ts, s0, s1)   # hvilke dage er ALLEREDE hentet
        else:
            emit(f"   frisk hoest (ingen eksisterende fil)")

        def write_cb(d, _p=path):
            write_csv(_p, d)

        try:
            done = await pull_segment(ib, contract, s0, s1, args.what, args.bar_size,
                                      args.chunk_days, by_ts, emit, write_cb, reconnect_cb,
                                      symbol=symbol, ym=ym)
            all_done = all_done and bool(done)
        except ConnectionError:
            raise
        if by_ts:
            vols = [b["volume"] for b in by_ts.values() if b["volume"] > 0]
            med = statistics.median(vols) if vols else 0
            ks = sorted(by_ts)
            emit(f"   SKREVET {path.name}: {len(by_ts)} bars · "
                 f"{by_ts[ks[0]]['et']:%Y-%m-%d %H:%M} -> {by_ts[ks[-1]]['et']:%Y-%m-%d %H:%M} ET · "
                 f"median 1-min volumen={med:.0f}")
            log_coverage(emit, symbol, ym, by_ts, s0, s1)   # slut-status: hentet vs mangler
        else:
            emit(f"   (ingen bars for {symbol} {ym})")
            all_done = False
    return all_done


async def main_async(args) -> int:
    def emit(s=""):
        print(s, flush=True)

    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        emit("FEJL: --start/--end skal vaere YYYY-MM-DD."); return 2
    if end < start:
        emit("FEJL: --end er foer --start."); return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    unknown = [s for s in symbols if s not in EXCHANGE]
    if unknown:
        emit(f"FEJL: ukendt(e) symbol(er): {unknown}. Kendte: {list(EXCHANGE)}"); return 2

    emit("=" * 78)
    emit("  MES/M2K 1-min HARVEST — fast interval, roll-bevidst (read-only, resumerbar)")
    emit("=" * 78)
    emit(f"  Tid: {datetime.now():%Y-%m-%d %H:%M}   TWS/Gateway: {args.host}:{args.port}   "
         f"client-id {args.client_id}")
    emit(f"  Symboler: {', '.join(symbols)}   Interval: {start} .. {end}   "
         f"chunk={args.chunk_days} D   out={args.out}\n")

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde til TWS/Gateway: {e}")
        return 1
    emit("  Forbundet.\n")

    async def do_reconnect():
        return await reconnect(ib, args.host, args.port, args.client_id, emit)

    all_complete = True
    interrupted = False
    try:
        for symbol in symbols:
            if not ib.isConnected() and not await do_reconnect():
                emit("  TWS varigt nede. Stopper rent (delvise CSV'er er gemt).")
                interrupted = True; break
            try:
                done = await harvest_symbol(ib, symbol, start, end, args, do_reconnect, emit)
                all_complete = all_complete and bool(done)
            except ConnectionError:
                emit("  TWS-forbindelse tabt under hoest. Alt hentet indtil nu er gemt — koer igen.")
                interrupted = True; break
            except Exception as e:
                emit(f"   FEJL ved {symbol}: {type(e).__name__}: {e}")
                all_complete = False
            emit("")
    finally:
        ib.disconnect()

    if all_complete and not interrupted:
        emit("  ✅ FAERDIG — alle TILGAENGELIGE segmenter fuldt hentet. (Er der en ℹ️-frontier-"
             "note ovenfor, findes aeldre data ikke hos IBKR — det er ikke en mangel.)")
        return 0
    emit("  ⚠ IKKE alt blev hentet (afbrudt/fejl). Delvise CSV'er er gemt —"
         " koer igen for at genoptage (resumerbar pr. fil).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Host 1-min bars for MES/M2K i et fast datointerval (roll-bevidst, resumerbar).")
    ap.add_argument("--symbols", default="MES,M2K", help="kommasepareret (default MES,M2K)")
    ap.add_argument("--start", default="2026-04-01", help="YYYY-MM-DD inkl. (default 2026-04-01)")
    ap.add_argument("--end", default="2026-06-30", help="YYYY-MM-DD inkl. (default 2026-06-30)")
    ap.add_argument("--bar-size", dest="bar_size", default=BAR_SIZE, help="default '1 min'")
    ap.add_argument("--what", default=WHAT_TO_SHOW, help="whatToShow (default TRADES)")
    ap.add_argument("--chunk-days", dest="chunk_days", type=int, default=CHUNK_DAYS,
                    help="dage pr. request (default 2; saenk til 1 ved pacing/tomme chunks)")
    ap.add_argument("--out", default=str(HARVEST_DIR), help="output-mappe (default data_harvest)")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", dest="client_id", type=int, default=CLIENT_ID)
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt — delvise CSV'er er gemt; koer igen for at genoptage.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

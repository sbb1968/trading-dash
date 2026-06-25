#!/usr/bin/env python3
"""
nikkei_harvest_1min.py — host OSE-Nikkei 1-min bars -> data_harvest/NIKKEI_1min.csv
═══════════════════════════════════════════════════════════════════════════════════════════════
Forudsaetning for nikkei_precondition.py (discovery-scan af den asiatiske session).
Skriver standard 1-min OHLCV CSV (samme format som nkd_harvest):
  timestamp,open,high,low,close,volume   (ISO ET-tidsstempler, tz-aware)
useRTH=False (baade Tokyo day- og night-session med; scanen session-gater selv).

Kontrakt via --contract {stor,mini,micro} (default MICRO = mest likvid; tynde bars laver
falsk mean-reversion, saa micro er det rigtige til en 1-min-scan). Alle OSE.JPN/JPY,
front-maaned, kvalificering loeftet fra asian_data_probe. Kontrakt-skift overskriver CSV'en
(blander aldrig to kontrakters bars — en .contract-markoer vogter det).

RESUME (1-min over ~15 mdr er langt + krydser TWS' 23.45-force-close):
  - CSV skrives efter HVER chunk -> en afbrudt koersel taber intet.
  - Ved genstart laeses den eksisterende CSV, og hoesten fortsaetter BAGUD fra aeldste bar
    (re-puller ikke det vi har). Koer den bare igen til den naar bunden af kontrakten.

Read-only: handler ikke, sender ingen ordrer. Egen client-id 47 (ingen kollision med en
koerende backend/strategi paa samme TWS). Kun historik -> kraever Japan/OSE-dataabonnement
(bekraeftet aktivt i asian_data_probe 25/6).

Python 3.14: event-loop-fix. ib_async. Kun stdlib derudover.

Brug (paa Soerens workstation, fra backend/):
    python nikkei_harvest_1min.py                 # micro (default), ~500 dage (bunder naturligt)
    python nikkei_harvest_1min.py --contract stor # eksplicit anden kontrakt
    python nikkei_harvest_1min.py --days 180

Placering: C:\\Projects\\trading_dash\\backend\\nikkei_harvest_1min.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

from ib_async import IB, Future

HOST          = "127.0.0.1"
PORT          = 7497
CLIENT_ID     = 47
BAR_SIZE      = "1 min"
CHUNK_DAYS    = 5                # "5 D" — gennemproevet 1-min chunk (probe_futures_depth)
SLEEP_BETWEEN = 0.8
PACING_WAIT   = 60
DEFAULT_DAYS  = 500
OUT_CSV       = Path("data_harvest") / "NIKKEI_1min.csv"
# OSE-Nikkei-kontrakter: samme indeks, VIDT forskellig likviditet -> mikrostruktur.
# micro er mest likvid (default) — tynde bars laver falsk mean-reversion. Alle OSE.JPN/JPY.
CONTRACTS = {"stor": "N225", "mini": "N225M", "micro": "N225MC"}
DEFAULT_CONTRACT = "micro"


def _et(dt) -> datetime:
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(ET) if ET else dt
    return dt.replace(tzinfo=ET) if ET is not None else dt


def load_existing(path: Path) -> dict:
    """Eksisterende CSV -> {utc_dt: row} til resume. Tom hvis filen ikke findes."""
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
    except Exception as e:
        print(f"   (kunne ikke laese eksisterende CSV: {e} — starter forfra)")
        return {}
    return by_ts


def write_csv(path: Path, by_ts: dict):
    """Skriv hele (merged, sorteret) datasaettet. Atomisk via .tmp. Kaldes efter hver chunk."""
    out = path if path.is_absolute() else (Path.cwd() / path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for k in sorted(by_ts):
            b = by_ts[k]
            w.writerow([b["et"].isoformat(), b["open"], b["high"], b["low"], b["close"], b["volume"]])
    tmp.replace(out)


async def qualify_front(ib, symbol, emit):
    """Naermeste ikke-udloebne OSE-Nikkei front (symbol via --contract). 10339-sikkert."""
    base = Future(symbol=symbol, exchange="OSE.JPN", currency="JPY")
    details = await asyncio.wait_for(ib.reqContractDetailsAsync(base), timeout=15)
    if not details:
        emit("   FEJL: ingen kontrakt-detaljer for N225@OSE.JPN (JPY).")
        return None

    def parse_exp(s: str):
        s = (s or "").strip()
        try:
            if len(s) >= 8:
                return datetime.strptime(s[:8], "%Y%m%d").date()
            return datetime.strptime(s[:6] + "01", "%Y%m%d").date()
        except ValueError:
            return None

    today = datetime.now().date()
    cands = [(parse_exp(d.contract.lastTradeDateOrContractMonth), d) for d in details]
    cands = [(e, d) for (e, d) in cands if e and e >= today]
    if not cands:
        emit("   FEJL: fandt kun udloebne OSE-Nikkei-kontrakter.")
        return None
    cands.sort(key=lambda t: t[0])
    c = cands[0][1].contract
    q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=15)
    c = q[0] if q else c
    emit(f"   KVALIFICERING: {c.localSymbol}  conId={c.conId}  udloeb {c.lastTradeDateOrContractMonth}"
         f"  mult={c.multiplier}  {c.currency}  ({c.exchange})")
    return c


async def pull_ohlcv(ib, contract, max_days, by_ts, emit, write_cb):
    """1-min OHLCV useRTH=False, walk bagud i 5 D-chunks. Resume: fortsaetter bagud fra
    aeldste eksisterende bar. Skriver efter hver chunk (afbrud-sikkert)."""
    target = datetime.now(timezone.utc) - timedelta(days=max_days)
    oldest = min(by_ts) if by_ts else None
    if oldest is not None and oldest <= target:
        emit(f"   Allerede dyb nok ({len(by_ts)} bars, aeldste {oldest.astimezone(ET):%Y-%m-%d}). Intet at hente.")
        return [by_ts[k] for k in sorted(by_ts)]
    if by_ts:
        emit(f"   RESUME: {len(by_ts)} bars findes (aeldste {oldest.astimezone(ET):%Y-%m-%d}) "
             f"-> fortsaetter bagud.")
        end_str = (oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
    else:
        end_str = ""

    for ci in range(max_days // CHUNK_DAYS + 8):
        bars = None
        for attempt in range(2):
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{CHUNK_DAYS} D",
                    barSizeSetting=BAR_SIZE, whatToShow="TRADES", useRTH=False,
                    formatDate=1), timeout=90)
                break
            except Exception as e:
                if "pacing" in str(e).lower():
                    emit(f"   (pacing - venter {PACING_WAIT}s)")
                    await asyncio.sleep(PACING_WAIT)
                    continue
                if attempt == 0:
                    await asyncio.sleep(3)
                    continue
                emit(f"   reqHistoricalData fejl: {e}")
                bars = None
        if not bars:
            emit(f"   chunk {ci + 1}: tom -> bunden for front-kontrakten er naaet.")
            break
        chunk_oldest = None
        for b in bars:
            dt = _et(b.date)
            dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt
            by_ts[dt_utc] = {
                "et": dt, "open": float(b.open), "high": float(b.high),
                "low": float(b.low), "close": float(b.close),
                "volume": int(b.volume) if b.volume else 0,
            }
            if chunk_oldest is None or dt_utc < chunk_oldest:
                chunk_oldest = dt_utc
        write_cb(by_ts)   # resume-sikkert: skriv efter hver chunk
        emit(f"   chunk {ci + 1}: +{len(bars)} bars (aeldste {chunk_oldest.astimezone(ET):%Y-%m-%d})"
             f"  | total {len(by_ts)} (skrevet)")
        if oldest is not None and chunk_oldest >= oldest:
            emit("   (ingen aeldre bars - bunden naaet)")
            break
        oldest = chunk_oldest
        if oldest <= target or len(bars) < 30:
            break
        end_str = (oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
        await asyncio.sleep(SLEEP_BETWEEN)
    return [by_ts[k] for k in sorted(by_ts)]


async def main_async(args):
    def emit(s=""):
        print(s, flush=True)

    symbol = CONTRACTS[args.contract]
    emit("=" * 78)
    emit(f"  OSE-NIKKEI ({args.contract}, {symbol}) 1-min HARVEST -> data_harvest/NIKKEI_1min.csv  (read-only, resumerbar)")
    emit("=" * 78)
    emit(f"  Tid: {datetime.now():%Y-%m-%d %H:%M}   Gateway: {args.host}:{args.port}   "
         f"client-id {args.client_id}   bar={BAR_SIZE}")

    out = OUT_CSV if OUT_CSV.is_absolute() else (Path.cwd() / OUT_CSV)
    meta = out.with_suffix(out.suffix + ".contract")   # husker hvilken kontrakt CSV'en er
    prev = meta.read_text(encoding="utf-8").strip() if meta.exists() else None
    if prev == args.contract:   # resume KUN naar markoeren matcher praecis (ingen markoer = ukendt = frisk)
        by_ts = load_existing(out)
        if by_ts:
            emit(f"  Fandt {len(by_ts)} eksisterende '{args.contract}'-bars -> resume-tilstand.\n")
        else:
            emit("  Ingen brugbar eksisterende CSV -> frisk hoest.\n")
    else:
        by_ts = {}
        if out.exists():
            emit(f"  Eksisterende CSV matcher ikke '{args.contract}' (markoer={prev}) "
                 f"-> starter FORFRA (overskriver; blander aldrig to kontrakter).\n")
        else:
            emit("  Ingen eksisterende CSV -> frisk hoest.\n")

    def write_cb(d):
        write_csv(OUT_CSV, d)
        try:
            meta.write_text(args.contract, encoding="utf-8")
        except OSError:
            pass

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde til TWS: {e}")
        return 1
    emit("  Forbundet.\n")

    try:
        contract = await qualify_front(ib, symbol, emit)
        if contract is None:
            return 1
        emit(f"\n  Henter 1-min OHLCV (useRTH=False) op til {args.days} dage bagud...")
        bars = await pull_ohlcv(ib, contract, args.days, by_ts, emit, write_cb)
    finally:
        ib.disconnect()

    if len(bars) < 50:
        emit(f"\n  FOR FAA BARS ({len(bars)}).")
        return 1

    oldest = bars[0]["et"]
    newest = bars[-1]["et"]
    emit("")
    emit("=" * 78)
    emit(f"  SKREVET: {out}")
    emit(f"  {len(bars)} bars  ·  {oldest:%Y-%m-%d %H:%M} -> {newest:%Y-%m-%d %H:%M} ET  "
         f"({(newest - oldest).days} kalenderdage)")
    emit("")
    emit("  Bunden ikke naaet endnu? Koer scriptet igen — det genoptager bagud fra aeldste bar.")
    emit("  Naeste: python nikkei_precondition.py  (offline discovery-scan af den asiatiske session)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Host OSE-Nikkei 1-min til data_harvest/NIKKEI_1min.csv (resumerbar)")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--contract", choices=list(CONTRACTS), default=DEFAULT_CONTRACT,
                    help=f"OSE-Nikkei-kontrakt (default {DEFAULT_CONTRACT} = mest likvid; "
                         f"skift overskriver CSV'en, blander ikke kontrakter)")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt — delvis CSV er gemt; koer igen for at genoptage.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

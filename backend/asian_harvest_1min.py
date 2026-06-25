#!/usr/bin/env python3
"""
asian_harvest_1min.py — host 1-min bars for HELE asian_registry -> data_harvest/{LABEL}_1min.csv
═════════════════════════════════════════════════════════════════════════════════════════════════
Generalisering af nikkei_harvest_1min over fem instrumenter (Nikkei mini, Hang Seng, A50,
USD/JPY, AUD/JPY). Forudsaetning for asian_sweep_precondition.py.

Pr. instrument:
  - futures: whatToShow=TRADES (rigtig volumen til THIN-flag), useRTH=False, front-maaned.
  - fx:      whatToShow=MIDPOINT (spot-FX paa IDEALPRO har INGEN trades), useRTH=False.
  - skriver standard OHLCV-CSV (timestamp ISO-ET) + rapporterer median 1-min volumen.

RESUMERBAR (langt: 5 instrumenter x mange mdr 1-min): CSV skrives efter HVER chunk, og en
.contract-markoer (= label) vogter hver fil -> resume KUN naar markoeren matcher; intet
blandes. Afbrudt? Koer igen — fortsaetter bagud fra aeldste bar pr. fil.

Read-only: handler ikke, sender ingen ordrer. Egen client-id (default 47; distinkt fra
backend). Kun historik -> live-abonnement IKKE noedvendigt (HSI/A50 har historik selv uden
realtid). Python 3.14 event-loop-fix. ib_async + stdlib.

Brug (paa Soerens workstation, fra backend/):
    python asian_harvest_1min.py                 # host hele registret (resumerbar)
    python asian_harvest_1min.py --only NIKKEI,HSI
    python asian_harvest_1min.py --days 300

Placering: C:\\Projects\\trading_dash\\backend\\asian_harvest_1min.py
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

from ib_async import IB, Future, Forex
from asian_registry import REGISTRY

HOST          = "127.0.0.1"
PORT          = 7497
CLIENT_ID     = 47
BAR_SIZE      = "1 min"
CHUNK_DAYS    = 5
SLEEP_BETWEEN = 0.8
PACING_WAIT   = 60
DEFAULT_DAYS  = 500
HARVEST_DIR   = Path("data_harvest")


def _et(dt) -> datetime:
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(ET) if ET else dt
    return dt.replace(tzinfo=ET) if ET is not None else dt


def out_path(label):
    p = HARVEST_DIR / f"{label}_1min.csv"
    return p if p.is_absolute() else (Path.cwd() / p)


def load_existing(path):
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


def write_csv(path, by_ts):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for k in sorted(by_ts):
            b = by_ts[k]
            w.writerow([b["et"].isoformat(), b["open"], b["high"], b["low"], b["close"], b["volume"]])
    tmp.replace(path)


async def qualify(ib, inst, emit):
    """futures: front-maaned af Future(symbol,exchange,currency); fx: Forex(pair). 10339-sikkert."""
    if inst["kind"] == "fx":
        cds = await asyncio.wait_for(ib.reqContractDetailsAsync(Forex(inst["pair"])), timeout=15)
        if not cds:
            emit(f"   FEJL: ingen kontrakt for FX {inst['pair']}")
            return None
        c = cds[0].contract
        emit(f"   KVALIFICERING: {c.localSymbol or inst['pair']} ({c.exchange}) [FX/MIDPOINT]")
        return c
    base = Future(symbol=inst["symbol"], exchange=inst["exchange"], currency=inst["currency"])
    details = await asyncio.wait_for(ib.reqContractDetailsAsync(base), timeout=15)
    if not details:
        emit(f"   FEJL: ingen kontrakt-detaljer for {inst['symbol']}@{inst['exchange']}")
        return None

    def parse_exp(s):
        s = (s or "").strip()
        try:
            return datetime.strptime((s[:8] if len(s) >= 8 else s[:6] + "01"), "%Y%m%d").date()
        except ValueError:
            return None

    today = datetime.now().date()
    cands = [(parse_exp(d.contract.lastTradeDateOrContractMonth), d) for d in details]
    cands = [(e, d) for (e, d) in cands if e and e >= today]
    if not cands:
        emit(f"   FEJL: kun udloebne kontrakter for {inst['symbol']}")
        return None
    cands.sort(key=lambda t: t[0])
    c = cands[0][1].contract
    q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=15)
    c = q[0] if q else c
    emit(f"   KVALIFICERING: {c.localSymbol}  conId={c.conId}  udloeb {c.lastTradeDateOrContractMonth}"
         f"  mult={c.multiplier}  {c.currency}  ({c.exchange})")
    return c


async def pull(ib, contract, what, max_days, by_ts, emit, write_cb):
    """1-min OHLCV, walk bagud i 5 D-chunks. Resume fra aeldste eksisterende. Skriv pr. chunk."""
    target = datetime.now(timezone.utc) - timedelta(days=max_days)
    oldest = min(by_ts) if by_ts else None
    if oldest is not None and oldest <= target:
        emit(f"   Allerede dyb nok ({len(by_ts)} bars). Springer over.")
        return [by_ts[k] for k in sorted(by_ts)]
    end_str = (oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC" if by_ts else ""
    if by_ts:
        emit(f"   RESUME: {len(by_ts)} bars (aeldste {oldest.astimezone(ET):%Y-%m-%d}) -> bagud.")
    for ci in range(max_days // CHUNK_DAYS + 8):
        bars = None
        for attempt in range(2):
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{CHUNK_DAYS} D",
                    barSizeSetting=BAR_SIZE, whatToShow=what, useRTH=False, formatDate=1), timeout=90)
                break
            except Exception as e:
                if "pacing" in str(e).lower():
                    emit(f"   (pacing - venter {PACING_WAIT}s)"); await asyncio.sleep(PACING_WAIT); continue
                if attempt == 0:
                    await asyncio.sleep(3); continue
                emit(f"   reqHistoricalData fejl: {e}"); bars = None
        if not bars:
            emit(f"   chunk {ci + 1}: tom -> bunden naaet."); break
        chunk_oldest = None
        for b in bars:
            dt = _et(b.date)
            dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt
            by_ts[dt_utc] = {"et": dt, "open": float(b.open), "high": float(b.high),
                             "low": float(b.low), "close": float(b.close),
                             "volume": int(b.volume) if b.volume else 0}
            if chunk_oldest is None or dt_utc < chunk_oldest:
                chunk_oldest = dt_utc
        write_cb(by_ts)
        emit(f"   chunk {ci + 1}: +{len(bars)} bars (aeldste {chunk_oldest.astimezone(ET):%Y-%m-%d}) | total {len(by_ts)}")
        if oldest is not None and chunk_oldest >= oldest:
            break
        oldest = chunk_oldest
        if oldest <= target or len(bars) < 30:
            break
        end_str = (oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
        await asyncio.sleep(SLEEP_BETWEEN)
    return [by_ts[k] for k in sorted(by_ts)]


async def harvest_one(ib, inst, days, emit):
    label = inst["label"]
    emit("─" * 70)
    emit(f"  {label}  ({inst['kind']}, what={inst['what']})")
    emit("─" * 70)
    path = out_path(label)
    meta = path.with_suffix(path.suffix + ".contract")
    prev = meta.read_text(encoding="utf-8").strip() if meta.exists() else None
    if prev == label:
        by_ts = load_existing(path)
        emit(f"   resume: {len(by_ts)} eksisterende bars" if by_ts else "   frisk hoest")
    else:
        by_ts = {}
        if path.exists():
            emit(f"   markoer != {label} (={prev}) -> starter FORFRA (overskriver)")

    def write_cb(d):
        write_csv(path, d)
        try:
            meta.write_text(label, encoding="utf-8")
        except OSError:
            pass

    contract = await qualify(ib, inst, emit)
    if contract is None:
        return
    bars = await pull(ib, contract, inst["what"], days, by_ts, emit, write_cb)
    if len(bars) < 50:
        emit(f"   FOR FAA BARS ({len(bars)}).")
        return
    vols = [b["volume"] for b in bars if b["volume"] > 0]
    med = statistics.median(vols) if vols else 0
    thin = " ⚠THIN" if (inst["kind"] == "futures" and med < 50) else ""
    emit(f"   SKREVET {path.name}: {len(bars)} bars · {bars[0]['et']:%Y-%m-%d} -> {bars[-1]['et']:%Y-%m-%d} ET"
         f" · median 1-min volumen={med:.0f}{thin}")


async def main_async(args):
    def emit(s=""):
        print(s, flush=True)

    only = set(x.strip().upper() for x in args.only.split(",")) if args.only else None
    insts = [i for i in REGISTRY if (only is None or i["label"] in only)]

    emit("=" * 78)
    emit("  ASIAN SWEEP — 1-min HARVEST af registret  (read-only, resumerbar)")
    emit("=" * 78)
    emit(f"  Tid: {datetime.now():%Y-%m-%d %H:%M}   Gateway: {args.host}:{args.port}   "
         f"client-id {args.client_id}   instrumenter: {', '.join(i['label'] for i in insts)}")

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde til TWS: {e}")
        return 1
    emit("  Forbundet.\n")
    try:
        for inst in insts:
            try:
                await harvest_one(ib, inst, args.days, emit)
            except Exception as e:
                emit(f"   FEJL ved {inst['label']}: {type(e).__name__}: {e}")
            emit("")
    finally:
        ib.disconnect()
    emit("  Faerdig. Bunden ikke naaet for alle? Koer igen — resumerbar pr. fil.")
    emit("  Naeste: python asian_sweep_precondition.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Host asian_registry 1-min (resumerbar)")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--only", default=None, help="kommasepareret subset, fx NIKKEI,HSI")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt — delvise CSV'er er gemt; koer igen for at genoptage.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

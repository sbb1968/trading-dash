#!/usr/bin/env python3
"""
nkd_harvest_15min.py — host NKD (CME Nikkei) 15-min bars til data_harvest/NKD_15min.csv
═══════════════════════════════════════════════════════════════════════════════════════
Skriver standard 15-min OHLCV CSV (NKD-strategiens eget data-grundlag):
  timestamp,open,high,low,close,volume   (ISO ET-tidsstempler, tz-aware -04:00)
useRTH=False (hele Globex-doegnet; backtesten session-gater selv).

Front-kontrakten (NKDU6) raekker ~3 maaneder (densitets-tjekket: tilbage til 2026-03-23)
- det er hvad EEN kontrakt giver. Dybere historik = stitching af flere front-maaneder;
byg det kun hvis den preliminaere backtest lover.

Skriver KUN data_harvest/NKD_15min.csv - roerer ikke andre instrumenters filer.
Genbruger densitets-tjekkets verificerede qualify + pull (4009 bars hentet rent).

Read-only: handler ikke, sender ingen ordrer. Egen client-id 46 (ingen kollision med
en koerende backend/strategi paa samme TWS). Kun historik -> kraever intet realtids-abonnement.

Python 3.14: event-loop-fix. ib_async. Kun stdlib derudover.

Brug (paa samme maskine som densitets-tjekket, fra backend/):
    python nkd_harvest_15min.py

Bagefter koeres NKD-strategiens egen backtest paa data_harvest/NKD_15min.csv (selvstaendig
mean-reversion-regel paa EU-morgen-vinduet; bygges separat).

Placering: C:\\Projects\\trading_dash\\backend\\nkd_harvest_15min.py
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
CLIENT_ID     = 46
BAR_SIZE      = "15 mins"
CHUNK_DAYS    = 30
SLEEP_BETWEEN = 0.6
PACING_WAIT   = 60
DEFAULT_DAYS  = 400
OUT_CSV       = Path("data_harvest") / "NKD_15min.csv"


def _et(dt) -> datetime:
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(ET) if ET else dt
    return dt.replace(tzinfo=ET) if ET is not None else dt


async def qualify_front(ib, emit):
    """Naermeste ikke-udloebne NKD-front (CME, USD). 10339-sikkert via contract details."""
    base = Future(symbol="NKD", exchange="CME", currency="USD")
    details = await asyncio.wait_for(ib.reqContractDetailsAsync(base), timeout=15)
    if not details:
        emit("   FEJL: ingen kontrakt-detaljer for NKD@CME (USD).")
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
        emit("   FEJL: fandt kun udloebne NKD-kontrakter.")
        return None
    cands.sort(key=lambda t: t[0])
    c = cands[0][1].contract
    q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=15)
    c = q[0] if q else c
    emit(f"   KVALIFICERING: {c.localSymbol}  conId={c.conId}  udloeb {c.lastTradeDateOrContractMonth}"
         f"  mult={c.multiplier}  {c.currency}  ({c.exchange})")
    return c


async def pull_ohlcv(ib, contract, max_days, emit):
    """15-min OHLCV useRTH=False, walk bagud i 30 D-chunks. Dedup paa tidsstempel."""
    by_ts = {}
    end_str = ""
    oldest = None
    target = datetime.now(timezone.utc) - timedelta(days=max_days)
    for ci in range(max_days // CHUNK_DAYS + 4):
        bars = None
        for attempt in range(2):
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{CHUNK_DAYS} D",
                    barSizeSetting=BAR_SIZE, whatToShow="TRADES", useRTH=False,
                    formatDate=1), timeout=60)
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
                "et": dt,
                "open": float(b.open), "high": float(b.high),
                "low": float(b.low), "close": float(b.close),
                "volume": int(b.volume) if b.volume else 0,
            }
            if chunk_oldest is None or dt_utc < chunk_oldest:
                chunk_oldest = dt_utc
        emit(f"   chunk {ci + 1}: +{len(bars)} bars (aeldste {chunk_oldest.astimezone(ET):%Y-%m-%d})"
             f"  | total {len(by_ts)}")
        if chunk_oldest is None or (oldest is not None and chunk_oldest >= oldest):
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

    emit("=" * 78)
    emit("  NKD (CME Nikkei) 15-min HARVEST -> data_harvest/NKD_15min.csv  (read-only)")
    emit("=" * 78)
    emit(f"  Tid: {datetime.now():%Y-%m-%d %H:%M}   Gateway: {args.host}:{args.port}   "
         f"client-id {args.client_id}   bar={BAR_SIZE}")

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde til TWS: {e}")
        return 1
    emit("  Forbundet.\n")

    try:
        contract = await qualify_front(ib, emit)
        if contract is None:
            return 1
        emit(f"\n  Henter 15-min OHLCV (useRTH=False) op til {args.days} dage bagud...")
        bars = await pull_ohlcv(ib, contract, args.days, emit)
    finally:
        ib.disconnect()

    if len(bars) < 50:
        emit(f"\n  FOR FAA BARS ({len(bars)}) - skriver ikke CSV.")
        return 1

    out = OUT_CSV
    if not out.is_absolute():
        out = Path.cwd() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b["et"].isoformat(), b["open"], b["high"], b["low"], b["close"], b["volume"]])

    oldest = bars[0]["et"]
    newest = bars[-1]["et"]
    emit("")
    emit("=" * 78)
    emit(f"  SKREVET: {out}")
    emit(f"  {len(bars)} bars  ·  {oldest:%Y-%m-%d %H:%M} -> {newest:%Y-%m-%d %H:%M} ET  "
         f"({(newest - oldest).days} kalenderdage)")
    emit("")
    emit("  Naeste: NKD-strategiens egen backtest paa NKD_15min.csv (selvstaendig regel, bygges separat).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Host NKD 15-min til data_harvest/NKD_15min.csv")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

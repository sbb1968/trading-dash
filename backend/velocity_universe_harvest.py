#!/usr/bin/env python3
"""
velocity_universe_harvest.py — neutralt univers 1-min bar-harvest (hastigheds-møllen)
═══════════════════════════════════════════════════════════════════════════════════
Host 1-min RTH-bars for ~200 NEUTRALE navne (velocity_universe_200.csv) — et cap-/
sektor-balanceret univers UDEN change%/ATR-forfilter og UDEN top-N-sortering. Formål:
re-køre velocity_backtest.py på data der IKKE er forhåndssorteret på bevægelse, så vi
ser om momentum-edgen (PF 1.3-1.6 på bar_cache) HOLDER eller var en artefakt af
top-25-sorteringen i K2/BuyTheDip's screener.

Genbruger den VERIFICEREDE bar-pagering fra catalyst_harvest.py (972ce56): 30 D pr.
reqHistoricalData-kald, walk bagud med eksplicit UTC-endDateTime. MEN skriver
tidsstempler i ET (offset, fx 2026-03-20T09:30:00-04:00) så skemaet matcher bar_cache
1:1 og velocity_backtest kan læse begge uændret.

KØR IKKE mens en strategi handler. Egen client-id (default 29). Python 3.14 → *Async.
Læs-only mod IBKR.

Brug (fra backend/):
    python velocity_universe_harvest.py                 # alle 200, ~110 dage
    python velocity_universe_harvest.py --limit 5       # verificér først
    python velocity_universe_harvest.py --days 110

Output:
    velocity_universe_data/bars/{TICKER}_{start}_{end}_1min.csv  (timestamp ET, OHLCV)
    velocity_universe_harvest_output/summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\velocity_universe_harvest.py
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

import pytz

ET = pytz.timezone("America/New_York")

IBKR_HOST  = "127.0.0.1"
IBKR_PORT  = 7497
CLIENT_ID  = 29
BAR_CHUNK_DAYS = 30          # 1-min: målt OK i ét kald (~11.700 bars/~17s)
SLEEP_BETWEEN  = 0.6
PACING_WAIT    = 60
DEFAULT_DAYS   = 110         # ~2026-03-01 → i dag: matcher bar_cache-perioden + lidt OOS
UNIVERSE_CSV   = "velocity_universe_200.csv"

PRIMARY = {"NYSE": "NYSE", "NASDAQ": "NASDAQ", "AMEX": "AMEX"}


def load_universe(path: Path):
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sym = (r.get("Symbol") or "").strip()
            exch = (r.get("Exchange") or "").strip()
            if sym:
                rows.append((sym, exch))
    return rows


async def harvest_bars(ib, contract, days: int, emit_dbg=None):
    """1-min RTH-bars ~`days` bagud, ET-tidsstempler. Walk bagud i 30 D-chunks med
    eksplicit UTC-endDateTime (verificeret korrekt i catalyst_harvest)."""
    target_start = datetime.now(timezone.utc) - timedelta(days=days)
    end_str = ""
    by_ts = {}
    oldest_utc = None
    for _chunk in range(days // BAR_CHUNK_DAYS + 6):
        bars = None
        for attempt in range(2):
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{BAR_CHUNK_DAYS} D",
                    barSizeSetting="1 min", whatToShow="TRADES", useRTH=True, formatDate=1),
                    timeout=60)
                break
            except Exception as e:
                if "pacing" in str(e).lower():
                    await asyncio.sleep(PACING_WAIT)
                    continue
                if attempt == 0:
                    await asyncio.sleep(3)
                    continue
                if emit_dbg:
                    emit_dbg(f"      reqHistoricalData fejl: {e}")
                bars = None
        if not bars:
            break
        chunk_oldest = None
        for b in bars:
            d = b.date
            if hasattr(d, "tzinfo") and d.tzinfo is not None:
                dt_et = d.astimezone(ET)
                dt_utc = d.astimezone(timezone.utc)
            elif hasattr(d, "isoformat"):
                dt_local = ET.localize(d)        # naiv → antag ET
                dt_et, dt_utc = dt_local, dt_local.astimezone(timezone.utc)
            else:
                continue
            by_ts[dt_utc] = {
                "timestamp": dt_et.isoformat(),
                "open": float(b.open), "high": float(b.high), "low": float(b.low),
                "close": float(b.close), "volume": int(b.volume) if b.volume else 0,
            }
            if chunk_oldest is None or dt_utc < chunk_oldest:
                chunk_oldest = dt_utc
        if chunk_oldest is None:
            break
        if oldest_utc is not None and chunk_oldest >= oldest_utc:
            break
        oldest_utc = chunk_oldest
        if oldest_utc <= target_start or len(bars) < 50:
            break
        end_str = (oldest_utc - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
        await asyncio.sleep(SLEEP_BETWEEN)
    return [by_ts[k] for k in sorted(by_ts)]


def _safe_name(sym: str) -> str:
    return sym.replace("/", "-").replace(".", "-")


async def main_async(args, emit) -> int:
    from ib_async import IB, Stock

    universe = load_universe(Path(args.universe))
    if args.limit:
        universe = universe[:args.limit]
    bars_dir = Path.cwd() / "velocity_universe_data" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)

    emit("=" * 78)
    emit("  HASTIGHEDS-MØLLEN — neutralt univers 1-min bar-harvest")
    emit("=" * 78)
    emit(f"Tid: {datetime.now():%Y-%m-%d %H:%M}   navne: {len(universe)}   periode: {args.days} dage")

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  ❌ FORBINDELSESFEJL: {e} (tjek TWS + port {args.port} + ledigt client-id {args.client_id})")
        return 1

    totals = {"bars": 0, "ok": 0, "skipped": 0, "missing": []}
    try:
        emit(f"  ✅ Forbundet. Konto: {', '.join(ib.managedAccounts()) or '(ingen)'}")
        emit("")
        emit(f"  {'ticker':<8}{'bars':>9}{'spænd (ældste→nyeste)':>30}")
        emit("  " + "─" * 60)
        for idx, (sym, exch) in enumerate(universe, 1):
            # RESUME: spring over hvis denne ticker allerede er hentet (medmindre --force).
            # Gør en genstart efter TWS-aflogning idempotent — kun manglende navne hentes.
            if not args.force and list(bars_dir.glob(f"{_safe_name(sym)}_*_1min.csv")):
                totals["skipped"] += 1
                emit(f"  {sym:<8}  (allerede hentet — springer over)  [{idx}/{len(universe)}]")
                continue
            primary = PRIMARY.get(exch.upper())
            try:
                contract = (Stock(sym, "SMART", "USD", primaryExchange=primary)
                            if primary else Stock(sym, "SMART", "USD"))
                await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10)
            except Exception as e:
                emit(f"  {sym:<8}  ❌ kvalificering: {str(e)[:50]}  [{idx}/{len(universe)}]")
                totals["missing"].append(sym)
                continue
            bars = await harvest_bars(ib, contract, args.days)
            n = len(bars)
            if n == 0:
                emit(f"  {sym:<8}{0:>9}   (ingen data)  [{idx}/{len(universe)}]")
                totals["missing"].append(sym)
                continue
            d0, d1 = bars[0]["timestamp"][:10], bars[-1]["timestamp"][:10]
            fname = f"{_safe_name(sym)}_{d0}_{d1}_1min.csv"
            with (bars_dir / fname).open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
                w.writeheader()
                w.writerows(bars)
            totals["bars"] += n
            totals["ok"] += 1
            emit(f"  {sym:<8}{n:>9}   {d0} → {d1}  [{idx}/{len(universe)}]")
        emit("")
        emit("  " + "═" * 60)
        emit(f"  TOTAL: {totals['ok']}/{len(universe)} hentet · {totals['skipped']} sprunget over "
             f"(allerede hentet) · {totals['bars']:,} 1-min bars")
        if totals["missing"]:
            emit(f"  Manglende ({len(totals['missing'])}): {', '.join(totals['missing'])}")
        emit(f"  CSV'er: {bars_dir}")
    finally:
        ib.disconnect()
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Neutralt univers 1-min bar-harvest (læs-only)")
    ap.add_argument("--host", default=IBKR_HOST)
    ap.add_argument("--port", type=int, default=IBKR_PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--universe", default=UNIVERSE_CSV)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true",
                    help="genhent også navne der allerede har en CSV (default: spring over = resume)")
    args = ap.parse_args()

    out_dir = Path.cwd() / "velocity_universe_harvest_output"
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    try:
        rc = asyncio.run(main_async(args, emit))
    except KeyboardInterrupt:
        emit("\nAfbrudt.")
        rc = 130
    finally:
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        emit(f"\nFil: {out_dir / 'summary.txt'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

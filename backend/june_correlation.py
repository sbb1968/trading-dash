#!/usr/bin/env python3
"""
june_correlation.py
═══════════════════
Det HÅRDE korrelationstal: kør washout-reclaim på K2's FAKTISKE juni-dage og
-univers, og læg den daglige P&L direkte op mod K2's egen daglige P&L.

Spørgsmålet vi afgør: på K2's tabsdage (især 4. juni) — taber washout-reclaim
OGSÅ (→ korreleret, IKKE et komplement), eller tjener den (→ ægte komplement)?

Kører på ALGOSERVEREN: læser K2's univers (universe_selected, source="Konfluens 2")
og K2's daglige P&L (trades) fra archives/iben_workstation, henter de manglende
juni 1-min bars fra Gateway (egen client-id 35), kører washout-reclaim i sin
deployerbare form (max 3 samtidige, prioritet=dybeste washout, åbningsvindue),
og sammenligner dag for dag.

Python 3.14: event-loop-fix øverst. ib_async. KØR I SIKKERT VINDUE.

Brug:
    python june_correlation.py
    python june_correlation.py --no-fetch        # kun cache + analyse
    python june_correlation.py --since 2026-06-01

Output i ./june_correlation_output/: summary.txt

Placering: C:\\projects\\trading_dash\\backend\\june_correlation.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date as date_cls, time as dtime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

SRC_K2 = "Konfluens 2"
CACHE_DIRNAME = "bar_cache"
OUTPUT_DIRNAME = "june_correlation_output"
DEFAULTS = dict(lookback=20, min_runup=3.0, washout=1.5, target=2.0)


@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


# ─────────────────────────────────────────────────────────────────────────────
# Sti / DB
# ─────────────────────────────────────────────────────────────────────────────
def find_backend_dir(explicit):
    cands = [Path(explicit)] if explicit else []
    cands += [Path.cwd(), Path(__file__).resolve().parent, Path.cwd() / "backend"]
    for c in cands:
        if c.exists() and ((c / "archives").exists() or (c / "trading_dash.db").exists()
                           or (c / CACHE_DIRNAME).exists()):
            return c.resolve()
    return Path.cwd().resolve()


def resolve_db(backend, source):
    if source:
        p = backend / "archives" / source / "trading_dash.db"
        if p.exists():
            return p
    p = backend / "archives" / "iben_workstation" / "trading_dash.db"
    if p.exists():
        return p
    return backend / "trading_dash.db"


def open_ro(path):
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.execute("SELECT 1")
        conn.row_factory = sqlite3.Row
        return conn, None
    except sqlite3.OperationalError:
        tmp = Path(tempfile.mkdtemp(prefix="jun_"))
        base = tmp / "trading_dash.db"
        shutil.copy2(path, base)
        for ext in ("-wal", "-shm"):
            sib = Path(str(path) + ext)
            if sib.exists():
                shutil.copy2(sib, Path(str(base) + ext))
        conn = sqlite3.connect(str(base), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn, tmp


def load_k2_universe(db_path, since):
    """{date_iso: [tickers]} fra K2's universe_selected-events fra og med 'since'."""
    if not db_path.exists():
        return {}
    conn, tmp = open_ro(db_path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return {}
        rows = conn.execute(
            "SELECT ts_local, payload_json FROM events WHERE event_type='universe_selected' AND source=?",
            (SRC_K2,)).fetchall()
        out = {}
        for r in rows:
            d = (r["ts_local"] or "")[:10]
            if not d or d < since:
                continue
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            tickers = [str(t).upper() for t in payload.get("tickers", [])]
            if tickers:
                out.setdefault(d, [])
                for t in tickers:
                    if t not in out[d]:
                        out[d].append(t)
        return out
    finally:
        conn.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def load_k2_daily_pnl(db_path, since):
    """{date_iso: sum_pnl_$} for K2's lukkede handler fra og med 'since'."""
    if not db_path.exists():
        return {}
    conn, tmp = open_ro(db_path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'").fetchone():
            return {}
        rows = conn.execute(
            "SELECT entry_time_et, pnl FROM trades WHERE source=? AND exit_time_utc IS NOT NULL",
            (SRC_K2,)).fetchall()
        out = defaultdict(float)
        for r in rows:
            et = r["entry_time_et"]
            if et and len(et) >= 10 and et[:10] >= since:
                try:
                    out[et[:10]] += float(r["pnl"])
                except (TypeError, ValueError):
                    pass
        return dict(out)
    finally:
        conn.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Cache (per-dag-filer)
# ─────────────────────────────────────────────────────────────────────────────
def cache_path(backend, ticker, d):
    return backend / CACHE_DIRNAME / f"{ticker}_{d}_{d}_1min.csv"


def find_cache_file(backend, ticker, d):
    cdir = backend / CACHE_DIRNAME
    if not cdir.exists():
        return None
    for p in cdir.glob(f"{ticker}_*_1min.csv"):
        stem = p.name[:-len("_1min.csv")]
        parts = stem.rsplit("_", 2)
        if len(parts) == 3:
            try:
                s, e = date_cls.fromisoformat(parts[1]), date_cls.fromisoformat(parts[2])
            except ValueError:
                continue
            if s <= d <= e and p.stat().st_size > 40:
                return p
    return None


def load_day_bars(backend, ticker, d):
    p = find_cache_file(backend, ticker, d)
    if p is None:
        return None
    bars = []
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None and ET is not None:
                ts = ts.replace(tzinfo=ET)
            elif ET is not None:
                ts = ts.astimezone(ET)
            if ts.date() != d:
                continue
            bars.append(Bar(ts, float(row["open"]), float(row["high"]),
                            float(row["low"]), float(row["close"]), float(row["volume"])))
    bars.sort(key=lambda b: b.ts)
    return bars


# ─────────────────────────────────────────────────────────────────────────────
# IBKR-fetch
# ─────────────────────────────────────────────────────────────────────────────
async def fetch_missing(backend, missing, host, port, client_id):
    from ib_async import IB, Stock
    ib = IB()
    await ib.connectAsync(host, port, clientId=client_id, timeout=15)
    try:
        for k, (ticker, d) in enumerate(missing, 1):
            print(f"  [{k}/{len(missing)}] henter {ticker} {d} ...", flush=True)
            contract = Stock(ticker, "SMART", "USD")
            try:
                await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10)
            except Exception:
                contract = Stock(ticker, "SMART", "USD", primaryExchange="NASDAQ")
                try:
                    await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10)
                except Exception:
                    continue
            end_dt = datetime(d.year, d.month, d.day, 16, 0)
            if ET is not None:
                end_dt = end_dt.replace(tzinfo=ET)
            try:
                raw = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_dt, durationStr="1 D",
                    barSizeSetting="1 min", whatToShow="TRADES", useRTH=True, formatDate=2),
                    timeout=20)
            except Exception as e:
                if "different IP" in str(e) or "session is connected" in str(e):
                    print("AFBRUDT: TWS-session fra anden IP. Kør i sikkert vindue.")
                    break
                continue
            p = cache_path(backend, ticker, d)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                for b in raw:
                    ts = b.date
                    if isinstance(ts, datetime) and ET is not None:
                        ts = ts.astimezone(ET) if ts.tzinfo else ts.replace(tzinfo=ET)
                    w.writerow([ts.isoformat(), b.open, b.high, b.low, b.close, b.volume or 0])
            await asyncio.sleep(1.2)
    finally:
        ib.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Washout-reclaim scan + portefølje (deployerbar form) — som portfolio-sim
# ─────────────────────────────────────────────────────────────────────────────
def scan_trade(bars, lookback, min_runup, washout, target):
    n = len(bars)
    if n < lookback + 3:
        return None
    for i in range(lookback, n - 2):
        window = bars[i - lookback + 1: i + 1]
        ref_high = max(b.high for b in window)
        ref_low = min(b.low for b in window)
        if ref_low <= 0:
            continue
        if (ref_high - ref_low) / ref_low * 100.0 < min_runup:
            continue
        if bars[i].low > ref_high * (1 - washout / 100.0):
            continue
        washout_low = min(b.low for b in window)
        entry_idx = None
        for j in range(i + 1, n - 1):
            if bars[j].close > bars[j - 1].close:
                entry_idx = j
                break
        if entry_idx is None:
            return None
        entry_open = bars[entry_idx].open
        stop_lvl = washout_low
        tgt_lvl = entry_open * (1 + target / 100.0)
        for b in bars[entry_idx + 1:]:
            if b.low <= stop_lvl:
                exit_base, reason, exit_ts = stop_lvl, "stop", b.ts
                break
            if b.high >= tgt_lvl:
                exit_base, reason, exit_ts = tgt_lvl, "target", b.ts
                break
        else:
            exit_base, reason, exit_ts = bars[-1].close, "eod", bars[-1].ts
        return {"entry_ts": bars[entry_idx].ts, "exit_ts": exit_ts, "entry_open": entry_open,
                "exit_base": exit_base, "reason": reason,
                "wo_depth": (ref_high - washout_low) / ref_high * 100.0}
    return None


def trade_pnl(t, slip):
    entry_fill = t["entry_open"] + slip
    if entry_fill <= 0:
        return 0.0
    return ((t["exit_base"] - slip) - entry_fill) / entry_fill * 100.0


def simulate_day(day_trades, max_concurrent, open_until):
    cands = [t for t in day_trades
             if open_until is None or t["entry_ts"].timetz().replace(tzinfo=None) <= open_until]
    by_ts = defaultdict(list)
    for t in cands:
        by_ts[t["entry_ts"]].append(t)
    open_exits, taken = [], []
    for ts in sorted(by_ts):
        open_exits = [x for x in open_exits if x > ts]
        free = max_concurrent - len(open_exits)
        if free <= 0:
            continue
        for t in sorted(by_ts[ts], key=lambda x: -x["wo_depth"])[:free]:
            taken.append(t)
            open_exits.append(t["exit_ts"])
    return taken


def correlation(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Juni-korrelation: washout-reclaim vs K2 dag-for-dag")
    ap.add_argument("--backend-dir", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--max-concurrent", type=int, default=3)
    ap.add_argument("--open-until", default="10:30")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=35)
    ap.add_argument("--lookback", type=int, default=DEFAULTS["lookback"])
    ap.add_argument("--min-runup", type=float, default=DEFAULTS["min_runup"])
    ap.add_argument("--washout", type=float, default=DEFAULTS["washout"])
    ap.add_argument("--target", type=float, default=DEFAULTS["target"])
    args = ap.parse_args()

    backend = find_backend_dir(args.backend_dir)
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    db_path = resolve_db(backend, args.source)
    hh, mm = (int(x) for x in args.open_until.split(":"))
    open_until = dtime(hh, mm)
    params = dict(lookback=args.lookback, min_runup=args.min_runup,
                  washout=args.washout, target=args.target)
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    emit("=" * 78)
    emit("  JUNI-KORRELATION — WASHOUT-RECLAIM vs K2 (dag for dag)")
    emit("=" * 78)
    emit(f"Backend: {backend}")
    emit(f"K2-DB:   {db_path}")
    emit(f"Fra dato: {args.since}   max samtidige: {args.max_concurrent}   "
         f"åbning indtil {args.open_until} ET")
    emit("")

    universe = load_k2_universe(db_path, args.since)
    k2_pnl = load_k2_daily_pnl(db_path, args.since)
    if not universe:
        emit("Ingen K2 universe_selected-events fra perioden. Tjek --source/--since.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1
    emit(f"K2-univers-dage: {sorted(universe)}")
    emit(f"K2 P&L-dage: {sorted(k2_pnl)}")
    emit("")

    # fetch manglende
    missing = []
    for d_str, tickers in universe.items():
        d = date_cls.fromisoformat(d_str)
        for t in tickers:
            if load_day_bars(backend, t, d) is None:
                missing.append((t, d))
    missing = sorted(set(missing))
    if missing and not args.no_fetch:
        emit(f"Henter {len(missing)} manglende ticker-dage (juni-bars) fra IBKR ...")
        asyncio.run(fetch_missing(backend, missing, args.host, args.port, args.client_id))
        emit("")
    elif missing:
        emit(f"--no-fetch: {len(missing)} ticker-dage mangler i cache (springes over).")
        emit("")

    # kør washout-reclaim pr. dag
    rows = []
    for d_str in sorted(universe):
        d = date_cls.fromisoformat(d_str)
        day_trades = []
        for t in universe[d_str]:
            bars = load_day_bars(backend, t, d)
            if not bars:
                continue
            tr = scan_trade(bars, **params)
            if tr:
                day_trades.append(tr)
        taken = simulate_day(day_trades, args.max_concurrent, open_until)
        pnl1 = [trade_pnl(t, 0.01) for t in taken]
        pnl2 = [trade_pnl(t, 0.02) for t in taken]
        rows.append({
            "date": d_str, "k2": k2_pnl.get(d_str),
            "n": len(taken),
            "wr_sum1": sum(pnl1), "wr_avg1": (sum(pnl1) / len(pnl1)) if pnl1 else None,
            "wr_sum2": sum(pnl2), "wr_avg2": (sum(pnl2) / len(pnl2)) if pnl2 else None,
        })

    # tabel
    emit("─" * 78)
    emit("  DAG FOR DAG  (washout = deployerbar max-3, åbningsvindue)")
    emit("─" * 78)
    emit(f"  {'dato':>12}{'K2 $':>10}{'WR n':>6}{'WR snit%@1¢':>13}{'WR sum%@1¢':>12}"
         f"{'WR snit%@2¢':>13}")
    for r in rows:
        k2s = f"{r['k2']:+.1f}" if r["k2"] is not None else "  —  "
        a1 = f"{r['wr_avg1']:+.3f}" if r["wr_avg1"] is not None else "  —  "
        a2 = f"{r['wr_avg2']:+.3f}" if r["wr_avg2"] is not None else "  —  "
        s1 = f"{r['wr_sum1']:+.1f}" if r["n"] else "  —  "
        emit(f"  {r['date']:>12}{k2s:>10}{r['n']:>6}{a1:>13}{s1:>12}{a2:>13}")
    emit("")

    # korrelation + sign-agreement (kernen)
    paired = [(r["k2"], r["wr_sum1"]) for r in rows if r["k2"] is not None and r["n"]]
    if paired:
        c = correlation([p[0] for p in paired], [p[1] for p in paired])
        k2_loss = [(k, w) for k, w in paired if k < 0]
        wr_also_loss = sum(1 for k, w in k2_loss if w < 0)
        emit("─" * 78)
        emit("  KERNESPØRGSMÅL — komplement eller korreleret?")
        emit("─" * 78)
        emit(f"  Korrelation (K2 $ vs washout sum%@1¢): "
             f"{c:+.2f}" if c is not None else "  Korrelation: — (for få dage)")
        emit(f"  På K2's TABSDAGE tabte washout-reclaim OGSÅ: {wr_also_loss}/{len(k2_loss)}")
        emit("")
        if k2_loss:
            if wr_also_loss >= max(1, len(k2_loss) * 0.6):
                emit("  → KORRELERET: washout tjener IKKE når K2 taber → ikke et hedge.")
            else:
                emit("  → KOMPLEMENTÆR: washout holder/tjener på K2's tabsdage → værd at se på.")
        emit("  (Lille stikprøve — signal, ikke endeligt bevis.)")
    else:
        emit("  Ingen overlappende dage med både K2-P&L og washout-handler.")
    emit("")

    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    emit("→ Send mig summary.txt.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

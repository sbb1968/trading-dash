#!/usr/bin/env python3
"""
futures_characterize.py
═══════════════════════
Karakterisér intradag-struktur i MES/MNQ/MGC (samme metode som FX-analysen) og
test komplementaritet mod K2: falder index-futures på K2's tabsdage?

To spørgsmål:
  1. STRUKTUR: efficiency ratio (ER) + continuation rate pr. ET-time. FX-majorer
     viste ER 0.12–0.17 / continuation 45–53% (møntkast). Har futures mere?
  2. KOMPLEMENTARITET: daglig RTH-afkast pr. kontrakt vs K2's daglige P&L. Et
     ægte gap-ned-hedge falder når K2 falder (4. juni-typen).

Henter 5-min RTH-bars for de bekræftede front-måneder (fra probe-outputtet),
cacher i futures_cache/. Read-only på trading_dash.db for K2's P&L.

Python 3.14: event-loop-fix øverst. ib_async. Egen client-id (27) så backenden
ikke forstyrres. KØR I SIKKERT VINDUE (efter K2-session / weekend).

Brug (fra backend-mappen):
    python futures_characterize.py
    python futures_characterize.py --no-fetch      # kun cache + analyse
    python futures_characterize.py --source iben_workstation

Output i ./futures_char_output/:  summary.txt, hourly_structure.csv, daily_vs_k2.csv

Placering: C:\\projects\\trading_dash\\backend\\futures_characterize.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date as date_cls, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    UTC = ZoneInfo("UTC")
except Exception:
    ET = UTC = None

CACHE_DIRNAME = "futures_cache"
OUTPUT_DIRNAME = "futures_char_output"
SRC_K2 = "Konfluens 2"

# Bekræftede front-måneder fra probe_futures_depth.py-outputtet (juni 2026).
# Opdatér ved rollover. (symbol, exchange, expiry YYYYMMDD, localSymbol, label)
CONTRACTS = [
    ("MES", "CME",   "20260618", "MESM6", "S&P500 micro"),
    ("MNQ", "CME",   "20260618", "MNQM6", "Nasdaq micro"),
    ("MGC", "COMEX", "20260626", "MGCM6", "guld micro"),
]


@dataclass
class Bar:
    ts: datetime  # ET tz-aware
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
        tmp = Path(tempfile.mkdtemp(prefix="fut_"))
        base = tmp / "trading_dash.db"
        shutil.copy2(path, base)
        for ext in ("-wal", "-shm"):
            sib = Path(str(path) + ext)
            if sib.exists():
                shutil.copy2(sib, Path(str(base) + ext))
        conn = sqlite3.connect(str(base), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn, tmp


def load_k2_daily_pnl(db_path):
    """Returnér {date_iso: sum_pnl} for K2's lukkede handler."""
    if not db_path.exists():
        return {}
    conn, tmp = open_ro(db_path)
    try:
        r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'").fetchone()
        if not r:
            return {}
        rows = conn.execute(
            "SELECT entry_time_et, pnl FROM trades WHERE source=? AND exit_time_utc IS NOT NULL",
            (SRC_K2,)).fetchall()
        out = defaultdict(float)
        for row in rows:
            et = row["entry_time_et"]
            if et and len(et) >= 10:
                try:
                    out[et[:10]] += float(row["pnl"])
                except (TypeError, ValueError):
                    pass
        return dict(out)
    finally:
        conn.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────
def cache_path(backend, local_symbol):
    return backend / CACHE_DIRNAME / f"{local_symbol}_5min_rth.csv"


def load_cache(p):
    if not p.exists():
        return None
    bars = []
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None and ET is not None:
                ts = ts.replace(tzinfo=ET)
            elif ET is not None:
                ts = ts.astimezone(ET)
            bars.append(Bar(ts, float(row["open"]), float(row["high"]),
                            float(row["low"]), float(row["close"]), float(row["volume"])))
    bars.sort(key=lambda b: b.ts)
    return bars


def save_cache(p, bars):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.ts.isoformat(), b.open, b.high, b.low, b.close, b.volume])


# ─────────────────────────────────────────────────────────────────────────────
# IBKR-hentning (mirror af probe-mønsteret; chunk bagud)
# ─────────────────────────────────────────────────────────────────────────────
async def qualify_future(ib, symbol, exchange, expiry, local_symbol):
    from ib_async import Future
    # 1) localSymbol + exchange (mest entydigt)
    c = Future(localSymbol=local_symbol, exchange=exchange, currency="USD")
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=10)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        if q and getattr(q[0], "conId", 0):
            return q[0]
    except Exception:
        pass
    # 2) symbol + expiry + exchange
    c = Future(symbol=symbol, lastTradeDateOrContractMonth=expiry, exchange=exchange, currency="USD")
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=10)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        if q and getattr(q[0], "conId", 0):
            return q[0]
    except Exception:
        pass
    return None


async def fetch_5m_rth(ib, contract, max_chunks=5):
    """Hent 5-min RTH-bars i 20-dages chunks bagud."""
    all_bars = {}
    end = ""  # nu
    for _ in range(max_chunks):
        try:
            raw = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                contract, endDateTime=end, durationStr="20 D",
                barSizeSetting="5 mins", whatToShow="TRADES", useRTH=True, formatDate=2),
                timeout=30)
        except Exception:
            break
        if not raw:
            break
        for b in raw:
            ts = b.date
            if isinstance(ts, datetime):
                if ts.tzinfo is None and ET is not None:
                    ts = ts.replace(tzinfo=ET)
                elif ET is not None:
                    ts = ts.astimezone(ET)
            all_bars[ts] = Bar(ts, float(b.open), float(b.high), float(b.low),
                               float(b.close), float(b.volume or 0))
        end = raw[0].date  # step bagud
        await asyncio.sleep(1.2)
    return sorted(all_bars.values(), key=lambda b: b.ts)


async def fetch_all(backend, host, port, client_id):
    from ib_async import IB
    ib = IB()
    await ib.connectAsync(host, port, clientId=client_id, timeout=15)
    out = {}
    try:
        for symbol, exch, expiry, local, label in CONTRACTS:
            print(f"  qualify {local} ({label}) ...", flush=True)
            c = await qualify_future(ib, symbol, exch, expiry, local)
            if c is None:
                print(f"    KUNNE IKKE qualify {local} — tjek exchange/expiry mod probe_futures_depth.py")
                continue
            bars = await fetch_5m_rth(ib, c)
            print(f"    {local}: {len(bars)} 5-min RTH-bars")
            out[local] = bars
            save_cache(cache_path(backend, local), bars)
    finally:
        ib.disconnect()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Analyse (TESTBAR)
# ─────────────────────────────────────────────────────────────────────────────
def efficiency_ratio(closes):
    """Kaufman ER over en sekvens af closes. 0=chop, 1=ren trend."""
    if len(closes) < 2:
        return None
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return (net / path) if path > 0 else None


def hourly_structure(bars):
    """Pr. ET-time: snit-ER (over timens bars hver dag) + continuation rate (pooled)."""
    by_day_hour = defaultdict(list)   # (date,hour) -> [closes]
    for b in bars:
        by_day_hour[(b.ts.date(), b.ts.hour)].append(b.close)
    er_by_hour = defaultdict(list)
    for (d, h), closes in by_day_hour.items():
        er = efficiency_ratio(closes)
        if er is not None and len(closes) >= 3:
            er_by_hour[h].append(er)
    # continuation: pooled consecutive 5-min returns pr. time
    cont_by_hour = defaultdict(lambda: [0, 0])  # hour -> [agree, total]
    by_day = defaultdict(list)
    for b in bars:
        by_day[b.ts.date()].append(b)
    for d, dbars in by_day.items():
        dbars.sort(key=lambda x: x.ts)
        for i in range(2, len(dbars)):
            r0 = dbars[i - 1].close - dbars[i - 2].close
            r1 = dbars[i].close - dbars[i - 1].close
            if r0 == 0 or r1 == 0:
                continue
            h = dbars[i].ts.hour
            cont_by_hour[h][1] += 1
            if (r0 > 0) == (r1 > 0):
                cont_by_hour[h][0] += 1
    rows = []
    for h in sorted(set(list(er_by_hour) + list(cont_by_hour))):
        ers = er_by_hour.get(h, [])
        agree, total = cont_by_hour.get(h, [0, 0])
        rows.append({
            "hour_et": h,
            "avg_er": round(sum(ers) / len(ers), 3) if ers else None,
            "n_days": len(ers),
            "continuation_pct": round(100 * agree / total, 1) if total else None,
            "n_pairs": total,
        })
    return rows


def daily_rth_return(bars):
    """{date_iso: pct return} fra dagens første open til sidste close (RTH)."""
    by_day = defaultdict(list)
    for b in bars:
        by_day[b.ts.date()].append(b)
    out = {}
    for d, dbars in by_day.items():
        dbars.sort(key=lambda x: x.ts)
        if dbars and dbars[0].open > 0:
            out[d.isoformat()] = (dbars[-1].close - dbars[0].open) / dbars[0].open * 100.0
    return out


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
    ap = argparse.ArgumentParser(description="Futures intradag-struktur + K2-korrelation")
    ap.add_argument("--backend-dir", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=27)
    args = ap.parse_args()

    backend = find_backend_dir(args.backend_dir)
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    emit("=" * 78)
    emit("  FUTURES INTRADAG-STRUKTUR + K2-KOMPLEMENTARITET")
    emit("=" * 78)
    emit(f"Backend: {backend}")

    # bars: cache først
    bars_by = {}
    need = []
    for symbol, exch, expiry, local, label in CONTRACTS:
        c = load_cache(cache_path(backend, local))
        if c:
            bars_by[local] = c
        else:
            need.append(local)
    emit(f"Cachet: {[k for k in bars_by]}   mangler: {need}")
    if need and not args.no_fetch:
        emit("Henter manglende fra IBKR ...")
        fetched = asyncio.run(fetch_all(backend, args.host, args.port, args.client_id))
        bars_by.update(fetched)
    emit("")

    if not bars_by:
        emit("Ingen futures-bars (hverken cache eller fetch). Kør uden --no-fetch i sikkert vindue.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # ── 1. struktur pr. time ───────────────────────────────────────────────
    emit("─" * 78)
    emit("  1. INTRADAG-STRUKTUR (FX-baseline: ER 0.12–0.17, continuation 45–53%)")
    emit("─" * 78)
    hourly_rows_all = []
    for local in bars_by:
        rows = hourly_structure(bars_by[local])
        emit(f"  {local}:")
        emit(f"     {'ET-time':>8}{'snit-ER':>9}{'dage':>6}{'continuation%':>15}{'par':>7}")
        for r in rows:
            er = f"{r['avg_er']:.3f}" if r["avg_er"] is not None else "  —  "
            ct = f"{r['continuation_pct']:.1f}" if r["continuation_pct"] is not None else "  —  "
            emit(f"     {r['hour_et']:>8}{er:>9}{r['n_days']:>6}{ct:>15}{r['n_pairs']:>7}")
            r["contract"] = local
            hourly_rows_all.append(r)
        # samlet ER på tværs af dagen
        all_er = [r["avg_er"] for r in rows if r["avg_er"] is not None]
        if all_er:
            emit(f"     → samlet snit-ER {sum(all_er)/len(all_er):.3f}")
        emit("")

    # ── 2. komplementaritet mod K2 ─────────────────────────────────────────
    emit("─" * 78)
    emit("  2. KOMPLEMENTARITET — daglig RTH-afkast vs K2's P&L")
    emit("─" * 78)
    db_path = resolve_db(backend, args.source)
    k2 = load_k2_daily_pnl(db_path)
    emit(f"  K2 P&L-kilde: {db_path.name if db_path.exists() else '(ingen)'}   "
         f"K2-dage: {len(k2)}")
    daily_by = {local: daily_rth_return(bars_by[local]) for local in bars_by}

    overlap_days = sorted(set(k2) & set().union(*[set(daily_by[l]) for l in daily_by]) if daily_by else set())
    if overlap_days:
        header = "     " + f"{'dato':>12}{'K2 $':>10}" + "".join(f"{l:>10}" for l in bars_by)
        emit(header)
        for d in overlap_days:
            row = f"     {d:>12}{k2.get(d, 0):>10.1f}"
            for l in bars_by:
                v = daily_by[l].get(d)
                row += (f"{v:>+10.2f}" if v is not None else f"{'—':>10}")
            emit(row)
        emit("")
        # korrelation K2-dag-P&L vs hver kontrakts dagsafkast (lille stikprøve!)
        emit("  Korrelation (K2 daglig $ vs futures daglig %) — LILLE stikprøve:")
        for l in bars_by:
            xs = [k2[d] for d in overlap_days if daily_by[l].get(d) is not None]
            ys = [daily_by[l][d] for d in overlap_days if daily_by[l].get(d) is not None]
            c = correlation(xs, ys)
            # sign-agreement: falder futures når K2 falder?
            both = [(k2[d], daily_by[l][d]) for d in overlap_days if daily_by[l].get(d) is not None]
            k2neg = [(p, f) for p, f in both if p < 0]
            agree = sum(1 for p, f in k2neg if f < 0)
            corr_str = f"{c:+.2f}" if c is not None else "—"
            emit(f"     {l}: korr={corr_str}   "
                 f"(på K2-tabsdage faldt {l} {agree}/{len(k2neg)} gange)")
    else:
        emit("  Ingen overlappende dage mellem K2's P&L og futures-bars endnu.")
        emit("  (Futures-historikken og K2's live-dage skal overlappe — udvid evt. hentningen.)")
    emit("")

    # ── filer ──────────────────────────────────────────────────────────────
    with (out_dir / "hourly_structure.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["contract", "hour_et", "avg_er", "n_days", "continuation_pct", "n_pairs"])
        for r in hourly_rows_all:
            w.writerow([r["contract"], r["hour_et"], r["avg_er"], r["n_days"],
                        r["continuation_pct"], r["n_pairs"]])
    with (out_dir / "daily_vs_k2.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "k2_pnl"] + list(bars_by.keys()))
        alld = sorted(set(k2) | set().union(*[set(daily_by[l]) for l in daily_by]) if daily_by else set(k2))
        for d in alld:
            w.writerow([d, round(k2.get(d, 0), 2)] +
                       [round(daily_by[l][d], 3) if daily_by[l].get(d) is not None else "" for l in bars_by])

    emit("─" * 78)
    emit("  TOLKNING")
    emit("─" * 78)
    emit("  STRUKTUR: ER markant over ~0.17 i bestemte timer = trend-vindue FX manglede.")
    emit("  KOMPLEMENT: negativ korr + futures falder på K2-tabsdage = ægte gap-ned-hedge.")
    emit("  (Begge på lille stikprøve — signal, ikke bevis.)")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Filer: {out_dir} (summary.txt, hourly_structure.csv, daily_vs_k2.csv)")
    emit("→ Send mig summary.txt + de to CSV'er.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
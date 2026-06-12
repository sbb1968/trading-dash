#!/usr/bin/env python3
"""
washout_portfolio_sim.py
════════════════════════
Handelbarheds-gaten for washout-reclaim. Per-handel-backtesten summerer ALLE
signaler — men i åbningen fyrer 60–90 samtidigt, og du kan kun holde 3. Det her
simulerer den ægte begrænsning: max N samtidige positioner, en prioriteringsregel
når flere signaler rammer samme minut, og REALISERET afkast ved 0/1/2¢.

Ikke-look-ahead: signaler behandles i tidsorden. Et signal du ikke kunne tage
(slots fyldt) er tabt — det genovervejes IKKE når en slot senere frigives.
Prioritet bruges kun til at vælge mellem signaler i SAMME minut.

Viser hver periode i to vinduer: hele dagen OG kun åbningen (--open-until,
default 10:30 ET), fordi per-handel-analysen viste ~95% af edgen ligger der.

Rent offline — genbruger bar_cache (parse-once). Kun stdlib.

Brug (fra backend-mappen):
    python washout_portfolio_sim.py
    python washout_portfolio_sim.py --max-concurrent 3 --priority washout
    python washout_portfolio_sim.py --open-until 10:30
    python washout_portfolio_sim.py --universe-file historical_universe_...json

Output i ./washout_portfolio_output/: summary.txt

Placering: C:\\projects\\trading_dash\\backend\\washout_portfolio_sim.py
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
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date as date_cls, time as dtime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

CACHE_DIRNAME = "bar_cache"
OUTPUT_DIRNAME = "washout_portfolio_output"
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
# Sti / cache (parse-once)
# ─────────────────────────────────────────────────────────────────────────────
def find_backend_dir(explicit):
    cands = [Path(explicit)] if explicit else []
    cands += [Path.cwd(), Path(__file__).resolve().parent, Path.cwd() / "backend"]
    for c in cands:
        if c.exists() and ((c / CACHE_DIRNAME).exists() or any(c.glob("historical_universe_*.json"))):
            return c.resolve()
    return Path.cwd().resolve()


def _parse_cache_range(name):
    if not name.endswith("_1min.csv"):
        return None
    stem = name[:-len("_1min.csv")]
    parts = stem.rsplit("_", 2)
    if len(parts) != 3:
        return None
    t, s, e = parts
    try:
        return t, date_cls.fromisoformat(s), date_cls.fromisoformat(e)
    except ValueError:
        return None


_TICKER_BARS = {}
_TICKER_HAS_FILE = {}


def preload_ticker(backend, ticker):
    if ticker in _TICKER_HAS_FILE:
        return
    cdir = backend / CACHE_DIRNAME
    by_date = {}
    found = False
    if cdir.exists():
        for p in cdir.glob(f"{ticker}_*_1min.csv"):
            if p.stat().st_size <= 40:
                continue
            found = True
            with p.open(newline="") as f:
                for row in csv.DictReader(f):
                    ts = datetime.fromisoformat(row["timestamp"])
                    if ts.tzinfo is None and ET is not None:
                        ts = ts.replace(tzinfo=ET)
                    elif ET is not None:
                        ts = ts.astimezone(ET)
                    by_date.setdefault(ts.date().isoformat(), {})[ts] = Bar(
                        ts, float(row["open"]), float(row["high"]), float(row["low"]),
                        float(row["close"]), float(row["volume"]))
    _TICKER_HAS_FILE[ticker] = found
    _TICKER_BARS[ticker] = {d: [b for _, b in sorted(m.items())] for d, m in by_date.items()}


def get_day_bars(backend, ticker, d):
    preload_ticker(backend, ticker)
    if not _TICKER_HAS_FILE.get(ticker):
        return None
    return _TICKER_BARS[ticker].get(d.isoformat(), [])


def cache_covers(backend, ticker, d):
    """Ren fil-tjek: findes en ikke-tom cache-fil hvis interval dækker d?
    Bruges til at finde manglende FØR fetch — uden at poisone parse-once-cachen."""
    cdir = backend / CACHE_DIRNAME
    if not cdir.exists():
        return False
    for p in cdir.glob(f"{ticker}_*_1min.csv"):
        if p.stat().st_size <= 40:
            continue
        pr = _parse_cache_range(p.name)
        if pr and pr[1] <= d <= pr[2]:
            return True
    return False


async def fetch_missing(backend, missing, host, port, client_id):
    """Hent manglende (ticker, dag) 1-min RTH-bars → per-dag CSV (afprøvet mønster)."""
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
            p = backend / CACHE_DIRNAME / f"{ticker}_{d}_{d}_1min.csv"
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



def _first_of_month(d):
    return d.replace(day=1)


def discover_cache_universes(backend):
    cdir = backend / CACHE_DIRNAME
    if not cdir.exists():
        return []
    ranges = {}
    for p in cdir.glob("*_1min.csv"):
        if p.stat().st_size <= 40:
            continue
        pr = _parse_cache_range(p.name)
        if pr and (pr[2] - pr[1]).days >= 5:
            ranges.setdefault((pr[1], pr[2]), set()).add(pr[0])
    jobs = []
    for (start, end), tickers in sorted(ranges.items()):
        ps = max(start, _first_of_month(end))
        data = {}
        d = ps
        while d <= end:
            if d.weekday() < 5:
                data[d.isoformat()] = sorted(tickers)
            d += timedelta(days=1)
        jobs.append((f"cache {ps}_{end} ({len(tickers)} tickere)", data))
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Scanner → fuldt trade-objekt med tidsstempler (slippage-uafhængigt)
# ─────────────────────────────────────────────────────────────────────────────
def scan_trade(bars, lookback, min_runup, washout, target):
    """Én washout-reclaim long pr. dag. Returnér trade-dict m. tidsstempler/niveauer eller None."""
    n = len(bars)
    if n < lookback + 3:
        return None
    for i in range(lookback, n - 2):
        window = bars[i - lookback + 1: i + 1]
        ref_high = max(b.high for b in window)
        ref_low = min(b.low for b in window)
        if ref_low <= 0:
            continue
        runup = (ref_high - ref_low) / ref_low * 100.0
        if runup < min_runup:
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
                exit_ts, exit_base, reason = b.ts, stop_lvl, "stop"
                break
            if b.high >= tgt_lvl:
                exit_ts, exit_base, reason = b.ts, tgt_lvl, "target"
                break
        else:
            exit_ts, exit_base, reason = bars[-1].ts, bars[-1].close, "eod"
        return {
            "entry_ts": bars[entry_idx].ts, "exit_ts": exit_ts,
            "entry_open": entry_open, "exit_base": exit_base, "reason": reason,
            "runup": runup, "wo_depth": (ref_high - washout_low) / ref_high * 100.0,
        }
    return None


def trade_pnl(t, slip):
    """Realiseret long-afkast i % ved given slippage (¢ omsat til pris)."""
    entry_fill = t["entry_open"] + slip
    if entry_fill <= 0:
        return 0.0
    exit_fill = t["exit_base"] - slip
    return (exit_fill - entry_fill) / entry_fill * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Portefølje-simulation pr. dag (max N samtidige, prioritet ved samtidighed)
# ─────────────────────────────────────────────────────────────────────────────
def simulate_day(day_trades, max_concurrent, priority_key, open_until):
    cands = [t for t in day_trades
             if open_until is None or t["entry_ts"].timetz().replace(tzinfo=None) <= open_until]
    by_ts = defaultdict(list)
    for t in cands:
        by_ts[t["entry_ts"]].append(t)
    open_exits = []  # exit_ts for åbne positioner
    taken = []
    for ts in sorted(by_ts):
        open_exits = [x for x in open_exits if x > ts]   # frigiv slots
        free = max_concurrent - len(open_exits)
        if free <= 0:
            continue
        group = sorted(by_ts[ts], key=lambda t: -t[priority_key])
        for t in group[:free]:
            taken.append(t)
            open_exits.append(t["exit_ts"])
    return taken, len(cands)


def aggregate(taken, slip):
    pnls = [trade_pnl(t, slip) for t in taken]
    n = len(pnls)
    if n == 0:
        return dict(n=0, wr=0, avg=0, sum=0, pf=0, worst=0)
    wins = [p for p in pnls if p > 0]
    gl = -sum(p for p in pnls if p < 0)
    gw = sum(wins)
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)
    return dict(n=n, wr=100 * len(wins) / n, avg=sum(pnls) / n, sum=sum(pnls),
                pf=pf, worst=min(pnls))


def fmt_pf(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# Kør én periode
# ─────────────────────────────────────────────────────────────────────────────
def scan_period(backend, data, params):
    """Returnér {date_iso: [trades]} for alle (ticker, dag) i universet."""
    by_day = defaultdict(list)
    for d_str, tickers in data.items():
        try:
            d = date_cls.fromisoformat(d_str)
        except ValueError:
            continue
        for t in tickers:
            bars = get_day_bars(backend, t, d)
            if not bars:
                continue
            tr = scan_trade(bars, params["lookback"], params["min_runup"],
                            params["washout"], params["target"])
            if tr:
                tr["ticker"] = t
                by_day[d_str].append(tr)
    return by_day


def main():
    ap = argparse.ArgumentParser(description="Washout-reclaim portefølje-handelbarheds-sim")
    ap.add_argument("--backend-dir", default=None)
    ap.add_argument("--universe-file", action="append", default=[])
    ap.add_argument("--max-concurrent", type=int, default=3)
    ap.add_argument("--priority", choices=["washout", "runup", "time"], default="washout",
                    help="valg blandt samtidige signaler (washout-dybde / runup / først)")
    ap.add_argument("--open-until", default="10:30", help="åbningsvindue-grænse ET (HH:MM)")
    ap.add_argument("--fetch", action="store_true",
                    help="hent manglende bars fra IBKR før analyse (kør i sikkert vindue)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=36)
    ap.add_argument("--lookback", type=int, default=DEFAULTS["lookback"])
    ap.add_argument("--min-runup", type=float, default=DEFAULTS["min_runup"])
    ap.add_argument("--washout", type=float, default=DEFAULTS["washout"])
    ap.add_argument("--target", type=float, default=DEFAULTS["target"])
    args = ap.parse_args()

    backend = find_backend_dir(args.backend_dir)
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    params = dict(lookback=args.lookback, min_runup=args.min_runup,
                  washout=args.washout, target=args.target)
    pk = {"washout": "wo_depth", "runup": "runup", "time": "entry_ts"}[args.priority]
    hh, mm = (int(x) for x in args.open_until.split(":"))
    open_until = dtime(hh, mm)

    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    emit("=" * 78)
    emit("  WASHOUT-RECLAIM — PORTEFØLJE-HANDELBARHEDS-SIM")
    emit("=" * 78)
    emit(f"Backend: {backend}")
    emit(f"Max samtidige: {args.max_concurrent}   prioritet: {args.priority}   "
         f"åbningsvindue: indtil {args.open_until} ET")
    emit(f"Parametre: lookback={params['lookback']}m  min_runup={params['min_runup']}%  "
         f"washout={params['washout']}%  target={params['target']}%")
    emit("")

    # jobs
    jobs = []
    if args.universe_file:
        for uf in args.universe_file:
            up = (backend / uf) if not Path(uf).is_absolute() else Path(uf)
            if up.exists():
                jobs.append((up.name, json.loads(up.read_text(encoding="utf-8"))))
    else:
        jobs = discover_cache_universes(backend)
    if not jobs:
        emit("Ingen perioder. Brug --universe-file eller sørg for bar_cache.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # hent manglende bars FØR analyse (ren fil-tjek — poisoner ikke parse-once-cachen)
    if args.fetch:
        missing = set()
        for _, data in jobs:
            for d_str, tickers in data.items():
                try:
                    d = date_cls.fromisoformat(d_str)
                except ValueError:
                    continue
                for t in tickers:
                    if not cache_covers(backend, t, d):
                        missing.add((t, d))
        missing = sorted(missing)
        if missing:
            emit(f"Henter {len(missing)} manglende ticker-dage fra IBKR "
                 f"(client-id {args.client_id}) ...")
            asyncio.run(fetch_missing(backend, missing, args.host, args.port, args.client_id))
            emit("")
        else:
            emit("--fetch: intet manglede i cache.")
            emit("")

    for label, data in jobs:
        by_day = scan_period(backend, data, params)
        all_trades = [t for day in by_day.values() for t in day]
        emit("─" * 78)
        emit(f"  PERIODE: {label}")
        emit("─" * 78)
        emit(f"  Rå signaler i alt: {len(all_trades)}")

        for win_label, ou in (("HELE DAGEN", None), (f"ÅBNING (≤{args.open_until})", open_until)):
            # uafhængigt af slippage: hvilke trades tages?
            taken_all, cand_total = [], 0
            for d_str, day in by_day.items():
                taken, cands = simulate_day(day, args.max_concurrent, pk, ou)
                taken_all += taken
                cand_total += cands
            util = 100 * len(taken_all) / cand_total if cand_total else 0
            emit("")
            emit(f"  [{win_label}]  kandidater={cand_total}  taget={len(taken_all)} "
                 f"(udnyttelse {util:.0f}% — resten afvist pga. fyldte slots)")
            emit(f"     {'slippage':>9}{'n':>5}{'WR%':>7}{'snit%':>8}{'sum%':>9}{'PF':>7}{'værst%':>9}")
            for cents in (0.0, 1.0, 2.0):
                a = aggregate(taken_all, cents / 100.0)
                emit(f"     {cents:>8.0f}¢{a['n']:>5}{a['wr']:>7.0f}{a['avg']:>8.3f}"
                     f"{a['sum']:>9.1f}{fmt_pf(a['pf']):>7}{a['worst']:>9.1f}")
            # reference: alle signaler ukapaciteret ved 1¢ (det per-handel-backtesten viste)
            unconstrained = [t for t in all_trades
                             if ou is None or t["entry_ts"].timetz().replace(tzinfo=None) <= ou]
            ua = aggregate(unconstrained, 0.01)
            emit(f"     (ukapaciteret @1¢: n={ua['n']} sum={ua['sum']:+.1f}% — "
                 f"forskel viser hvad kapacitetsloftet koster)")
        emit("")

    emit("─" * 78)
    emit("  TOLKNING")
    emit("─" * 78)
    emit("  Handelbar HVIS realiseret (max-N) snit%/handel forbliver positiv ved 2¢")
    emit("  — helst i åbningsvinduet. Hvis kun det ukapaciterede tal er positivt,")
    emit("  er edgen en backtest-artefakt der ikke kan deployes med 3 slots.")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    emit("→ Send mig summary.txt.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
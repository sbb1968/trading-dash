#!/usr/bin/env python3
"""
washout_reclaim_backtest.py
═══════════════════════════
Selvstændig backtest af en LONG washout-reclaim mean-reversion-strategi der
finder sine EGNE entries (ikke parasitisk på K2's signaler). Komplement til K2:
K2 køber impuls-toppen; denne køber pullback-bunden efter impulsen og rider
rebounden.

Entry-logik (per ticker per dag, én handel pr. dag):
  1. Rullende vindue (lookback min): kræv en ægte impuls — vinduets
     (high-low)/low ≥ min_runup%  (vi handler kun navne der HAR spiket).
  2. Washout: en bar's low ≤ vinduets ref-high × (1 − washout%).
  3. Reclaim: første efterfølgende bar med close > forrige close → køb @ næste open.
  4. Stop: under washout-low (struktur). Target: +target% (cappet) eller EOD.

OOS-design: kør mod april- OG maj-universet. Maj = udvikling, april =
out-of-sample-bekræftelse (eller omvendt). Edgen skal holde på BEGGE.

Genbruger K2-backtestens bar_cache ({TICKER}_{start}_{end}_1min.csv) — kør på
workstationen hvor cachen allerede findes, så ingen ny hentning. Manglende
ticker-dage rapporteres; med --fetch hentes de fra IBKR (kør i sikkert vindue).

Kun stdlib (+ ib_async kun hvis --fetch). Python 3.14: event-loop-fix øverst.

Brug (fra backend-mappen, workstation):
    python washout_reclaim_backtest.py \
        --universe-file historical_universe_2026-05-01_2026-05-29.json \
        --universe-file historical_universe_2026-04-01_2026-04-30.json
    python washout_reclaim_backtest.py --universe-file <fil> --sweep
    python washout_reclaim_backtest.py --universe-file <fil> --fetch   # hent manglende

Output i ./washout_reclaim_output/:
    trades_<universefil>.csv   — alle fundne handler
    summary.txt                — per-periode aggregat + slippage + per-dag

Placering: C:\\Projects\\trading_dash\\backend\\washout_reclaim_backtest.py
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
import sys
from dataclasses import dataclass
from datetime import datetime, date as date_cls, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

CACHE_DIRNAME = "bar_cache"
OUTPUT_DIRNAME = "washout_reclaim_output"


@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


# ── Default parametre (den robuste form fra fase 3) ───────────────────────────
DEFAULTS = dict(lookback=20, min_runup=3.0, washout=1.5, target=2.0, stop_pct=None)
# stop_pct None = strukturstop (under washout-low)


# ─────────────────────────────────────────────────────────────────────────────
# Sti / cache
# ─────────────────────────────────────────────────────────────────────────────
def find_backend_dir(explicit):
    cands = [Path(explicit)] if explicit else []
    cands += [Path.cwd(), Path(__file__).resolve().parent, Path.cwd() / "backend"]
    for c in cands:
        if c.exists() and ((c / CACHE_DIRNAME).exists() or any(c.glob("historical_universe_*.json"))):
            return c.resolve()
    return Path.cwd().resolve()


def _parse_cache_range(name: str):
    """{TICKER}_{start}_{end}_1min.csv → (ticker, start_date, end_date) | None."""
    if not name.endswith("_1min.csv"):
        return None
    stem = name[:-len("_1min.csv")]
    parts = stem.rsplit("_", 2)
    if len(parts) != 3:
        return None
    ticker, s, e = parts
    try:
        return ticker, date_cls.fromisoformat(s), date_cls.fromisoformat(e)
    except ValueError:
        return None


def find_cache_file(backend: Path, ticker: str, d: date_cls):
    """Find en cache-fil hvis interval dækker d (genbruger K2-backtestens period-filer
    OG per-dag-filer fra forward-path-scriptet)."""
    cdir = backend / CACHE_DIRNAME
    if not cdir.exists():
        return None
    for p in cdir.glob(f"{ticker}_*_1min.csv"):
        pr = _parse_cache_range(p.name)
        if pr and pr[1] <= d <= pr[2]:
            return p
    return None


def load_day_bars(backend: Path, ticker: str, d: date_cls):
    """Returnér RTH-bars for ticker på dag d fra cache (filtreret), eller None."""
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


# ── Parse-once in-memory cache: hver cache-fil læses og parses NØJAGTIG én gang,
#    uanset hvor mange dage/slippage-niveauer/sweep-configs der slår op i den.
#    Det er den store optimering — uden den genparses hver tickers 2-måneders-fil
#    én gang PR. dag PR. config (milliarder af rækker i sweep).
_TICKER_BARS: dict[str, dict] = {}     # ticker -> {date_iso: [Bar sorteret]}
_TICKER_HAS_FILE: dict[str, bool] = {}  # ticker -> fandtes en (ikke-tom) cache-fil?


def preload_ticker(backend: Path, ticker: str) -> None:
    if ticker in _TICKER_HAS_FILE:
        return
    cdir = backend / CACHE_DIRNAME
    by_date: dict[str, dict] = {}
    found = False
    if cdir.exists():
        for p in cdir.glob(f"{ticker}_*_1min.csv"):
            if p.stat().st_size <= 40:   # tom 38-byte-fil
                continue
            found = True
            with p.open(newline="") as f:
                for row in csv.DictReader(f):
                    ts = datetime.fromisoformat(row["timestamp"])
                    if ts.tzinfo is None and ET is not None:
                        ts = ts.replace(tzinfo=ET)
                    elif ET is not None:
                        ts = ts.astimezone(ET)
                    bucket = by_date.setdefault(ts.date().isoformat(), {})
                    bucket[ts] = Bar(ts, float(row["open"]), float(row["high"]),
                                     float(row["low"]), float(row["close"]), float(row["volume"]))
    _TICKER_HAS_FILE[ticker] = found
    _TICKER_BARS[ticker] = {d: [b for _, b in sorted(m.items())] for d, m in by_date.items()}


def get_day_bars(backend: Path, ticker: str, d: date_cls):
    """Som load_day_bars, men parser hver fil kun én gang (RAM-cache).
    None = ingen cache-fil (manglende). [] = fil findes, men ingen bars den dag."""
    preload_ticker(backend, ticker)
    if not _TICKER_HAS_FILE.get(ticker):
        return None
    return _TICKER_BARS[ticker].get(d.isoformat(), [])


# ─────────────────────────────────────────────────────────────────────────────
# Scanner + simulator (TESTBAR)
# ─────────────────────────────────────────────────────────────────────────────
def scan_and_sim(bars, lookback, min_runup, washout, target, stop_pct, slip):
    """Find ÉN washout-reclaim long pr. dag og simulér den. Returnér dict|None."""
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
        wo_lvl = ref_high * (1 - washout / 100.0)
        if bars[i].low > wo_lvl:
            continue
        washout_low = min(b.low for b in window)
        # reclaim
        entry_idx = None
        for j in range(i + 1, n - 1):
            if bars[j].close > bars[j - 1].close:
                entry_idx = j
                break
        if entry_idx is None:
            return None
        p_in = bars[entry_idx].open
        fill_in = p_in + slip
        if fill_in <= 0:
            return None
        stop_lvl = washout_low if stop_pct is None else p_in * (1 - stop_pct / 100.0)
        tgt_lvl = p_in * (1 + target / 100.0) if target is not None else None
        exit_reason = "eod"
        pnl = None
        for b in bars[entry_idx + 1:]:
            if b.low <= stop_lvl:
                pnl = ((stop_lvl - slip) - fill_in) / fill_in * 100.0
                exit_reason = "stop"
                break
            if tgt_lvl is not None and b.high >= tgt_lvl:
                pnl = ((tgt_lvl - slip) - fill_in) / fill_in * 100.0
                exit_reason = "target"
                break
        if pnl is None:
            pnl = ((bars[-1].close - slip) - fill_in) / fill_in * 100.0
        return {
            "date": bars[0].ts.date().isoformat(),
            "entry_time": bars[entry_idx].ts.strftime("%H:%M"),
            "entry_price": round(p_in, 4), "ref_high": round(ref_high, 4),
            "washout_low": round(washout_low, 4), "runup_pct": round(runup, 2),
            "exit_reason": exit_reason, "pnl_pct": round(pnl, 3),
        }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Aggregering
# ─────────────────────────────────────────────────────────────────────────────
def aggregate(pnls):
    n = len(pnls)
    if n == 0:
        return {"n": 0, "wr": 0, "avg": 0, "sum": 0, "pf": 0, "worst": 0, "best": 0}
    wins = [p for p in pnls if p > 0]
    gw = sum(wins)
    gl = -sum(p for p in pnls if p < 0)
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)
    return {"n": n, "wr": 100 * len(wins) / n, "avg": sum(pnls) / n,
            "sum": sum(pnls), "pf": pf, "worst": min(pnls), "best": max(pnls)}


def fmt_pf(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# IBKR-fetch (kun ved --fetch, samme mønster som forward-path)
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


# ─────────────────────────────────────────────────────────────────────────────
# Kør én universe-fil
# ─────────────────────────────────────────────────────────────────────────────
def _first_of_month(d: date_cls) -> date_cls:
    return d.replace(day=1)


def discover_cache_universes(backend: Path):
    """Byg universer DIREKTE fra bar_cache (ingen JSON nødvendig).

    Grupperer cache-filer efter deres (start,end)-interval = én 'periode'. For
    hver periode scannes alle dens tickere på hver handelsdag i [periode-start,
    end], hvor periode-start = første dag i end-datoens måned (udelukker warmup).
    Selv-filtrering sker via min_runup i scanneren.

    Returnerer liste af (label, {date_str: [tickers]}).
    """
    cdir = backend / CACHE_DIRNAME
    if not cdir.exists():
        return []
    ranges: dict[tuple, set] = {}
    for p in cdir.glob("*_1min.csv"):
        if p.stat().st_size <= 40:   # spring tomme 38-byte-filer over
            continue
        pr = _parse_cache_range(p.name)
        if pr and (pr[2] - pr[1]).days >= 5:   # kun ægte periode-filer (ikke per-dag)
            ranges.setdefault((pr[1], pr[2]), set()).add(pr[0])
    jobs = []
    for (start, end), tickers in sorted(ranges.items()):
        period_start = max(start, _first_of_month(end))
        data: dict[str, list] = {}
        d = period_start
        while d <= end:
            if d.weekday() < 5:  # man-fre
                data[d.isoformat()] = sorted(tickers)
            d += timedelta(days=1)
        label = f"cache {period_start}_{end} ({len(tickers)} tickere)"
        jobs.append((label, data))
    return jobs


def run_universe(backend, data, params, slip_cents, emit):
    trades, missing, scanned = [], [], 0
    slip = slip_cents / 100.0
    for d_str, tickers in sorted(data.items()):
        try:
            d = date_cls.fromisoformat(d_str)
        except ValueError:
            continue
        for t in tickers:
            bars = get_day_bars(backend, t, d)
            if bars is None:
                missing.append((t, d))
                continue
            scanned += 1
            tr = scan_and_sim(bars, params["lookback"], params["min_runup"],
                              params["washout"], params["target"], params["stop_pct"], slip)
            if tr:
                tr["ticker"] = t
                trades.append(tr)
    return trades, missing, scanned


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Washout-reclaim long backtest (OOS + slippage)")
    ap.add_argument("--backend-dir", default=None)
    ap.add_argument("--universe-file", action="append", default=[],
                    help="kan angives flere gange (fx maj OG april til OOS)")
    ap.add_argument("--scan-cache", action="store_true",
                    help="byg universet direkte fra bar_cache (ingen JSON nødvendig)")
    ap.add_argument("--lookback", type=int, default=DEFAULTS["lookback"])
    ap.add_argument("--min-runup", type=float, default=DEFAULTS["min_runup"])
    ap.add_argument("--washout", type=float, default=DEFAULTS["washout"])
    ap.add_argument("--target", type=float, default=DEFAULTS["target"])
    ap.add_argument("--stop-pct", type=float, default=None, help="None=strukturstop")
    ap.add_argument("--sweep", action="store_true", help="sweep washout×target×lookback")
    ap.add_argument("--fetch", action="store_true", help="hent manglende ticker-dage fra IBKR")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=34)
    args = ap.parse_args()

    backend = find_backend_dir(args.backend_dir)
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    emit("=" * 78)
    emit("  WASHOUT-RECLAIM LONG — SELVSTÆNDIG BACKTEST")
    emit("=" * 78)
    emit(f"Backend: {backend}")
    base = dict(lookback=args.lookback, min_runup=args.min_runup,
                washout=args.washout, target=args.target, stop_pct=args.stop_pct)
    emit(f"Parametre: lookback={base['lookback']}m  min_runup={base['min_runup']}%  "
         f"washout={base['washout']}%  target="
         f"{'EOD' if base['target'] is None else str(base['target'])+'%'}  "
         f"stop={'struktur' if base['stop_pct'] is None else str(base['stop_pct'])+'%'}")

    # ── byg jobs: (label, slug, data-dict) fra cache eller JSON-filer ──────
    jobs = []  # (label, slug, data)
    if args.scan_cache:
        emit("Kilde: bar_cache (scan-cache mode — ingen universe-JSON)")
        for label, data in discover_cache_universes(backend):
            slug = label.split()[1] if len(label.split()) > 1 else "cache"
            jobs.append((label, slug, data))
    else:
        emit(f"Kilde: universe-filer {args.universe_file}")
        for uf in args.universe_file:
            up = (backend / uf) if not Path(uf).is_absolute() else Path(uf)
            if not up.exists():
                emit(f"  MANGLER: {up}")
                continue
            jobs.append((up.name, up.stem, json.loads(up.read_text(encoding="utf-8"))))
    emit("")
    if not jobs:
        emit("Ingen perioder at køre. Brug --scan-cache, eller angiv --universe-file <fil>.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # ── valgfri fetch af manglende ticker-dage (kun ved JSON-input) ───────
    if args.fetch and not args.scan_cache:
        all_missing = []
        for _, _, data in jobs:
            for d_str, tickers in data.items():
                try:
                    d = date_cls.fromisoformat(d_str)
                except ValueError:
                    continue
                for t in tickers:
                    if get_day_bars(backend, t, d) is None:
                        all_missing.append((t, d))
        all_missing = sorted(set(all_missing))
        if all_missing:
            emit(f"Henter {len(all_missing)} manglende ticker-dage fra IBKR ...")
            asyncio.run(fetch_missing(backend, all_missing, args.host, args.port, args.client_id))
            emit("")

    # ── per periode ───────────────────────────────────────────────────────
    for label, slug, data in jobs:
        emit("─" * 78)
        emit(f"  PERIODE: {label}")
        emit("─" * 78)

        if args.sweep:
            emit(f"  {'lookbk':>7}{'wash%':>7}{'target':>8}{'n':>5}{'WR%':>7}"
                 f"{'snit%':>8}{'sum%':>9}{'PF':>7}{'værst%':>9}{'bedst%':>9}")
            for lb in (15, 20, 30):
                for wo in (1.5, 2.5, 4.0):
                    for tg in (2.0, 3.0, None):
                        p = dict(base, lookback=lb, washout=wo, target=tg)
                        trs, _, _ = run_universe(backend, data, p, 1.0, emit)
                        a = aggregate([t["pnl_pct"] for t in trs])
                        tgl = "EOD" if tg is None else f"{tg}"
                        emit(f"  {lb:>7}{wo:>7}{tgl:>8}{a['n']:>5}{a['wr']:>7.0f}"
                             f"{a['avg']:>8.2f}{a['sum']:>9.1f}{fmt_pf(a['pf']):>7}"
                             f"{a['worst']:>9.1f}{a['best']:>9.1f}")
            emit("")
            continue

        # enkelt-config: slippage-følsomhed + per-dag
        emit("  Slippage-følsomhed:")
        emit(f"     {'slippage':>10}{'n':>5}{'WR%':>7}{'snit%':>8}{'sum%':>9}{'PF':>7}{'værst%':>9}")
        trades = None
        miss_n = scan_n = 0
        for cents in (0.0, 1.0, 2.0):
            trs, missing, scanned = run_universe(backend, data, base, cents, emit)
            if cents == 1.0:
                trades = trs
                miss_n, scan_n = len(missing), scanned
            a = aggregate([t["pnl_pct"] for t in trs])
            emit(f"     {cents:>9.0f}¢{a['n']:>5}{a['wr']:>7.0f}{a['avg']:>8.2f}"
                 f"{a['sum']:>9.1f}{fmt_pf(a['pf']):>7}{a['worst']:>9.1f}")
        emit("")
        emit(f"  Scannede ticker-dage: {scan_n}   manglende i cache: {miss_n}"
             + ("  (kør med --fetch i sikkert vindue)" if miss_n else ""))
        emit("")

        # per-dag (ved 1¢)
        by_day = {}
        for t in (trades or []):
            by_day.setdefault(t["date"], []).append(t["pnl_pct"])
        emit("  Per-dag (snit-% / antal / sum-%, 1¢):")
        for d in sorted(by_day):
            v = by_day[d]
            emit(f"     {d}  snit={sum(v)/len(v):+.2f}%  n={len(v):<3} sum={sum(v):+.1f}%")
        emit("")

        # skriv handler
        if trades:
            cols = ["ticker", "date", "entry_time", "entry_price", "ref_high",
                    "washout_low", "runup_pct", "exit_reason", "pnl_pct"]
            cf = out_dir / f"trades_{slug}.csv"
            with cf.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for t in trades:
                    w.writerow({k: t.get(k) for k in cols})

    emit("─" * 78)
    emit("  TOLKNING")
    emit("─" * 78)
    emit("  Edgen er ægte hvis den holder på BEGGE perioder (OOS) ved ≥1¢ med PF>1")
    emit("  og uden at hænge på 1-2 hale-handler (tjek bedst% vs snit%).")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Filer: {out_dir}  (summary.txt + trades_*.csv)")
    emit("→ Send mig summary.txt + trades_*.csv.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
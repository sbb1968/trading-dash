#!/usr/bin/env python3
"""
orb_revalidate.py — Trin 3-revalidering af orb_classic på REPRÆSENTATIVT data
═════════════════════════════════════════════════════════════════════════════
Backtester den reverterede simple ORB (orb_classic) POINT-IN-TIME på K2's
historiske top-gainer-univers — altså kun på de dage hver aktie FAKTISK var en
gainer — i stedet for på vilkårlige dage i et statisk meme-datasæt. Det er
forskellen mellem at måle en edge og at måle støj.

Reglen reproduceres 1:1 efter strategies/momentum_orb (verificeret mod koden):
  • ORB-vindue 09:30–09:44 ET (5-min bars: 09:30, 09:35, 09:40) → orb_high/orb_low
  • avg_vol = gennemsnit af ALLE dagens 09:30–16:00 5-min bars' volumen
    (= build_day_context: total_vol / len(market))
  • ENTRY (direkte breakout, require_retest=False) i 09:45–10:30:
        close > orb_high  OG  bar.volume ≥ 1.5 × avg_vol  OG  RSI(14) < 80
        (RSI via Wilder, akkumuleret fra 09:30; <15 closes ⇒ 50.0 ⇒ filter passerer)
  • EXIT (hvad end først): stop −2% · target +4% · force-close 10:30 ET
  • long-only · én handel pr. ticker pr. dag · kapital $2.500/handel

DISCIPLIN (det Trin 2 IKKE kunne, fordi meme-data var fladt):
  • SLIPPAGE-sensitivitet 0/1/2/3 ¢/aktie (entry+exit) — small-cap breakouts har
    reel slippage; backtest_momentum.py kører friktionsfrit.
  • OOS: hver univers-fil rapporteres separat (udvikl på én måned, bekræft på den
    anden) + samlet.
  • REGIME-split: efficiency-ratio pr. ticker-dag. NB: for en BREAKOUT-strategi er
    HØJ ER (trend/follow-through) forventet GUNSTIG — modsat mean-reversion.

PARITETS-TJEK (--parity): kører orb_classic på de gamle meme-5min-CSV'er og
sammenligner aggregeret (antal handler, P&L, PF) mod backtest_momentum.py's
orb_classic-output (306 handler, +$233,81, PF 1,08). Matcher det, er
reimplementeringen tro mod den rigtige kode → vi kan stole på frisk-data-tallene.

Rent offline. Kun stdlib (+ zoneinfo). Ingen IBKR.

Brug (på Sørens workstation, fra backend/):
    # Paritets-tjek først (validér at scriptet matcher den rigtige kode):
    python orb_revalidate.py --parity --data-dir data

    # Revalidering på frisk, repræsentativt data:
    python orb_revalidate.py \
        --universe historical_universe_2026-04-01_2026-04-30.json \
                   historical_universe_2026-05-01_2026-05-29.json \
        --bar-cache bar_cache

Placering: C:\\Projects\\trading_dash\\backend\\orb_revalidate.py
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from statistics import pstdev

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = None

# ── Regel-konstanter (matcher orb_classic-varianten) ──────────
ORB_START      = dtime(9, 30)
ORB_END        = dtime(9, 44)     # orb_end_minutes=14 → inkl. 09:30/35/40-bars
TRADE_START    = dtime(9, 45)
FORCE_CLOSE    = dtime(10, 30)    # entry_end_time = trade_end_time = 10:30
MARKET_OPEN    = dtime(9, 30)
MARKET_CLOSE   = dtime(16, 0)
VOL_MULT       = 1.5
RSI_MAX        = 80.0
RSI_PERIOD     = 14
STOP_PCT       = 0.02
TARGET_PCT     = 0.04
CAPITAL        = 2500.0
SLIPPAGE_CENTS = [0.0, 1.0, 2.0, 3.0]   # ¢/aktie, entry+exit


@dataclass
class Bar:
    ts: datetime          # ET-aware (eller normaliseret til ET)
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def t(self) -> dtime:
        return self.ts.time()

    @property
    def day(self):
        return self.ts.date()


@dataclass
class Trade:
    ticker: str
    day: object
    entry_ts: datetime
    exit_ts: datetime
    entry: float
    exit: float
    shares: int
    reason: str

    def pnl(self, slip_cents=0.0):
        slip = slip_cents / 100.0
        return (self.exit - self.entry - 2 * slip) * self.shares


# ──────────────────────────────────────────────────────────────
# Tidszone-normalisering: naive ⇒ UTC ⇒ ET; aware ⇒ ET.
# (bar_cache er ET-aware ISO; Yahoo-meme-CSV er typisk naive UTC.)
# ──────────────────────────────────────────────────────────────
def to_et(dt: datetime) -> datetime:
    if ET is None:
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def parse_ts(raw: str) -> datetime | None:
    raw = raw.strip()
    try:
        return to_et(datetime.fromisoformat(raw))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return to_et(datetime.strptime(raw, fmt))
            except ValueError:
                continue
    return None


def _read_ohlcv_csv(path: Path, ts_key_candidates=("timestamp", "date", "datetime", "")) -> list[Bar]:
    """Læs en OHLCV-CSV. Første kolonne = tidsstempel hvis ingen 'timestamp'-header."""
    bars: list[Bar] = []
    with path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return bars
        lower = [h.strip().lower() for h in header]
        def idx(*names):
            for n in names:
                if n in lower:
                    return lower.index(n)
            return None
        i_ts = idx("timestamp", "date", "datetime")
        if i_ts is None:
            i_ts = 0  # første kolonne
        i_o, i_h, i_l, i_c, i_v = (idx("open"), idx("high"), idx("low"),
                                    idx("close"), idx("volume"))
        if None in (i_o, i_h, i_l, i_c, i_v):
            return bars
        for row in reader:
            if not row or len(row) <= max(i_ts, i_o, i_h, i_l, i_c, i_v):
                continue
            ts = parse_ts(row[i_ts])
            if ts is None:
                continue
            try:
                bars.append(Bar(ts, float(row[i_o]), float(row[i_h]),
                                float(row[i_l]), float(row[i_c]), float(row[i_v])))
            except (ValueError, TypeError):
                continue
    return bars


def load_1min_merged(ticker: str, cache_dir: Path) -> list[Bar]:
    """Saml alle {TICKER}_*_1min.csv (begge perioder), dedup på timestamp."""
    files = sorted(glob.glob(str(cache_dir / f"{ticker}_*_1min.csv")))
    seen = {}
    for fp in files:
        p = Path(fp)
        if p.stat().st_size <= 64:   # 38-byte-faldgruben: tom = fejlet hentning
            continue
        for b in _read_ohlcv_csv(p):
            seen[b.ts] = b
    return sorted(seen.values(), key=lambda b: b.ts)


def aggregate_5min(bars_1min: list[Bar]) -> list[Bar]:
    """1-min → 5-min, clock-alignet (09:30, 09:35, ...). Kun RTH 09:30–16:00."""
    buckets: dict[tuple, list[Bar]] = defaultdict(list)
    for b in bars_1min:
        if not (MARKET_OPEN <= b.t < MARKET_CLOSE):
            continue
        bmin = (b.ts.minute // 5) * 5
        key = (b.ts.date(), b.ts.hour, bmin)
        buckets[key].append(b)
    out = []
    for (d, hh, mm), bs in buckets.items():
        bs.sort(key=lambda x: x.ts)
        start = bs[0].ts.replace(minute=mm, second=0, microsecond=0)
        out.append(Bar(start, bs[0].o, max(x.h for x in bs), min(x.l for x in bs),
                       bs[-1].c, sum(x.v for x in bs)))
    return sorted(out, key=lambda b: b.ts)


# ──────────────────────────────────────────────────────────────
# RSI (Wilder) — matcher calc_rsi_from_closes 1:1
# ──────────────────────────────────────────────────────────────
def rsi(closes: list[float], period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = sum(d for d in deltas[-period:] if d > 0) / period
    losses = sum(-d for d in deltas[-period:] if d < 0) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))


# ──────────────────────────────────────────────────────────────
# orb_classic på én ticker-dag (5-min bars for dagen)
# ──────────────────────────────────────────────────────────────
def backtest_ticker_day(ticker, day, day_bars: list[Bar]) -> Trade | None:
    market = [b for b in day_bars if MARKET_OPEN <= b.t <= MARKET_CLOSE]
    if len(market) < 8:
        return None
    orb_bars = [b for b in market if ORB_START <= b.t <= ORB_END]
    if not orb_bars:
        return None
    orb_high = max(b.h for b in orb_bars)
    orb_low  = min(b.l for b in orb_bars)
    total_vol = sum(b.v for b in market)
    if total_vol <= 0:
        return None
    avg_vol = total_vol / len(market)

    closes: list[float] = []
    pos = None  # (entry_price, entry_ts)
    for b in market:
        closes.append(b.c)   # RSI akkumulerer fra 09:30 (prior_closes=[])
        # ── styr åben position (exit-tjek på denne bar) ──
        if pos is not None:
            entry, ets = pos
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            if b.t >= FORCE_CLOSE:
                return _mk(ticker, day, ets, b.ts, entry, b.c, "force_close")
            # konservativt: stop tjekkes før target hvis begge i samme bar
            if b.l <= stop:
                return _mk(ticker, day, ets, b.ts, entry, stop, "stop")
            if b.h >= target:
                return _mk(ticker, day, ets, b.ts, entry, target, "target")
            continue
        # ── entry (kun flad, i entry-vinduet 09:45 ≤ t < 10:30) ──
        if TRADE_START <= b.t < FORCE_CLOSE:
            if b.c > orb_high and b.v >= VOL_MULT * avg_vol and rsi(closes) < RSI_MAX:
                pos = (b.c, b.ts)   # direkte breakout: entry = bar close
    # stadig åben ved sidste bar (skulle være fanget af force_close, men backstop):
    if pos is not None:
        entry, ets = pos
        last = market[-1]
        return _mk(ticker, day, ets, last.ts, entry, last.c, "eod")
    return None


def _mk(ticker, day, ets, xts, entry, exit_, reason) -> Trade:
    shares = int(CAPITAL / entry) if entry > 0 else 0
    return Trade(ticker, day, ets, xts, entry, exit_, shares, reason)


# ──────────────────────────────────────────────────────────────
# Statistik + slippage
# ──────────────────────────────────────────────────────────────
def stats(trades: list[Trade], slip=0.0) -> dict:
    pnls = [t.pnl(slip) for t in trades]
    n = len(pnls)
    if n == 0:
        return dict(n=0, wr=0, total=0, pf=0, avg=0)
    wins = [p for p in pnls if p > 0]
    gl = -sum(p for p in pnls if p < 0)
    gw = sum(wins)
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)
    return dict(n=n, wr=100 * len(wins) / n, total=sum(pnls), pf=pf, avg=sum(pnls) / n)


def fmt_pf(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def efficiency_ratio(day_bars: list[Bar]) -> float | None:
    """ER over morgenvinduet 09:30–10:30 (det ORB faktisk arbejder i)."""
    bs = sorted([b for b in day_bars if ORB_START <= b.t <= FORCE_CLOSE], key=lambda x: x.ts)
    if len(bs) < 3:
        return None
    closes = [b.c for b in bs]
    denom = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if denom <= 0:
        return None
    return abs(closes[-1] - closes[0]) / denom


# ──────────────────────────────────────────────────────────────
# Kørsel: point-in-time på univers-fil(er)
# ──────────────────────────────────────────────────────────────
def run_universe(universe_files, cache_dir: Path, emit):
    # Saml per-periode (OOS) og samlet
    period_trades: dict[str, list[Trade]] = {}
    all_trades: list[Trade] = []
    er_by_keyday: dict[tuple, float] = {}     # (ticker,day) -> ER for regime
    bars_cache: dict[str, list[Bar]] = {}     # ticker -> 5-min bars (merged)
    missing = set()

    def get_5min(ticker):
        if ticker not in bars_cache:
            one = load_1min_merged(ticker, cache_dir)
            if not one:
                missing.add(ticker)
                bars_cache[ticker] = []
            else:
                bars_cache[ticker] = aggregate_5min(one)
        return bars_cache[ticker]

    for uf in universe_files:
        label = Path(uf).stem.replace("historical_universe_", "")
        days = json.loads(Path(uf).read_text())
        ptrades: list[Trade] = []
        for day_str, tickers in sorted(days.items()):
            try:
                day = datetime.fromisoformat(day_str).date()
            except ValueError:
                day = datetime.strptime(day_str[:10], "%Y-%m-%d").date()
            for ticker in tickers:
                bars5 = get_5min(ticker)
                day_bars = [b for b in bars5 if b.day == day]
                if not day_bars:
                    continue
                er = efficiency_ratio(day_bars)
                if er is not None:
                    er_by_keyday[(ticker, day)] = er
                tr = backtest_ticker_day(ticker, day, day_bars)
                if tr is not None:
                    ptrades.append(tr)
        period_trades[label] = ptrades
        all_trades.extend(ptrades)

    # ── Output ──
    emit("=" * 80)
    emit("  ORB CLASSIC — TRIN 3 REVALIDERING (point-in-time, K2-univers)")
    emit("=" * 80)
    emit(f"Regel: direkte breakout · ORB 09:30–09:44 · vol≥{VOL_MULT}×snit · RSI<{RSI_MAX:.0f} · "
         f"stop −{STOP_PCT*100:.0f}% · target +{TARGET_PCT*100:.0f}% · exit {FORCE_CLOSE.strftime('%H:%M')} ET")
    emit(f"Kapital ${CAPITAL:.0f}/handel · long-only · slippage vist 0/1/2/3 ¢/aktie")
    if missing:
        emit(f"⚠ Manglende/tomme bar_cache for {len(missing)}: {', '.join(sorted(missing)[:12])}"
             + (" …" if len(missing) > 12 else ""))
    emit("")

    # OOS: per periode
    for label, trs in period_trades.items():
        emit("─" * 80)
        emit(f"  PERIODE {label}  ({len(trs)} handler)")
        emit("─" * 80)
        rmix = defaultdict(int)
        for t in trs:
            rmix[t.reason] += 1
        emit("     exit-mix: " + "  ".join(f"{k}={v}" for k, v in sorted(rmix.items(), key=lambda x: -x[1])))
        emit(f"     {'slip':>6}{'n':>6}{'WR%':>7}{'P&L$':>11}{'PF':>7}{'snit$':>9}")
        for s in SLIPPAGE_CENTS:
            st = stats(trs, s)
            emit(f"     {s:>4.0f}¢{st['n']:>6}{st['wr']:>7.0f}{st['total']:>11,.0f}"
                 f"{fmt_pf(st['pf']):>7}{st['avg']:>9.2f}")
        emit("")

    # Samlet + slippage
    emit("─" * 80)
    emit(f"  SAMLET ({len(all_trades)} handler, alle perioder)")
    emit("─" * 80)
    emit(f"     {'slip':>6}{'n':>6}{'WR%':>7}{'P&L$':>11}{'PF':>7}{'snit$':>9}")
    for s in SLIPPAGE_CENTS:
        st = stats(all_trades, s)
        emit(f"     {s:>4.0f}¢{st['n']:>6}{st['wr']:>7.0f}{st['total']:>11,.0f}"
             f"{fmt_pf(st['pf']):>7}{st['avg']:>9.2f}")
    emit("")

    # Regime-split (ER-terciler), aflæst ved 2¢
    vals = sorted(er_by_keyday.values())
    if len(vals) >= 3:
        lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]
        buckets = defaultdict(list)
        for t in all_trades:
            er = er_by_keyday.get((t.ticker, t.day))
            if er is None:
                continue
            lbl = "lav" if er <= lo else ("høj" if er >= hi else "mid")
            buckets[lbl].append(t)
        emit("─" * 80)
        emit(f"  REGIME (ER-tercil, morgenvindue, @2¢) — grænser {lo:.3f} / {hi:.3f}")
        emit("  NB: for en BREAKOUT-strategi er HØJ ER (trend/follow-through) forventet GUNSTIG.")
        emit("─" * 80)
        for lbl in ("lav", "mid", "høj"):
            st = stats(buckets.get(lbl, []), 2.0)
            emit(f"        {lbl:>3}: n={st['n']:>3}  WR={st['wr']:>3.0f}%  "
                 f"P&L={st['total']:>+8,.0f}$  PF={fmt_pf(st['pf'])}")
        emit("")

    emit("─" * 80)
    emit("  DOM")
    emit("─" * 80)
    emit("  LEVEDYGTIG: PF > 1 ved ~2¢ slippage OG holder i BEGGE perioder (OOS).")
    emit("  IKKE: PF kollapser mod/under 1 ved 2¢, eller virker kun i én periode.")
    emit("  Sammenlign IKKE med 1,62 (det var friktionsfrit, ikke point-in-time, lille udsnit).")
    return all_trades


# ──────────────────────────────────────────────────────────────
# Paritets-tjek mod backtest_momentum.py (meme-5min, alle dage)
# ──────────────────────────────────────────────────────────────
def run_parity(data_dir: Path, emit):
    files = sorted(glob.glob(str(data_dir / "*_5m_*.csv")))
    trades: list[Trade] = []
    skip = {"SPY", "QQQ", "IWM", "BACKTEST"}
    tickers_used = []
    for fp in files:
        p = Path(fp)
        ticker = p.stem.split("_")[0].upper()
        if ticker in skip or ticker == "BACKTEST":
            continue
        bars = _read_ohlcv_csv(p)
        if len(bars) < 20:
            continue
        tickers_used.append(ticker)
        by_day = defaultdict(list)
        for b in bars:
            by_day[b.day].append(b)
        for day, db in by_day.items():
            tr = backtest_ticker_day(ticker, day, sorted(db, key=lambda x: x.ts))
            if tr is not None:
                trades.append(tr)

    st = stats(trades, 0.0)
    emit("=" * 80)
    emit("  PARITETS-TJEK — orb_classic på meme-5min (alle dage, friktionsfrit)")
    emit("=" * 80)
    emit(f"  Tickers: {', '.join(sorted(tickers_used))}")
    emit(f"  Reimplementering:  {st['n']} handler · WR {st['wr']:.1f}% · "
         f"P&L ${st['total']:,.2f} · PF {fmt_pf(st['pf'])}")
    emit(f"  backtest_momentum: 306 handler · WR 40,2% · P&L +$233,81 · PF 1,08  (reference)")
    emit("")
    emit("  Tæt match (handler ±~5%, P&L samme fortegn/størrelse) ⇒ reimplementeringen er")
    emit("  tro mod den rigtige kode → frisk-data-revalideringen kan stoles på.")
    emit("  Stor afvigelse ⇒ en detalje (fill-timing, tz, avg_vol) divergerer — undersøg før Trin 3.")
    return trades


def main():
    ap = argparse.ArgumentParser(description="Trin 3-revalidering af orb_classic")
    ap.add_argument("--universe", nargs="+", help="én eller flere historical_universe_*.json")
    ap.add_argument("--bar-cache", default="bar_cache")
    ap.add_argument("--parity", action="store_true", help="kør paritets-tjek mod meme-5min i stedet")
    ap.add_argument("--data-dir", default="data", help="meme-5min-mappe (til --parity)")
    args = ap.parse_args()

    out_dir = Path.cwd() / "orb_revalidate_output"
    out_dir.mkdir(exist_ok=True)
    lines = []
    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    if args.parity:
        dd = Path(args.data_dir)
        if not dd.is_absolute():
            dd = Path.cwd() / dd
        if not dd.exists():
            emit(f"Mappen {dd} findes ikke.")
            return 1
        run_parity(dd, emit)
        (out_dir / "parity.txt").write_text("\n".join(lines), encoding="utf-8")
        emit(f"\nFil: {out_dir / 'parity.txt'}")
        return 0

    if not args.universe:
        emit("Angiv --universe <fil(er)> eller --parity. Se --help.")
        return 1
    cache_dir = Path(args.bar_cache)
    if not cache_dir.is_absolute():
        cache_dir = Path.cwd() / cache_dir
    if not cache_dir.exists():
        emit(f"bar_cache-mappen {cache_dir} findes ikke.")
        return 1
    run_universe(args.universe, cache_dir, emit)
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"\nFil: {out_dir / 'summary.txt'}")
    emit("→ Send mig summary.txt (og parity.txt hvis du kørte --parity).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
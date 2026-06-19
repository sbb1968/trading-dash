#!/usr/bin/env python3
"""
meanrev_regime.py
═════════════════
Regime-split af EUREVERSION-strategien fra eureversion_backtest.py. Svarer på det
ene spørgsmål baseline efterlod: FINDES DER ET REGIME HVOR DEN BLØDER?

Baggrund: baseline viste OOS stærkere end in-sample for MES og M2K — usædvanligt,
og mest sandsynligt fordi den sidste del af perioden var et mere choppy/reverterende
regime. En mean-reversion-strategi tjener i chop og kan tabe i trend. Det her måler det.

To slags analyse — vigtig forskel:

  1. DESKRIPTIV (samme-dags ER/vol-terciler): "hvor lever edgen?" Klassificerer hver
     handelsdag efter dens EGEN realiserede efficiency-ratio (trend vs chop) og volatilitet.
     Bruger hele dagens info — det er en DIAGNOSE, ikke en handelsregel (man kender ikke
     dagens ER før sessionen er slut).

  2. IMPLEMENTERBAR (trailing-ER-filter): "kan vi handle på det?" Bruger KUN de
     foregående K dages ER — info man har FØR sessionen åbner. Hvis de choppy-regime-dage
     (lav trailing-ER) har klart bedre PF, kan et live-filter fravælge trend-regimet.

Plus: in-sample vs OOS gennemsnits-ER/vol — tester direkte om OOS-perioden var choppier
(forklaringen på asymmetrien).

Efficiency-ratio (ER) pr. session-dag = |sidste − første close| / Σ|bar-til-bar bevægelse|.
  ER → 0 = ren chop (godt for mean-reversion) · ER → 1 = ren trend (farligt).

Importerer backtest-motoren fra eureversion_backtest.py (samme mappe) — ÉN kilde til logikken,
så resultaterne er sammenlignelige. Begge filer SKAL ligge i samme mappe.

Rent offline. Kun stdlib. Ingen IBKR.

Brug:
    python meanrev_regime.py
    python meanrev_regime.py --only MES,M2K
    python meanrev_regime.py --entry-z 2.0 --exit-z 0.5 --lookback 20 --trail-days 3

Placering: C:\\Projects\\trading_dash\\backend\\meanrev_regime.py
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median, pstdev

try:
    from eureversion_backtest import (
        load_15min, run_backtest, session_of, SESSIONS,
        stats, fmt_pf, OUTPUT_DIRNAME,
    )
except ImportError:
    print("FEJL: eureversion_backtest.py skal ligge i samme mappe som dette script.")
    sys.exit(1)

DEFAULT_INSTR = ["MES", "MNQ", "M2K"]


def session_day_metrics(bars, session):
    """Pr. session-dag: (efficiency_ratio, realiseret_vol). Kun dage med ≥5 bars."""
    by_day = defaultdict(list)
    for b in bars:
        if session_of(b.ts.hour) == session:
            by_day[b.ts.date()].append(b)
    er_by_day, vol_by_day = {}, {}
    for day, bs in by_day.items():
        bs.sort(key=lambda x: x.ts)
        if len(bs) < 5:
            continue
        closes = [b.close for b in bs]
        moves = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
        denom = sum(moves)
        er_by_day[day] = abs(closes[-1] - closes[0]) / denom if denom > 0 else 0.0
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
                for i in range(1, len(closes)) if closes[i - 1] > 0]
        vol_by_day[day] = pstdev(rets) if len(rets) > 1 else 0.0
    return er_by_day, vol_by_day


def tercile_bounds(values):
    v = sorted(values)
    if len(v) < 3:
        return None, None
    return v[len(v) // 3], v[2 * len(v) // 3]


def tercile_buckets(trades, metric_by_day, cost, emit, axis):
    lo, hi = tercile_bounds(list(metric_by_day.values()))
    if lo is None:
        emit(f"     (for få dage til {axis}-terciler)")
        return
    buckets = defaultdict(list)
    for t in trades:
        m = metric_by_day.get(t.entry_ts.date())
        if m is None:
            continue
        lbl = "lav" if m <= lo else ("høj" if m >= hi else "mid")
        buckets[lbl].append(t)
    emit(f"     {axis}-tercil (grænser {lo:.3f} / {hi:.3f}):")
    for lbl in ("lav", "mid", "høj"):
        s = stats(buckets.get(lbl, []), cost)
        emit(f"        {lbl:>3}: n={s['n']:>3}  WR={s['wr']:>3.0f}%  "
             f"sum={s['sum']:>+6.1f}%  PF={fmt_pf(s['pf'])}")


def trailing_er(er_by_day, k):
    """Pr. dag: gennemsnits-ER over de K FOREGÅENDE session-dage (kun fortid)."""
    days = sorted(er_by_day)
    out = {}
    for i, d in enumerate(days):
        prior = days[max(0, i - k):i]
        if prior:
            out[d] = sum(er_by_day[p] for p in prior) / len(prior)
    return out


def trailing_filter(trades, trail_by_day, cost, emit, k):
    vals = [trail_by_day[t.entry_ts.date()] for t in trades if t.entry_ts.date() in trail_by_day]
    if len(vals) < 4:
        emit("     (for få handler til trailing-filter)")
        return
    med = median(vals)
    low, high = [], []
    for t in trades:
        tv = trail_by_day.get(t.entry_ts.date())
        if tv is None:
            continue
        (low if tv <= med else high).append(t)
    sl, sh = stats(low, cost), stats(high, cost)
    emit(f"     trailing-{k}d-ER-filter (median {med:.3f}; kun fortidsinfo → implementerbart):")
    emit(f"        LAV trailing-ER  (choppy regime → HANDL):  "
         f"n={sl['n']:>3}  WR={sl['wr']:>3.0f}%  sum={sl['sum']:>+6.1f}%  PF={fmt_pf(sl['pf'])}")
    emit(f"        HØJ trailing-ER  (trend regime → SKIP):    "
         f"n={sh['n']:>3}  WR={sh['wr']:>3.0f}%  sum={sh['sum']:>+6.1f}%  PF={fmt_pf(sh['pf'])}")


def insample_oos_regime(er_by_day, vol_by_day, frac, emit):
    days = sorted(er_by_day)
    if len(days) < 4:
        return
    cut = days[min(len(days) - 1, int(len(days) * frac))]
    ins_er = [er_by_day[d] for d in days if d <= cut]
    oos_er = [er_by_day[d] for d in days if d > cut]
    ins_vol = [vol_by_day[d] for d in days if d <= cut and d in vol_by_day]
    oos_vol = [vol_by_day[d] for d in days if d > cut and d in vol_by_day]
    if not ins_er or not oos_er:
        return
    emit(f"     in-sample vs OOS regime ({len(ins_er)} vs {len(oos_er)} dage):")
    emit(f"        gennemsnits-ER:   in-sample {sum(ins_er)/len(ins_er):.3f}   "
         f"OOS {sum(oos_er)/len(oos_er):.3f}   (lavere ER = mere chop)")
    if ins_vol and oos_vol:
        emit(f"        gennemsnits-vol:  in-sample {sum(ins_vol)/len(ins_vol):.4f}   "
             f"OOS {sum(oos_vol)/len(oos_vol):.4f}")


def main():
    ap = argparse.ArgumentParser(description="Regime-split af mean-reversion-strategien")
    ap.add_argument("--data-dir", default="data_harvest")
    ap.add_argument("--only", default=None)
    ap.add_argument("--session", default="europaeisk", choices=list(SESSIONS.keys()))
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--entry-z", type=float, default=2.0)
    ap.add_argument("--exit-z", type=float, default=0.5)
    ap.add_argument("--stop-z", type=float, default=3.5)
    ap.add_argument("--min-vol-pct", type=float, default=50.0)
    ap.add_argument("--cost-bp", type=float, default=2.0)
    ap.add_argument("--oos-split", type=float, default=0.6)
    ap.add_argument("--trail-days", type=int, default=3)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    instruments = [s.strip().upper() for s in args.only.split(",")] if args.only else DEFAULT_INSTR
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  MEAN-REVERSION — REGIME-SPLIT (trend vs chop)")
    emit("=" * 78)
    emit(f"Data: {data_dir}   session: {args.session}   instrumenter: {', '.join(instruments)}")
    emit(f"Regel: entry |z|≥{args.entry_z}  exit |z|≤{args.exit_z}  stop |z|≥{args.stop_z}  "
         f"lookback={args.lookback}   aflæst @ {args.cost_bp:.0f} bp")
    emit("ER = |net bevægelse| / Σ|bar-bevægelse| pr. session-dag.  ER→0 chop (godt), ER→1 trend (farligt).")
    emit("")

    if not data_dir.exists():
        emit(f"Mappen {data_dir} findes ikke.")
        (out_dir / "regime_summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    for label in instruments:
        p = data_dir / f"{label}_15min.csv"
        if not p.exists():
            emit(f"── {label}: ingen fil — springer over\n")
            continue
        bars = load_15min(p)
        if len(bars) < args.lookback + 50:
            emit(f"── {label}: for få bars ({len(bars)})\n")
            continue
        vols = sorted(b.volume for b in bars if b.volume > 0)
        thr = vols[min(len(vols) - 1, int(len(vols) * args.min_vol_pct / 100))] if vols else None
        if thr is not None and thr <= 0:
            thr = None

        trades = run_backtest(bars, args.session, args.lookback, args.entry_z,
                              args.exit_z, args.stop_z, thr)
        er_by_day, vol_by_day = session_day_metrics(bars, args.session)

        s_all = stats(trades, args.cost_bp)
        emit("─" * 78)
        emit(f"  {label}   (handler={s_all['n']}, samlet PF={fmt_pf(s_all['pf'])} @ {args.cost_bp:.0f} bp)")
        emit("─" * 78)
        tercile_buckets(trades, er_by_day, args.cost_bp, emit, "ER")
        tercile_buckets(trades, vol_by_day, args.cost_bp, emit, "vol")
        trail = trailing_er(er_by_day, args.trail_days)
        trailing_filter(trades, trail, args.cost_bp, emit, args.trail_days)
        insample_oos_regime(er_by_day, vol_by_day, args.oos_split, emit)
        emit("")

    emit("─" * 78)
    emit("  DOM")
    emit("─" * 78)
    emit("  ROBUST: PF > 1 i ALLE ER-terciler (også høj-ER/trend). Så behøves intet regime-filter.")
    emit("  REGIME-AFHÆNGIG: PF kollapser/negativ i høj-ER (trend). Så er trailing-ER-filteret")
    emit("    løsningen — HVIS LAV trailing-ER har klart bedre PF end HØJ (det er implementerbart live).")
    emit("  Hvis OOS-ER < in-sample-ER: bekræfter at OOS-styrken var et choppier regime, ikke held.")
    emit("")
    (out_dir / "regime_summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'regime_summary.txt'}")
    emit("→ Send mig regime_summary.txt.")
    (out_dir / "regime_summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
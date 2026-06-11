#!/usr/bin/env python3
"""
orb_regime_filter.py — kan ORB's høj-ER-edge HANDLES? (sidste test før pensionering)
═══════════════════════════════════════════════════════════════════════════════════
Trin 3 viste at orb_classic samlet er ~break-even efter omkostninger, MEN at edgen
er voldsomt regime-afhængig: høj-ER-dage (follow-through) PF 2,62, lav-ER-dage
(choppy) PF 0,04 — de udligner hinanden til ~nul.

Det her afgør om den spredning er HANDELBAR. Høj-ER PF 2,62 er kun penge værd hvis
man kan vide PÅ FORHÅND at i dag bliver en follow-through-dag. Den eneste lovlige
prædiktor er fortiden: de seneste K dages markeds-regime (trailing-ER), som er kendt
FØR dagens entry-vindue (09:45).

To spørgsmål:
  1. PRÆDIKTIVITET: forudsiger trailing-ER (sidste K dage) dagens markeds-ER?
     - Pearson-korrelation trailing-ER vs dagens ER.
     - Hit-rate: af dage med HØJ trailing-ER, hvor mange havde reelt HØJ ER? (base 50%)
  2. HANDELBARHED (det afgørende): hvis vi KUN handler orb_classic på dage med høj
     trailing-ER (forudsagt follow-through) og springer resten over — er PF > 1 ved
     2¢ slippage, OG holder det i BEGGE perioder (OOS)?

Markeds-ER for en dag = gennemsnit af morgen-ER (09:30–10:30) på tværs af dagens
univers-tickers. Det er regimet for det small-cap-momentum-univers ORB handler.
Trailing-ER[dag] = gennemsnit af markeds-ER over de K FOREGÅENDE handelsdage (kun fortid).

Rent offline. Importerer fra orb_revalidate.py (samme regel + bar-parsing → konsistens).
Begge filer + universe-JSON skal ligge i backend/.

Brug (fra backend/):
    python orb_regime_filter.py --universe reconstructed_universe_2026-04.json \
                                            reconstructed_universe_2026-05.json \
                                --bar-cache bar_cache
    python orb_regime_filter.py ... --trail-days 5

Placering: C:\\Projects\\trading_dash\\backend\\orb_regime_filter.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

try:
    from orb_revalidate import (
        load_1min_merged, aggregate_5min, backtest_ticker_day,
        efficiency_ratio, stats, fmt_pf,
    )
except ImportError:
    print("FEJL: orb_revalidate.py skal ligge i samme mappe som dette script.")
    sys.exit(1)

READ_SLIP = 2.0   # ¢/aktie — realistisk aflæsnings-omkostning


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def main():
    ap = argparse.ArgumentParser(description="Trailing-ER prædiktivitets- og handelbarhedstest for ORB")
    ap.add_argument("--universe", nargs="+", required=True)
    ap.add_argument("--bar-cache", default="bar_cache")
    ap.add_argument("--trail-days", type=int, default=3, help="trailing-vindue K (default 3)")
    args = ap.parse_args()

    cache_dir = Path(args.bar_cache)
    if not cache_dir.is_absolute():
        cache_dir = Path.cwd() / cache_dir
    if not cache_dir.exists():
        print(f"bar_cache-mappen {cache_dir} findes ikke.")
        return 1

    out_dir = Path.cwd() / "orb_revalidate_output"
    out_dir.mkdir(exist_ok=True)
    lines = []
    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    # ── Indlæs univers + bars; saml per-dag markeds-ER og orb_classic-handler ──
    bars_cache: dict[str, list] = {}
    def get_5min(ticker):
        if ticker not in bars_cache:
            one = load_1min_merged(ticker, cache_dir)
            bars_cache[ticker] = aggregate_5min(one) if one else []
        return bars_cache[ticker]

    day_ers: dict[object, list[float]] = defaultdict(list)   # dag -> [ticker-ER]
    day_trades: dict[object, list] = defaultdict(list)       # dag -> [Trade]
    day_period: dict[object, str] = {}

    for uf in args.universe:
        label = Path(uf).stem.replace("reconstructed_universe_", "").replace("historical_universe_", "")
        days = json.loads(Path(uf).read_text())
        for day_str, tickers in days.items():
            try:
                from datetime import datetime
                day = datetime.fromisoformat(day_str).date()
            except ValueError:
                from datetime import datetime
                day = datetime.strptime(day_str[:10], "%Y-%m-%d").date()
            day_period.setdefault(day, label)
            for ticker in tickers:
                day_bars = [b for b in get_5min(ticker) if b.day == day]
                if not day_bars:
                    continue
                er = efficiency_ratio(day_bars)
                if er is not None:
                    day_ers[day].append(er)
                tr = backtest_ticker_day(ticker, day, day_bars)
                if tr is not None:
                    day_trades[day].append(tr)

    # markeds-ER pr. dag = gennemsnit af ticker-ER
    market_er = {d: mean(ers) for d, ers in day_ers.items() if ers}
    all_days = sorted(market_er.keys())
    if len(all_days) < args.trail_days + 5:
        emit(f"For få dage ({len(all_days)}) til en meningsfuld test.")
        (out_dir / "regime_filter.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # trailing-ER[dag] = gennemsnit af markeds-ER de K foregående dage (kun fortid)
    K = args.trail_days
    trailing = {}
    for i, d in enumerate(all_days):
        if i < K:
            continue
        prior = [market_er[all_days[j]] for j in range(i - K, i)]
        trailing[d] = mean(prior)

    eval_days = [d for d in all_days if d in trailing]   # dage med trailing-signal

    emit("=" * 80)
    emit("  ORB — KAN HØJ-ER-EDGEN HANDLES? (trailing-ER prædiktivitet + filter)")
    emit("=" * 80)
    emit(f"Univers: {', '.join(Path(u).stem for u in args.universe)}")
    emit(f"Markeds-ER pr. dag = snit af morgen-ER over dagens univers. Trailing = sidste {K} dage.")
    emit(f"Aflæst ved {READ_SLIP:.0f}¢ slippage. {len(eval_days)} dage med trailing-signal.")
    emit("")

    # ── 1) PRÆDIKTIVITET ──
    xs = [trailing[d] for d in eval_days]
    ys = [market_er[d] for d in eval_days]
    r = pearson(xs, ys)
    med_tr = median(xs)
    med_today = median(ys)
    # hit-rate: af dage med høj trailing-ER, hvor mange havde reelt høj ER?
    hi_tr = [d for d in eval_days if trailing[d] >= med_tr]
    hit = sum(1 for d in hi_tr if market_er[d] >= med_today)
    hit_rate = 100 * hit / len(hi_tr) if hi_tr else 0
    emit("─" * 80)
    emit("  1) PRÆDIKTIVITET — forudsiger fortiden dagens regime?")
    emit("─" * 80)
    emit(f"     Pearson(trailing-ER, dagens ER) = {r:+.3f}")
    emit(f"       (≈0 = ingen forudsigelse; klart positiv = regime persisterer)")
    emit(f"     Hit-rate: af {len(hi_tr)} høj-trailing-dage havde {hit} ({hit_rate:.0f}%) reelt høj ER")
    emit(f"       (50% = ingen edge i forudsigelsen; >>50% = forudsigeligt)")
    emit("")

    # ── 2) HANDELBARHED — orb_classic filtreret på trailing-ER ──
    def trades_for(days_subset):
        out = []
        for d in days_subset:
            out.extend(day_trades.get(d, []))
        return out

    hi_days = [d for d in eval_days if trailing[d] >= med_tr]   # forudsagt follow-through → HANDL
    lo_days = [d for d in eval_days if trailing[d] <  med_tr]   # forudsagt choppy → SKIP

    base = stats(trades_for(eval_days), READ_SLIP)
    hi   = stats(trades_for(hi_days), READ_SLIP)
    lo   = stats(trades_for(lo_days), READ_SLIP)

    emit("─" * 80)
    emit(f"  2) HANDELBARHED — orb_classic @ {READ_SLIP:.0f}¢, opdelt på trailing-ER (median-split)")
    emit("─" * 80)
    emit(f"     {'gruppe':<26}{'dage':>5}{'n':>6}{'WR%':>6}{'P&L$':>10}{'PF':>7}")
    emit(f"     {'ALLE (ufiltreret)':<26}{len(eval_days):>5}{base['n']:>6}{base['wr']:>6.0f}"
         f"{base['total']:>10,.0f}{fmt_pf(base['pf']):>7}")
    emit(f"     {'HØJ trailing → HANDL':<26}{len(hi_days):>5}{hi['n']:>6}{hi['wr']:>6.0f}"
         f"{hi['total']:>10,.0f}{fmt_pf(hi['pf']):>7}")
    emit(f"     {'LAV trailing → SKIP':<26}{len(lo_days):>5}{lo['n']:>6}{lo['wr']:>6.0f}"
         f"{lo['total']:>10,.0f}{fmt_pf(lo['pf']):>7}")
    emit("")

    # ── 3) OOS: det filtrerede (HØJ trailing) pr. periode ──
    emit("─" * 80)
    emit(f"  3) OOS — det FILTREREDE (kun høj-trailing-dage) @ {READ_SLIP:.0f}¢, pr. periode")
    emit("─" * 80)
    periods = sorted(set(day_period[d] for d in eval_days))
    for per in periods:
        per_hi = [d for d in hi_days if day_period.get(d) == per]
        s = stats(trades_for(per_hi), READ_SLIP)
        emit(f"     {per:<34} dage={len(per_hi):>3}  n={s['n']:>4}  "
             f"P&L={s['total']:>+8,.0f}$  PF={fmt_pf(s['pf'])}")
    emit("")

    # ── DOM ──
    emit("─" * 80)
    emit("  DOM")
    emit("─" * 80)
    emit("  REDDER ORB hvis ALT følgende: korrelation klart positiv · hit-rate klart >50% ·")
    emit("    HØJ-trailing PF > 1 ved 2¢ · OG det filtrerede holder PF > 1 i BEGGE perioder.")
    emit("  DØD hvis: korrelation ≈0 / hit-rate ≈50% (regime er ikke forudsigeligt), ELLER")
    emit("    det filtrerede stadig fejler 2¢ / kun virker i én periode.")
    emit("  NB: median-splittet bruger hele datasættet (mildt look-ahead). Er signalet svagt,")
    emit("    tæller det IMOD; er det fraværende, er svaret endegyldigt nej uanset.")
    emit("")
    (out_dir / "regime_filter.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'regime_filter.txt'}")
    emit("→ Send mig regime_filter.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
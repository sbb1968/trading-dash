#!/usr/bin/env python3
"""
build_orb_universe.py — rekonstruér point-in-time ORB-univers fra bar_cache
═══════════════════════════════════════════════════════════════════════════
Bygger univers-filer ({dato → tickers}) DIREKTE fra de cachede 1-min bars du
allerede har — ingen IBKR, så det generer ikke K2 eller ORB. Erstatter de
manglende `historical_universe_*.json`.

VIGTIG FORSKEL fra K2's univers (og hvorfor den er RENERE for ORB):
K2's univers udvalgte dagens gainers på dagens FULDE bevægelse (intradag-gain,
open→high) — det er look-ahead for en breakout-strategi, fordi det forhåndsvælger
præcis de dage aktien LØB op, og dermed garanterer at breakoutet lykkedes.

Her udvælges dagens kandidater på **GAP fra gårsdagens luk** ((open − prev_close)
/ prev_close) — KUN information der var kendt ved åbningen 09:30, FØR ORB's
entry-vindue (09:45–10:30). Det approksimerer hvad en gainer-scanner viser ved
åbningen (samme idé som ORB's live TV-screener kører ÉN gang ved open), uden at
vide hvordan dagen endte. Toppen rangordnet efter gap → de N største gappere/dag.

FORBEHOLD (vær ærlig om dette): kandidat-poolen er de tickers der ligger i
bar_cache, som SELV er K2's gainer-univers. Så der er en residual selektions-
skævhed på POOL-niveau (ikke hele markedet). DAG-niveau-udvælgelsen (hvilke dage
hver ticker er "i spil") er dog nu pre-entry og uden intradag-look-ahead. Et
resultat hvor orb_classic kun lige består skal derfor læses skeptisk (universet
er mildt favorabelt); et hvor den fejler er desto mere afgørende.

Output: én JSON pr. kalendermåned i cachen (så orb_revalidate kan tage dem som
separate OOS-perioder), navngivet reconstructed_universe_{YYYY-MM}.json.

Rent offline. Importerer bar-indlæsning fra orb_revalidate.py (samme parsing som
backtesten → garanteret konsistens). Begge filer skal ligge i samme mappe.

Brug (fra backend/):
    python build_orb_universe.py --bar-cache bar_cache
    python build_orb_universe.py --bar-cache bar_cache --top-n 25 --min-volume 500000

Derefter:
    python orb_revalidate.py --universe reconstructed_universe_2026-04.json \
                                        reconstructed_universe_2026-05.json \
                             --bar-cache bar_cache

Placering: C:\\Projects\\trading_dash\\backend\\build_orb_universe.py
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from datetime import time as dtime
from pathlib import Path
from statistics import median

try:
    from orb_revalidate import load_1min_merged
except ImportError:
    print("FEJL: orb_revalidate.py skal ligge i samme mappe som dette script.")
    sys.exit(1)

RTH_START = dtime(9, 30)
RTH_END   = dtime(16, 0)


def ticker_names(cache_dir: Path) -> list[str]:
    names = set()
    for fp in glob.glob(str(cache_dir / "*_1min.csv")):
        stem = Path(fp).stem            # {TICKER}_{start}_{end}_1min
        names.add(stem.split("_")[0].upper())
    return sorted(names)


def day_metrics(bars):
    """Pr. dag for én ticker: (open, close, volume) over RTH. Kun dage med ≥30 bars."""
    by_day = defaultdict(list)
    for b in bars:
        if RTH_START <= b.t <= RTH_END:
            by_day[b.day].append(b)
    out = {}
    for day, bs in by_day.items():
        if len(bs) < 30:           # undgå halve dage / datahuller
            continue
        bs.sort(key=lambda x: x.ts)
        out[day] = (bs[0].o, bs[-1].c, sum(x.v for x in bs))
    return out


def main():
    ap = argparse.ArgumentParser(description="Rekonstruér point-in-time ORB-univers fra bar_cache")
    ap.add_argument("--bar-cache", default="bar_cache")
    ap.add_argument("--top-n", type=int, default=25, help="aktier pr. dag (default 25, som K2)")
    ap.add_argument("--price-min", type=float, default=2.0)
    ap.add_argument("--price-max", type=float, default=50.0)
    ap.add_argument("--min-volume", type=float, default=500_000,
                    help="ticker-niveau median dagsvolumen-gulv (likviditet, pre-entry)")
    ap.add_argument("--min-gap", type=float, default=0.0,
                    help="krævet minimum gap%% (default 0 = ren top-N efter gap)")
    args = ap.parse_args()

    cache_dir = Path(args.bar_cache)
    if not cache_dir.is_absolute():
        cache_dir = Path.cwd() / cache_dir
    if not cache_dir.exists():
        print(f"bar_cache-mappen {cache_dir} findes ikke.")
        return 1

    tickers = ticker_names(cache_dir)
    if not tickers:
        print(f"Ingen *_1min.csv i {cache_dir}.")
        return 1
    print(f"Fundet {len(tickers)} tickers i bar_cache. Indlæser...")

    # (day, ticker) -> gap%   + likviditetsfilter på ticker-niveau
    candidates: dict[object, list[tuple[float, str, float]]] = defaultdict(list)  # day -> [(gap, ticker, open)]
    kept, illiquid, nodata = 0, 0, 0
    for i, t in enumerate(tickers, 1):
        bars = load_1min_merged(t, cache_dir)
        if not bars:
            nodata += 1
            continue
        dm = day_metrics(bars)
        if not dm:
            nodata += 1
            continue
        vols = [v for (_, _, v) in dm.values()]
        if median(vols) < args.min_volume:     # stabil likviditets-egenskab, ikke dags-look-ahead
            illiquid += 1
            continue
        kept += 1
        days_sorted = sorted(dm.keys())
        for j, day in enumerate(days_sorted):
            if j == 0:
                continue                         # ingen forrige dag → intet gap
            prev_close = dm[days_sorted[j - 1]][1]
            o = dm[day][0]
            if prev_close <= 0 or not (args.price_min <= o <= args.price_max):
                continue
            gap = (o - prev_close) / prev_close * 100.0
            if gap < args.min_gap:
                continue
            candidates[day].append((gap, t, o))
        if i % 25 == 0:
            print(f"  ...{i}/{len(tickers)}")

    print(f"Likviditets-OK: {kept} · for illikvide: {illiquid} · uden brugbar data: {nodata}")

    # Udvælg top-N pr. dag efter gap (desc); gruppér pr. måned
    by_month: dict[str, dict[str, list[str]]] = defaultdict(dict)
    total_days, total_picks, gap_sum = 0, 0, 0.0
    for day in sorted(candidates.keys()):
        ranked = sorted(candidates[day], key=lambda x: x[0], reverse=True)[:args.top_n]
        if not ranked:
            continue
        picks = [tk for (_, tk, _) in ranked]
        month = f"{day.year:04d}-{day.month:02d}"
        by_month[month][day.isoformat()] = picks
        total_days += 1
        total_picks += len(picks)
        gap_sum += sum(g for (g, _, _) in ranked)

    if not by_month:
        print("Ingen dage kunne udvælges (tjek gap/pris/volumen-filtre).")
        return 1

    # Skriv én fil pr. måned
    written = []
    for month, days in sorted(by_month.items()):
        path = Path.cwd() / f"reconstructed_universe_{month}.json"
        path.write_text(json.dumps(days, indent=2), encoding="utf-8")
        uniq = len({t for ts in days.values() for t in ts})
        written.append((path, len(days), uniq))

    print("\n" + "=" * 70)
    print("  REKONSTRUEREDE UNIVERS-FILER")
    print("=" * 70)
    for path, ndays, uniq in written:
        print(f"  {path.name}: {ndays} handelsdage, {uniq} unikke aktier")
    avg_gap = gap_sum / total_picks if total_picks else 0
    print(f"\n  I alt: {total_days} dage, {total_picks} udvælgelser, snit-gap {avg_gap:+.2f}%")
    print(f"  (udvalgt på gap fra gårsdagens luk — pre-entry, intet intradag-look-ahead)")
    print("\n  Kør nu revalideringen:")
    files = " ".join(p.name for p, _, _ in written)
    print(f"    python orb_revalidate.py --universe {files} --bar-cache {args.bar_cache}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
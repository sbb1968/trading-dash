#!/usr/bin/env python3
"""
phase_b_prep.py — bro fra Fase A (screener_lab) til Fase B (washout-backtest)
═════════════════════════════════════════════════════════════════════════════
Tager de puljedefinitioner vi valgte i Fase A, bygger dem fra daily_cache, og
producerer ALT Fase B har brug for:

  1. Pr. pulje, pr. valideringsmåned: en univers-JSON ({dato → tickers}) i præcis
     det format washout_reclaim_backtest.py / washout_portfolio_sim.py /
     washout_regime.py forventer via --universe-file. To måneder = OOS-split,
     ligesom det oprindelige washout-univers.
  2. phase_b_tickers.txt: UNIONEN af alle navne på tværs af alle puljer/måneder
     — listen download_midcap_bars.py skal hente 1-min bars for.

Genbruger screener_lab's verificerede pulje-logik (samme metrikker, samme
point-in-time-disciplin) — ingen duplikeret logik. Begge filer i samme mappe.

VIGTIGT — den tunge del ligger BAGEFTER: dette script er sekunder (kun daglige
bars). Men 1-min-downloaden for unionen er mange IBKR-kald og kan tage timer.
Derfor printer dette unionens størrelse, så du kan beslutte omfanget FØR du
starter downloaden (fx kun én måned først hvis unionen er meget stor).

Rent offline. Kun stdlib (+ screener_lab). Ingen IBKR.

Brug (fra backend/, EFTER download_daily_universe + screener_lab):
    python phase_b_prep.py
    python phase_b_prep.py --defs baseline,bredt_prisbaand,momentum_alignet
    python phase_b_prep.py --periods 2026-04-01:2026-04-30,2026-05-01:2026-05-29

Placering: C:\\projects\\trading_dash\\backend\\phase_b_prep.py
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import date as date_cls
from pathlib import Path

try:
    import screener_lab as sl
except ImportError as e:
    print(f"FEJL: screener_lab.py skal ligge i samme mappe. ({e})")
    sys.exit(1)

DEFAULT_DEFS = ["baseline", "bredt_prisbaand", "momentum_alignet"]
DEFAULT_PERIODS = "2026-04-01:2026-04-30,2026-05-01:2026-05-29"


def parse_periods(spec: str):
    """'a:b,c:d' → [(label, start, end), ...]; label = startens YYYY-MM."""
    out = []
    for chunk in spec.split(","):
        a, b = chunk.split(":")
        s, e = date_cls.fromisoformat(a.strip()), date_cls.fromisoformat(b.strip())
        out.append((f"{s.year:04d}-{s.month:02d}", s, e))
    return out


def slice_pool(pool: dict, start: date_cls, end: date_cls) -> dict:
    """Behold kun dage i [start,end]. pool: {date_obj: [tickers]}."""
    out = {}
    for d, ticks in pool.items():
        if start <= d <= end:
            out[d.isoformat()] = ticks
    return out


def load_all_metrics(cache_dir: Path, emit):
    tickers = sorted({Path(fp).name.split("_")[0].upper()
                      for fp in glob.glob(str(cache_dir / "*_daily.csv"))})
    metrics, skipped = {}, 0
    for t in tickers:
        bars = sl.load_daily(cache_dir, t)
        if len(bars) < sl.MIN_HISTORY + 1:
            skipped += 1
            continue
        m = sl.per_day_metrics(bars)
        if m:
            metrics[t] = m
    emit(f"Tickers i cache: {len(tickers)}   med nok historik: {len(metrics)}   (sprunget over: {skipped})")
    return metrics


def main():
    ap = argparse.ArgumentParser(description="Byg Fase B-input fra de valgte puljedefinitioner")
    ap.add_argument("--cache-dir", default=sl.CACHE_DIRNAME)
    ap.add_argument("--defs", default=",".join(DEFAULT_DEFS),
                    help="komma-separerede definitionsnavne fra screener_lab.POOL_DEFS")
    ap.add_argument("--periods", default=DEFAULT_PERIODS,
                    help="komma-separerede start:slut-intervaller (ET-handelsdage)")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    cache_dir = Path.cwd() / args.cache_dir if not Path(args.cache_dir).is_absolute() else Path(args.cache_dir)
    out_dir = Path.cwd() / args.out_dir if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def emit(s=""):
        print(s, flush=True)

    emit("=" * 78)
    emit("  PHASE B PREP — byg univers-filer + 1-min-download-liste")
    emit("=" * 78)
    if not cache_dir.exists():
        emit(f"FEJL: {cache_dir} findes ikke — kør download_daily_universe.py først.")
        return 1

    chosen = [d.strip() for d in args.defs.split(",") if d.strip()]
    defs_by_name = {d["name"]: d for d in sl.POOL_DEFS}
    unknown = [c for c in chosen if c not in defs_by_name]
    if unknown:
        emit(f"FEJL: ukendte definitioner: {unknown}. Tilgængelige: {list(defs_by_name)}")
        return 1
    periods = parse_periods(args.periods)
    emit(f"Definitioner: {', '.join(chosen)}")
    emit(f"Perioder: {', '.join(f'{lbl} ({s}…{e})' for lbl, s, e in periods)}")
    emit("")

    metrics = load_all_metrics(cache_dir, emit)
    emit("")

    union = set()
    written = []
    emit("─" * 78)
    emit("  UNIVERS-FILER PR. PULJE/MÅNED")
    emit("─" * 78)
    for name in chosen:
        pool = sl.build_pool(metrics, defs_by_name[name])   # {date_obj: [tickers]}
        for lbl, s, e in periods:
            sliced = slice_pool(pool, s, e)
            fn = out_dir / f"pool_{name}_{lbl}.json"
            fn.write_text(json.dumps(sliced, indent=2), encoding="utf-8")
            day_ct = len(sliced)
            names = {t for ticks in sliced.values() for t in ticks}
            union |= names
            written.append(fn.name)
            emit(f"  {fn.name:<40} {day_ct:>3} dage  {len(names):>4} distinkte navne")
    emit("")

    # Union-ticker-liste til 1-min-download
    tickers_file = out_dir / "phase_b_tickers.txt"
    tickers_file.write_text("\n".join(sorted(union)) + "\n", encoding="utf-8")

    emit("─" * 78)
    emit("  DOWNLOAD-OMFANG (læs FØR du starter 1-min-downloaden)")
    emit("─" * 78)
    emit(f"  Union af alle navne på tværs af puljer/måneder: {len(union)}")
    emit(f"  Skrevet til: {tickers_file}")
    # groft estimat: ~50 handelsdage, ét kald pr (ticker, dag-vindue). 1-min er tungt.
    emit(f"  ⚠ 1-min-download for {len(union)} navne over ~2 mdr er MANGE IBKR-kald og kan")
    emit(f"    tage timer. Kør i et sikkert vindue på algoserveren (uden for evt. ORB).")
    emit(f"    Vil du teste billigere først, så hent kun den ene måneds navne.")
    emit("")
    emit("  Næste skridt:")
    emit(f"    python download_midcap_bars.py --tickers phase_b_tickers.txt --start 2026-04-01 --end 2026-05-29")
    emit(f"    (washout bruger kun intraday-bars → ingen warmup nødvendig. Derefter")
    emit(f"     washout_reclaim_backtest / portfolio_sim / regime pr. pulje mod")
    emit(f"     pool_<navn>_2026-04.json og pool_<navn>_2026-05.json)")
    emit("")
    emit(f"Skrev {len(written)} univers-filer + {tickers_file.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
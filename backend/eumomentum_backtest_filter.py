#!/usr/bin/env python3
"""
eumomentum_backtest_filter.py — validér meta-modellen som filter i den RIGTIGE backtest.
═══════════════════════════════════════════════════════════════════════════════════════════
Ikke-invasivt: importerer run_backtest fra eureversion_backtest (roerer den IKKE), koerer den
kanoniske Europa-reversion (lookback=30, entry|z|>=2.0, exit|z|<=0.5, stop|z|>=3.5, europaeisk
session, min_vol 50-pct), og anvender meta-modellens P(extension) som FILTER paa OOS-handlerne.

Ét spoergsmaal: loefter filteret den FAKTISKE strategis OOS-P&L (ikke det grove label-bar-exit
i eumomentum_model, men strategiens egne z-baserede exits)?

Metode:
  - Traen meta-modellen paa events FOER holdout (samme laaste model som eumomentum_model).
  - Join hver backtest-handel til events.csv paa (instrument, entry_ts) -> features -> P(ext).
    Backtest-entries er en delmaengde af study'ens events (samme entry-regel), saa de fleste
    matcher; umatchede (kunne ikke scores) rapporteres separat og indgaar IKKE i sammenligningen.
  - Paa OOS-handler (entry-dato >= holdout): stats WITH vs WITHOUT filter (skip P(ext) >= train-70pct).

PRAE-REGISTRERET DOM (samme aand som (a)/(b)):
  Den OEKONOMISK meningsfulde test af et meta-filter er om det haever GENNEMSNITTET pr. handel
  (avg% — dvs. skippede det faktisk taberne?). En hoejere PF alene er ikke nok: PF kan "snydes"
  ved bare at handle mindre (fjerne flere tab end gevinster proportionelt) UDEN at haeve avg%.
  ROED : filter haever IKKE avg% pr. handel (eller <30 matchede OOS-handler).
  GUL  : filter haever avg% men filtreret PF < 1.25 (svag/marginal edge).
  GROEN: filter haever avg% OG filtreret PF >= 1.25 (aegte per-handel-edge).

KOEBER INTET. Rent offline. Roerer ikke handelsstien eller den validerede backtest-kerne.

Koersel:  python eumomentum_backtest_filter.py
          python eumomentum_backtest_filter.py --data-dir data_harvest/mes_m2k_stitched
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from eureversion_backtest import run_backtest, load_15min, stats
from eumomentum_model import FEATURES, make_model, load_events, COST_BP, SKIP_PCTILE

INSTRUMENTS = ["MES", "M2K"]
LOOKBACK, ENTRY_Z, EXIT_Z, STOP_Z = 30, 2.0, 0.5, 3.5
SESSION      = "europaeisk"
MIN_VOL_PCT  = 50.0


def vol_threshold(bars, pct):
    pos = sorted(b.volume for b in bars if b.volume > 0)
    return pos[min(len(pos) - 1, int(len(pos) * pct / 100))] if pos else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Validér meta-modellen som filter i den rigtige backtest.")
    ap.add_argument("--data-dir", default="data_harvest/mes_m2k_stitched")
    ap.add_argument("--events", default="eumomentum_separability_output/events.csv")
    ap.add_argument("--holdout", default="2025-11-19")
    ap.add_argument("--cost-bp", type=float, default=COST_BP)
    a = ap.parse_args()

    data_dir = Path(a.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    ev_path = Path(a.events)
    if not ev_path.is_absolute():
        ev_path = Path.cwd() / ev_path
    holdout = datetime.strptime(a.holdout, "%Y-%m-%d").date()

    def line(s=""): print(s)
    line("=" * 78)
    line("  EUMOMENTUM — validering: meta-model som filter i den RIGTIGE eureversion_backtest")
    line(f"  Regel: lookback={LOOKBACK} entry|z|>={ENTRY_Z} exit|z|<={EXIT_Z} stop|z|>={STOP_Z} "
         f"session={SESSION}  cost={a.cost_bp}bp")
    line("=" * 78)

    # ── Traen meta-modellen paa events FOER holdout; scor ALLE events ──
    df = load_events(ev_path)
    tr = df[df["date"] < holdout]
    if tr["y"].nunique() < 2:
        line("FEJL: for faa train-events."); return 1
    model = make_model(tr["y"])
    model.fit(tr[FEATURES], tr["y"])
    df = df.assign(p_ext=model.predict_proba(df[FEATURES])[:, 1])
    thr = float(np.percentile(model.predict_proba(tr[FEATURES])[:, 1], SKIP_PCTILE))
    lookup = {(r.inst, pd.Timestamp(r.ts).tz_convert("UTC")): r.p_ext for r in df.itertuples()}
    line(f"  Model trained paa {len(tr)} pre-holdout events · skip-taerskel P(ext)>={thr:.2f} "
         f"(train-{SKIP_PCTILE}pct)")

    # ── Koer den rigtige backtest pr. instrument, tag hver handel med P(ext) ──
    rows = []
    n_unmatched = 0
    for inst in INSTRUMENTS:
        p = data_dir / f"{inst}_15min.csv"
        bars = load_15min(p) if p.exists() else []
        if not bars:
            line(f"  ⚠ {inst}: ingen data i {p}"); continue
        mv = vol_threshold(bars, MIN_VOL_PCT)
        trades = run_backtest(bars, SESSION, LOOKBACK, ENTRY_Z, EXIT_Z, STOP_Z, mv)
        for t in trades:
            key = (inst, pd.Timestamp(t.entry_ts).tz_convert("UTC"))
            p_ext = lookup.get(key)
            if p_ext is None:
                n_unmatched += 1
                continue
            rows.append({"inst": inst, "entry_ts": t.entry_ts, "date": t.entry_ts.date(),
                         "net": t.net_pct(a.cost_bp), "p_ext": float(p_ext)})
        line(f"  {inst}: {len(trades)} handler (min_vol={mv})")

    bt = pd.DataFrame(rows)
    if bt.empty:
        line("FEJL: ingen matchede handler."); return 1
    matched = len(bt); total_bt = matched + n_unmatched
    line(f"\n  Matchede handler: {matched}/{total_bt} "
         f"({100*matched/total_bt:.0f}% — umatchede kunne ikke scores og udelades)")

    oos = bt[bt["date"] >= holdout]
    if len(oos) < 30:
        line(f"\nDOM: ROED — kun {len(oos)} matchede OOS-handler (<30), for lidt at konkludere paa.")
        return 0

    def pf_of(net: pd.Series) -> dict:
        wins = net[net > 0].sum(); losses = -net[net < 0].sum()
        pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0)
        return {"n": len(net), "pf": pf, "win": float((net > 0).mean() * 100),
                "avg": float(net.mean()), "sum": float(net.sum())}

    all_m = pf_of(oos["net"])
    kept = oos[oos["p_ext"] < thr]
    kept_m = pf_of(kept["net"])
    line("\n── OOS (den rigtige strategis handler, @%gbp) ────────────────────────" % a.cost_bp)
    line(f"  {'':18}{'n':>6}{'PF':>8}{'win%':>8}{'avg%':>9}{'sum%':>10}")
    line(f"  {'alle':18}{all_m['n']:>6}{all_m['pf']:>8.2f}{all_m['win']:>7.1f}"
         f"{all_m['avg']:>+9.3f}{all_m['sum']:>+10.2f}")
    line(f"  {'model-filtreret':18}{kept_m['n']:>6}{kept_m['pf']:>8.2f}{kept_m['win']:>7.1f}"
         f"{kept_m['avg']:>+9.3f}{kept_m['sum']:>+10.2f}   (skippede {all_m['n']-kept_m['n']} handler)")

    edge = kept_m["avg"] > all_m["avg"] and kept_m["n"] >= 30   # haevede filteret per-handel-edge?
    line(f"\n  Per-handel-edge: avg% {all_m['avg']:+.3f} -> {kept_m['avg']:+.3f}  "
         f"({'HOEJERE' if kept_m['avg'] > all_m['avg'] else 'IKKE hoejere'})  ·  "
         f"total% {all_m['sum']:+.1f} -> {kept_m['sum']:+.1f}  (handler {all_m['n']}->{kept_m['n']})")
    line("=" * 78)
    if not edge:
        verdict = "ROED"; why = (f"filteret haever IKKE avg% ({all_m['avg']:+.3f} -> {kept_m['avg']:+.3f}) — "
                                 f"PF-loeftet {all_m['pf']:.2f}->{kept_m['pf']:.2f} er mekanisk (faerre handler, "
                                 f"total {all_m['sum']:+.1f}->{kept_m['sum']:+.1f}), ikke aegte per-handel-edge")
    elif kept_m["pf"] >= 1.25:
        verdict = "GROEN"; why = (f"filter haever avg% {all_m['avg']:+.3f}->{kept_m['avg']:+.3f} OG "
                                  f"PF {kept_m['pf']:.2f}>=1.25 i den RIGTIGE strategi")
    else:
        verdict = "GUL"; why = (f"filter haever avg% {all_m['avg']:+.3f}->{kept_m['avg']:+.3f} men "
                                f"PF {kept_m['pf']:.2f}<1.25")
    line(f"DOM: {verdict} — {why}")
    line("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())

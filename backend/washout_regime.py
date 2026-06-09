#!/usr/bin/env python3
"""
washout_regime.py
═════════════════
Regime-split af washout-reclaim — søster til meanrev_regime.py, men for en
MANGE-AKTIER-strategi i stedet for to futures. Svarer på det ene spørgsmål
valideringen efterlod:

    FINDES DER ET REGIME HVOR WASHOUT BLØDER?

Mean-reversion (og washout-reclaim er mean-reversion: køb pullback-bunden efter
en impuls) tjener i chop og kan tabe i trend. Det her måler om edgen holder, også
på trend-dage.

VIGTIGT — hvad dette script ER og IKKE er:

  • Det måler regimet PR. AKTIE (hver handels egen ticker-dags-efficiency-ratio),
    fordi vi ikke har SPY/QQQ til et markedsmål. Det gør analysen DESKRIPTIV:
    "findes der et regime hvor den bløder?" — IKKE et handlbart live-filter.
    Grunden: en akties efficiency-ratio kender man først når dagens session er
    slut, og washout springer mellem 15+ aktier dagligt, så man har sjældent en
    akties "trailing-ER" (man handlede den måske ikke i går). Et IMPLEMENTERBART
    filter kræver et markedsmål (SPY/QQQ-ER de foregående dage) — det bygges når
    den data er i bar_cache. Indtil da svarer dette på diagnosen, ikke reglen.

  • Det måler regimet på PORTEFØLJE-SIMULERINGENS faktisk-tagne handler (max-N
    slots, no-look-ahead) — IKKE alle rå signaler. Det er de handler du reelt
    kunne tage, så diagnosen gælder den handlbare strategi, ikke en teoretisk.

Genbruger signal- + pnl-logikken fra washout_portfolio_sim.py (ÉN kilde, så
resultaterne er sammenlignelige). Begge filer SKAL ligge i samme mappe.

Efficiency-ratio (ER) pr. ticker-dag = |sidste − første close| / Σ|bar-til-bar|.
  ER → 0 = ren chop (godt for mean-reversion) · ER → 1 = ren trend (farligt).

Rent offline. Kun stdlib (+ washout_portfolio_sim). Ingen IBKR.

Brug (fra backend-mappen, samme universe-filer som porteføljesimuleringen):
    python washout_regime.py --universe-file historical_universe_midcap_2026-04-01_2026-04-30.json --universe-file historical_universe_midcap_2026-05-01_2026-05-29.json
    python washout_regime.py --universe-file ...april.json --universe-file ...maj.json --max-concurrent 3 --cost-cents 2

Placering: C:\\projects\\trading_dash\\backend\\washout_regime.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date as date_cls, time as dtime
from pathlib import Path

try:
    from washout_portfolio_sim import (
        find_backend_dir, get_day_bars, scan_trade,
        trade_pnl, aggregate, fmt_pf, DEFAULTS,
    )
except ImportError as e:
    print(f"FEJL: washout_portfolio_sim.py skal ligge i samme mappe som dette script. ({e})")
    sys.exit(1)

OUTPUT_DIRNAME = "washout_regime_output"


# ─────────────────────────────────────────────────────────────────────────────
# Slot-simulation — KOPI af washout_portfolio_sim.simulate_day (priority-versionen)
# Reimplementeret her (ikke importeret) for at undgå at hænge på en præcis
# import-signatur; logikken er ord-for-ord den samme, så de tagne handler er
# IDENTISKE med porteføljesimuleringens.
# ─────────────────────────────────────────────────────────────────────────────
def simulate_day(day_trades, max_concurrent, priority_key, open_until):
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
        for t in sorted(by_ts[ts], key=lambda x: -x[priority_key])[:free]:
            taken.append(t)
            open_exits.append(t["exit_ts"])
    return taken


def scan_period(backend, data, params):
    """{date_iso: [trades]} for alle (ticker, dag) — KOPI af portfolio-sim'ens."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Efficiency-ratio pr. ticker-dag
# ─────────────────────────────────────────────────────────────────────────────
def day_er(bars):
    """ER over en dags bars: |sidste−første close| / Σ|bar-til-bar|. None hvis <5 bars."""
    if not bars or len(bars) < 5:
        return None
    closes = [b.close for b in bars]
    moves = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if moves <= 0:
        return None
    return abs(closes[-1] - closes[0]) / moves


_ER_CACHE: dict = {}


def er_for_trade(backend, t):
    """Fuld-dags-ER for handlens ticker den dag (regimet er en dags-egenskab,
    så vi bruger hele dagen, også for åbnings-vinduet)."""
    key = (t["ticker"], t["entry_ts"].date())
    if key in _ER_CACHE:
        return _ER_CACHE[key]
    bars = get_day_bars(backend, t["ticker"], t["entry_ts"].date())
    er = day_er(bars) if bars else None
    _ER_CACHE[key] = er
    return er


# ─────────────────────────────────────────────────────────────────────────────
# Tercil-rapport
# ─────────────────────────────────────────────────────────────────────────────
def er_terciles(backend, taken, slip, emit):
    paired = [(t, er_for_trade(backend, t)) for t in taken]
    paired = [(t, e) for t, e in paired if e is not None]
    if len(paired) < 6:
        emit(f"        (for få handler med ER til terciler: {len(paired)})")
        return None
    ers = sorted(e for _, e in paired)
    lo = ers[len(ers) // 3]
    hi = ers[2 * len(ers) // 3]
    if lo == hi:
        emit(f"        (ER-værdierne er for klyngede til terciler — grænse {lo:.3f}; "
             f"forventes ikke på ægte kontinuerte data)")
    buckets = {"lav (chop)": [], "mid": [], "høj (trend)": []}
    for t, e in paired:
        if e <= lo:
            buckets["lav (chop)"].append(t)
        elif e >= hi:
            buckets["høj (trend)"].append(t)
        else:
            buckets["mid"].append(t)
    emit(f"        ER-tercil (grænser {lo:.3f} / {hi:.3f};  lav=chop=godt, høj=trend=farligt):")
    pfs = {}
    for lbl in ("lav (chop)", "mid", "høj (trend)"):
        s = aggregate(buckets[lbl], slip)
        pfs[lbl] = s["pf"]
        emit(f"           {lbl:>12}: n={s['n']:>3}  WR={s['wr']:>3.0f}%  "
             f"snit={s['avg']:>+5.2f}%  sum={s['sum']:>+6.1f}%  PF={fmt_pf(s['pf'])}")
    return pfs


def window_block(backend, by_day, max_concurrent, pk, open_until, slip, emit):
    """Saml tagne handler over alle dage i ét vindue, vis overall + ER-terciler."""
    taken = []
    for d in sorted(by_day):
        taken += simulate_day(by_day[d], max_concurrent, pk, open_until)
    s = aggregate(taken, slip)
    emit(f"        taget i alt: n={s['n']}  WR={s['wr']:.0f}%  snit={s['avg']:+.2f}%  "
         f"sum={s['sum']:+.1f}%  PF={fmt_pf(s['pf'])}  (@ {slip*100:.0f}¢)")
    pfs = er_terciles(backend, taken, slip, emit)
    return taken, pfs


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Regime-split af washout-reclaim (per-aktie ER)")
    ap.add_argument("--backend-dir", default=None)
    ap.add_argument("--universe-file", action="append", default=[],
                    help="samme universe-filer som porteføljesimuleringen (kan gentages)")
    ap.add_argument("--max-concurrent", type=int, default=3)
    ap.add_argument("--priority", choices=["washout", "time"], default="washout",
                    help="valg blandt samtidige signaler (washout-dybde / først)")
    ap.add_argument("--open-until", default="10:30", help="åbningsvindue-grænse ET (HH:MM)")
    ap.add_argument("--cost-cents", type=float, default=2.0,
                    help="slippage pr. aktie i cent til tercil-tabellen (default 2)")
    ap.add_argument("--lookback", type=int, default=DEFAULTS["lookback"])
    ap.add_argument("--min-runup", type=float, default=DEFAULTS["min_runup"])
    ap.add_argument("--washout", type=float, default=DEFAULTS["washout"])
    ap.add_argument("--target", type=float, default=DEFAULTS["target"])
    args = ap.parse_args()

    if not args.universe_file:
        print("FEJL: angiv mindst én --universe-file (samme som porteføljesimuleringen).")
        return 1

    backend = find_backend_dir(args.backend_dir)
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    params = dict(lookback=args.lookback, min_runup=args.min_runup,
                  washout=args.washout, target=args.target)
    pk = {"washout": "wo_depth", "time": "entry_ts"}[args.priority]
    hh, mm = (int(x) for x in args.open_until.split(":"))
    open_until = dtime(hh, mm)
    slip = args.cost_cents / 100.0

    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  WASHOUT-RECLAIM — REGIME-SPLIT (trend vs chop, per aktie)")
    emit("=" * 78)
    emit(f"Backend: {backend}")
    emit(f"Max samtidige: {args.max_concurrent}   prioritet: {args.priority}   "
         f"åbningsvindue: indtil {args.open_until} ET   tercil-aflæsning @ {args.cost_cents:.0f}¢")
    emit(f"Parametre: lookback={params['lookback']}m  min_runup={params['min_runup']}%  "
         f"washout={params['washout']}%  target={params['target']}%")
    emit("ER = |net bevægelse| / Σ|bar-bevægelse| pr. ticker-dag.  ER→0 chop (godt), ER→1 trend (farligt).")
    emit("Regime måles PR. AKTIE → DESKRIPTIV diagnose (ikke et handlbart live-filter; det kræver SPY/QQQ).")
    emit("")

    # Saml på tværs af perioder til en pooled headline.
    pooled = {"HELE DAGEN": [], "ÅBNING": []}

    for uf in args.universe_file:
        up = (backend / uf) if not Path(uf).is_absolute() else Path(uf)
        if not up.exists():
            emit(f"── {uf}: findes ikke — springer over\n")
            continue
        data = json.loads(up.read_text(encoding="utf-8"))
        by_day = scan_period(backend, data, params)
        raw = sum(len(v) for v in by_day.values())

        emit("─" * 78)
        emit(f"  PERIODE: {up.name}")
        emit("─" * 78)
        emit(f"  Rå signaler i alt: {raw}")
        emit("")
        emit("  [HELE DAGEN]")
        taken_d, _ = window_block(backend, by_day, args.max_concurrent, pk, None, slip, emit)
        pooled["HELE DAGEN"] += taken_d
        emit("")
        emit(f"  [ÅBNING (≤{args.open_until})]")
        taken_o, _ = window_block(backend, by_day, args.max_concurrent, pk, open_until, slip, emit)
        pooled["ÅBNING"] += taken_o
        emit("")

    # ── Pooled headline (mest statistisk styrke til "bløder den i trend?") ──
    emit("─" * 78)
    emit("  ALLE PERIODER SAMLET  (headline — flest handler pr. tercil)")
    emit("─" * 78)
    verdict_pfs = {}
    for win in ("HELE DAGEN", "ÅBNING"):
        emit(f"  [{win}]  taget i alt: {len(pooled[win])}")
        s = aggregate(pooled[win], slip)
        emit(f"        overall: n={s['n']}  WR={s['wr']:.0f}%  snit={s['avg']:+.2f}%  "
             f"sum={s['sum']:+.1f}%  PF={fmt_pf(s['pf'])}")
        verdict_pfs[win] = er_terciles(backend, pooled[win], slip, emit)
        emit("")

    # ── Dom ──
    emit("─" * 78)
    emit("  DOM")
    emit("─" * 78)
    emit("  ROBUST: PF > 1 i ALLE ER-terciler (også høj-ER/trend) → washout behøver INTET")
    emit("    regime-filter; mean-reversions trend-svaghed viser sig ikke i data.")
    emit("  REGIME-AFHÆNGIG: PF kollapser/negativ i høj-ER (trend) → svagheden ER der. Næste")
    emit("    skridt er IKKE et per-aktie-filter (ikke handlbart), men at skaffe SPY/QQQ 1-min")
    emit("    og bygge et markeds-ER-filter der fravælger trend-dage FØR de handles.")
    # Auto-pejling hvis muligt
    hd = verdict_pfs.get("HELE DAGEN")
    if hd and all(v is not None for v in hd.values()):
        hi_pf = hd["høj (trend)"]
        if hi_pf != float("inf") and hi_pf < 1.0:
            emit("")
            emit(f"  → Foreløbig pejling: høj-ER (trend) PF={fmt_pf(hi_pf)} < 1 på hele-dags-tagne "
                 f"handler — tegn på regime-afhængighed. Overvej markedsfilteret.")
        else:
            emit("")
            emit(f"  → Foreløbig pejling: høj-ER (trend) PF={fmt_pf(hi_pf)} ≥ 1 — washout ser robust")
            emit(f"    ud også i trend-tercilen. (Bekræft i begge perioder ovenfor.)")
    emit("")

    (out_dir / "regime_summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'regime_summary.txt'}")
    emit("→ Send mig regime_summary.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
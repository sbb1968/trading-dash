#!/usr/bin/env python3
"""
asian_meanrev_backtest.py — SELVSTAENDIG mean-reversion backtest paa sweepens nominerede
═════════════════════════════════════════════════════════════════════════════════════════
Naeste skridt efter asian_sweep_precondition: de instrumenter der bestod screenen (A50 +
AUDJPY) faar nu en DESIGNET regel paalagt KOST og splittet IS/OOS. Det er den egentlige
dom — en revert-signatur (lille lag-1 autokorr) skal OVERLEVE omkostninger OG GENTAGE
out-of-sample. Et multiple-comparison-heldigt signal gentager ikke.

Regel (z-score mean-reversion, 5-min bars, session-gated til instrumentets asiatiske vindue):
  z = (close - rullende middel) / rullende std  over LOOKBACK 5-min bars (pr. kontiguert run,
  saa vinduet ALDRIG krydser frokost/overnight). |z|>=ENTRY -> fade (z>0 short, z<0 long).
  Exit: |z|<=EXIT (revert) · |z|>=STOP (stop) · run-slut (tids-stop). Defaults, IKKE optimeret.

KOST: rundtur i bp paalagt pr. handel; sweepes 0..N. Edge skal holde ved instrumentets
realistiske kost (FX ~1 bp · OSE/HKFE/SGX-mini ~5 bp). OOS: foerste halvdel dage = IS,
anden halvdel = OOS — signalet skal gentage.

Rent OFFLINE: stdlib + asian_registry + data-hjaelpere fra asian_sweep_precondition. Ingen
eureversion-kobling. Input: data_harvest/{LABEL}_1min.csv. Output: ./asian_meanrev_output/.

Brug (fra backend/, efter asian_harvest_1min):
    python asian_meanrev_backtest.py                       # A50 + AUDJPY (defaults)
    python asian_meanrev_backtest.py --only A50 --entry-z 2.5 --lookback 30
    python asian_meanrev_backtest.py --only AUDJPY,A50 --cost-bp 0,1,2,3,5,10

Placering: C:\\Projects\\trading_dash\\backend\\asian_meanrev_backtest.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from asian_registry import REGISTRY
from asian_sweep_precondition import load_bars, in_windows, session_days, to_5min_runs, VOL_FLOOR_FRAC

OUT_DIR = Path("asian_meanrev_output")
DEFAULT_ONLY = "A50,AUDJPY"


def zscores(closes, lookback):
    z = [None] * len(closes)
    for i in range(lookback, len(closes)):
        win = closes[i - lookback:i]
        m = sum(win) / lookback
        sd = statistics.pstdev(win)
        z[i] = (closes[i] - m) / sd if sd > 0 else None
    return z


def run_rule(runs, lookback, entry_z, exit_z, stop_z):
    """Gennemloeb hvert kontiguert 5-min run -> liste af handler (dt, gross-afkast, reason)."""
    trades = []
    for run in runs:
        closes = [b["c"] for b in run]
        if len(closes) < lookback + 2:
            continue
        z = zscores(closes, lookback)
        pos, entry = 0, 0.0
        for i in range(len(closes)):
            zi = z[i]
            if zi is None:
                continue
            if pos == 0:
                if abs(zi) >= entry_z:
                    pos = -1 if zi > 0 else 1          # fade udstraekningen
                    entry = closes[i]
            else:
                reason = "revert" if abs(zi) <= exit_z else ("stop" if abs(zi) >= stop_z else None)
                if reason and entry > 0:
                    trades.append({"dt": run[i]["dt"], "gross": (closes[i] - entry) / entry * pos,
                                   "reason": reason})
                    pos = 0
        if pos != 0 and entry > 0:                     # tids-stop ved run-slut
            trades.append({"dt": run[-1]["dt"], "gross": (closes[-1] - entry) / entry * pos,
                           "reason": "session_end"})
    return trades


def metrics(trades, cost_bp):
    c = cost_bp / 10000.0
    nets = [t["gross"] - c for t in trades]
    n = len(nets)
    if n == 0:
        return None
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    sl = sum(losses)
    pf = (sum(wins) / abs(sl)) if sl < 0 else (float("inf") if wins else 0.0)
    return dict(n=n, wr=100 * len(wins) / n, mean=statistics.mean(nets) * 100,
                sum=sum(nets) * 100, pf=pf, worst=min(nets) * 100)


def build_runs(inst):
    """Indlaes + session-gate + 5-min runs pr. dag. Returnerer (runs_pr_dag_sorteret, n_days, dq)."""
    path = Path("data_harvest") / f"{inst['label']}_1min.csv"
    path = path if path.is_absolute() else (Path.cwd() / path)
    if not path.exists():
        return None, 0, f"(mangler {path.name})"
    tz = ZoneInfo(inst["tz"])
    bars = load_bars(path, tz)
    win = inst["windows"]
    wb = [b for b in bars if in_windows(b["dt"].timetz().replace(tzinfo=None), win)]
    if len(wb) < 300:
        return None, 0, f"(for faa bars: {len(wb)})"
    vols = [b["v"] for b in wb if b["v"] > 0]
    med_vol = statistics.median(vols) if vols else 0
    min_vol = max(1, med_vol * VOL_FLOOR_FRAC) if inst["kind"] == "futures" else 0
    days = session_days(bars, win, inst["kind"], min_vol)
    ds = sorted(days.items())
    dq = (f"median vol={med_vol:.0f}" if inst["kind"] == "futures" else "FX") + f" · {len(ds)} dage"
    return ds, len(ds), dq


def pf_s(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def analyze(inst, args, emit):
    label = inst["label"]
    emit("─" * 78)
    emit(f"  {label}  ({inst['kind']})  regel: entry|z|>={args.entry_z} exit|z|<={args.exit_z} "
         f"stop|z|>={args.stop_z} lookback={args.lookback} (5-min, DEFAULTS)")
    emit("─" * 78)
    ds, n_days, dq = build_runs(inst)
    if not ds:
        emit(f"   {dq}")
        return None
    cut = ds[len(ds) // 2][0]    # median-dato -> IS (foer) / OOS (fra og med)

    runs_all, runs_is, runs_oos = [], [], []
    for d, dbars in ds:
        rs = to_5min_runs(dbars)
        runs_all += rs
        (runs_is if d < cut else runs_oos).extend(rs)

    tr_all = run_rule(runs_all, args.lookback, args.entry_z, args.exit_z, args.stop_z)
    if not tr_all:
        emit(f"   {dq} · INGEN handler (regel for stram for dette instrument?)")
        return None
    mix = {}
    for t in tr_all:
        mix[t["reason"]] = mix.get(t["reason"], 0) + 1
    emit(f"   {dq} · {len(tr_all)} handler ({len(tr_all)/max(n_days,1):.1f}/dag) · "
         f"exit-mix: {', '.join(f'{k}={v}' for k, v in sorted(mix.items()))}")
    emit(f"   IS/OOS-skaering: {cut} (IS foer · OOS fra og med)")

    emit(f"      {'kost':>6} {'n':>5} {'WR%':>5} {'snit%':>8} {'sum%':>8} {'PF':>6} {'vaerst%':>8}")
    for cb in args.costs:
        m = metrics(tr_all, cb)
        if m:
            emit(f"      {cb:>4}bp {m['n']:>5} {m['wr']:>5.0f} {m['mean']:>8.3f} {m['sum']:>8.1f} "
                 f"{pf_s(m['pf']):>6} {m['worst']:>8.2f}")

    hint = 1 if inst["kind"] == "fx" else 5
    mi = metrics([t for t in tr_all if t["dt"].date() < cut], hint)
    mo = metrics([t for t in tr_all if t["dt"].date() >= cut], hint)
    is_s = f"n={mi['n']} sum={mi['sum']:+.1f}% PF={pf_s(mi['pf'])}" if mi else "n=0"
    oo_s = f"n={mo['n']} sum={mo['sum']:+.1f}% PF={pf_s(mo['pf'])}" if mo else "n=0"
    emit(f"   @ {hint}bp (realistisk):  IS [{is_s}]  |  OOS [{oo_s}]")

    full = metrics(tr_all, hint)
    holds = bool(full and full["pf"] > 1 and mo and mo["pf"] > 1 and mo["sum"] > 0)
    return {"label": label, "hint": hint, "full": full, "is": mi, "oos": mo, "holds": holds}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Selvstaendig mean-reversion backtest (z-score, OOS, kost)")
    ap.add_argument("--only", default=DEFAULT_ONLY, help="kommasepareret, fx A50,AUDJPY")
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--entry-z", type=float, default=2.0)
    ap.add_argument("--exit-z", type=float, default=0.5)
    ap.add_argument("--stop-z", type=float, default=3.5)
    ap.add_argument("--cost-bp", default="0,1,2,3,5,10")
    a = ap.parse_args()
    a.costs = [int(x) for x in a.cost_bp.split(",") if x.strip()]

    out_dir = OUT_DIR if OUT_DIR.is_absolute() else (Path.cwd() / OUT_DIR)
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    only = set(x.strip().upper() for x in a.only.split(",")) if a.only else None
    insts = [i for i in REGISTRY if (only is None or i["label"] in only)]

    emit("=" * 78)
    emit("  ASIAN MEAN-REVERSION BACKTEST — z-score, session-gated, OOS-split, KOST paalagt")
    emit("=" * 78)
    emit("  Edge = PF>1 ved realistisk kost OG positiv+PF>1 OOS. Et screen-held gentager IKKE.")
    emit(f"  Instrumenter: {', '.join(i['label'] for i in insts)}")
    emit("")

    res = []
    for inst in insts:
        try:
            r = analyze(inst, a, emit)
        except Exception as e:
            emit(f"   FEJL ved {inst['label']}: {type(e).__name__}: {e}")
            r = None
        if r:
            res.append(r)
        emit("")

    emit("=" * 78)
    emit("  DOM")
    emit("=" * 78)
    for r in res:
        verdict = "✅ OVERLEVER (PF>1 ved kost OG holder OOS)" if r["holds"] else \
                  "❌ falder (kost eller OOS draeber edgen)"
        f = r["full"]
        emit(f"  {r['label']:<8} @ {r['hint']}bp: PF={pf_s(f['pf'])} sum={f['sum']:+.1f}% · {verdict}")
    if not res:
        emit("  (ingen instrumenter kunne backtestes)")
    emit("")
    emit("  Defaults er IKKE optimeret -> et positivt resultat er konservativt. Overlever et")
    emit("  instrument -> naeste skridt er parameter-sweep + regime-split (IKKE her).")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"  Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

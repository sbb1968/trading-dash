#!/usr/bin/env python3
"""
asian_sweep_precondition.py — KOMPARATIV precondition-scan over asian_registret (5 markeder)
═════════════════════════════════════════════════════════════════════════════════════════════
Generalisering af nikkei_precondition til en RANGERET sammenligning. Hvert instrument maales i
DETS asiatiske session, saa futures og FX er sammenlignelige. Output = en rangliste; kun toppen
gaar videre til regel + kost/OOS-backtest (naeste spec).

To vaern mod multiple-comparison (kerne):
  1. TIDLIG/SEN-STABILITET: hver noegle-signatur paa fuld sample + foerste vs anden halvdel.
     Skifter fortegn mellem halvdelene = stoej (ikke STABIL). Indbygget mini-OOS.
  2. STATISTISK AERLIGHED: n + SE + "|effekt|>2*SE?"-flag pr. signatur; median-volumen + THIN-flag.

Maalinger (alt i instrumentets aktive asiatiske vindue):
  U1  Autokorr pr. blok (5-min, lag-1; aabning/midt/sent + ALLE) — trend/revert, HOVEDAKSE.
  U2  Aktivitets-/vol-profil pr. 30-min-bucket — lokaliserer det aktive sub-vindue.
  F2  (futures) Aabnings-range-braek -> kontinuation (OR 15/30) — momentum vs fade.
  F3  (futures) Overnight-gap fill vs ride.
  FX: kun U1/U2 (kontinuerlig handel — ingen diskret aabning/gap); renset paa aktive timer.

GRAENSE: maaler en SIGNATUR, ikke en bevist edge — omkostninger IKKE paalagt (naeste skridt).
Rangeringen er en SCREEN; toppen skal GENTAGE i en designet OOS-backtest med kost.

Rent OFFLINE: stdlib + asian_registry. Input: data_harvest/{LABEL}_1min.csv (fra asian_harvest).
Output: ./asian_sweep_output/summary.txt

Brug (fra backend/):  python asian_sweep_precondition.py
Placering: C:\\Projects\\trading_dash\\backend\\asian_sweep_precondition.py
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from asian_registry import REGISTRY

HARVEST_DIR = Path("data_harvest")
OUT_DIR     = Path("asian_sweep_output")
VOL_FLOOR_FRAC = 0.10
MIN_DAY_BARS   = 40
THIN_VOL       = 50         # median 1-min volumen under dette = THIN (futures)
GAP_MIN        = 6          # 5-min bars > dette fra hinanden = brud paa kontiguitet (frokost/hul)


# ── statistik-hjaelpere ───────────────────────────────────────────
def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def lag1_seqs(seqs):
    xs, ys = [], []
    for s in seqs:
        for i in range(len(s) - 1):
            xs.append(s[i]); ys.append(s[i + 1])
    return pearson(xs, ys), len(xs)


def ac_sig(daylist, block):
    """Autokorr-signatur for en blok: (ac, n, se, distinguishable, stabil)."""
    def _ac(days):
        seqs = []
        for d in days:
            seqs += d.get(block, [])
        return lag1_seqs(seqs)
    ac, n = _ac(daylist)
    if ac is None or n < 30:
        return (ac, n, None, False, False)
    se = 1.0 / math.sqrt(n)
    dist = abs(ac) > 2 * se
    h = len(daylist) // 2
    a1, _ = _ac(daylist[:h]); a2, _ = _ac(daylist[h:])
    stable = (a1 is not None and a2 is not None and (a1 > 0) == (a2 > 0))
    return (ac, n, se, dist, stable)


def mean_sig(vals):
    """Middel-signatur (kontinuation): (mean, n, se, distinguishable, stabil)."""
    n = len(vals)
    if n < 20:
        return (statistics.mean(vals) if vals else None, n, None, False, False)
    mu = statistics.mean(vals)
    se = (statistics.pstdev(vals) / math.sqrt(n)) or 1e-12
    dist = abs(mu) > 2 * se
    h = n // 2
    m1, m2 = statistics.mean(vals[:h]), statistics.mean(vals[h:])
    stable = (m1 > 0) == (m2 > 0)
    return (mu, n, se, dist, stable)


def fmt_sig(name, sig):
    eff, n, se, dist, stable = sig
    if eff is None:
        return f"   {name:<10} n/a (n={n})"
    se_s = f"+-{se:.4f}" if se is not None else ""
    flags = ("DISTINGUISHABLE" if dist else "stoej") + (" · STABIL" if stable else " · ustabil")
    return f"   {name:<10} {eff:+.4f} {se_s} (n={n})  [{flags}]"


# ── data ──────────────────────────────────────────────────────────
def load_bars(path, tz):
    out = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(r["timestamp"]).astimezone(tz)
                o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
                v = int(float(r["volume"])) if r.get("volume") else 0
            except (ValueError, KeyError, TypeError):
                continue
            if c <= 0 or h <= 0:
                continue
            out.append({"dt": dt, "o": o, "h": h, "l": l, "c": c, "v": v})
    out.sort(key=lambda b: b["dt"])
    return out


def in_windows(t, windows):
    return any(ws <= t < we for ws, we in windows)


def session_days(bars, windows, kind, min_vol):
    days = {}
    for b in bars:
        t = b["dt"].timetz().replace(tzinfo=None)
        if not in_windows(t, windows):
            continue
        if kind == "futures" and b["v"] < min_vol:
            continue
        days.setdefault(b["dt"].date(), []).append(b)
    return {d: sorted(bs, key=lambda b: b["dt"]) for d, bs in days.items() if len(bs) >= MIN_DAY_BARS}


def to_5min_runs(day_bars):
    bymin = {}
    for b in day_bars:
        key = b["dt"].replace(minute=(b["dt"].minute // 5) * 5, second=0, microsecond=0)
        bymin.setdefault(key, {"dt": key, "c": b["c"]})["c"] = b["c"]
    bars5 = [bymin[k] for k in sorted(bymin)]
    runs, cur = [], []
    for b in bars5:
        if cur and (b["dt"] - cur[-1]["dt"]).total_seconds() > GAP_MIN * 60 + 1:
            runs.append(cur); cur = []
        cur.append(b)
    if cur:
        runs.append(cur)
    return runs


def day_blocks(day_bars):
    """{block: [return-sekvenser]} for én dag — blok efter naerhed til dagens aabning/luk."""
    runs = to_5min_runs(day_bars)
    flat = [b for r in runs for b in r]
    if len(flat) < 2:
        return {}
    open_dt, close_dt = flat[0]["dt"], flat[-1]["dt"]

    def block_of(dt):
        if (dt - open_dt).total_seconds() <= 30 * 60:
            return "AABNING"
        if (close_dt - dt).total_seconds() <= 30 * 60:
            return "SENT"
        return "MIDT"

    res = {"AABNING": [], "MIDT": [], "SENT": [], "ALLE": []}
    for run in runs:
        if len(run) < 2:
            continue
        rets = [(run[i]["c"] - run[i - 1]["c"]) / run[i - 1]["c"] for i in range(1, len(run))]
        res["ALLE"].append(rets)
        cur_b, cur = None, []
        for i, r in enumerate(rets):
            b = block_of(run[i + 1]["dt"])
            if b == cur_b:
                cur.append(r)
            else:
                if cur:
                    res[cur_b].append(cur)
                cur_b, cur = b, [r]
        if cur:
            res[cur_b].append(cur)
    return res


# ── F2/F3 (futures) ───────────────────────────────────────────────
def or_break(days_sorted, open_t, N):
    """OR-braek -> kontinuation pr. dag (signeret i braek-retning). Returnerer (vals, break_rate, n_days, open_move_med)."""
    vals, moves, n_days, n_break = [], [], 0, 0
    or_end_min = open_t.hour * 60 + open_t.minute + N
    for _, dbars in days_sorted:
        orb = [b for b in dbars if (b["dt"].hour * 60 + b["dt"].minute) < or_end_min]
        rest = [b for b in dbars if (b["dt"].hour * 60 + b["dt"].minute) >= or_end_min]
        if len(orb) < 3 or len(rest) < 5:
            continue
        n_days += 1
        or_hi, or_lo = max(b["h"] for b in orb), min(b["l"] for b in orb)
        day_open, close = orb[0]["o"], rest[-1]["c"]
        if day_open > 0:
            moves.append(max(or_hi - day_open, day_open - or_lo) / day_open)
        brk = None
        for b in rest:
            if b["h"] >= or_hi:
                brk = ("up", or_hi); break
            if b["l"] <= or_lo:
                brk = ("dn", or_lo); break
        if brk is None or brk[1] <= 0:
            continue
        n_break += 1
        d, price = brk
        vals.append((close - price) / price if d == "up" else (price - close) / price)
    move_med = statistics.median(moves) if moves else 0.0
    return vals, (n_break / n_days if n_days else 0.0), n_days, move_med


def overnight_gap(days_sorted):
    """Gap fill vs ride. Returnerer (cont_vals, fill_rate, median_gap, n_gap)."""
    cont, fills, gaps = [], 0, []
    for i in range(1, len(days_sorted)):
        prev_close = days_sorted[i - 1][1][-1]["c"]
        today = days_sorted[i][1]
        day_open = today[0]["o"]
        if prev_close <= 0 or abs(day_open - prev_close) < 1e-12:
            continue
        gap = (day_open - prev_close) / prev_close
        gaps.append(abs(gap))
        filled = any(b["l"] <= prev_close for b in today) if gap > 0 else any(b["h"] >= prev_close for b in today)
        if filled:
            fills += 1
        cont.append(((today[-1]["c"] - day_open) / day_open) * (1 if gap > 0 else -1))
    return cont, (fills / len(gaps) if gaps else 0.0), (statistics.median(gaps) if gaps else 0.0), len(gaps)


# ── per-instrument ────────────────────────────────────────────────
def analyze(inst, emit):
    label, kind = inst["label"], inst["kind"]
    path = (HARVEST_DIR / f"{label}_1min.csv")
    path = path if path.is_absolute() else (Path.cwd() / path)
    emit("─" * 78)
    emit(f"  {label}  ({kind})")
    emit("─" * 78)
    if not path.exists():
        emit(f"   (mangler {path.name} — host foerst)")
        return None
    tz = ZoneInfo(inst["tz"])
    bars = load_bars(path, tz)
    win = inst["windows"]
    win_bars = [b for b in bars if in_windows(b["dt"].timetz().replace(tzinfo=None), win)]
    if len(win_bars) < 300:
        emit(f"   (for faa bars i vinduet: {len(win_bars)})")
        return None
    vols = [b["v"] for b in win_bars if b["v"] > 0]
    med_vol = statistics.median(vols) if vols else 0
    min_vol = max(1, med_vol * VOL_FLOOR_FRAC) if kind == "futures" else 0
    thin = (kind == "futures" and med_vol < THIN_VOL)
    days = session_days(bars, win, kind, min_vol)
    ds = sorted(days.items())
    dq = (f"median 1-min vol={med_vol:.0f}" if kind == "futures" else "FX (ingen volumen)") \
        + f" · {len(ds)} handelsdage" + (" · ⚠THIN" if thin else "")
    emit(f"   datakvalitet: {dq}")
    if len(ds) < 12:
        emit("   (for faa handelsdage til stabil halvdels-test)")

    # U1
    dblocks = [day_blocks(b) for _, b in ds]
    u1 = {blk: ac_sig(dblocks, blk) for blk in ("AABNING", "MIDT", "SENT", "ALLE")}
    emit("   U1 autokorr (5-min, lag-1; <0 revert / >0 trend):")
    for blk in ("AABNING", "MIDT", "SENT", "ALLE"):
        emit(fmt_sig(blk, u1[blk]))

    cands = []
    for blk in ("AABNING", "MIDT", "SENT", "ALLE"):
        ac, n, se, dist, stable = u1[blk]
        if dist and stable and se:
            cands.append({"type": "revert" if ac < 0 else "trend", "scope": f"U1:{blk}",
                          "effect": ac, "se": se, "score": abs(ac) / se})

    # F2/F3 (futures)
    if kind == "futures":
        for N in (15, 30):
            vals, brate, ndays, move_med = or_break(ds, inst["open"], N)
            sig = mean_sig(vals)
            eff, nn, se, dist, stable = sig
            emit(f"   F2 OR={N}m: braek {100*brate:.0f}% · aabn-move {100*move_med:.2f}% · "
                 + fmt_sig("kont", sig).strip())
            if dist and stable and se and N == 30:
                cands.append({"type": "OR-momentum" if eff > 0 else "OR-fade", "scope": f"F2:OR{N}",
                              "effect": eff, "se": se, "score": abs(eff) / se})
        cont, fill, mgap, ngap = overnight_gap(ds)
        sig = mean_sig(cont)
        eff, nn, se, dist, stable = sig
        emit(f"   F3 gap: median |gap| {100*mgap:.2f}% · fill {100*fill:.0f}% · "
             + fmt_sig("ride/fade", sig).strip())
        if dist and stable and se:
            cands.append({"type": "gap-ride" if eff > 0 else "gap-fade", "scope": "F3:gap",
                          "effect": eff, "se": se, "score": abs(eff) / se})

    # U2 (kort: top-3 mest aktive buckets)
    buck = {}
    prev = None
    for b in win_bars:
        if prev is not None and prev["c"] > 0:
            key = (b["dt"].hour, (b["dt"].minute // 30) * 30)
            buck.setdefault(key, []).append(abs((b["c"] - prev["c"]) / prev["c"]))
        prev = b
    if buck:
        top = sorted(buck.items(), key=lambda kv: -statistics.mean(kv[1]))[:3]
        emit("   U2 mest aktive 30-min-buckets (lokal tid): "
             + ", ".join(f"{h:02d}:{m:02d} ({100*statistics.mean(v):.3f}%)" for (h, m), v in top))

    best = max(cands, key=lambda c: c["score"]) if cands else None
    return {"label": label, "kind": kind, "med_vol": med_vol, "thin": thin,
            "n_days": len(ds), "best": best, "cands": cands}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    out_dir = OUT_DIR if OUT_DIR.is_absolute() else (Path.cwd() / OUT_DIR)
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  ASIAN SWEEP — KOMPARATIV PRECONDITION (offline; omkostninger IKKE paalagt)")
    emit("=" * 78)
    emit("  Vaern: tidlig/sen-stabilitet (mini-OOS) + n/SE/distinguishable + THIN-flag.")
    emit("")

    results = []
    for inst in REGISTRY:
        try:
            r = analyze(inst, emit)
        except Exception as e:
            emit(f"   FEJL ved {inst['label']}: {type(e).__name__}: {e}")
            r = None
        if r:
            results.append(r)
        emit("")

    # ── RANGERING ──
    emit("=" * 78)
    emit("  RANGERING — staerkeste signatur der er BAADE distinguishable OG stabil (|effekt|/SE)")
    emit("=" * 78)
    ranked = sorted([r for r in results if r["best"]], key=lambda r: -r["best"]["score"])
    none_sig = [r for r in results if not r["best"]]
    if ranked:
        emit(f"  {'instrument':<10} {'signatur':<14} {'scope':<10} {'effekt':>9} {'±SE':>8} {'score':>6} {'datakvalitet'}")
        for r in ranked:
            b = r["best"]
            dq = (f"vol={r['med_vol']:.0f}" if r["kind"] == "futures" else "FX") + (" THIN" if r["thin"] else "")
            emit(f"  {r['label']:<10} {b['type']:<14} {b['scope']:<10} {b['effect']:>+9.4f} "
                 f"{b['se']:>8.4f} {b['score']:>6.1f} {dq}")
    else:
        emit("  (ingen instrument har en distinguishable+stabil signatur)")
    if none_sig:
        emit("")
        emit("  Ingen signatur (under stoej eller ustabil): " + ", ".join(r["label"] for r in none_sig))

    emit("")
    emit("  NB: rangeringen er en SCREEN, ikke en dom. Toppen gaar videre til en DESIGNET regel")
    emit("      backtestet paa FRISK OOS MED kost — hvor signalet skal GENTAGE. Et multiple-")
    emit("      comparison-heldigt signal gentager ikke; det er det egentlige vaern. Sweepet nominerer.")
    emit("      Kost-kontekst: FX <1 bp rundtur · OSE/HKFE/SGX-mini ~5 bp i hjemme-timer.")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"  Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

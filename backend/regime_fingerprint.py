#!/usr/bin/env python3
"""
regime_fingerprint.py — Regime-fingeraftryk (Trin 0).
════════════════════════════════════════════════════════════════════════════════════════
BESKRIVENDE, INGEN HANDLER, INGEN EDGE-PAASTAND. Rent offline: kun stdlib, laeser eksisterende
cache (bar_cache/, data_harvest/, historical_universe_midcap_*.json). Ingen IBKR, ingen netvaerk.

Formaal: maal det regime vi rent faktisk staar i (sidste ~30 handelsdage) + sammenlign mod en
tidligere periode (er regimet SKIFTET?), og udled en PRAE-REGISTRERET verdikt der peger paa en
strategifamilie (A=spread/relativ vaerdi, B=halt-resumption, C=tids/flow-overnight). Mapping
tal->familie staar i summary.txt FOER output fortolkes (afsnit 5 i spec), saa intet er post-hoc.

Vinduer: recent (~sidste 30 handelsdage), prior (~30 foer), apr (2026-04), maj (2026-05).
Look-ahead-assert pr. metrik. Coverage rapporteres altid. Manglende kilde -> degrader paent.

Koersel (fra backend/):
    python regime_fingerprint.py                 # alle vinduer, default
    python regime_fingerprint.py --window recent # kun seneste ~30 dage
    python regime_fingerprint.py --pair ES,RTY   # kun ett spread-par

Output: regime_fingerprint_output/summary.txt + fingerprint_{RUNDATE}.json
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import statistics as st
import sys
from datetime import datetime, date, time as dtime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
BAR_CACHE = BACKEND / "bar_cache"
HARVEST = BACKEND / "data_harvest"
OUT_DIR = BACKEND / "regime_fingerprint_output"
RTH_START, RTH_END = dtime(9, 30), dtime(16, 0)
FUT = ["ES", "NQ", "RTY"]
DEFAULT_PAIRS = [("ES", "RTY"), ("ES", "NQ"), ("NQ", "RTY")]
APR = (date(2026, 4, 1), date(2026, 4, 30))
MAJ = (date(2026, 5, 1), date(2026, 5, 29))
STITCHED = HARVEST / "mes_m2k_stitched"        # MES=ES, M2K=RTY, frisk 15-min (2 aar)
BAR_GAP_15MIN = 15 * 60                          # 900s; > dette = ny kontiguert run
# Sanity-gulve for spor-A-reglen (afsnit 2, betingelse 4): et signal paa faa runs taeller ikke.
SPORA_MIN_OBS = 500
SPORA_MIN_RUNS = 20

# Coverage-noter samles her og skrives i summary.
NOTES: list[str] = []


# ═══════════════════════════════════════════════════════════════════
# Stdlib-hjaelpere (Pearson, autokorr, variance-ratio, half-life)
# ═══════════════════════════════════════════════════════════════════
def pearson(xs, ys):
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def autocorr1(series):
    """Lag-1 autokorr = Pearson(series[:-1], series[1:])."""
    if len(series) < 4:
        return None
    return pearson(series[:-1], series[1:])


def variance_ratio(rets, k):
    """VR(k) = Var(k-periode-afkast) / (k * Var(1-periode-afkast)). <1 mean-rev, >1 trend."""
    n = len(rets)
    if n < k * 3 + 2 or k < 2:
        return None
    var1 = st.pvariance(rets)
    if var1 <= 0:
        return None
    kagg = [sum(rets[i:i + k]) for i in range(0, n - k + 1)]
    if len(kagg) < 3:
        return None
    vark = st.pvariance(kagg)
    return vark / (k * var1)


def half_life(level_series):
    """Half-life af mean-reversion via OLS: d_t = alpha + beta*level_{t-1}.
    half_life = -ln(2)/ln(1+beta), gyldig kun for -1<beta<0 (revererende)."""
    if len(level_series) < 20:
        return None
    x = level_series[:-1]
    d = [level_series[i + 1] - level_series[i] for i in range(len(level_series) - 1)]
    n = len(x)
    mx, md = sum(x) / n, sum(d) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0:
        return None
    beta = sum((xi - mx) * (di - md) for xi, di in zip(x, d)) / sxx
    if beta >= 0 or beta <= -1:
        return None
    return -math.log(2) / math.log(1 + beta)


def daily_returns(closes):
    return [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes)) if closes[i - 1] > 0]


def contiguous_runs(bars, max_gap_sec):
    """Split intraday-bars i runs der ALDRIG krydser en tids-gap > max_gap_sec
    (frokost/overnight). bars: liste af (dt, o,h,l,c,v) sorteret."""
    runs, cur = [], []
    for b in bars:
        if cur and (b[0] - cur[-1][0]).total_seconds() > max_gap_sec:
            runs.append(cur); cur = []
        cur.append(b)
    if cur:
        runs.append(cur)
    return runs


# ═══════════════════════════════════════════════════════════════════
# Loaders (ren stdlib)
# ═══════════════════════════════════════════════════════════════════
def _num(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def load_futures_daily(label):
    """[(date, o,h,l,c,v)] sorteret. timestamp = 'YYYY-MM-DD'."""
    p = HARVEST / f"{label}_1day.csv"
    out = []
    if not p.exists():
        return out
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                d = date.fromisoformat(r["timestamp"][:10])
                o, h, l, c = (_num(r[k]) for k in ("open", "high", "low", "close"))
                if None in (o, h, l, c):
                    continue
                out.append((d, o, h, l, c, _num(r.get("volume")) or 0.0))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda t: t[0])


def load_futures_15min(label):
    """[(dt, o,h,l,c,v)] tz-aware ET, sorteret. timestamp = ISO m. offset."""
    p = HARVEST / f"{label}_15min.csv"
    out = []
    if not p.exists():
        return out
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(r["timestamp"])
                o, h, l, c = (_num(r[k]) for k in ("open", "high", "low", "close"))
                if None in (o, h, l, c):
                    continue
                out.append((dt, o, h, l, c, _num(r.get("volume")) or 0.0))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda t: t[0])


def load_universe():
    """date-str -> set(tickers), merget fra historical_universe_midcap_*.json."""
    uni = {}
    files = sorted(glob.glob(str(BACKEND / "historical_universe_midcap_*.json")))
    if not files:
        NOTES.append("universe: INGEN historical_universe_midcap_*.json fundet -> small-cap-metrikker udeladt.")
    for fp in files:
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        for k, v in d.items():
            uni.setdefault(k, set()).update(v)
    return uni


def _rth(bars):
    return [b for b in bars if RTH_START <= b[0].timetz().replace(tzinfo=None) < RTH_END]


def load_smallcap_1min():
    """ticker -> {date -> [ (dt,o,h,l,c,v) RTH-sorteret ]}. Deduper paa ts."""
    by_tk = {}
    for fp in glob.glob(str(BAR_CACHE / "*_1min.csv")):
        name = Path(fp).name
        tk = name.split("_", 1)[0]
        try:
            with open(fp, newline="") as f:
                for r in csv.DictReader(f):
                    try:
                        dt = datetime.fromisoformat(r["timestamp"])
                    except (ValueError, KeyError):
                        continue
                    t = dt.timetz().replace(tzinfo=None)
                    if not (RTH_START <= t < RTH_END):
                        continue
                    o, h, l, c = (_num(r[k]) for k in ("open", "high", "low", "close"))
                    if None in (o, h, l, c):
                        continue
                    by_tk.setdefault(tk, {}).setdefault(dt.date(), {})[dt] = \
                        (dt, o, h, l, c, _num(r.get("volume")) or 0.0)
        except OSError:
            continue
    # dict-per-dag -> sorteret liste
    out = {}
    for tk, days in by_tk.items():
        out[tk] = {d: [days[d][k] for k in sorted(days[d])] for d in days}
    return out


# ═══════════════════════════════════════════════════════════════════
# Vinduer
# ═══════════════════════════════════════════════════════════════════
def make_windows(sorted_dates):
    """recent/prior fra de faktiske handelsdage; apr/maj faste kalender-spaend."""
    win = {}
    ds = sorted(set(sorted_dates))
    win["recent"] = (ds[-30], ds[-1]) if len(ds) >= 1 else None
    win["prior"] = (ds[-60], ds[-31]) if len(ds) >= 31 else None
    win["apr"], win["maj"] = APR, MAJ
    return win


def in_win(d, win):
    return win is not None and win[0] <= d <= win[1]


# ═══════════════════════════════════════════════════════════════════
# METRIKKER — small-cap lag
# ═══════════════════════════════════════════════════════════════════
def _time_of(dt):
    return dt.timetz().replace(tzinfo=None)


def _nearest_bar(day_bars, target):
    cands = [b for b in day_bars if _time_of(b[0]) <= target]
    return cands[-1] if cands else (day_bars[0] if day_bars else None)


def smallcap_metrics(sc, uni, win_start, win_end):
    """Metrik 1-5 for small-cap i [win_start, win_end]. Returnerer dict + coverage."""
    gaps_ft, fade_depths = [], []           # 1
    ret5_pairs_x, ret5_pairs_y = [], []     # 2
    atr5_pcts, day_ranges = [], []          # 3
    atr5_first, atr5_last = [], []          # 3 ekspansion (foerste/sidste tredjedel)
    hod_bins = {"0930-1000": 0, "1000-1100": 0, "1100-1400": 0, "1400-1600": 0}  # 4
    green_by_day, ret_by_day = {}, {}       # 5
    n_names, n_daypoints = set(), 0
    max_date_seen = None

    days_in_win = sorted(d for d in uni if win_start <= date.fromisoformat(d) <= win_end)
    for dstr in days_in_win:
        d = date.fromisoformat(dstr)
        for tk in uni[dstr]:
            days = sc.get(tk)
            if not days or d not in days:
                continue
            db = days[d]
            if len(db) < 10:
                continue
            n_names.add(tk); n_daypoints += 1
            max_date_seen = d if (max_date_seen is None or d > max_date_seen) else max_date_seen
            o0930 = db[0][1]
            b1200 = _nearest_bar(db, dtime(12, 0))
            dhigh = max(b[2] for b in db); dlow = min(b[3] for b in db)
            dclose = db[-1][4]
            # 1. gap follow-through (kraever prior-dags close)
            prior = [pd for pd in sorted(days) if pd < d]
            if prior and days[prior[-1]]:
                pclose = days[prior[-1]][-1][4]
                if pclose > 0 and o0930 > 0 and b1200:
                    gap = (o0930 - pclose) / pclose
                    move = (b1200[4] - o0930) / o0930
                    if abs(gap) > 0.005:            # kun reelle gaps (>0.5%)
                        ft = (gap > 0) == (move > 0)
                        gaps_ft.append(1 if ft else 0)
                        if not ft:
                            fade_depths.append(abs(move))
            # 3. daglig range
            if dlow > 0:
                day_ranges.append((dhigh - dlow) / dlow * 100.0)
            # 4. HOD-timing
            hb = max(db, key=lambda b: b[2]); ht = _time_of(hb[0])
            if ht < dtime(10, 0): hod_bins["0930-1000"] += 1
            elif ht < dtime(11, 0): hod_bins["1000-1100"] += 1
            elif ht < dtime(14, 0): hod_bins["1100-1400"] += 1
            else: hod_bins["1400-1600"] += 1
            # 5. breadth
            if o0930 > 0:
                r = (dclose - o0930) / o0930
                green_by_day.setdefault(dstr, []).append(1 if dclose > o0930 else 0)
                ret_by_day.setdefault(dstr, []).append(r)
            # 2 + 3b. 5-min afkast + ATR% pr. contiguous run
            runs = contiguous_runs(db, 60)     # 1-min -> gap>60s = ny run
            for run in runs:
                # resample 1-min -> 5-min buckets inden for run
                five = _resample_5min(run)
                closes5 = [b[4] for b in five]
                r5 = [(closes5[i] / closes5[i - 1] - 1.0) for i in range(1, len(closes5)) if closes5[i - 1] > 0]
                for i in range(len(r5) - 1):
                    ret5_pairs_x.append(r5[i]); ret5_pairs_y.append(r5[i + 1])
                for b in five:
                    if b[4] > 0:
                        atr5_pcts.append((b[2] - b[3]) / b[4] * 100.0)
            # ekspansion: foerste vs sidste tredjedel af dagens 5-min ATR%
            five_all = _resample_5min(db)
            if len(five_all) >= 6:
                thirds = len(five_all) // 3
                fa = [(b[2] - b[3]) / b[4] * 100.0 for b in five_all[:thirds] if b[4] > 0]
                la = [(b[2] - b[3]) / b[4] * 100.0 for b in five_all[-thirds:] if b[4] > 0]
                if fa: atr5_first.append(st.median(fa))
                if la: atr5_last.append(st.median(la))

    if max_date_seen is not None:
        assert max_date_seen <= win_end, f"LOOK-AHEAD small-cap: {max_date_seen} > {win_end}"

    ac2 = pearson(ret5_pairs_x, ret5_pairs_y)
    ftr = (sum(gaps_ft) / len(gaps_ft)) if gaps_ft else None
    disp = st.mean([st.pstdev(v) for v in ret_by_day.values() if len(v) >= 3]) if ret_by_day else None
    breadth = st.mean([sum(v) / len(v) for v in green_by_day.values()]) if green_by_day else None
    hod_tot = sum(hod_bins.values()) or 1
    return {
        "coverage": {"names": len(n_names), "day_points": n_daypoints, "days": len(days_in_win)},
        "m1_gap_follow_through_rate": _r(ftr), "m1_median_fade_depth_pct": _r(st.median(fade_depths) * 100 if fade_depths else None),
        "m1_gap_events": len(gaps_ft),
        "m2_intraday_autocorr_5min": _r(ac2), "m2_pairs": len(ret5_pairs_x),
        "m3_median_5min_atr_pct": _r(st.median(atr5_pcts) if atr5_pcts else None),
        "m3_median_daily_range_pct": _r(st.median(day_ranges) if day_ranges else None),
        "m3_atr_expansion_ratio": _r((st.median(atr5_last) / st.median(atr5_first)) if (atr5_first and atr5_last and st.median(atr5_first) > 0) else None),
        "m4_hod_bins_pct": {k: round(v / hod_tot * 100, 1) for k, v in hod_bins.items()},
        "m4_hod_morning_dominated": (hod_bins["0930-1000"] + hod_bins["1000-1100"]) / hod_tot > 0.5,
        "m5_breadth_pct_green": _r(breadth * 100 if breadth is not None else None),
        "m5_name_dispersion_pct": _r(disp * 100 if disp is not None else None),
        "m6_halt_frequency": None,   # ingen halt-log -> udeladt
    }


def _resample_5min(bars):
    """1-min bars -> 5-min buckets (ankret paa hele 5-min). bars sorteret, samme dag/run."""
    buckets = {}
    for b in bars:
        dt = b[0]
        key = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
        buckets.setdefault(key, []).append(b)
    out = []
    for key in sorted(buckets):
        g = buckets[key]
        out.append((key, g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], sum(x[5] for x in g)))
    return out


# ═══════════════════════════════════════════════════════════════════
# METRIKKER — index/futures lag
# ═══════════════════════════════════════════════════════════════════
def futures_daily_metrics(daily, win_start, win_end):
    """Metrik 7,8,9 pr. index for daglige bars i vinduet."""
    rows = [b for b in daily if win_start <= b[0] <= win_end]
    if len(rows) < 5:
        return {"coverage_days": len(rows)}
    assert max(b[0] for b in rows) <= win_end, "LOOK-AHEAD futures-daily"
    closes = [b[4] for b in rows]
    rets = daily_returns(closes)
    # 7. trend-persistens
    ac = autocorr1(rets)
    cont = []          # dage der lukker samme retning som de aabnede (intraday fortsaetter overnight-gap)
    over_sum, intra_sum = 0.0, 0.0
    for i in range(1, len(rows)):
        pc = rows[i - 1][4]; o, c = rows[i][1], rows[i][4]
        if pc <= 0:
            continue
        over = (o - pc) / pc; intra = (c - o) / o if o > 0 else 0.0
        over_sum += over; intra_sum += intra
        if abs(over) > 1e-9 and abs(intra) > 1e-9:
            cont.append(1 if (over > 0) == (intra > 0) else 0)
    # 9. realiseret vol kort/lang
    def rvol(k):
        r = rets[-k:] if len(rets) >= k else rets
        return st.pstdev(r) * math.sqrt(252) if len(r) >= 3 else None
    rv_s, rv_l = rvol(10), rvol(40)
    return {
        "coverage_days": len(rows),
        "m7_daily_autocorr": _r(ac),
        "m7_continuation_rate": _r(sum(cont) / len(cont) if cont else None),
        "m8_overnight_sum_pct": _r(over_sum * 100), "m8_intraday_sum_pct": _r(intra_sum * 100),
        "m8_overnight_intraday_ratio": _r(abs(over_sum) / abs(intra_sum) if abs(intra_sum) > 1e-9 else None),
        "m9_rvol_short_10d": _r(rv_s), "m9_rvol_long_40d": _r(rv_l),
        "m9_term_ratio_short_long": _r(rv_s / rv_l if (rv_s and rv_l and rv_l > 0) else None),
    }


def spread_metrics(a_daily, b_daily, win_start, win_end):
    """Metrik 10: spread = log(A) - log(B), lag-1 autokorr + VR(2,5,10) + half-life. Daglig."""
    da = {b[0]: b[4] for b in a_daily}
    db = {b[0]: b[4] for b in b_daily}
    dates = sorted(d for d in (set(da) & set(db)) if win_start <= d <= win_end)
    if len(dates) < 12:
        return {"coverage_days": len(dates)}
    assert max(dates) <= win_end, "LOOK-AHEAD spread"
    spread = [math.log(da[d]) - math.log(db[d]) for d in dates if da[d] > 0 and db[d] > 0]
    dspread = [spread[i + 1] - spread[i] for i in range(len(spread) - 1)]
    return {
        "coverage_days": len(dates),
        "m10_spread_autocorr1": _r(autocorr1(spread)),
        "m10_VR2": _r(variance_ratio(dspread, 2)),
        "m10_VR5": _r(variance_ratio(dspread, 5)),
        "m10_VR10": _r(variance_ratio(dspread, 10)),
        "m10_half_life_bars": _r(half_life(spread)),
    }


def _r(x, nd=4):
    return round(x, nd) if isinstance(x, (int, float)) else None


# ═══════════════════════════════════════════════════════════════════
# METRIK 10b — 15-min intradag MES-M2K spread (=ES-RTY; addendum til afsnit 3.10)
# ═══════════════════════════════════════════════════════════════════
def load_stitched_15min(label):
    """[(dt, o,h,l,c,v)] tz-aware ET fra data_harvest/mes_m2k_stitched/{label}_15min.csv."""
    p = STITCHED / f"{label}_15min.csv"
    out = []
    if not p.exists():
        return out
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(r["timestamp"])
                c = _num(r["close"])
                if c is None or c <= 0:
                    continue
                out.append((dt, c))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda t: t[0])


def _spread_runs(mes, m2k, win_start, win_end):
    """spread = log(MES_close) - log(M2K_close) paa FAELLES timestamps i vinduet, split i
    kontiguerte 15-min runs (aldrig paa tvaers af session-gap/roll/overnight). Returnerer
    liste af runs; hver run = liste af (dt, spread)."""
    m = {dt: c for dt, c in mes}
    k = {dt: c for dt, c in m2k}
    common = sorted(set(m) & set(k))
    pts = [(dt, math.log(m[dt]) - math.log(k[dt])) for dt in common
           if win_start <= dt.date() <= win_end]
    runs, cur = [], []
    for dt, s in pts:
        if cur and (dt - cur[-1][0]).total_seconds() > BAR_GAP_15MIN:
            runs.append(cur); cur = []
        cur.append((dt, s))
    if cur:
        runs.append(cur)
    return runs


def variance_ratio_runs(run_deltas, k):
    """VR(k) run-bevidst: k-aggregaterne dannes KUN inden for runs (aldrig paa tvaers af seams)."""
    all1 = [d for r in run_deltas for d in r]
    if len(all1) < k * 3 + 2 or k < 2:
        return None
    var1 = st.pvariance(all1)
    if var1 <= 0:
        return None
    kagg = [sum(r[i:i + k]) for r in run_deltas for i in range(0, len(r) - k + 1)]
    if len(kagg) < 3:
        return None
    return st.pvariance(kagg) / (k * var1)


def half_life_ols(xs, ds):
    """half-life via OLS af delta paa niveau_{t-1} (pooled, run-bevidste par). None hvis ej revererende."""
    n = len(xs)
    if n < 20:
        return None
    mx, md = sum(xs) / n, sum(ds) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    beta = sum((x - mx) * (d - md) for x, d in zip(xs, ds)) / sxx
    if beta >= 0 or beta <= -1:
        return None
    return -math.log(2) / math.log(1 + beta)


def spread_intraday_metrics(mes, m2k, win_start, win_end):
    """Metrik 10b: 15-min intradag MES-M2K spread. Kontiguerte runs; VR2/5/10 + half-life +
    autokorr (niveau og delta) + N. Look-ahead-assert. Alt inden for [win_start, win_end]."""
    runs = _spread_runs(mes, m2k, win_start, win_end)
    runs = [r for r in runs if len(r) >= 3]
    n_obs = sum(len(r) for r in runs)
    if not runs or n_obs < 10:
        return {"n_runs": len(runs), "n_obs": n_obs}
    assert max(dt for r in runs for dt, _ in r).date() <= win_end, "LOOK-AHEAD 15-min spread"
    lvl_x, lvl_y, dlt_x, dlt_y, hl_x, hl_d = [], [], [], [], [], []
    run_deltas = []
    for r in runs:
        lv = [s for _, s in r]
        dl = [lv[i + 1] - lv[i] for i in range(len(lv) - 1)]
        run_deltas.append(dl)
        for i in range(len(lv) - 1):
            lvl_x.append(lv[i]); lvl_y.append(lv[i + 1])
            hl_x.append(lv[i]); hl_d.append(dl[i])
        for i in range(len(dl) - 1):
            dlt_x.append(dl[i]); dlt_y.append(dl[i + 1])
    hl_bars = half_life_ols(hl_x, hl_d)
    return {
        "n_runs": len(runs), "n_obs": n_obs,
        "autocorr1_level": _r(pearson(lvl_x, lvl_y)),
        "autocorr1_delta": _r(pearson(dlt_x, dlt_y)),
        "VR2": _r(variance_ratio_runs(run_deltas, 2)),
        "VR5": _r(variance_ratio_runs(run_deltas, 5)),
        "VR10": _r(variance_ratio_runs(run_deltas, 10)),
        "half_life_bars": _r(hl_bars),
        "half_life_min": _r(hl_bars * 15 if hl_bars is not None else None, 1),
    }


# ═══════════════════════════════════════════════════════════════════
# PRAE-REGISTRERET verdikt (afsnit 5) — mapping tal -> familie
# ═══════════════════════════════════════════════════════════════════
VERDICT_TABLE = [
    "| Observation (recent) | Familie |",
    "|---|---|",
    "| follow_through>0.55 OG intraday_autocorr>+0.05 OG HOD morgen-domineret | Momentum-fortsaettelse (HAR: K2/TrendJoin) |",
    "| intraday_autocorr<-0.05 OG follow_through<0.45 | Intraday mean-reversion (HAR: BuyTheDip/washout) |",
    "| ES-RTY DAGLIG spread VR5=1.22>0.9 | Daglig-hold spread DISKVALIFICERET (staar) — spor A vurderes KUN paa 15-min (se regel nedenfor) |",
    "| index overnight/intraday-ratio>~1.5 | SPOR C (tids/flow, overnight-edge) |",
    "| halt-frekvens hoej+stigende | SPOR B (halt-resumption) har raamateriale |",
    "| hoej navne-dispersion + lav index-trend-persistens | Relativ vaerdi / stock-picking generelt |",
]

# PRAEREGISTRERET spor-A regel (addendum afsnit 2) — LAAST FOER 15-min-tallene beregnes/ses.
# Spor A rejses (og KUN som "vaerd at backteste") HVIS OG KUN HVIS alle 5 holder i recent-vinduet
# paa mes_m2k_stitched 15-min (MES=ES, M2K=RTY), inden for kontiguerte runs:
SPORA_RULE = [
    "PRAEREGISTRERET SPOR-A REGEL (15-min intradag MES-M2K spread; alle 5 skal holde i recent):",
    "  1. VR(5) < 0.9 paa delta_spread            (primaer mean-reversion-test)",
    "  2. half-life < 10 bars (=150 min)          (reverterer fuldt inden for en session)",
    "  3. VR(10) < 1.0                            (konsistens paa tvaers af horisonter; anti-fluke)",
    f"  4. N: n_obs >= {SPORA_MIN_OBS} OG n_runs >= {SPORA_MIN_RUNS}          (sanity-gate; faa runs taeller ikke)",
    "  5. signaturen (VR5<0.9 & half-life<10) GENTAGER i MINDST ETT uafhaengigt vindue (prior/IS/OOS)",
    "  Ellers: SPOR A FORBLIVER LUKKET; vi gaar videre med spor D (tvaersnitlig relativ styrke).",
    "  Rejst = kun 'vaerd at backteste'. Fingeraftrykket paastaar INGEN edge.",
]


def _sporA_signature(m):
    """Betingelse 5-hjaelper: VR5<0.9 & half-life<10 i et givent vindue."""
    if not m:
        return False
    vr5, hl = m.get("VR5"), m.get("half_life_bars")
    return vr5 is not None and hl is not None and vr5 < 0.9 and hl < 10


def verdict(recent, spread15):
    """recent: recent-vinduets metrik-dict. spread15: dict vindue->15-min-spread-metrik.
    Returnerer liste af flaggede familier. Spor A afgoeres af den PRAEREGISTREREDE 5-delte regel."""
    flags = []
    sc = recent.get("smallcap", {})
    ft = sc.get("m1_gap_follow_through_rate")
    ac = sc.get("m2_intraday_autocorr_5min")
    morn = sc.get("m4_hod_morning_dominated")
    disp = sc.get("m5_name_dispersion_pct")
    # momentum-fortsaettelse
    if ft is not None and ac is not None and ft > 0.55 and ac > 0.05 and morn:
        flags.append("Momentum-fortsaettelse (kun relevant hvis K2/TrendJoin svigter)")
    # intraday mean-reversion
    if ft is not None and ac is not None and ac < -0.05 and ft < 0.45:
        flags.append("Intraday mean-reversion (HAR allerede BuyTheDip/washout)")
    # ── SPOR A — PRAEREGISTRERET 5-delt regel paa 15-min intradag MES-M2K spread ──
    rm = (spread15 or {}).get("recent", {})
    c1 = rm.get("VR5") is not None and rm["VR5"] < 0.9
    c2 = rm.get("half_life_bars") is not None and rm["half_life_bars"] < 10
    c3 = rm.get("VR10") is not None and rm["VR10"] < 1.0
    c4 = (rm.get("n_obs", 0) >= SPORA_MIN_OBS) and (rm.get("n_runs", 0) >= SPORA_MIN_RUNS)
    c5 = any(_sporA_signature((spread15 or {}).get(w)) for w in ("prior", "IS", "OOS"))
    if c1 and c2 and c3 and c4 and c5:
        flags.append(f"SPOR A REJST (15-min intradag MES-M2K spread) — VAERD AT BACKTESTE "
                     f"(VR5={rm['VR5']}, half-life={rm['half_life_bars']} bars, VR10={rm['VR10']}, "
                     f"n_obs={rm['n_obs']}, gentages OOS)")
    else:
        miss = [n for n, ok in (("VR5<0.9", c1), ("hl<10", c2), ("VR10<1.0", c3),
                                ("N-gate", c4), ("gentag-OOS", c5)) if not ok]
        flags.append("SPOR A FORBLIVER LUKKET (15-min-regel ikke opfyldt: " + ", ".join(miss) +
                     ") -> gaa videre med spor D (tvaersnitlig relativ styrke)")
    # SPOR C — overnight-dominans (mindst ett index)
    c_flags = []
    for idx, fm in recent.get("futures", {}).items():
        r = fm.get("m8_overnight_intraday_ratio")
        if r is not None and r > 1.5:
            c_flags.append(f"{idx}={r}")
    if c_flags:
        flags.append("SPOR C (tids/flow, overnight-edge) — index m. overnight-dominans: " + ", ".join(c_flags))
    # SPOR B — halt (ingen data)
    # generel relativ vaerdi
    persist = [fm.get("m7_daily_autocorr") for fm in recent.get("futures", {}).values() if fm.get("m7_daily_autocorr") is not None]
    if disp is not None and persist and disp > 3.0 and st.mean(persist) < 0.05:
        flags.append("Relativ vaerdi / stock-picking generelt (hoej dispersion + lav index-trend-persistens)")
    return flags


# ═══════════════════════════════════════════════════════════════════
# Menneskeligt regime-briefing (ren praesentation af de samme tal — INGEN ny maaling)
# ═══════════════════════════════════════════════════════════════════
def _primary_regime(block) -> str:
    """Kort regime-etiket for ETT vindue (samme taerskler som verdict). Prioriteret:
    stock-picking > momentum > mean-reversion > blandet. Bruges til skift-detektion + historik."""
    if not isinstance(block, dict):
        return "ukendt"
    sc = block.get("smallcap", {})
    ft, ac = sc.get("m1_gap_follow_through_rate"), sc.get("m2_intraday_autocorr_5min")
    disp, morn = sc.get("m5_name_dispersion_pct"), sc.get("m4_hod_morning_dominated")
    persist = [fm.get("m7_daily_autocorr") for fm in block.get("futures", {}).values()
               if fm.get("m7_daily_autocorr") is not None]
    mp = st.mean(persist) if persist else None
    if disp is not None and mp is not None and disp > 3.0 and mp < 0.05:
        return "Stock-picking (relativ vaerdi)"
    if ft is not None and ac is not None and ft > 0.55 and ac > 0.05 and morn:
        return "Momentum-fortsaettelse"
    if ft is not None and ac is not None and ac < -0.05 and ft < 0.45:
        return "Intraday mean-reversion"
    return "Blandet / uklart"


def _mean_persist(block):
    p = [fm.get("m7_daily_autocorr") for fm in block.get("futures", {}).values()
         if fm.get("m7_daily_autocorr") is not None] if isinstance(block, dict) else []
    return st.mean(p) if p else None


def _band(x, hi, lo, labels):
    if x is None:
        return "ukendt"
    return labels[0] if x >= hi else (labels[2] if x < lo else labels[1])


def _dir(new, old, eps):
    if new is None or old is None:
        return "ukendt", ""
    d = new - old
    if abs(d) < eps:
        return "uaendret", ""
    return ("stigende" if d > 0 else "faldende"), f" ({old:+.2f} -> {new:+.2f})"


HISTORY_FIELDS = ["run_date", "span_end", "regime", "dispersion_pct", "follow_through",
                  "intraday_autocorr", "hod_morning", "index_trend_persist",
                  "es_rty_vr5_15min", "es_rty_halflife_bars_15min"]


def _persist_regime_history(out_dir, run_date, result, label):
    """Upsert dagens regime-fingeraftryk til en AKKUMULERENDE CSV, saa skift over uger bliver
    synligt (og en fremtidig meta-strategi kan laese trenden). Idempotent pr. run_date."""
    rec = result["windows"].get("recent", {})
    sc = rec.get("smallcap", {}) if isinstance(rec, dict) else {}
    rm = (result.get("intraday_spread_MES_M2K") or {}).get("recent", {})
    row = {
        "run_date": run_date,
        "span_end": (rec.get("span") or ["", ""])[1] if isinstance(rec, dict) else "",
        "regime": label,
        "dispersion_pct": sc.get("m5_name_dispersion_pct"),
        "follow_through": sc.get("m1_gap_follow_through_rate"),
        "intraday_autocorr": sc.get("m2_intraday_autocorr_5min"),
        "hod_morning": int(bool(sc.get("m4_hod_morning_dominated"))),
        "index_trend_persist": _r(_mean_persist(rec)) if isinstance(rec, dict) else None,
        "es_rty_vr5_15min": rm.get("VR5"),
        "es_rty_halflife_bars_15min": rm.get("half_life_bars"),
    }
    path = out_dir / "regime_history.csv"
    rows = []
    if path.exists():
        try:
            with path.open(newline="", encoding="utf-8") as f:
                rows = [r for r in csv.DictReader(f) if r.get("run_date") != run_date]
        except Exception:
            rows = []
    rows.append({k: ("" if row.get(k) is None else row.get(k)) for k in HISTORY_FIELDS})
    rows.sort(key=lambda r: str(r.get("run_date")))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def build_briefing(result, run_date, label, history_rows):
    """Menneskelig oversaettelse af fingeraftrykket: hvad markedet goer, hvilken strategifamilie
    det favoriserer, og OM regimet har skiftet (nu vs sidste maaned + historik). Beskrivende."""
    L = []
    def e(s=""):
        L.append(s)

    rec = result["windows"].get("recent", {})
    pri = result["windows"].get("prior", {})
    sc = rec.get("smallcap", {}) if isinstance(rec, dict) else {}
    span = (rec.get("span") if isinstance(rec, dict) else None) or ["?", "?"]

    ft = sc.get("m1_gap_follow_through_rate")
    ac = sc.get("m2_intraday_autocorr_5min")
    disp = sc.get("m5_name_dispersion_pct")
    morn = sc.get("m4_hod_morning_dominated")
    green = sc.get("m5_breadth_pct_green")
    mp = _mean_persist(rec)

    e("=" * 84)
    e(f"  REGIME-BRIEFING (menneskelig oversaettelse)   —   koersel {run_date}")
    e("  Beskrivende. Ingen handler, ingen edge-paastand. Bygger paa summary.txt' tal.")
    e("=" * 84)
    e("")
    e(f"NUVAERENDE REGIME:  {label}")
    if label.startswith("Stock-picking"):
        e("  Et stock-picker-marked: de RIGTIGE navne loeber, de forkerte goer ikke, og der er")
        e("  et bredt spaend imellem — men indekset som helhed har ingen paalidelig retning.")
        e("  Fordelen ligger i at VAELGE MELLEM navne, ikke i at ride markedet.")
    elif label.startswith("Momentum"):
        e("  Momentum-marked: det der er staerkt fortsaetter, morgenretningen holder, og")
        e("  bevaegelsen er koncentreret tidligt paa dagen.")
    elif label.startswith("Intraday mean"):
        e("  Mean-reversion-marked: bevaegelser overdriver og traekkes tilbage inden for dagen")
        e("  (choppy), og morgenretningen vender oftere end den holder.")
    else:
        e("  Blandet billede — ingen enkelt familie dominerer tydeligt lige nu.")
    e("")

    e(f"HVAD MARKEDET GOER LIGE NU  (seneste ~30 handelsdage: {span[0]} .. {span[1]})")
    dband = _band(disp, 3.8, 3.0, ("stor", "moderat", "lille"))
    e(f"  - Spredning mellem aktier : {dband.upper():8} ({disp}% dagligt) — hvor uafhaengigt navnene bevaeger sig")
    if mp is not None:
        trend = "ingen paalidelig retning" if mp < 0.05 else "en vis trend"
        e(f"  - Retning i indekset      : {trend} (trend-persistens {mp:+.2f}; ~0 = ingen)")
    if ft is not None:
        hold = "HOLDER (foelger igennem)" if ft > 0.55 else ("VENDER (fader)" if ft < 0.45 else "blandet")
        e(f"  - Morgenretningen         : {hold} — sker paa {ft*100:.0f}% af gap-dagene")
    if morn is not None:
        e(f"  - Hvornaar paa dagen      : {'mest i de foerste 30-60 min' if morn else 'spredt ud over dagen'}")
    if ac is not None:
        chop = "trendende" if ac > 0.05 else ("choppy (mean-rev)" if ac < -0.05 else "hverken/eller")
        e(f"  - Inden for dagen         : {chop} (5-min autokorr {ac:+.3f})")
    if green is not None:
        e(f"  - Bredde                  : {green:.0f}% af navnene groenne paa en dag")
    e("")

    e("HVILKEN STRATEGI-FAMILIE PASSER TIL DETTE REGIME:")
    fam = {
        "Stock-picking (relativ vaerdi)": "-> Relativ Styrke (tvaersnitlig rangering, spor D)",
        "Momentum-fortsaettelse":         "-> Konfluens 2 / Trend Join Long (momentum-breakout)",
        "Intraday mean-reversion":        "-> BuyTheDip (koeber dykket)",
        "Blandet / uklart":               "-> ingen klar favorit; koer bredt eller afvent",
    }
    e(f"  {fam.get(label, '-> (ingen mapping)')}")
    for fl in result.get("verdict", []):
        if fl.startswith("SPOR A") or fl.startswith("SPOR C"):
            continue
        e(f"    (fingeraftryk-flag: {fl})")
    e("")

    e("SKIFTER REGIMET?  (nu vs sidste maaned)")
    prev_label = _primary_regime(pri)
    pspan = (pri.get("span") if isinstance(pri, dict) else None) or ["?", "?"]
    e(f"  Sidste maaned ({pspan[0]}..{pspan[1]}): {prev_label}")
    e(f"  Nu           ({span[0]}..{span[1]}): {label}")
    if prev_label == label:
        e(f"  -> Regimet er STABILT — samme familie som sidste maaned.")
    else:
        e(f"  -> REGIMET HAR SKIFTET: fra \"{prev_label}\" til \"{label}\".")
    psc = pri.get("smallcap", {}) if isinstance(pri, dict) else {}
    for lbl, key, eps in (("Spredning     ", "m5_name_dispersion_pct", 0.3),
                          ("Morgen-follow ", "m1_gap_follow_through_rate", 0.05),
                          ("Intraday chop ", "m2_intraday_autocorr_5min", 0.02)):
        d, detail = _dir(sc.get(key), psc.get(key), eps)
        e(f"    - {lbl}: {d}{detail}")
    d, detail = _dir(mp, _mean_persist(pri), 0.03)
    e(f"    - Index-trend  : {d}{detail}")
    e("")

    if history_rows and len(history_rows) >= 2:
        e("HISTORIK (seneste koersler — laes skift over tid):")
        e(f"  {'dato':>11}  {'regime':<28}{'disp%':>7}{'follow':>8}{'idx-tr':>8}{'morgen':>8}")
        for r in history_rows[-8:]:
            def g(k):
                v = r.get(k, "")
                return v if v not in (None, "") else "-"
            morn_s = "ja" if str(r.get("hod_morning")) == "1" else "nej"
            e(f"  {str(g('run_date')):>11}  {str(g('regime'))[:28]:<28}"
              f"{str(g('dispersion_pct')):>7}{str(g('follow_through')):>8}"
              f"{str(g('index_trend_persist')):>8}{morn_s:>8}")
        e("  (naar 'regime'-kolonnen skifter vaerdi mellem raekker, har markedet skiftet karakter.)")
    else:
        e("HISTORIK: kun én koersel endnu — skift over tid bliver synligt naar fingeraftrykket")
        e("  har koert et par uger (det koerer automatisk hver mandag paa algoserveren).")
    e("")
    e("=" * 84)
    return L


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Regime-fingeraftryk (beskrivende, offline).")
    ap.add_argument("--window", default=None, help="kun ett vindue: recent|prior|apr|maj")
    ap.add_argument("--pair", default=None, help="kun ett spread-par, fx ES,RTY")
    a = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    run_date = date.today().isoformat() if False else _today()  # Date.now-fri (Py3.14-fix ikke noedv.)

    # ── Indlaes ──
    fut_daily = {lb: load_futures_daily(lb) for lb in FUT}
    fut_15 = {lb: load_futures_15min(lb) for lb in FUT}
    for lb in FUT:
        if not fut_daily[lb]:
            NOTES.append(f"futures {lb}|1day: ingen data.")
        if fut_15[lb]:
            newest = fut_15[lb][-1][0].date()
            if newest < date(2026, 6, 20):
                NOTES.append(f"futures {lb}|15min: nyeste bar {newest} (staar ~5+ uger) -> 1day er primaer for daglige metrikker.")
    uni = load_universe()
    NOTES.append("Small-cap-lag: {} 1min-cache-filer.".format(len(glob.glob(str(BAR_CACHE / '*_1min.csv')))))
    sc = load_smallcap_1min() if uni else {}
    NOTES.append("HALT-frekvens (metrik 6): INGEN halt-log paa disk -> UDELADT.")
    NOTES.append("VR(10) kraever ~32+ dage; 30-dags-vinduer er for korte -> VR10=n/a (VR2/VR5/half-life daekker spor A-vurderingen).")
    NOTES.append("15-min spread (afsnit 3.10): UDELADT (futures-15min staar + kortere overlap); DAGLIG spread brugt til VR/half-life.")

    pairs = DEFAULT_PAIRS
    if a.pair:
        p = tuple(x.strip().upper() for x in a.pair.split(","))
        pairs = [p] if len(p) == 2 else DEFAULT_PAIRS

    # ── Vinduer (baseret paa futures-daily datoer + universe) ──
    fut_dates = sorted(set(d for lb in FUT for d, *_ in fut_daily[lb]))
    fwin = make_windows(fut_dates)
    windows = ["recent", "prior", "apr", "maj"]
    if a.window:
        windows = [a.window]

    result = {"run_date": run_date, "windows": {}, "notes": NOTES}
    for w in windows:
        wr = fwin.get(w)
        if wr is None:
            result["windows"][w] = {"error": "vindue mangler datoer"}
            continue
        ws, we = wr
        block = {"span": [ws.isoformat(), we.isoformat()], "futures": {}, "spread": {}}
        for lb in FUT:
            if fut_daily[lb]:
                block["futures"][lb] = futures_daily_metrics(fut_daily[lb], ws, we)
        for a_lb, b_lb in pairs:
            if fut_daily.get(a_lb) and fut_daily.get(b_lb):
                block["spread"][f"{a_lb}-{b_lb}"] = spread_metrics(fut_daily[a_lb], fut_daily[b_lb], ws, we)
        if sc:
            block["smallcap"] = smallcap_metrics(sc, uni, ws, we)
        result["windows"][w] = block

    # ── 15-min intradag MES-M2K spread (addendum) — recent/prior/apr/maj + IS/OOS-split ──
    mes15, m2k15 = load_stitched_15min("MES"), load_stitched_15min("M2K")
    spread15 = {}
    if mes15 and m2k15:
        common = sorted(set(dt.date() for dt, _ in mes15) &
                        set(dt.date() for dt, _ in m2k15))
        s15win = make_windows(common)              # recent/prior/apr/maj
        if len(common) >= 4:                        # laengere IS/OOS-split (foerste vs anden halvdel)
            mid = common[len(common) // 2]
            s15win["IS"] = (common[0], mid)
            s15win["OOS"] = (common[len(common) // 2 + 1], common[-1]) if len(common) > 2 else None
        for w, wr in s15win.items():
            if wr is None:
                continue
            if a.window and a.window not in (w, "recent", "prior", "IS", "OOS"):
                continue                            # ved --window: behold ogsaa recent/prior/IS/OOS til reglen
            spread15[w] = spread_intraday_metrics(mes15, m2k15, wr[0], wr[1])
        result["intraday_spread_MES_M2K"] = spread15
    else:
        NOTES.append("15-min MES-M2K spread: mangler mes_m2k_stitched/{MES,M2K}_15min.csv -> spor-A-15min ubestemt.")

    # verdikt paa recent (spor A afgoeres af den praeregistrerede 5-delte 15-min-regel)
    rec = result["windows"].get("recent", {})
    flags = verdict(rec, spread15) if isinstance(rec, dict) and "futures" in rec else []
    result["verdict"] = flags

    # ── Menneskeligt briefing + akkumuleret historik (praesentation af samme tal) ──
    label = _primary_regime(rec) if isinstance(rec, dict) and "smallcap" in rec else "ukendt"
    result["regime_label"] = label
    history_rows = _persist_regime_history(OUT_DIR, run_date, result, label)
    briefing = build_briefing(result, run_date, label, history_rows)
    (OUT_DIR / "regime_briefing.txt").write_text("\n".join(briefing), encoding="utf-8")

    # ── Skriv output ──
    (OUT_DIR / f"fingerprint_{run_date}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    _write_summary(result, run_date, windows)
    print(f"Skrev {OUT_DIR/('fingerprint_'+run_date+'.json')} + {OUT_DIR/'summary.txt'} + regime_briefing.txt")
    print("\nVERDIKT (recent):")
    print("  " + ("\n  ".join(flags) if flags else "INGEN ny familie klart favoriseret (tving ikke en beslutning)."))
    return 0


def _today():
    # datetime.now() er tilladt her (ikke async, ikke look-ahead-kritisk; kun til filnavn).
    return datetime.now().date().isoformat()


def _fmt(v):
    if isinstance(v, bool):
        return "ja" if v else "nej"
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:+.4f}" if abs(v) < 100 else f"{v:.1f}"
    return str(v)


def _write_summary(result, run_date, windows):
    L = []
    def e(s=""): L.append(s)
    e("=" * 84)
    e("  REGIME-FINGERAFTRYK (Trin 0)  —  BESKRIVENDE, INGEN HANDLER, INGEN EDGE-PAASTAND")
    e(f"  Koersel: {run_date}   Vinduer: {', '.join(windows)}")
    e("=" * 84)
    e("\nPRAE-REGISTRERET MAPPING (tal -> familie) — skrevet FOER output fortolkes:")
    for row in VERDICT_TABLE:
        e("  " + row)
    e("")
    for row in SPORA_RULE:
        e("  " + row)
    e("\nCOVERAGE-NOTER:")
    for n in result.get("notes", []):
        e("  - " + n)
    for w in windows:
        b = result["windows"].get(w, {})
        e("\n" + "-" * 84)
        e(f"VINDUE: {w}   span={b.get('span','?')}")
        e("-" * 84)
        if "error" in b:
            e("  " + b["error"]); continue
        sc = b.get("smallcap")
        if sc:
            cov = sc["coverage"]
            e(f"  SMALL-CAP (navne={cov['names']}, dag-punkter={cov['day_points']}, dage={cov['days']}):")
            e(f"    1. gap follow-through-rate : {_fmt(sc['m1_gap_follow_through_rate'])}  (n_gaps={sc['m1_gap_events']}, median fade-dybde {_fmt(sc['m1_median_fade_depth_pct'])}%)")
            e(f"    2. intraday 5-min autokorr : {_fmt(sc['m2_intraday_autocorr_5min'])}  (n_par={sc['m2_pairs']})   [+=trend, -=chop]")
            e(f"    3. median 5-min ATR%={_fmt(sc['m3_median_5min_atr_pct'])}  daglig range%={_fmt(sc['m3_median_daily_range_pct'])}  ekspansion(sidste/foerste)={_fmt(sc['m3_atr_expansion_ratio'])}")
            e(f"    4. HOD-bins% {sc['m4_hod_bins_pct']}  morgen-domineret={_fmt(sc['m4_hod_morning_dominated'])}")
            e(f"    5. breadth %groenne={_fmt(sc['m5_breadth_pct_green'])}  navne-dispersion%={_fmt(sc['m5_name_dispersion_pct'])}")
            e(f"    6. halt-frekvens: UDELADT (ingen halt-log)")
        else:
            e("  SMALL-CAP: ingen data i vinduet.")
        e("  INDEX/FUTURES:")
        for lb, fm in b.get("futures", {}).items():
            if "m7_daily_autocorr" not in fm:
                e(f"    {lb}: kun {fm.get('coverage_days',0)} dage (for lidt)"); continue
            e(f"    {lb} (dage={fm['coverage_days']}): 7.autokorr={_fmt(fm['m7_daily_autocorr'])} cont-rate={_fmt(fm['m7_continuation_rate'])}  "
              f"8.overnight={_fmt(fm['m8_overnight_sum_pct'])}% intraday={_fmt(fm['m8_intraday_sum_pct'])}% ratio={_fmt(fm['m8_overnight_intraday_ratio'])}  "
              f"9.rvol10={_fmt(fm['m9_rvol_short_10d'])} rvol40={_fmt(fm['m9_rvol_long_40d'])} term={_fmt(fm['m9_term_ratio_short_long'])}")
        e("  SPREAD (10 — kerne for spor A):")
        for pr, sm in b.get("spread", {}).items():
            if "m10_VR5" not in sm:
                e(f"    {pr}: kun {sm.get('coverage_days',0)} dage (for lidt)"); continue
            e(f"    {pr} (dage={sm['coverage_days']}): autokorr1={_fmt(sm['m10_spread_autocorr1'])}  VR2={_fmt(sm['m10_VR2'])} VR5={_fmt(sm['m10_VR5'])} VR10={_fmt(sm['m10_VR10'])}  half-life={_fmt(sm['m10_half_life_bars'])}  [VR<1=mean-rev]")
    # ── 15-min intradag MES-M2K spread (metrik 10b, addendum) ──
    s15 = result.get("intraday_spread_MES_M2K")
    if s15:
        e("\n" + "-" * 84)
        e("15-MIN INTRADAG MES-M2K SPREAD (=ES-RTY; kontiguerte runs)  [kerne for spor-A-reglen]")
        e("-" * 84)
        e(f"  {'vindue':8}{'n_runs':>8}{'n_obs':>8}{'ac1_lvl':>9}{'VR2':>8}{'VR5':>8}{'VR10':>8}   half-life")
        for w in ("recent", "prior", "apr", "maj", "IS", "OOS"):
            m = s15.get(w)
            if not m:
                continue
            if "VR5" not in m:
                e(f"  {w:8}{m.get('n_runs',0):>8}{m.get('n_obs',0):>8}   (for faa obs)"); continue
            hl = f"{_fmt(m['half_life_bars'])}b/{_fmt(m['half_life_min'])}m"
            e(f"  {w:8}{m['n_runs']:>8}{m['n_obs']:>8}{_fmt(m['autocorr1_level']):>9}"
              f"{_fmt(m['VR2']):>8}{_fmt(m['VR5']):>8}{_fmt(m['VR10']):>8}   {hl}")
        e("  [VR<1 = mean-rev intradag. Spor A afgoeres af den PRAEREGISTREREDE 5-delte regel ovenfor.]")

    e("\n" + "=" * 84)
    e("VERDIKT (recent-vindue):")
    fl = result.get("verdict", [])
    if fl:
        for f in fl:
            e("  -> " + f)
    else:
        e("  INGEN ny familie er klart favoriseret. Tving ikke en daarlig beslutning.")
    e("=" * 84)
    e("Beskrivende fingeraftryk. Ingen handler simuleret, ingen omkostninger, ingen edge-paastand.")
    (OUT_DIR / "summary.txt").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

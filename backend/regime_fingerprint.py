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
# PRAE-REGISTRERET verdikt (afsnit 5) — mapping tal -> familie
# ═══════════════════════════════════════════════════════════════════
VERDICT_TABLE = [
    "| Observation (recent) | Familie |",
    "|---|---|",
    "| follow_through>0.55 OG intraday_autocorr>+0.05 OG HOD morgen-domineret | Momentum-fortsaettelse (HAR: K2/TrendJoin) |",
    "| intraday_autocorr<-0.05 OG follow_through<0.45 | Intraday mean-reversion (HAR: BuyTheDip/washout) |",
    "| ES-RTY spread: VR(5)<0.9 OG half_life<~10 | SPOR A (relativ vaerdi/spread) levedygtigt |",
    "| index overnight/intraday-ratio>~1.5 | SPOR C (tids/flow, overnight-edge) |",
    "| halt-frekvens hoej+stigende | SPOR B (halt-resumption) har raamateriale |",
    "| hoej navne-dispersion + lav index-trend-persistens | Relativ vaerdi / stock-picking generelt |",
]


def verdict(recent):
    """recent: samlet metrik-dict for recent-vinduet. Returnerer liste af flaggede familier."""
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
    # SPOR A — ES-RTY spread
    sp = recent.get("spread", {}).get("ES-RTY", {})
    vr5, hl = sp.get("m10_VR5"), sp.get("m10_half_life_bars")
    if vr5 is not None and hl is not None and vr5 < 0.9 and hl < 10:
        flags.append("SPOR A (relativ vaerdi / ES-RTY spread mean-reverting NU) — LEVEDYGTIGT")
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

    # verdikt paa recent
    rec = result["windows"].get("recent", {})
    flags = verdict(rec) if isinstance(rec, dict) and "futures" in rec else []
    result["verdict"] = flags

    # ── Skriv output ──
    (OUT_DIR / f"fingerprint_{run_date}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    _write_summary(result, run_date, windows)
    print(f"Skrev {OUT_DIR/('fingerprint_'+run_date+'.json')} + {OUT_DIR/'summary.txt'}")
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

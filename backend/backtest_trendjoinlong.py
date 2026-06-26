#!/usr/bin/env python3
"""
backtest_trendjoinlong.py — backtest af "Trend Join Long" (rules.json) MED kost + frisk OOS
═══════════════════════════════════════════════════════════════════════════════════════════
Gap-and-go momentum-long. Laeser reglerne fra rules.json og 5-min CSV'er (m. premarket) fra
--data-dir (default data_trendjoin/, hostet af harvest_trendjoin_5min.py). Kun backtest —
intet telegram/dashboard/scheduling.

Regel (fra rules.json), alt pr. handelsdag pr. ticker:
  DAGLIGT filter:   D2 forrige luk > SMA200(daglig)
  INTRADAG (entry-bar, 10:05-15:30 ET): D3 pris >= 3% over forrige luk (intradag-mover — matcher
                    HumbledTraders 30-min-re-scan, ikke kun open-gap) · D1 pris > forrige dags high ·
                    I1 pris > premarket-high · I2 ny HOD · I3 RVOL >= 2 (kum. RTH-vol vs 14-dages snit)
  ENTRY:  foerste bar i vinduet hvor ALLE rammer -> koeb paa bar-LUK.
  STOP:   LOD(RTH ved entry) x 0.99.  R = entry - stop.
  EXIT:   partial 1/3 ved +0.75R · breakeven ved +1.0R · derefter trail til 5m swing-low(2,2) ·
          force-close 15:51 ET (holder ALDRIG over natten).  Intrabar: STOP-FIRST (pessimistisk).
  Resultat pr. handel i R-multipel + %-afkast (positions-uafhaengigt; max 5 samtidige er en
  portefoelje-grense, ikke modelleret i R-analysen).

KOST: rundtur i bp paalagt %-afkastet (+ halv for partial-benet); sweepes. OOS: kronologisk
70/30 (reglen ALDRIG set OOS). DOM: positiv forventning (PF>1 / middel-R>0) ved realistisk
kost OG holder OOS OG nok handler. Defaults fra eureversion-aanden: ikke tunet -> konservativt.

Rent OFFLINE: pandas/numpy + stdlib. Brug (efter harvest_trendjoin_5min):
    python backtest_trendjoinlong.py
    python backtest_trendjoinlong.py --data-dir data_trendjoin --cost-bp 0,2,5,10

Placering: C:\\Projects\\trading_dash\\backend\\backtest_trendjoinlong.py
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

RTH_START_MIN = 9 * 60 + 30     # 09:30 ET
RTH_END_MIN   = 16 * 60         # 16:00 ET


def load_rules(path):
    r = json.loads(Path(path).read_text(encoding="utf-8"))
    df = r["daily_filters"]; itf = r["intraday_filters"]; tf = r["time_filter"]; ex = r["exit"]
    return {
        "min_price": r["universe_filters"]["min_price_usd"],
        "sma_len": 200,
        "gap_min": df["D3_min_gap_pct_from_prior_close"],
        "rvol_min": itf["I3_rvol_min"], "rvol_lb": itf["I3_rvol_lookback_days"],
        "entry_lo": _to_min(tf["earliest_entry_et"]), "entry_hi": _to_min(tf["latest_entry_et"]),
        "force_close": _to_min(tf["force_close_et"]),
        "stop_pct": 0.01, "partial_R": ex["partial_profit_trigger_R"],
        "partial_frac": ex["partial_profit_fraction"], "be_R": ex["breakeven_trigger_R"],
    }


def _to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def load_5m(path):
    df = pd.read_csv(path)
    if df.empty or "timestamp" not in df.columns:
        return None
    dt = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
    df = df.assign(dt=dt).dropna(subset=["dt"]).sort_values("dt")
    df = df[(df["close"] > 0) & (df["high"] > 0)]
    if len(df) < 200:
        return None
    df["date"] = df["dt"].dt.date
    df["tmin"] = df["dt"].dt.hour * 60 + df["dt"].dt.minute
    df["rth"] = (df["tmin"] >= RTH_START_MIN) & (df["tmin"] < RTH_END_MIN)
    return df


def daily_table(df):
    rth = df[df["rth"]]
    if rth.empty:
        return None
    g = rth.groupby("date")
    d = pd.DataFrame({
        "open": g.apply(lambda x: x.iloc[0]["open"]), "high": g["high"].max(),
        "low": g["low"].min(), "close": g.apply(lambda x: x.iloc[-1]["close"]),
        "vol": g["volume"].sum(),
    }).sort_index()
    d["sma"] = d["close"].rolling(200).mean()
    d["prior_high"] = d["high"].shift(1)
    d["prior_close"] = d["close"].shift(1)
    d["prior_sma"] = d["sma"].shift(1)
    return d


def swing_low_2_2(lows, i):
    """Seneste BEKRAEFTEDE 5m swing-low (2 bars hver side) ved/ foer bar i (bekraeftes ved j+2)."""
    best = None
    for j in range(2, i - 1):   # j+2 <= i -> bekraeftet
        lo = lows[j]
        if lo < lows[j - 1] and lo < lows[j - 2] and lo < lows[j + 1] and lo < lows[j + 2]:
            best = lo
    return best


def simulate_day(day_bars, prior_high, R_rules, premarket_high, prior_close):
    """day_bars: liste af dicts (tmin,o,h,l,c) for RTH-dagen, sorteret. Returnerer trade-dict eller None."""
    rk = R_rules
    n = len(day_bars)
    hod = -1.0
    cum_lo = float("inf")
    entry_idx = None
    for i in range(n):
        b = day_bars[i]
        t = b["tmin"]
        cum_lo = min(cum_lo, b["l"])
        new_hod = b["h"] > hod
        if rk["entry_lo"] <= t <= rk["entry_hi"]:
            cond = (b["c"] > prior_high and b["c"] > premarket_high and new_hod
                    and b["rvol"] >= rk["rvol_min"]
                    and (b["c"] - prior_close) / prior_close * 100 >= rk["gap_min"])
            if cond:
                entry_idx = i
                break
        hod = max(hod, b["h"])
    if entry_idx is None:
        return None

    e = day_bars[entry_idx]
    entry = e["c"]
    lod = min(b["l"] for b in day_bars[:entry_idx + 1])
    stop0 = lod * (1 - rk["stop_pct"])
    R = entry - stop0
    if R <= 0:
        return None

    lows = [b["l"] for b in day_bars]
    stop = stop0
    partial_done = False
    be_done = False
    legs = []   # (frac, exit_price)
    remaining = 1.0
    reason = "force_close"
    for i in range(entry_idx + 1, n):
        b = day_bars[i]
        # STOP-FIRST
        if b["l"] <= stop:
            legs.append((remaining, stop)); remaining = 0.0; reason = "stop"; break
        # partial target
        if not partial_done and b["h"] >= entry + rk["partial_R"] * R:
            legs.append((rk["partial_frac"], entry + rk["partial_R"] * R))
            remaining -= rk["partial_frac"]; partial_done = True
        # breakeven
        if not be_done and b["h"] >= entry + rk["be_R"] * R:
            stop = max(stop, entry); be_done = True
        # trailing efter BE
        if be_done:
            sl = swing_low_2_2(lows, i)
            if sl is not None:
                stop = max(stop, sl)
        # force-close
        if b["tmin"] >= rk["force_close"]:
            legs.append((remaining, b["c"])); remaining = 0.0; reason = "force_close"; break
    if remaining > 0:
        legs.append((remaining, day_bars[-1]["c"])); reason = "eod"

    pnl_per_share = sum(frac * (px - entry) for frac, px in legs)
    ret_pct = pnl_per_share / entry * 100
    r_mult = pnl_per_share / R
    return {"entry": entry, "R": R, "ret_pct": ret_pct, "r_mult": r_mult,
            "reason": reason, "partial": partial_done}


def build_rvol(df):
    """Pr. (date,tmin): kumulativ RTH-vol. + 14-dages snit ved samme tmin -> RVOL pr. entry."""
    rth = df[df["rth"]].copy()
    rth["cum"] = rth.groupby("date")["volume"].cumsum()
    # map: date -> dict tmin->cum
    cum = {}
    for d, grp in rth.groupby("date"):
        cum[d] = dict(zip(grp["tmin"], grp["cum"]))
    dates = sorted(cum)
    return cum, dates


def analyze_ticker(path, rules, emit):
    df = load_5m(path)
    if df is None:
        return []
    dt = daily_table(df)
    if dt is None or dt["sma"].notna().sum() < 5:
        return []
    cum, dates = build_rvol(df)
    didx = {d: k for k, d in enumerate(dates)}
    rth = df[df["rth"]]
    pm = df[~df["rth"] & (df["tmin"] < RTH_START_MIN)]
    pm_high = pm.groupby("date")["high"].max()

    trades = []
    for d, row in dt.iterrows():
        if pd.isna(row["prior_close"]) or pd.isna(row["prior_sma"]) or pd.isna(row["prior_high"]):
            continue
        if row["open"] < rules["min_price"]:
            continue
        # D2 (dagligt): forrige luk > SMA200. D3 (>=3% over forrige luk) er flyttet til
        # entry-baren -> intradag-mover, matcher 30-min-re-scanen (ikke kun open-gap).
        if not (row["prior_close"] > row["prior_sma"]):
            continue
        ph = pm_high.get(d, -1.0)            # premarket-high (mangler -> -1 = altid over)
        day = rth[rth["date"] == d]
        if len(day) < 5:
            continue
        # 14-dages snit cum-vol ved hver tmin (til RVOL)
        k = didx.get(d, 0)
        prior_dates = dates[max(0, k - rules["rvol_lb"]):k]
        bars = []
        for _, b in day.iterrows():
            t = int(b["tmin"])
            ctoday = cum[d].get(t, np.nan)
            ref = [cum[pd_].get(t) for pd_ in prior_dates if cum[pd_].get(t) is not None]
            rvol = (ctoday / (statistics.mean(ref))) if (ref and statistics.mean(ref) > 0
                                                         and not np.isnan(ctoday)) else 0.0
            bars.append({"tmin": t, "o": b["open"], "h": b["high"], "l": b["low"],
                         "c": b["close"], "rvol": rvol})
        tr = simulate_day(bars, row["prior_high"], rules, ph, row["prior_close"])
        if tr:
            tr["date"] = d
            trades.append(tr)
    return trades


def metrics(rets, cost_bp, partials):
    c = cost_bp / 10000.0
    net = []
    for r, p in zip(rets, partials):
        cost = c * (1 + (0.3333 if p else 0))    # rundtur + halv extra hvis partial-ben
        net.append(r / 100 - cost)
    n = len(net)
    if n == 0:
        return None
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    sl = sum(losses)
    pf = (sum(wins) / abs(sl)) if sl < 0 else (float("inf") if wins else 0.0)
    return dict(n=n, wr=100 * len(wins) / n, mean=statistics.mean(net) * 100,
                total=sum(net) * 100, pf=pf, worst=min(net) * 100)


def pf_s(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Backtest Trend Join Long (rules.json, kost + OOS)")
    ap.add_argument("--data-dir", default="data_trendjoin")
    ap.add_argument("--rules", default="rules.json")
    ap.add_argument("--cost-bp", default="0,2,5,10")
    a = ap.parse_args()
    costs = [int(x) for x in a.cost_bp.split(",") if x.strip()]

    rules = load_rules(a.rules)
    ddir = Path(a.data_dir) if Path(a.data_dir).is_absolute() else (Path.cwd() / a.data_dir)
    out_dir = Path.cwd() / "trendjoin_backtest_output"
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  TREND JOIN LONG — BACKTEST (rules.json; kost paalagt + kronologisk OOS)")
    emit("=" * 78)
    files = sorted(ddir.glob("*_5min.csv"))
    if not files:
        emit(f"  Ingen *_5min.csv i {ddir}. Koer harvest_trendjoin_5min.py foerst.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1
    emit(f"  Univers: {len(files)} tickers fra {ddir.name}/  ·  regel-defaults (ikke tunet)")
    emit(f"  D3 >={rules['gap_min']}% over forrige luk (intradag) · I3 RVOL>={rules['rvol_min']} · entry "
         f"{rules['entry_lo']//60:02d}:{rules['entry_lo']%60:02d}-"
         f"{rules['entry_hi']//60:02d}:{rules['entry_hi']%60:02d} ET · stop LOD-1% · "
         f"partial {int(rules['partial_frac']*100)}%@{rules['partial_R']}R · BE@{rules['be_R']}R")

    all_tr = []
    for p in files:
        try:
            tr = analyze_ticker(p, rules, emit)
        except Exception as e:
            emit(f"   FEJL {p.name}: {type(e).__name__}: {e}")
            tr = []
        for t in tr:
            t["ticker"] = p.name.split("_")[0]
        all_tr += tr
    if not all_tr:
        emit("\n  INGEN handler dannet (large caps gapper sjaeldent 3%+; udvid univers/periode "
             "eller saenk gap-taersklen for at se flere).")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 0

    all_tr.sort(key=lambda t: t["date"])
    rets = [t["ret_pct"] for t in all_tr]
    parts = [t["partial"] for t in all_tr]
    n = len(all_tr)
    cut = all_tr[int(n * 0.70)]["date"]
    mix = {}
    for t in all_tr:
        mix[t["reason"]] = mix.get(t["reason"], 0) + 1
    span = f"{all_tr[0]['date']} -> {all_tr[-1]['date']}"
    emit(f"\n  {n} handler · {span} · exit-mix: {', '.join(f'{k}={v}' for k,v in sorted(mix.items()))}")
    emit(f"  IS/OOS-skaering: {cut} (70/30 kronologisk)")

    emit(f"\n      {'kost':>6} {'n':>5} {'WR%':>5} {'middel%':>8} {'sum%':>8} {'PF':>6} {'vaerst%':>8}")
    for cb in costs:
        m = metrics(rets, cb, parts)
        emit(f"      {cb:>4}bp {m['n']:>5} {m['wr']:>5.0f} {m['mean']:>8.3f} {m['total']:>8.1f} "
             f"{pf_s(m['pf']):>6} {m['worst']:>8.2f}")

    hint = 5   # realistisk rundtur for likvide large caps (~spread + kommission)
    is_tr = [t for t in all_tr if t["date"] < cut]
    oo_tr = [t for t in all_tr if t["date"] >= cut]
    mi = metrics([t["ret_pct"] for t in is_tr], hint, [t["partial"] for t in is_tr])
    mo = metrics([t["ret_pct"] for t in oo_tr], hint, [t["partial"] for t in oo_tr])
    emit(f"\n   @ {hint}bp:  IS [n={mi['n'] if mi else 0} sum={mi['total']:+.1f}% PF={pf_s(mi['pf'])}]"
         + (f"  |  OOS [n={mo['n']} sum={mo['total']:+.1f}% PF={pf_s(mo['pf'])}]" if mo else "  |  OOS [n=0]"))
    r_mean = statistics.mean([t["r_mult"] for t in all_tr])
    emit(f"   middel-R (uden kost): {r_mean:+.3f}  ·  WR {metrics(rets,0,parts)['wr']:.0f}%")

    emit("\n" + "=" * 78)
    emit("  DOM")
    emit("=" * 78)
    full = metrics(rets, hint, parts)
    holds = bool(full and full["pf"] > 1 and mo and mo["pf"] > 1 and mo["total"] > 0 and n >= 30)
    emit(f"  @ {hint}bp: PF={pf_s(full['pf'])} sum={full['total']:+.1f}% · "
         + ("✅ OVERLEVER (PF>1 ved kost OG OOS)" if holds else "❌ falder / for faa handler"))
    if n < 30:
        emit(f"  ⚠ kun {n} handler — for tyndt til en trovaerdig dom; host flere tickers/laengere periode.")
    emit("\n  Defaults ikke tunet -> konservativt. Overlever -> param-sweep + fill-realisme FOER paper.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"  Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

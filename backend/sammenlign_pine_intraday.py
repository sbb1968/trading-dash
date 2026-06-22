#!/usr/bin/env python3
"""
sammenlign_pine_intraday.py — GATEN for intradag-laget.

Samme idé som sammenlign_pine.py, men for intradag: vi sammenligner Python-laget
(technical_intraday.compute_intraday_series) bit-for-bit mod Pine-indikatoren
"Day trading konfluens v1 [Long]". Vi doemmer paa at TALLENE matcher pr. bar.

──────────────────────────────────────────────────────────────────────────────
PROTOKOL (kort)
1. I TradingView paa "Day trading konfluens v1": tilfoej MIDLERTIDIGT disse plots
   (een pr. gruppe + total), saet chartet paa symbol X, tidsramme T, ETH = TIL:

       plot(dagScore,          "DAG")
       plot(f_groupScore(0),   "G_VWAP")   // VWAP / trend
       plot(f_groupScore(1),   "G_VOL")    // Volumen
       plot(f_groupScore(2),   "G_MOM")    // Momentum
       plot(f_groupScore(3),   "G_OPEN")   // Aabning / ORB
       plot(f_groupScore(4),   "G_VOLA")   // Volatilitet
       plot(f_groupScore(5),   "G_RS")     // Rel. styrke
       plot(f_groupScore(6),   "G_TRIG")   // Trigger

   Eksportér chart-data til CSV (tid + OHLCV + de 8 serier).  ← dette er --pine

2. Eksportér SPY paa SAMME tidsramme/vindue til en CSV (tid + OHLCV).  ← --spy
   Brug SAMME OHLCV-kilde paa begge sider i foerste runde (TradingView-CSV som
   bars) for at eliminere datakilde-forskelle.

3. Hav en DAGLIG OHLCV-CSV med nok historik FOER testvinduet (til adv20/adr20/
   prev_day_*; vi shifter dem 1 dag internt).  ← --daily

4. Koer:
     python sammenlign_pine_intraday.py --pine STOCK.csv --spy SPY.csv --daily DAILY.csv

   Den joiner paa tidsstempel og rapporterer pr. serie: max-afvigelse, median og
   antal bars uden for tolerance.

TOLERANCE: TOL=0.5 score-point pr. bar (groen); median < 0.1. Smaa afvigelser er
forventelige fra float-akkumulering/bar-alignment. En HEL gruppe skaev = roedt.

FOKUS (her opstaar afvigelser): VWAP-anchor/premarket-start, RSI/ATR-seeding
(drop warmup), daglige aggregater som FORRIGE dags (shift 1), sessions-skel paa
ET-klokken, partiel sidste bar. Se SPEC §8.
──────────────────────────────────────────────────────────────────────────────
ASCII i kode; dansk prosa.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from technical_intraday import (
    compute_intraday_series, IntradayParams, DEFAULT_PARAMS, GROUP_KEYS, GROUP_NAMES,
)

_ET = "America/New_York"

# Pine-plot-titler -> Python-kolonner. Redigér her hvis du gav plottene andre navne.
PINE_COLS = {
    "lag_score":  "DAG",
    "group_vwap": "G_VWAP",
    "group_vol":  "G_VOL",
    "group_mom":  "G_MOM",
    "group_open": "G_OPEN",
    "group_vola": "G_VOLA",
    "group_rs":   "G_RS",
    "group_trig": "G_TRIG",
}

TOL = 0.5
MEDIAN_TOL = 0.1


def _parse_time(col: pd.Series) -> pd.DatetimeIndex:
    """TradingView-eksport: 'time' er typisk unix-sekunder, men kan vaere ISO."""
    s = col
    try:
        v = pd.to_numeric(s)
        # unix-sekunder hvis vaerdierne er store heltal
        idx = pd.to_datetime(v, unit="s", utc=True)
    except (ValueError, TypeError):
        idx = pd.to_datetime(s, utc=True)
    return pd.DatetimeIndex(idx).tz_convert(_ET)


def _read_ohlcv(path: str) -> pd.DataFrame:
    """Laes en CSV med en tid-kolonne + OHLCV (TradingView-eksport). ET-index."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    timecol = next((c for c in df.columns if c in ("time", "date", "datetime", "timestamp")), df.columns[0])
    idx = _parse_time(df[timecol])
    df = df.set_index(idx).sort_index()
    return df


def _load_daily(path: str) -> pd.DataFrame:
    """Raa daglig OHLCV -> FORRIGE dags prev_day_*, adv20, adr20 (shift 1)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    timecol = next((c for c in df.columns if c in ("time", "date", "datetime", "timestamp")), df.columns[0])
    dates = pd.to_datetime(df[timecol]).dt.date
    out = pd.DataFrame(index=pd.Index(dates, name="date"))
    out["prev_day_close"] = df["close"].shift(1).to_numpy()
    out["prev_day_high"] = df["high"].shift(1).to_numpy()
    out["prev_day_low"] = df["low"].shift(1).to_numpy()
    out["adv20"] = df["volume"].rolling(20).mean().shift(1).to_numpy()
    out["adr20"] = (df["high"] - df["low"]).rolling(20).mean().shift(1).to_numpy()
    return out


def _compare(name: str, py: pd.Series, pine: pd.Series, tol: float) -> dict:
    j = pd.concat([py.rename("py"), pine.rename("pine")], axis=1).dropna()
    if j.empty:
        return {"name": name, "n": 0, "max": np.nan, "median": np.nan, "out": 0, "ok": False}
    dev = (j["py"] - j["pine"]).abs()
    out = int((dev > tol).sum())
    mx = float(dev.max()); med = float(dev.median())
    ok = mx <= tol and med < MEDIAN_TOL
    return {"name": name, "n": len(j), "max": mx, "median": med, "out": out, "ok": ok}


def main():
    ap = argparse.ArgumentParser(description="Sammenlign intradag-laget mod Pine bit-for-bit")
    ap.add_argument("--pine", required=True, help="TradingView-CSV: tid + OHLCV + DAG/G_*-plots")
    ap.add_argument("--spy", help="TradingView-CSV for SPY (samme TF/vindue): tid + OHLCV")
    ap.add_argument("--daily", help="Daglig OHLCV-CSV (nok historik foer vinduet)")
    ap.add_argument("--orb", type=int, default=5, choices=[5, 15], help="opening-range minutter")
    ap.add_argument("--tol", type=float, default=TOL, help="tolerance pr. bar (score-point)")
    ap.add_argument("--warmup", type=int, default=None, help="antal bars at droppe (default max(rsi,atr,26))")
    args = ap.parse_args()
    tol = args.tol

    pine_raw = _read_ohlcv(args.pine)
    bars = pine_raw[["open", "high", "low", "close", "volume"]].copy()

    spy_bars = None
    if args.spy:
        spy_raw = _read_ohlcv(args.spy)
        spy_bars = spy_raw[["open", "close"]].copy()
    else:
        print("! Ingen --spy: RS-faktorerne bliver NaN, og DAG/Rel.styrke vil afvige.")

    daily = None
    if args.daily:
        daily = _load_daily(args.daily)
    else:
        print("! Ingen --daily: gap/RVOL/ADR-faktorerne bliver NaN og vil afvige.")

    params = IntradayParams(orb_minutes=args.orb)
    series = compute_intraday_series(bars, spy_bars, daily, params=params)

    # Drop warmup-hale (RSI/ATR/MACD-RMA opvarmning)
    warmup = args.warmup if args.warmup is not None else max(params.rsi_len, params.atr_len, 26)
    series = series.iloc[warmup:]

    print("=" * 78)
    print(" Intradag-lag vs Pine 'Day trading konfluens v1'")
    print(f" pine={args.pine}  spy={args.spy or '-'}  daily={args.daily or '-'}  TF-orb={args.orb}m")
    print(f" warmup droppet: {warmup} bars   TOL={tol}  median-TOL<{MEDIAN_TOL}")
    print("=" * 78)
    print(f"{'Serie':<16}{'bars':>6}{'max-afv':>10}{'median':>10}{'>TOL':>7}  status")
    print("-" * 78)

    rows = []
    cols_lc = {c.lower(): c for c in pine_raw.columns}     # case-insensitiv opslag
    for py_col, pine_col in PINE_COLS.items():
        src = cols_lc.get(pine_col.lower())
        if src is None:
            print(f"{py_col:<16}  (Pine-kolonne '{pine_col}' mangler i CSV - sprunget over)")
            continue
        if py_col not in series.columns:
            print(f"{py_col:<16}  (Python-kolonne mangler - sprunget over)")
            continue
        pine_series = pine_raw[src].reindex(series.index)
        r = _compare(py_col, series[py_col], pine_series, tol)
        rows.append(r)
        if r["n"] == 0:
            print(f"{py_col:<16}{0:>6}{'-':>10}{'-':>10}{'-':>7}  ! ingen overlap")
            continue
        status = "OK" if r["ok"] else "FEJL (ROED)"
        print(f"{py_col:<16}{r['n']:>6}{r['max']:>10.3f}{r['median']:>10.3f}{r['out']:>7}  {status}")

    print("-" * 78)
    all_ok = bool(rows) and all(r["ok"] for r in rows if r["n"] > 0)
    if all_ok:
        print("RESULTAT: OK - alle serier inden for tolerance; Python-laget matcher Pine.")
    else:
        print("RESULTAT: FEJL - mindst en serie uden for tolerance. Tjek SPEC afsnit 8:")
        print("  VWAP-anchor/premarket-start · RSI/ATR-seeding (oeg --warmup) · daglige")
        print("  aggregater = FORRIGE dag (shift 1) · sessions-skel paa ET · partiel sidste bar.")
    print("=" * 78)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

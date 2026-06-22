#!/usr/bin/env python3
"""
technical_intraday.py — Intradag teknisk lag (DAG-SCORE).

Python-tvilling til Pine-indikatoren "Day trading konfluens v1 [Long]".
Den producerer den TEKNISKE intradag-score (DAG-SCORE) ud fra de samme bars
som chartet, saa intradag-rapporten og chartet viser SAMME tal — verificeret
bit-for-bit mod Pine via sammenlign_pine_intraday.py.

DESIGN: spejler swing-laget (technical_score.py) saa taet som muligt, saa de to
tidsrammer er ens i struktur:
    * helpers (clamp + band-funktioner) som Pine f_*
    * _build_context()  — beregn ALT een gang (her: en per-bar context-DataFrame)
    * tynde s_*-scorere der laeser ctx og anvender band-funktionen
    * PARAMS-register (key, name, group, weight, scorer, detailer) = eneste
      sandhedskilde for noegler/navne/grupper/vaegte (= Pine's faktor-register)
    * ParamResult med .raw/.signal/.weight/.weighted  (SAMME dataklasse som swing)
    * compute_technical_intraday() returnerer en dict i SAMME form som swing's
      compute_technical() {results, excluded, lag_score} + group_scores + asof,
      saa intradag_report.py (trin 4) kan plugge det ind identisk.
    * _band() + format_technical_report() som swing.

Hvor desktop-Claude-spec'en foreslog egne dataklasser (IntradayParamResult /
IntradayTechnicalResult med .detail), VINDER swing-moenstret bevidst: vi bruger
ParamResult.raw og dict-returen, saa de to lag er kodemaessigt ens.

Forskellen fra swing er teknisk noedvendig: intradag-laget er VEKTORISERET pr.
bar (hele serien kan sammenlignes med Pine), fordi dags-akkumulatorerne (VWAP,
cumVol, OBV, ORB, dayHigh/Low) afhaenger af reset-punkter i loebet af dagen.
Indikatorerne genbruges fra strategies/shared/indicators.py (Pine-matchet).

Data-kontrakt (se ogsaa docstring paa compute_intraday_series):
    bars   : DataFrame, DatetimeIndex i ET, kolonner open/high/low/close/volume,
             een tidsramme (1/3/5 min), inkl. premarket fra 04:00 ET.
    spy_bars: DataFrame, samme tidsramme/index, kolonner open/close.
    daily  : DataFrame indekseret paa ET session-dato (datetime.date), hvor
             vaerdierne ALLEREDE er FORRIGE dags taerter:
             prev_day_close/high/low, adv20, adr20.

ASCII i kode; dansk i visningstekster (som swing).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

try:
    from zoneinfo import ZoneInfo
except ImportError:  # py<3.9 fallback (bruges ikke paa 3.14, men skader ikke)
    ZoneInfo = None  # type: ignore

from strategies.shared.indicators import (
    ema, rsi as _ind_rsi, atr as _ind_atr, sma, vwap_with_bands, macd as _ind_macd,
)

_ET = "America/New_York"


# === Parametre (spejler Pine-inputs) ========================================
@dataclass
class IntradayParams:
    ema_fast: int = 9
    ema_slow: int = 20
    rsi_len: int = 14
    atr_len: int = 14
    vwap_mult: float = 1.5
    overext_atr: float = 2.5
    orb_minutes: int = 5            # 5 eller 15


DEFAULT_PARAMS = IntradayParams()


# === Helpers: band-funktioner — EKSAKT som Pine f_* (vektoriseret) ==========
# Alle bevarer NaN hvor input er NaN, og evaluerer betingelser i Pine-raekkefoelge.
def clamp(x, lo=-100.0, hi=100.0):
    """Skalar clamp (som swing's clamp)."""
    return float(max(lo, min(hi, x)))


def _clamp(s: pd.Series) -> pd.Series:
    return s.clip(-100.0, 100.0)


def _select_nan(conds, choices, default, like: pd.Series, nan_mask) -> pd.Series:
    out = np.select(conds, choices, default=float(default)).astype(float)
    out = np.where(nan_mask, np.nan, out)
    return pd.Series(out, index=like.index)


def _level_pair(c, hi, lo, a) -> pd.Series:
    """Pine f_levelPair — bruges af ORB, PMH/PML, PDH/PDL."""
    c_a = c.to_numpy(float); hi_a = hi.to_numpy(float)
    lo_a = lo.to_numpy(float); a_a = a.to_numpy(float)
    valid = (~np.isnan(hi_a)) & (~np.isnan(lo_a)) & (~np.isnan(a_a)) & (a_a > 0) & (hi_a > lo_a)
    with np.errstate(invalid="ignore", divide="ignore"):
        above_v = np.minimum(100.0, 40.0 + (c_a - hi_a) / a_a * 60.0)
        below_v = np.maximum(-100.0, -40.0 - (lo_a - c_a) / a_a * 60.0)
        mid_v = np.clip(((c_a - lo_a) / (hi_a - lo_a) * 2.0 - 1.0) * 40.0, -100.0, 100.0)
    above = c_a > hi_a
    below = c_a < lo_a
    out = np.full(len(c_a), np.nan)
    out = np.where(valid & above, above_v, out)
    out = np.where(valid & (~above) & below, below_v, out)
    out = np.where(valid & (~above) & (~below), mid_v, out)
    return pd.Series(out, index=c.index)


def _rvol_score(rp: pd.Series) -> pd.Series:
    x = rp.to_numpy(float)
    return _select_nan([x >= 3, x >= 2, x >= 1.5, x >= 1, x >= 0.7],
                       [100, 80, 55, 25, -10], -50, rp, np.isnan(x))


def _vol_spike_score(x_s: pd.Series) -> pd.Series:
    x = x_s.to_numpy(float)
    return _select_nan([x >= 3, x >= 2, x >= 1.5, x >= 1],
                       [100, 70, 40, 10], -20, x_s, np.isnan(x))


def _rsi_score(r_s: pd.Series) -> pd.Series:
    x = r_s.to_numpy(float)
    return _select_nan([x < 30, x < 40, x < 50, x < 70, x < 78, x < 85],
                       [-80, -40, 10, 60, 35, 0], -30, r_s, np.isnan(x))


def _gap_score(g_s: pd.Series) -> pd.Series:
    x = g_s.to_numpy(float)
    return _select_nan([x >= 20, x >= 8, x >= 3, x >= 1, x >= -1, x >= -3],
                       [30, 70, 85, 50, 0, -30], -60, g_s, np.isnan(x))


def _rs_score(x_s: pd.Series) -> pd.Series:
    x = x_s.to_numpy(float)
    return _select_nan([x >= 3, x >= 1.5, x >= 0.5, x >= 0, x >= -0.5, x >= -1.5],
                       [90, 70, 45, 15, -15, -45], -80, x_s, np.isnan(x))


def _atr_room_score(p_s: pd.Series) -> pd.Series:
    x = p_s.to_numpy(float)
    return _select_nan([x >= 0.5, x >= 0.3, x >= 0.2, x >= 0.1],
                       [70, 50, 25, 0], -40, p_s, np.isnan(x))


def _range_used_score(fr_s: pd.Series) -> pd.Series:
    x = fr_s.to_numpy(float)
    return _select_nan([x < 0.3, x < 0.7, x < 1.0, x < 1.5],
                       [10, 50, 20, -20], -50, fr_s, np.isnan(x))


def _hod_score(dist: pd.Series, at_high: pd.Series) -> pd.Series:
    d = dist.to_numpy(float)
    ah = at_high.to_numpy(bool)
    # at_high har foersteret (Pine: atHigh ? 90 : ...). NaN-maskning sker i s_hod_dist.
    out = np.select([ah, d < 0.5, d < 1.5, d < 3.0], [90, 60, 20, -20], default=-60.0)
    return pd.Series(out.astype(float), index=dist.index)


def _vwap_band_score(c, vw, up, lo) -> pd.Series:
    c_a = c.to_numpy(float); vw_a = vw.to_numpy(float)
    up_a = up.to_numpy(float); lo_a = lo.to_numpy(float)
    nan_mask = np.isnan(vw_a) | np.isnan(up_a) | np.isnan(lo_a)
    return _select_nan([c_a > up_a, c_a > vw_a, c_a > lo_a],
                       [20, 50, -30], -70, c, nan_mask)


# === Context: beregn alt een gang (vektoriseret + eet sekventielt dags-pass) =
def _to_et(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("technical_intraday kraever DatetimeIndex (ET) paa bars")
    if df.index.tz is None:
        df = df.tz_localize(_ET)
    else:
        df = df.tz_convert(_ET)
    return df


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Accepter baade 'Close' (swing-konvention) og 'close' (intradag-kontrakt)."""
    ren = {col: col.lower() for col in df.columns}
    return df.rename(columns=ren)


def _build_context(bars: pd.DataFrame, spy_bars: pd.DataFrame, daily: pd.DataFrame,
                   params: IntradayParams) -> pd.DataFrame:
    bars = _to_et(_norm_cols(bars)).sort_index()
    spy = _norm_cols(spy_bars) if spy_bars is not None else None

    o = bars["open"]; h = bars["high"]; l = bars["low"]
    c = bars["close"]; vol = bars["volume"]
    idx = bars.index
    n = len(bars)

    ctx = pd.DataFrame(index=idx)
    ctx["open"] = o; ctx["high"] = h; ctx["low"] = l; ctx["close"] = c; ctx["volume"] = vol

    # ── Vektoriserede indikatorer (genbrug, Pine-matchet) ───────────────────
    ema9 = ema(c, params.ema_fast)
    ema20 = ema(c, params.ema_slow)
    atr = _ind_atr(h, l, c, params.atr_len)
    rsi_s = _ind_rsi(c, params.rsi_len)
    _, _, macd_hist = _ind_macd(c)
    vwap, vw_up, vw_lo = vwap_with_bands(h, l, c, vol, params.vwap_mult)
    vol_sma = sma(vol, 20)

    ctx["ema9"] = ema9; ctx["ema20"] = ema20; ctx["atr"] = atr; ctx["rsi"] = rsi_s
    ctx["macd_hist"] = macd_hist
    ctx["vwap"] = vwap; ctx["vw_upper"] = vw_up; ctx["vw_lower"] = vw_lo
    ctx["vwap_prev5"] = vwap.shift(5)
    ctx["vol_sma"] = vol_sma

    # ── SPY paa samme index ─────────────────────────────────────────────────
    if spy is not None and not spy.empty:
        spy = _to_et(spy).sort_index().reindex(idx)
        spy_open = spy["open"]; spy_close = spy["close"]
    else:
        spy_open = pd.Series(np.nan, index=idx)
        spy_close = pd.Series(np.nan, index=idx)

    # ── Dags-aggregater fra daily (FORRIGE dags taerter — slaaet op pr. bar) ─
    bar_dates = pd.Series(idx.date, index=idx)
    for col in ("prev_day_close", "prev_day_high", "prev_day_low", "adv20", "adr20"):
        if daily is not None and col in daily.columns:
            ctx[col] = bar_dates.map(daily[col]).astype(float)
        else:
            ctx[col] = np.nan

    # ── Sessions-flag (ET-klokketid) ────────────────────────────────────────
    mod = idx.hour * 60 + idx.minute               # minutter siden ET-midnat
    in_rth = (mod >= 570) & (mod < 960)            # 09:30–16:00
    in_pm = (mod >= 240) & (mod < 570)             # 04:00–09:30
    orb_end = 575 if params.orb_minutes == 5 else 585   # 09:35 / 09:45
    in_orb = (mod >= 570) & (mod < orb_end)
    prev_rth = np.r_[False, in_rth[:-1]]
    prev_pm = np.r_[False, in_pm[:-1]]
    rth_start = in_rth & (~prev_rth)
    pm_start = in_pm & (~prev_pm)
    ctx["in_rth"] = in_rth; ctx["in_pm"] = in_pm

    # ── Sekventielt dags-pass (var-persisterende akkumulatorer som Pine) ─────
    o_a = o.to_numpy(float); h_a = h.to_numpy(float); l_a = l.to_numpy(float)
    c_a = c.to_numpy(float); v_a = vol.to_numpy(float)
    spy_open_a = spy_open.to_numpy(float)
    # naivt datetime64 (resolutions-uafhaengigt: pandas 2.x kan vaere us/ns) til mins-siden-open.
    # tz_localize(None) foerst: ellers giver to_numpy() et object-array af tz-aware Timestamps.
    idx_dt = idx.tz_convert("UTC").tz_localize(None).to_numpy()

    prev_close = np.empty(n); prev_close[:] = np.nan
    if n > 1:
        prev_close[1:] = c_a[:-1]
    obv_step = np.where(c_a > prev_close, v_a, np.where(c_a < prev_close, -v_a, 0.0))

    day_open = np.full(n, np.nan)
    day_start_dt = np.full(n, np.datetime64("NaT"), dtype=idx_dt.dtype)   # rth-start-tid (persisterer)
    day_high = np.full(n, np.nan); day_low = np.full(n, np.nan)
    cum_vol = np.full(n, np.nan); obv_cum = np.full(n, np.nan)
    orb_high = np.full(n, np.nan); orb_low = np.full(n, np.nan)
    pm_high = np.full(n, np.nan); pm_low = np.full(n, np.nan)
    spy_day_open = np.full(n, np.nan)

    cdo = cdh = cdl = ccv = cob = coh = col_ = cph = cpl = cso = np.nan
    cds = np.datetime64("NaT")                        # day_start sentinel (NaT = ikke sat)
    for i in range(n):
        if rth_start[i]:
            cdo = o_a[i]; cds = idx_dt[i]; cdh = h_a[i]; cdl = l_a[i]
            ccv = v_a[i]; cob = obv_step[i]; coh = np.nan; col_ = np.nan
            cso = spy_open_a[i]
        elif in_rth[i]:
            cdh = h_a[i] if np.isnan(cdh) else max(cdh, h_a[i])
            cdl = l_a[i] if np.isnan(cdl) else min(cdl, l_a[i])
            ccv = v_a[i] if np.isnan(ccv) else ccv + v_a[i]
            cob = obv_step[i] if np.isnan(cob) else cob + obv_step[i]
        if in_orb[i]:
            coh = h_a[i] if np.isnan(coh) else max(coh, h_a[i])
            col_ = l_a[i] if np.isnan(col_) else min(col_, l_a[i])
        if pm_start[i]:
            cph = h_a[i]; cpl = l_a[i]
        elif in_pm[i]:
            cph = h_a[i] if np.isnan(cph) else max(cph, h_a[i])
            cpl = l_a[i] if np.isnan(cpl) else min(cpl, l_a[i])
        day_open[i] = cdo; day_start_dt[i] = cds; day_high[i] = cdh; day_low[i] = cdl
        cum_vol[i] = ccv; obv_cum[i] = cob; orb_high[i] = coh; orb_low[i] = col_
        pm_high[i] = cph; pm_low[i] = cpl; spy_day_open[i] = cso

    ctx["day_open"] = day_open; ctx["day_high"] = day_high; ctx["day_low"] = day_low
    ctx["cum_vol"] = cum_vol; ctx["obv_cum"] = obv_cum
    ctx["orb_high"] = orb_high; ctx["orb_low"] = orb_low
    ctx["pm_high"] = pm_high; ctx["pm_low"] = pm_low

    # ── Afledte (pr. bar) ───────────────────────────────────────────────────
    with np.errstate(invalid="ignore", divide="ignore"):
        mins_since_open = (idx_dt - day_start_dt) / np.timedelta64(1, "m")   # float min, NaN hvor NaT
        frac = np.clip(mins_since_open / 390.0, 0.05, 1.0)
        adv20 = ctx["adv20"].to_numpy(float)
        rvol_pace = np.where((adv20 > 0) & (~np.isnan(adv20)), cum_vol / (adv20 * frac), np.nan)
        stock_chg = np.where(day_open > 0, (c_a / day_open - 1.0) * 100.0, np.nan)
        spy_chg = np.where(spy_day_open > 0, (spy_close.to_numpy(float) / spy_day_open - 1.0) * 100.0, np.nan)
    rs_val = stock_chg - spy_chg

    # rs_open: frys foerste gang mins>=15 og rs_val har data (reset ved rth_start)
    rs_open = np.full(n, np.nan)
    cro = np.nan
    for i in range(n):
        if rth_start[i]:
            cro = np.nan
        if in_rth[i] and (not np.isnan(mins_since_open[i])) and mins_since_open[i] >= 15.0 \
           and np.isnan(cro) and (not np.isnan(rs_val[i])):
            cro = rs_val[i]
        rs_open[i] = cro

    ctx["rvol_pace"] = rvol_pace
    ctx["stock_chg"] = stock_chg
    ctx["spy_chg"] = spy_chg
    ctx["rs_val"] = rs_val
    ctx["rs_open"] = rs_open

    # ── Faerdig-afledte til scorere + detaljer ─────────────────────────────
    ctx["atr_pct"] = np.where(c_a > 0, atr.to_numpy(float) / c_a * 100.0, np.nan)
    ctx["roc5"] = (c / c.shift(5) - 1.0) * 100.0
    with np.errstate(invalid="ignore", divide="ignore"):
        ctx["vol_ratio"] = np.where((vol_sma.to_numpy(float) > 0), v_a / vol_sma.to_numpy(float), np.nan)
        ctx["obv_frac"] = np.where(cum_vol > 0, obv_cum / cum_vol, np.nan)
        ctx["gap_pct"] = np.where((ctx["prev_day_close"].to_numpy(float) > 0) & (~np.isnan(day_open)),
                                  (day_open - ctx["prev_day_close"].to_numpy(float)) / ctx["prev_day_close"].to_numpy(float) * 100.0,
                                  np.nan)
        ctx["vwap_dist_atr"] = np.where(atr.to_numpy(float) > 0, (c_a - vwap.to_numpy(float)) / atr.to_numpy(float), np.nan)
        ctx["hod_dist"] = np.where(atr.to_numpy(float) > 0, (day_high - c_a) / atr.to_numpy(float), np.nan)
        ctx["day_range"] = day_high - day_low
        adr20 = ctx["adr20"].to_numpy(float)
        ctx["range_frac"] = np.where((adr20 > 0) & (~np.isnan(adr20)), (day_high - day_low) / adr20, np.nan)
    ctx["at_new_high"] = pd.Series(in_rth, index=idx) & (h >= pd.Series(day_high, index=idx))
    ctx["broke"] = h > h.shift(1)
    ctx["overext"] = ema9.notna() & atr.notna() & (c > ema9 + params.overext_atr * atr)
    return ctx


# === Scorere (ctx -> signed pd.Series, NaN hvor udefineret) =================
# Tynde, som swing: laeser ctx og anvender band-funktionen. Hver NaN-guard
# matcher Pine praecist (ellers afviger renormaliseringen).
def s_vwap_side(x):
    return _clamp((x["close"] - x["vwap"]) / x["atr"] / 1.5 * 100.0).where(x["atr"] > 0)


def s_vwap_slope(x):
    sig = _clamp((x["vwap"] - x["vwap_prev5"]) / (x["atr"] * 0.3) * 100.0)
    return sig.where((x["atr"] > 0) & x["vwap_prev5"].notna())


def s_vwap_band(x):
    return _vwap_band_score(x["close"], x["vwap"], x["vw_upper"], x["vw_lower"])


def s_ema_fast(x):
    return _clamp((x["close"] - x["ema9"]) / x["atr"] * 100.0).where(x["atr"] > 0)


def s_ema_align(x):
    return _clamp((x["ema9"] - x["ema20"]) / (x["atr"] * 0.5) * 100.0).where(x["atr"] > 0)


def s_rvol_tod(x):
    return _rvol_score(x["rvol_pace"])


def s_vol_spike(x):
    return _vol_spike_score(x["vol_ratio"])


def s_obv_intra(x):
    return _clamp(x["obv_frac"] * 120.0)


def s_rsi_intra(x):
    return _rsi_score(x["rsi"])


def s_macd_intra(x):
    return _clamp(x["macd_hist"] / (x["atr"] * 0.5) * 100.0).where(x["atr"] > 0)


def s_roc_5bar(x):
    sig = _clamp(x["roc5"] / (x["atr_pct"] * 1.5) * 100.0)
    return sig.where(x["atr_pct"] > 0)


def s_impulse(x):
    c, o, h, l = x["close"], x["open"], x["high"], x["low"]
    rng = h - l
    cp = ((c - l) / rng).where(rng > 0, 0.5)
    br = (((c - o).abs()) / rng).where(rng > 0, 0.0)
    up_sig = _clamp((br * 0.5 + cp * 0.5) * 200.0 - 70.0)
    dn_sig = _clamp(-((br * 0.5 + (1.0 - cp) * 0.5) * 200.0 - 70.0))
    return up_sig.where(c > o, dn_sig)


def s_orb_status(x):
    return _level_pair(x["close"], x["orb_high"], x["orb_low"], x["atr"]).where(x["in_rth"])


def s_gap_pct(x):
    return _gap_score(x["gap_pct"])


def s_pmh_pml(x):
    return _level_pair(x["close"], x["pm_high"], x["pm_low"], x["atr"])


def s_pdh_pdl(x):
    return _level_pair(x["close"], x["prev_day_high"], x["prev_day_low"], x["atr"])


def s_hod_dist(x):
    sig = _hod_score(x["hod_dist"], x["at_new_high"])
    return sig.where(x["hod_dist"].notna() & x["in_rth"])


def s_atr_room(x):
    return _atr_room_score(x["atr_pct"])


def s_range_used(x):
    sig = _range_used_score(x["range_frac"])
    return sig.where(x["range_frac"].notna() & x["in_rth"])


def s_rs_spy(x):
    return _rs_score(x["rs_val"])


def s_rs_open(x):
    return _rs_score(x["rs_open"])


def s_break_ext(x):
    overext = x["overext"].to_numpy(bool)
    broke = x["broke"].fillna(False).to_numpy(bool)
    out = np.select([overext & broke, overext, broke], [20, -30, 70], default=0.0)
    return pd.Series(out.astype(float), index=x.index)


# === Detalje-funktioner (raa-aflaesning af sidste bar — spejler Pine d_*) ===
def _isnum(v) -> bool:
    return v is not None and not (isinstance(v, float) and np.isnan(v))


def _f(v, fmt: str) -> str:
    return format(v, fmt) if _isnum(v) else "-"


def d_vwap_side(r):
    return f"{_f(r.get('vwap_dist_atr'), '+.1f')} ATR" if _isnum(r.get("vwap_dist_atr")) else "-"


def d_vwap_slope(r):
    vw, vw5 = r.get("vwap"), r.get("vwap_prev5")
    if not (_isnum(vw) and _isnum(vw5)):
        return "-"
    return "stigende" if vw > vw5 else "falder" if vw < vw5 else "flad"


def d_vwap_band(r):
    c, vw, up, lo = r.get("close"), r.get("vwap"), r.get("vw_upper"), r.get("vw_lower")
    if not (_isnum(vw) and _isnum(up) and _isnum(lo)):
        return "-"
    return "over oevre" if c > up else "over VWAP" if c > vw else "under VWAP" if c > lo else "under nedre"


def d_ema_fast(r):
    c, e = r.get("close"), r.get("ema9")
    return "-" if not _isnum(e) else "over EMA9" if c >= e else "under EMA9"


def d_ema_align(r):
    a, b = r.get("ema9"), r.get("ema20")
    return "-" if not (_isnum(a) and _isnum(b)) else "bullish" if a >= b else "bearish"


def d_rvol(r):
    return f"{_f(r.get('rvol_pace'), '.1f')}x" if _isnum(r.get("rvol_pace")) else "-"


def d_vol_spike(r):
    return f"{_f(r.get('vol_ratio'), '.1f')}x" if _isnum(r.get("vol_ratio")) else "-"


def d_obv(r):
    f = r.get("obv_frac")
    return "-" if not _isnum(f) else "akk. koeb" if f >= 0 else "akk. salg"


def d_rsi(r):
    return _f(r.get("rsi"), ".0f")


def d_macd(r):
    hm = r.get("macd_hist")
    return "-" if not _isnum(hm) else "hist +" if hm >= 0 else "hist -"


def d_roc5(r):
    return f"{_f(r.get('roc5'), '+.1f')}%" if _isnum(r.get("roc5")) else "-"


def d_impulse(r):
    c, o = r.get("close"), r.get("open")
    if not (_isnum(c) and _isnum(o)):
        return "-"
    return "groen krop" if c > o else "roed krop" if c < o else "doji"


def d_orb(r):
    c, hi, lo = r.get("close"), r.get("orb_high"), r.get("orb_low")
    if not (_isnum(hi) and _isnum(lo)):
        return "-"
    return "over ORH" if c > hi else "under ORL" if c < lo else "i range"


def d_gap(r):
    return f"{_f(r.get('gap_pct'), '+.1f')}%" if _isnum(r.get("gap_pct")) else "-"


def d_pmh(r):
    c, hi, lo = r.get("close"), r.get("pm_high"), r.get("pm_low")
    if not (_isnum(hi) and _isnum(lo)):
        return "-"
    return "over PMH" if c > hi else "under PML" if c < lo else "i PM-range"


def d_pdh(r):
    c, hi, lo = r.get("close"), r.get("prev_day_high"), r.get("prev_day_low")
    if not (_isnum(hi) and _isnum(lo)):
        return "-"
    return "over PDH" if c > hi else "under PDL" if c < lo else "i PD-range"


def d_hod(r):
    if not (r.get("in_rth") and _isnum(r.get("hod_dist"))):
        return "-"
    if r.get("at_new_high"):
        return "ny HOD"
    return f"{_f(r.get('hod_dist'), '.1f')} ATR"


def d_atr_room(r):
    return f"{_f(r.get('atr_pct'), '.1f')}%" if _isnum(r.get("atr_pct")) else "-"


def d_range_used(r):
    rf = r.get("range_frac")
    return f"{rf * 100:.0f}%" if _isnum(rf) else "-"


def d_rs_spy(r):
    return f"{_f(r.get('rs_val'), '+.1f')}%" if _isnum(r.get("rs_val")) else "-"


def d_rs_open(r):
    return f"{_f(r.get('rs_open'), '+.1f')}%" if _isnum(r.get("rs_open")) else "-"


def d_break(r):
    return "strakt" if r.get("overext") else "break" if r.get("broke") else "-"


# === Gruppe-navne (= Pine-tabellens grupper) ===============================
GROUP_NAMES = ["VWAP / trend", "Volumen", "Momentum", "Aabning / ORB",
               "Volatilitet", "Rel. styrke", "Trigger"]
GROUP_KEYS = {
    "VWAP / trend": "vwap", "Volumen": "vol", "Momentum": "mom",
    "Aabning / ORB": "open", "Volatilitet": "vola", "Rel. styrke": "rs",
    "Trigger": "trig",
}


# === Parameter-register (vaegt = andel af laget; summer til 1.00) ===========
# (key, navn, gruppe, vaegt, scorer, detailer) — eneste sandhedskilde, = Pine.
PARAMS = [
    ("vwap_side",  "Pris vs VWAP",      "VWAP / trend", 0.070, s_vwap_side,  d_vwap_side),
    ("vwap_slope", "VWAP-haeldning",    "VWAP / trend", 0.045, s_vwap_slope, d_vwap_slope),
    ("vwap_band",  "VWAP-baand",        "VWAP / trend", 0.030, s_vwap_band,  d_vwap_band),
    ("ema_fast",   "Pris vs EMA9",      "VWAP / trend", 0.030, s_ema_fast,   d_ema_fast),
    ("ema_align",  "EMA9 vs EMA20",     "VWAP / trend", 0.025, s_ema_align,  d_ema_align),
    ("rvol_tod",   "RVOL-tempo",        "Volumen",      0.110, s_rvol_tod,   d_rvol),
    ("vol_spike",  "Volumen-spike",     "Volumen",      0.060, s_vol_spike,  d_vol_spike),
    ("obv_intra",  "Akkumulation OBV",  "Volumen",      0.050, s_obv_intra,  d_obv),
    ("rsi_intra",  "RSI(14)",           "Momentum",     0.055, s_rsi_intra,  d_rsi),
    ("macd_intra", "MACD-hist.",        "Momentum",     0.045, s_macd_intra, d_macd),
    ("roc_5bar",   "5-bars ROC",        "Momentum",     0.045, s_roc_5bar,   d_roc5),
    ("impulse",    "Impuls-candle",     "Momentum",     0.035, s_impulse,    d_impulse),
    ("orb_status", "ORB-status",        "Aabning / ORB",0.055, s_orb_status, d_orb),
    ("gap_pct",    "Gap %",             "Aabning / ORB",0.040, s_gap_pct,    d_gap),
    ("pmh_pml",    "Premarket H/L",     "Aabning / ORB",0.035, s_pmh_pml,    d_pmh),
    ("pdh_pdl",    "Forrige dag H/L",   "Aabning / ORB",0.030, s_pdh_pdl,    d_pdh),
    ("hod_dist",   "Afstand til HOD",   "Volatilitet",  0.045, s_hod_dist,   d_hod),
    ("atr_room",   "ATR-plads",         "Volatilitet",  0.040, s_atr_room,   d_atr_room),
    ("range_used", "ADR brugt",         "Volatilitet",  0.035, s_range_used, d_range_used),
    ("rs_spy",     "RS vs SPY",         "Rel. styrke",  0.045, s_rs_spy,     d_rs_spy),
    ("rs_open",    "RS aabning 15m",    "Rel. styrke",  0.025, s_rs_open,    d_rs_open),
    ("break_ext",  "Break + ej strakt", "Trigger",      0.050, s_break_ext,  d_break),
]


@dataclass
class ParamResult:
    key: str
    name: str
    group: str
    raw: str
    signal: float
    weight: float
    weighted: float


# === Sammensaetning ========================================================
def compute_intraday_series(bars: pd.DataFrame, spy_bars: pd.DataFrame,
                            daily: pd.DataFrame, *, params: IntradayParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """Per-bar frame: sig_<key> (22), group_<g> (7) og lag_score.
       Bruges af sammenlign_pine_intraday.py og af backtest. Renormaliserer
       pr. bar over faktorer MED data (NaN-faktorer udelades — IKKE 0)."""
    if bars is None or bars.empty:
        return pd.DataFrame(columns=[f"sig_{p[0]}" for p in PARAMS]
                            + [f"group_{GROUP_KEYS[g]}" for g in GROUP_NAMES] + ["lag_score"])
    ctx = _build_context(bars, spy_bars, daily, params)
    return _series_from_ctx(ctx)


def compute_technical_intraday(bars: pd.DataFrame, spy_bars: pd.DataFrame,
                               daily: pd.DataFrame, *, params: IntradayParams = DEFAULT_PARAMS) -> dict:
    """Seneste-bar resultat i SAMME form som swing's compute_technical:
       {results, excluded, lag_score} + group_scores + asof.
       Bruges af intradag_report.py og scanneren."""
    if bars is None or bars.empty:
        return {"results": [], "excluded": [("alle", "ingen prisdata")],
                "lag_score": None, "group_scores": {}, "asof": None}

    ctx = _build_context(bars, spy_bars, daily, params)
    series = _series_from_ctx(ctx)
    last = series.iloc[-1]
    row = ctx.iloc[-1].to_dict()

    results, excluded = [], []
    for key, name, group, weight, scorer, detailer in PARAMS:
        sig = last.get(f"sig_{key}")
        if pd.isna(sig):
            excluded.append((name, "mangler data"))
            continue
        results.append(ParamResult(key, name, group, detailer(row), float(sig), weight, 0.0))

    total_w = sum(r.weight for r in results)
    if total_w > 0:
        for r in results:
            r.weight /= total_w
            r.weighted = r.weight * r.signal

    lag = last.get("lag_score")
    lag_score = None if pd.isna(lag) else float(lag)
    group_scores = {}
    for gname in GROUP_NAMES:
        gv = last.get(f"group_{GROUP_KEYS[gname]}")
        group_scores[gname] = None if pd.isna(gv) else float(gv)

    return {"results": results, "excluded": excluded, "lag_score": lag_score,
            "group_scores": group_scores, "asof": series.index[-1]}


def _series_from_ctx(ctx: pd.DataFrame) -> pd.DataFrame:
    """Intern: byg sig/group/lag-frame fra en allerede-bygget context."""
    out = pd.DataFrame(index=ctx.index)
    for key, name, group, weight, scorer, detailer in PARAMS:
        out[f"sig_{key}"] = scorer(ctx)
    keys = [p[0] for p in PARAMS]
    w = np.array([p[3] for p in PARAMS], dtype=float)
    groups = [p[2] for p in PARAMS]
    S = out[[f"sig_{k}" for k in keys]].to_numpy(float)
    present = ~np.isnan(S)
    with np.errstate(invalid="ignore"):
        num = np.where(present, S * w, 0.0).sum(axis=1)
        den = (present * w).sum(axis=1)
        out["lag_score"] = np.where(den > 0, num / den, np.nan)
        for gname in GROUP_NAMES:
            cols = [i for i, g in enumerate(groups) if g == gname]
            Sg = S[:, cols]; wg = w[cols]; pg = ~np.isnan(Sg)
            numg = np.where(pg, Sg * wg, 0.0).sum(axis=1)
            deng = (pg * wg).sum(axis=1)
            out[f"group_{GROUP_KEYS[gname]}"] = np.where(deng > 0, numg / deng, np.nan)
    return out


def _band(score) -> str:
    if score is None:
        return "ingen data"
    if score >= 50:
        return "Staerk intradag-opstilling"
    if score >= 20:
        return "Medvind"
    if score > -20:
        return "Neutral / blandet"
    if score > -50:
        return "Svag"
    return "Fraraades (intradag)"


def format_technical_report(ticker: str, res: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append(f" INTRADAG TEKNISK LAG — {ticker.upper()}   (asof {res.get('asof')})")
    L.append("=" * 78)
    L.append(f"{'Parameter':<28}{'Vaerdi':<22}{'Bidrag':>7}{'Vaegt':>8}{'Vaegtet':>9}")
    L.append("-" * 78)
    last = None
    for r in res["results"]:
        if r.group != last:
            L.append(f"[{r.group}]")
            last = r.group
        L.append(f"  {r.name:<26}{r.raw:<22}{r.signal:>+6.0f} {r.weight*100:>6.1f}% {r.weighted:>+8.1f}")
    L.append("-" * 78)
    score = res["lag_score"]
    sval = 0.0 if score is None else score
    L.append(f"{'INTRADAG LAG-SCORE (DAG-SCORE)':<60}{sval:>+8.1f}")
    L.append(f"  -> {_band(score)}")
    if res.get("group_scores"):
        L.append("")
        L.append("Gruppe-scorer:")
        for g, v in res["group_scores"].items():
            L.append(f"  {g:<18}{('-' if v is None else f'{v:+.0f}')}")
    if res["excluded"]:
        L.append("")
        L.append("Ekskluderet (mangler data):")
        for name, why in res["excluded"]:
            L.append(f"  - {name} ({why})")
    L.append("=" * 78)
    return "\n".join(L)


if __name__ == "__main__":
    # Hurtig smoke-test (Soeren koerer foer browser): valider register + vaegte.
    wsum = sum(p[3] for p in PARAMS)
    print(f"Faktorer: {len(PARAMS)}  (forventet 22)")
    print(f"Vaegtsum: {wsum:.4f}  (forventet 1.0000)")
    assert len(PARAMS) == 22, "skal vaere 22 faktorer"
    assert abs(wsum - 1.0) < 1e-9, "vaegtene skal summe til 1.00"
    by_group = {}
    for key, name, group, weight, scorer, detailer in PARAMS:
        by_group.setdefault(group, 0.0)
        by_group[group] += weight
    print("\nGruppe-vaegte:")
    for g in GROUP_NAMES:
        print(f"  {g:<16}{by_group.get(g, 0.0):.3f}")
    print("\nRegister OK — koer sammenlign_pine_intraday.py for bit-for-bit Pine-verifikation.")

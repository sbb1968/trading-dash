#!/usr/bin/env python3
"""
technical_score.py — Teknisk lag-scoring (Lag 2, 55 % af helheden).

GENSKABT fra vaegtnings-skemaet + de to soester-moduler (fundamental_score /
catalyst_score) + den kontrakt swing_report.py forventer. Samme design:
motoren er AFKOBLET fra kilden. compute_technical() tager en daglig OHLCV-
DataFrame ind (+ valgfri benchmark- og sektor-DataFrame til relativ styrke)
og producerer signaler paa skalaen -100..+100, vaegtet til en lag-score.

Kontrakt (laest af swing_report.combine / run_full):
    SECTOR_ETF                 dict: FMP-sektornavn -> sektor-ETF-symbol
    _yf_ohlcv(ticker, period)  yfinance-fallback, daglig OHLCV
    compute_technical(df, benchmark=None, sector=None)
        -> {"results": [ParamResult...], "excluded": [...], "lag_score": float}
        ParamResult har .name .group .raw .signal .weight .weighted
    format_technical_report(ticker, res) -> str

Vaegtene er ANDEL AF LAGET (gruppe% x parameter-i-gruppe% fra skemaet) og
normaliseres blandt de parametre der faktisk har data — praecis som soester-
modulerne. Mangler benchmark/sektor, ekskluderes RS paent uden at vaelte resten.

Scoring-kurverne er foerste-udkast-heuristikker til kalibrering (samme stance
som soester-modulerne). Retningen kommer fra regnearket; de praecise taerskler
er aabne for justering i "Aabne beslutninger".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


def clamp(x, lo=-100.0, hi=100.0):
    return float(max(lo, min(hi, x)))


def _band_score(value, bands, default):
    """bands: liste af (oevre_graense, score), sorteret stigende."""
    if value is None:
        return None
    for hi, score in bands:
        if value <= hi:
            return float(score)
    return float(default)


# Sektornavn -> SPDR sektor-ETF (til relativ styrke vs sektor). Nøglet på BÅDE
# yfinance/FMP-taxonomien OG TradingViews mere granulære sektor-vokabular, så
# sektor-RS virker uanset hvilken kilde sektoren kom fra (TV er nu primær).
SECTOR_ETF = {
    # yfinance / FMP (GICS-lite)
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
    # TradingView (granulær taxonomi → nærmeste SPDR-sektor)
    "Technology Services": "XLK",
    "Electronic Technology": "XLK",
    "Health Technology": "XLV",
    "Health Services": "XLV",
    "Finance": "XLF",
    "Consumer Durables": "XLY",
    "Consumer Services": "XLY",
    "Retail Trade": "XLY",
    "Consumer Non-Durables": "XLP",
    "Energy Minerals": "XLE",
    "Producer Manufacturing": "XLI",
    "Industrial Services": "XLI",
    "Commercial Services": "XLI",
    "Transportation": "XLI",
    "Distribution Services": "XLI",
    "Process Industries": "XLB",
    "Non-Energy Minerals": "XLB",
    "Communications": "XLC",
}


# === Indikator-helpers (ren pandas/numpy — ingen TA-Lib) ====================
def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _sma(s, n):
    return s.rolling(n).mean()


def _last(s):
    """Sidste ikke-NaN vaerdi som float, ellers None."""
    if s is None or len(s) == 0:
        return None
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else None


def _rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1 / n, adjust=False).mean() / dn.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + rs)


def _atr(h, l, c, n=14):
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _adx(h, l, c, n=14):
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm = pd.Series(plus_dm, index=h.index)
    minus_dm = pd.Series(minus_dm, index=h.index)
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    eps = 1e-9
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / (atr + eps)
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / (atr + eps)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + eps)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def _stoch(h, l, c, k=14, d=3):
    ll = l.rolling(k).min()
    hh = h.rolling(k).max()
    kk = 100 * (c - ll) / (hh - ll + 1e-9)
    return kk, kk.rolling(d).mean()


def _macd(c, fast=12, slow=26, sig=9):
    macd = _ema(c, fast) - _ema(c, slow)
    signal = _ema(macd, sig)
    return macd, signal, macd - signal


def _tsi(c, long=25, short=13):
    m = c.diff()
    m1 = _ema(_ema(m, long), short)
    a1 = _ema(_ema(m.abs(), long), short)
    return 100 * m1 / (a1 + 1e-9)


def _bollinger(c, n=20, k=2):
    mid = _sma(c, n)
    std = c.rolling(n).std()
    upper, lower = mid + k * std, mid - k * std
    width = (upper - lower) / (mid + 1e-9)
    pctb = (c - lower) / (upper - lower + 1e-9)
    return width, pctb


def _obv(c, v):
    direction = np.sign(c.diff().fillna(0.0))
    return (direction * v).cumsum()


def _pivots(df, lookback=3):
    h, l = df["High"].values, df["Low"].values
    n = len(df)
    highs, lows = [], []
    for i in range(lookback, n - lookback):
        if h[i] == h[i - lookback:i + lookback + 1].max():
            highs.append((df.index[i], float(h[i])))
        if l[i] == l[i - lookback:i + lookback + 1].min():
            lows.append((df.index[i], float(l[i])))
    return highs, lows


def _structure(highs, lows, n=3):
    rh = [p for _, p in highs[-n:]]
    rl = [p for _, p in lows[-n:]]

    def direction(seq):
        if len(seq) < 2:
            return 0
        return (sum(b > a for a, b in zip(seq, seq[1:]))
                - sum(b < a for a, b in zip(seq, seq[1:])))

    th, tl = direction(rh), direction(rl)
    if th > 0 and tl > 0:
        return "HH/HL (optrend)"
    if th < 0 and tl < 0:
        return "LH/LL (nedtrend)"
    return "sidelaens / blandet"


def _ret_pct(close, bars):
    if close is None or len(close) <= bars:
        return None
    a, b = float(close.iloc[-bars - 1]), float(close.iloc[-1])
    if a == 0:
        return None
    return (b / a - 1) * 100


# === Context: beregn alt én gang ============================================
def _build_context(df: pd.DataFrame, benchmark=None, sector=None) -> dict:
    ctx: dict = {}
    c, h, l = df["Close"], df["High"], df["Low"]
    v = df["Volume"]
    ctx["price"] = float(c.iloc[-1])
    n = len(df)

    def safe(fn):
        try:
            return fn()
        except Exception:
            return None

    # Trend
    ctx["ma20"] = _last(_sma(c, 20)) if n >= 20 else None
    ctx["ma50"] = _last(_sma(c, 50)) if n >= 50 else None
    ctx["ma200"] = _last(_sma(c, 200)) if n >= 200 else None
    ma50s = _sma(c, 50)
    ctx["ma50_slope"] = None
    if n >= 71 and not np.isnan(ma50s.iloc[-21]):
        base = ma50s.iloc[-21]
        if base:
            ctx["ma50_slope"] = (ma50s.iloc[-1] / base - 1) * 100
    # Golden/death cross-naerhed (sign-skift i ma50-ma200 inden for 30 bars)
    ctx["cross"] = None
    if n >= 230:
        diff = (_sma(c, 50) - _sma(c, 200)).dropna()
        if len(diff) > 31:
            window = np.sign(diff.iloc[-31:].values)
            if window[0] < 0 and window[-1] > 0:
                ctx["cross"] = "golden"
            elif window[0] > 0 and window[-1] < 0:
                ctx["cross"] = "death"
            else:
                ctx["cross"] = "ingen"

    ctx["adx"] = safe(lambda: _last(_adx(h, l, c))) if n >= 30 else None
    ctx["rsi"] = safe(lambda: _last(_rsi(c))) if n >= 20 else None
    mh = safe(lambda: _macd(c))
    if mh:
        macd, signal, hist = mh
        ctx["macd_hist"] = _last(hist)
        cu = cd = False
        d = (macd - signal).dropna()
        if len(d) > 6:
            w = np.sign(d.iloc[-6:].values)
            cu = (w[0] <= 0) and (w[-1] > 0)
            cd = (w[0] >= 0) and (w[-1] < 0)
        ctx["macd_cross"] = "up" if cu else "down" if cd else None
    tsi = safe(lambda: _tsi(c))
    if tsi is not None and len(tsi.dropna()) >= 2:
        t = tsi.dropna()
        ctx["tsi"] = float(t.iloc[-1])
        ctx["tsi_rising"] = bool(t.iloc[-1] > t.iloc[-2])
    ctx["roc"] = _ret_pct(c, 12)
    sk = safe(lambda: _stoch(h, l, c))
    if sk:
        kk, dd = sk
        ctx["stoch_k"] = _last(kk)
        ctx["stoch_d"] = _last(dd)
        k2 = kk.dropna()
        d2 = dd.dropna()
        ctx["stoch_cross_up"] = bool(len(k2) >= 2 and len(d2) >= 2
                                     and k2.iloc[-2] <= d2.iloc[-2]
                                     and k2.iloc[-1] > d2.iloc[-1])

    # Volatilitet
    atr = safe(lambda: _last(_atr(h, l, c)))
    ctx["atr_pct"] = (atr / ctx["price"] * 100) if (atr and ctx["price"]) else None
    ctx["atr_dollar"] = float(atr) if atr else None   # dollar-ATR til info ved siden af ATR%
    bb = safe(lambda: _bollinger(c))
    if bb:
        bw, pb = bb
        ctx["bb_width"] = _last(bw)
        ctx["bb_pctb"] = _last(pb)
    rets = c.pct_change().dropna()
    ctx["hist_vol"] = float(rets.tail(63).std() * np.sqrt(252) * 100) if len(rets) >= 20 else None

    # Volumen
    if n >= 50:
        vsma = float(v.tail(50).mean())
        ctx["rel_vol"] = (float(v.iloc[-1]) / vsma) if vsma else None
        obv = _obv(c, v)
        if len(obv) > 21:
            ctx["obv_z"] = (obv.iloc[-1] - obv.iloc[-21]) / (vsma * 20 + 1e-9)

    # Struktur & placering
    highs, lows = _pivots(df)
    ctx["structure"] = _structure(highs, lows) if (highs or lows) else None
    if n >= 20:
        dc_up = float(h.tail(20).max())
        dc_lo = float(l.tail(20).min())
        ctx["range_pos"] = (ctx["price"] - dc_lo) / (dc_up - dc_lo + 1e-9)
    look = min(252, n)
    if look >= 30:
        hi52 = float(h.tail(look).max())
        ctx["dist_52w_high"] = (ctx["price"] / hi52 - 1) * 100 if hi52 else None
    # Naermeste pivot-stoette under / -modstand over (seneste ~130 bars)
    cutoff = df.index[-min(130, n)]
    sup = [p for d, p in lows if d >= cutoff and p < ctx["price"]]
    res = [p for d, p in highs if d >= cutoff and p > ctx["price"]]
    ctx["support"] = max(sup) if sup else None
    ctx["resistance"] = min(res) if res else None

    # Relativ styrke (kraever benchmark / sektor) — 63 bars ~ 3 mdr
    ctx["rs_index"] = None
    ctx["beta"] = None
    if benchmark is not None and not benchmark.empty:
        s3, b3 = _ret_pct(c, 63), _ret_pct(benchmark["Close"], 63)
        if s3 is not None and b3 is not None:
            ctx["rs_index"] = s3 - b3
        sr = c.pct_change().dropna()
        br = benchmark["Close"].pct_change().dropna()
        j = pd.concat([sr, br], axis=1, join="inner").dropna()
        if len(j) >= 60:
            var = j.iloc[:, 1].var()
            if var:
                ctx["beta"] = float(j.iloc[:, 0].cov(j.iloc[:, 1]) / var)
    ctx["rs_sector"] = None
    if sector is not None and not sector.empty:
        s3, sec3 = _ret_pct(c, 63), _ret_pct(sector["Close"], 63)
        if s3 is not None and sec3 is not None:
            ctx["rs_sector"] = s3 - sec3
    return ctx


# === Scorere (ctx -> (raw, signal) eller None) ==============================
def s_price_vs_ma(x):
    mas = [x.get("ma20"), x.get("ma50"), x.get("ma200")]
    present = [m for m in mas if m is not None]
    if not present:
        return None
    above = sum(x["price"] > m for m in present)
    frac = above / len(present)
    return f"{above}/{len(present)} MA'er under pris", clamp((frac * 2 - 1) * 90)


def s_ma_align(x):
    a, b, c = x.get("ma20"), x.get("ma50"), x.get("ma200")
    if None in (a, b, c):
        return None
    bull = (a > b) + (b > c)
    sig = 100 if bull == 2 else 10 if bull == 1 else -100
    lbl = "bullish stack" if bull == 2 else "delvis" if bull == 1 else "bearish stack"
    return lbl, float(sig)


def s_adx(x):
    a = x.get("adx")
    if a is None:
        return None
    # ADX = trendstyrke uafhaengig af retning (jf. regneark). Hoej = positiv.
    sig = _band_score(a, [(15, -30), (20, -10), (25, 10), (40, 55)], 80)
    return f"ADX {a:.0f}", sig


def s_ma_slope(x):
    s = x.get("ma50_slope")
    if s is None:
        return None
    return f"{s:+.1f}% / 20d", clamp(s * 8)


def s_cross(x):
    cr = x.get("cross")
    if cr is None:
        return None
    if cr == "golden":
        return "nyligt golden cross", 60.0
    if cr == "death":
        return "nyligt death cross", -60.0
    return "intet nyligt kryds", 0.0


def s_rs_index(x):
    r = x.get("rs_index")
    if r is None:
        return None
    return f"{r:+.1f}% vs SPY 3m", clamp(r * 5)


def s_rs_sector(x):
    r = x.get("rs_sector")
    if r is None:
        return None
    return f"{r:+.1f}% vs sektor 3m", clamp(r * 5)


def s_macd(x):
    hist = x.get("macd_hist")
    if hist is None:
        return None
    sig = 40.0 if hist > 0 else -40.0
    cr = x.get("macd_cross")
    note = ""
    if cr == "up":
        sig = clamp(sig + 35)
        note = " (bullish kryds)"
    elif cr == "down":
        sig = clamp(sig - 35)
        note = " (bearish kryds)"
    return f"hist {hist:+.3f}{note}", sig


def s_tsi(x):
    t = x.get("tsi")
    if t is None:
        return None
    sig = clamp(t)
    sig = clamp(sig + (15 if x.get("tsi_rising") else -15))
    return f"TSI {t:+.0f}{' stigende' if x.get('tsi_rising') else ''}", sig


def s_rsi(x):
    r = x.get("rsi")
    if r is None:
        return None
    if 40 <= r <= 65:
        sig = 60
    elif 30 <= r < 40:
        sig = 20
    elif 65 < r <= 72:
        sig = 5
    elif r > 72:
        sig = -45
    elif 25 <= r < 30:
        sig = 15
    else:
        sig = -10
    return f"RSI {r:.0f}", float(sig)


def s_stoch(x):
    k = x.get("stoch_k")
    if k is None:
        return None
    cu = x.get("stoch_cross_up")
    if k < 25 and cu:
        sig = 60
    elif k < 25:
        sig = 20
    elif k > 80:
        sig = -40
    elif cu:
        sig = 25
    else:
        sig = 0
    return f"%K {k:.0f}{' kryds op' if cu else ''}", float(sig)


def s_roc(x):
    r = x.get("roc")
    if r is None:
        return None
    return f"ROC {r:+.1f}%", clamp(r * 4)


def s_structure(x):
    st = x.get("structure")
    if st is None:
        return None
    sig = 80 if "optrend" in st else -80 if "nedtrend" in st else 0
    return st, float(sig)


def s_range_pos(x):
    p = x.get("range_pos")
    if p is None:
        return None
    if p <= 0.33:
        sig = 55
    elif p <= 0.66:
        sig = 10
    else:
        sig = -25
    return f"{p*100:.0f}% af 20d-range", float(sig)


def s_sr_prox(x):
    sup, res = x.get("support"), x.get("resistance")
    price = x["price"]
    if sup is None and res is None:
        return None
    sig = 0.0
    parts = []
    if sup is not None:
        ds = (price / sup - 1) * 100   # % over stoette
        parts.append(f"S {sup:.2f} (+{ds:.1f}%)")
        sig += 50 if ds <= 4 else 20 if ds <= 8 else 0
    if res is not None:
        dr = (res / price - 1) * 100   # % til modstand
        parts.append(f"R {res:.2f} (+{dr:.1f}%)")
        if dr <= 2:
            sig -= 25
    return " / ".join(parts), clamp(sig)


def s_dist_52w(x):
    d = x.get("dist_52w_high")
    if d is None:
        return None
    if d >= -5:
        sig = 60
    elif d >= -15:
        sig = 25
    elif d >= -35:
        sig = -10
    else:
        sig = -40
    return f"{d:+.0f}% fra 52u high", float(sig)


def s_atr(x):
    a = x.get("atr_pct")
    if a is None:
        return None
    if 2 <= a <= 6:
        sig = 40
    elif 1.2 <= a < 2:
        sig = 15
    elif 6 < a <= 10:
        sig = 0
    elif a < 1.2:
        sig = -30
    else:
        sig = -40
    ad = x.get("atr_dollar")
    dtxt = f" (${ad:.2f})" if ad is not None else ""
    return f"ATR {a:.1f}%{dtxt}", float(sig)


def s_bb(x):
    w = x.get("bb_width")
    if w is None:
        return None
    if w < 0.10:
        sig = 40            # squeeze foer breakout = bonus
    elif w < 0.18:
        sig = 15
    elif w > 0.35:
        sig = -10           # allerede udvidet
    else:
        sig = 0
    pb = x.get("bb_pctb")
    pbs = f" %B {pb:.2f}" if pb is not None else ""
    return f"bredde {w*100:.0f}%{pbs}", float(sig)


def s_hvol(x):
    hv = x.get("hist_vol")
    if hv is None:
        return None
    if 20 <= hv <= 45:
        sig = 30
    elif 12 <= hv < 20:
        sig = 10
    elif 45 < hv <= 70:
        sig = 0
    elif hv < 12:
        sig = -25
    else:
        sig = -35
    return f"HV {hv:.0f}%", float(sig)


def s_beta(x):
    b = x.get("beta")
    if b is None:
        return None
    if 0.8 <= b <= 1.5:
        sig = 10
    elif 0.5 <= b < 0.8:
        sig = 0
    elif 1.5 < b <= 2.2:
        sig = -10
    elif b < 0.5:
        sig = 0
    else:
        sig = -25
    return f"beta {b:.2f}", float(sig)


def s_rel_vol(x):
    r = x.get("rel_vol")
    if r is None:
        return None
    if r >= 2:
        sig = 60
    elif r >= 1.3:
        sig = 30
    elif r >= 0.8:
        sig = 0
    else:
        sig = -20
    return f"{r:.1f}x snit", float(sig)


def s_obv(x):
    z = x.get("obv_z")
    if z is None:
        return None
    return f"OBV {'stigende' if z > 0 else 'faldende'}", clamp(z * 60)


# === Parameter-register (vaegt = andel af det tekniske lag) =================
# gruppe% x parameter-i-gruppe% fra vaegtnings-skemaet. Summer til 1.00.
PARAMS = [
    ("price_vs_ma", "Pris vs 20/50/200 MA", "Trend", 0.0624, s_price_vs_ma),
    ("ma_align", "MA-alignment (20>50>200)", "Trend", 0.0624, s_ma_align),
    ("adx", "ADX (14) trendstyrke", "Trend", 0.0528, s_adx),
    ("ma_slope", "MA-haeldning (50d)", "Trend", 0.0384, s_ma_slope),
    ("cross", "Golden/death cross", "Trend", 0.0240, s_cross),
    ("rs_index", "RS vs indeks (3m)", "Relativ styrke", 0.1440, s_rs_index),
    ("rs_sector", "RS vs sektor (3m)", "Relativ styrke", 0.0960, s_rs_sector),
    ("macd", "MACD (12,26,9)", "Momentum", 0.0468, s_macd),
    ("tsi", "TSI", "Momentum", 0.0432, s_tsi),
    ("rsi", "RSI (14)", "Momentum", 0.0324, s_rsi),
    ("stoch", "Stochastic", "Momentum", 0.0288, s_stoch),
    ("roc", "Rate of Change", "Momentum", 0.0288, s_roc),
    ("structure", "Swing-struktur (HH/HL)", "Struktur", 0.0480, s_structure),
    ("range_pos", "Position i range (Donchian)", "Struktur", 0.0448, s_range_pos),
    ("sr_prox", "Support/modstand-naerhed", "Struktur", 0.0384, s_sr_prox),
    ("dist_52w", "Afstand til 52u high", "Struktur", 0.0288, s_dist_52w),
    ("atr", "ATR & ATR %", "Volatilitet", 0.0400, s_atr),
    ("bb", "Bollinger-bredde / %B", "Volatilitet", 0.0250, s_bb),
    ("hvol", "Historisk volatilitet", "Volatilitet", 0.0200, s_hvol),
    ("beta", "Beta", "Volatilitet", 0.0150, s_beta),
    ("rel_vol", "Relativ volumen", "Volumen", 0.0440, s_rel_vol),
    ("obv", "OBV-trend", "Volumen", 0.0360, s_obv),
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


def compute_technical(df: pd.DataFrame, benchmark=None, sector=None):
    """df: daglig OHLCV (Open/High/Low/Close/Volume). benchmark/sector: samme form, valgfri."""
    if df is None or df.empty:
        return {"results": [], "excluded": [("alle", "ingen prisdata")], "lag_score": 0.0}
    ctx = _build_context(df, benchmark, sector)

    results, excluded = [], []
    for key, name, group, weight, scorer in PARAMS:
        try:
            out = scorer(ctx)
        except Exception as e:
            out = None
            excluded.append((name, f"fejl: {type(e).__name__}"))
        if out is None:
            excluded.append((name, "mangler data"))
            continue
        raw, signal = out
        results.append(ParamResult(key, name, group, raw, float(signal), weight, 0.0))

    total_w = sum(r.weight for r in results)
    if total_w > 0:
        for r in results:
            r.weight /= total_w
            r.weighted = r.weight * r.signal
    lag_score = sum(r.weighted for r in results)
    return {"results": results, "excluded": excluded, "lag_score": lag_score}


def _band(score):
    if score >= 50:
        return "Staerk teknisk opstilling"
    if score >= 20:
        return "Medvind"
    if score > -20:
        return "Neutral / blandet"
    if score > -50:
        return "Svag"
    return "Fraraades (teknisk)"


def format_technical_report(ticker: str, res: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append(f" TEKNISK LAG — {ticker.upper()}")
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
    L.append(f"{'TEKNISK LAG-SCORE':<60}{score:>+8.1f}")
    L.append(f"  -> {_band(score)}")
    if res["excluded"]:
        L.append("")
        L.append("Ekskluderet (mangler data / for kort historik):")
        for name, why in res["excluded"]:
            L.append(f"  - {name} ({why})")
    L.append("=" * 78)
    return "\n".join(L)


# === yfinance-fallback (swing_report importerer denne) ======================
def _yf_ohlcv(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance er ikke installeret. Koer: pip install yfinance")
    df = yf.download(ticker, period=period, interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    return df if not df.empty else None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Teknisk lag-scoring (swing)")
    ap.add_argument("ticker")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--no-rs", action="store_true",
                    help="spring relativ styrke over (ingen SPY/sektor-hentning)")
    args = ap.parse_args()
    d = _yf_ohlcv(args.ticker, args.period)
    bench = None if args.no_rs else _yf_ohlcv("SPY", args.period)
    print(format_technical_report(args.ticker, compute_technical(d, benchmark=bench)))

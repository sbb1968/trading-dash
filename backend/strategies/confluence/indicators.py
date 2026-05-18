"""
strategies/confluence/indicators.py
────────────────────────────────────
Indikator-beregninger der matcher Pine Script v5 1:1.

KRITISKE DETALJER for at matche TradingView:

1. EMA:
   Pine bruger ta.ema(src, length) som er:
     alpha = 2 / (length + 1)
     ema[i] = src[i] * alpha + ema[i-1] * (1 - alpha)
   Med initialisering: ema starter på sma(src, length) efter første length bars,
   eller ved første ikke-na værdi. Vi bruger pandas .ewm(span=length, adjust=False)
   som producerer identisk output.

2. RSI (ta.rsi):
   Pine bruger Wilder's RMA (running moving average):
     change = src - src[1]
     up    = max(change, 0)
     down  = -min(change, 0)
     avg_up   = rma(up, length)
     avg_down = rma(down, length)
     rs  = avg_up / avg_down
     rsi = 100 - 100 / (1 + rs)
   RMA er ta.rma(src, length) = alpha 1/length, ikke 2/(length+1):
     rma[i] = src[i] * (1/length) + rma[i-1] * (1 - 1/length)
   Vi bruger pandas .ewm(alpha=1/length, adjust=False) som matcher.

3. ATR (ta.atr):
   TR = max(high-low, abs(high-close[1]), abs(low-close[1]))
   ATR = rma(TR, length)
   Bemærk: rma, IKKE ema.

4. SMA (ta.sma): Almindelig rolling mean — pandas .rolling(length).mean()

5. VWAP (ta.vwap med daglig anchor + std bands):
   Pine: [vwap, upper, lower] = ta.vwap(hlc3, anchor=timeframe.change("D"), stdev_mult=1.5)
   - hlc3 = (high + low + close) / 3
   - Anchor reset hver dag — vwap akkumulerer fra dagens første bar
   - upper = vwap + stdev_mult × stdev (af hlc3 indtil nu i dag, vægtet med volume)
   - lower = vwap - stdev_mult × stdev
   Pine bruger volume-vægtet std.dev. Vi efterligner.

6. HTF (request.security):
   Pine: ta.ema(close, 50) på 15min tf.
   Pine henter 15min lukkeprisen for det 15min-interval bar'en hører til, og
   anvender ta.ema på den serie. lookahead=barmerge.lookahead_off betyder vi
   IKKE må se fremad — på en 09:35-bar (5min) ser vi 09:00-15min-EMA'en
   (den seneste FÆRDIGE 15min bar er 09:00-09:14 — eller hvis vi bruger
   pandas resample-konvention der ankrer ved bar-start, så 09:30 er det).

   ⚠ FALDGRUBE: Pine's 15min-tf har naturlige grænser ved :00, :15, :30, :45.
   Når en 5m bar ankommer kl. 09:35, ser den 15m EMA(50) for 15m-bar'en der
   STARTER 09:30 og IKKE er færdig endnu. Pine returnerer den seneste
   færdige 15m-bar's værdi for at undgå future leak.

   Implementation: Vi resampler intraday 5m bars til 15m, beregner EMA på
   den serie, og merger tilbage på de oprindelige 5m bars med "previous"
   konvention (forward-fill men forsinket).

7. Pivot Highs/Lows (ta.pivothigh/pivotlow med L=R=3):
   En "pivot high" identificeres på bar `i` hvis:
     high[i] > high[i-1], high[i-2], high[i-3]  AND
     high[i] > high[i+1], high[i+2], high[i+3]
   Pine returnerer pivot-værdien på bar `i+R` (dvs. 3 bars EFTER den faktiske
   pivot), ikke på pivot-bar'en selv. Det er fordi vi ikke kan vide om en
   pivot er en pivot før vi har set 3 efterfølgende bars.

   ⚠ Pine-pivotværdien returneres som `na` på alle bars undtagen den hvor
   den bekræftes. Vi gengiver samme adfærd.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────
# Grundlæggende moving averages
# ─────────────────────────────────────────────────────────────────

def ema(series: pd.Series, length: int) -> pd.Series:
    """
    Exponential Moving Average — matcher Pine's ta.ema.
    alpha = 2 / (length + 1), adjust=False (rekursiv beregning).
    """
    return series.ewm(span=length, adjust=False).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """
    Wilder's Running Moving Average — matcher Pine's ta.rma.
    alpha = 1 / length, adjust=False.
    Bruges til RSI og ATR.
    """
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple Moving Average — matcher Pine's ta.sma."""
    return series.rolling(window=length, min_periods=length).mean()


# ─────────────────────────────────────────────────────────────────
# RSI — Wilder's, matcher ta.rsi
# ─────────────────────────────────────────────────────────────────

def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """
    Relative Strength Index med Wilder's smoothing.

    Pine ta.rsi formel:
        change   = src - src[1]
        up_move  = max(change, 0)
        dn_move  = -min(change, 0)   (= max(-change, 0))
        avg_up   = rma(up_move, length)
        avg_dn   = rma(dn_move, length)
        rs       = avg_up / avg_dn
        rsi      = 100 - 100/(1+rs)

    Edge case: hvis avg_dn = 0 → rsi = 100. Hvis avg_up = 0 → rsi = 0.
    """
    delta   = close.diff()
    up_move = delta.clip(lower=0.0)
    dn_move = (-delta).clip(lower=0.0)

    avg_up = rma(up_move, length)
    avg_dn = rma(dn_move, length)

    # Undgå division by zero — hvor avg_dn=0 sætter vi RSI=100
    rs  = avg_up / avg_dn.replace(0, np.nan)
    rsi_val = 100.0 - 100.0 / (1.0 + rs)

    # Når avg_dn=0 og avg_up>0  → RSI = 100
    # Når avg_up=0 og avg_dn=0  → RSI udefineret, vi sætter 50 (Pine: na)
    rsi_val = rsi_val.where(avg_dn != 0, 100.0)
    rsi_val = rsi_val.where(~((avg_up == 0) & (avg_dn == 0)), 50.0)
    return rsi_val


# ─────────────────────────────────────────────────────────────────
# ATR — Wilder's, matcher ta.atr
# ─────────────────────────────────────────────────────────────────

def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        length: int = 14) -> pd.Series:
    """
    Average True Range med Wilder's smoothing.

    TR = max(high-low, |high-close[1]|, |low-close[1]|)
    ATR = rma(TR, length)
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return rma(true_range, length)


# ─────────────────────────────────────────────────────────────────
# VWAP med daglig anchor + std-bands
# ─────────────────────────────────────────────────────────────────

def vwap_with_bands(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    stdev_mult: float = 1.5,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Volume-Weighted Average Price med std-bands, ankret pr. handelsdag.

    Matcher Pine's:
        [vwap, upper, lower] = ta.vwap(hlc3, anchor=timeframe.change("D"),
                                       stdev_mult=mult)

    Detaljer:
      - hlc3 = (high + low + close) / 3
      - vwap akkumulerer fra dagens første bar og resettes hver dag
      - std.dev er volume-vægtet (volume-weighted variance)
        Formel:  variance = Σ(vol_i × (hlc3_i - vwap)²) / Σ(vol_i)
        Bemærk: vwap her er den LØBENDE vwap (op til og med bar i), ikke
        endelig dags-vwap. Pine beregner dette korrekt rekursivt.

    Antagelse: input-serier har DatetimeIndex i en konsistent tidszone
               (vi grupperer på .date for at finde dagsskifter).

    Returnerer (vwap, upper, lower) som pd.Series med samme index.
    """
    if not isinstance(close.index, pd.DatetimeIndex):
        raise TypeError("vwap_with_bands kræver DatetimeIndex på input-serier")

    hlc3 = (high + low + close) / 3.0

    vwap_vals  = np.full(len(close), np.nan, dtype=np.float64)
    upper_vals = np.full(len(close), np.nan, dtype=np.float64)
    lower_vals = np.full(len(close), np.nan, dtype=np.float64)

    # Akkumulatorer pr. dag
    cum_pv     = 0.0   # Σ price × volume
    cum_v      = 0.0   # Σ volume
    cum_pv_sq  = 0.0   # Σ (price - vwap)² × volume  — opdateres anderledes, se nedenfor
    cum_p2v    = 0.0   # Σ price² × volume  (alternativ formel)

    dates = close.index.date
    last_date = None

    hlc3_vals = hlc3.values
    vol_vals  = volume.values

    for i in range(len(close)):
        d = dates[i]
        if d != last_date:
            # Reset dagens akkumulatorer
            cum_pv  = 0.0
            cum_v   = 0.0
            cum_p2v = 0.0
            last_date = d

        p = hlc3_vals[i]
        v = vol_vals[i]
        if np.isnan(p) or np.isnan(v) or v <= 0:
            # Behold seneste værdier eller na — Pine sætter normalt na her
            if cum_v > 0:
                vwap_vals[i]  = cum_pv / cum_v
            continue

        cum_pv  += p * v
        cum_v   += v
        cum_p2v += p * p * v

        vw = cum_pv / cum_v
        # Volume-vægtet varians = E[X²] − E[X]²
        # E[X²]_w = cum_p2v / cum_v
        # E[X]_w  = vw
        var = (cum_p2v / cum_v) - (vw * vw)
        var = max(var, 0.0)   # numerisk støj kan give svag negativ
        sd  = math.sqrt(var)

        vwap_vals[i]  = vw
        upper_vals[i] = vw + stdev_mult * sd
        lower_vals[i] = vw - stdev_mult * sd

    return (
        pd.Series(vwap_vals,  index=close.index, name="vwap"),
        pd.Series(upper_vals, index=close.index, name="vwap_upper"),
        pd.Series(lower_vals, index=close.index, name="vwap_lower"),
    )


# ─────────────────────────────────────────────────────────────────
# HTF EMA via resampling — matcher request.security
# ─────────────────────────────────────────────────────────────────

def htf_ema(
    close: pd.Series,
    htf_freq: str,
    ema_length: int,
) -> pd.Series:
    """
    Beregn EMA på højere timeframe og merge tilbage til primær timeframe.

    Pine:
        htfEma = request.security(syminfo.tickerid, "15", ta.ema(close, 50),
                                  lookahead=barmerge.lookahead_off)

    Med lookahead_off ser man kun den SENESTE FÆRDIGE bar på det højere
    tidsinterval. På en 5m bar kl. 09:35 ser man derfor 15m-bar'en der
    sluttede 09:30 (dvs. den der starter 09:15-09:30).

    htf_freq: pandas resample-frekvens som "15min", "1H", "30min"

    Implementation:
      1. Resample lukkepriser til htf_freq med last-værdi pr. bar
      2. Beregn EMA(ema_length) på den serie
      3. Reindex til oprindelig index med "ffill" — men vi skal SHIFTE
         én HTF-bar frem for at undgå future leak (lookahead_off).

    Resultat: htf_ema[i] = EMA-værdien fra den seneste FÆRDIGE htf-bar
              før eller på tidspunktet for close.index[i].
    """
    if not isinstance(close.index, pd.DatetimeIndex):
        raise TypeError("htf_ema kræver DatetimeIndex på close")

    # Resample: hvert 15-min interval får luknings-prisen for den sidste 5m bar i
    # intervallet. label='left' så bar'en kl 09:30 dækker 09:30-09:44.
    htf_close = close.resample(htf_freq, label="left", closed="left").last()
    htf_close = htf_close.dropna()

    if len(htf_close) == 0:
        return pd.Series(np.nan, index=close.index, name="htf_ema")

    htf_ema_series = ema(htf_close, ema_length)

    # Shift fremad én htf-bar så vi kun har FÆRDIGE htf-bars at se på.
    # Dette modsvarer lookahead_off.
    htf_ema_shifted = htf_ema_series.shift(1)

    # Reindex tilbage til 5m-grid og forward-fill
    result = htf_ema_shifted.reindex(close.index, method="ffill")
    result.name = "htf_ema"
    return result


# ─────────────────────────────────────────────────────────────────
# Pivot Highs / Lows — matcher ta.pivothigh / ta.pivotlow
# ─────────────────────────────────────────────────────────────────

def pivot_highs(high: pd.Series, left: int, right: int) -> pd.Series:
    """
    Identificér pivot highs.

    En bar `i` er en pivot high hvis:
      high[i] > high[i-1], high[i-2], ..., high[i-left]   (strengt højere)
      high[i] > high[i+1], high[i+2], ..., high[i+right]  (strengt højere)

    Pine ta.pivothigh returnerer pivotværdien på bar `i + right` (når den
    bekræftes), og `na` ellers. Vi gengiver præcis denne adfærd.

    Returneret serie har samme index som input. Værdi = high på pivot-bar
    placeret right bars frem; na ellers.
    """
    n = len(high)
    result = pd.Series(np.nan, index=high.index, dtype=np.float64)
    h = high.values

    for i in range(left, n - right):
        center = h[i]
        if np.isnan(center):
            continue

        is_pivot = True
        # Tjek venstre side
        for k in range(1, left + 1):
            if not (center > h[i - k]):
                is_pivot = False
                break
        if not is_pivot:
            continue
        # Tjek højre side
        for k in range(1, right + 1):
            if not (center > h[i + k]):
                is_pivot = False
                break
        if is_pivot:
            # Placér resultatet på bar i+right (bekræftelses-bar)
            result.iloc[i + right] = center

    return result


def pivot_lows(low: pd.Series, left: int, right: int) -> pd.Series:
    """
    Identificér pivot lows. Spejlvendt af pivot_highs:
      low[i] < low[i±k] for alle k i [1, left] og [1, right].
    """
    n = len(low)
    result = pd.Series(np.nan, index=low.index, dtype=np.float64)
    lo = low.values

    for i in range(left, n - right):
        center = lo[i]
        if np.isnan(center):
            continue

        is_pivot = True
        for k in range(1, left + 1):
            if not (center < lo[i - k]):
                is_pivot = False
                break
        if not is_pivot:
            continue
        for k in range(1, right + 1):
            if not (center < lo[i + k]):
                is_pivot = False
                break
        if is_pivot:
            result.iloc[i + right] = center

    return result


# ─────────────────────────────────────────────────────────────────
# Pivot state tracking (lastSwingHigh / prevSwingHigh / ...)
# ─────────────────────────────────────────────────────────────────

def pivot_state_track(pivot_series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """
    Pine:
        var float lastSwingHigh = na
        var float prevSwingHigh = na
        if not na(pivotHigh)
            prevSwingHigh := lastSwingHigh
            lastSwingHigh := pivotHigh

    Returnerer (last_swing, prev_swing) som forward-fyldte serier — værdierne
    holdes konstant bar for bar indtil næste pivot bekræftes.
    """
    last_swing = pd.Series(np.nan, index=pivot_series.index, dtype=np.float64)
    prev_swing = pd.Series(np.nan, index=pivot_series.index, dtype=np.float64)

    cur_last = np.nan
    cur_prev = np.nan

    vals = pivot_series.values
    for i in range(len(pivot_series)):
        if not np.isnan(vals[i]):
            cur_prev = cur_last
            cur_last = vals[i]
        last_swing.iloc[i] = cur_last
        prev_swing.iloc[i] = cur_prev

    return last_swing, prev_swing


# ─────────────────────────────────────────────────────────────────
# Candle patterns — bullish/bearish engulfing, hammer, shooting star
# ─────────────────────────────────────────────────────────────────

def candle_features(open_: pd.Series, high: pd.Series, low: pd.Series,
                    close: pd.Series) -> dict[str, pd.Series]:
    """
    Beregn alle Pine-candle-features brugt i entry/exit-konfluens.

    Pine-koden:
        body      = math.abs(close - open)
        rangeBar  = high - low
        upperWick = high - math.max(close, open)
        lowerWick = math.min(close, open) - low

        isBullEng = close > open and close[1] < open[1] and close > open[1] and open < close[1]
        isHammer  = lowerWick > body * 2 and upperWick < body and close > open
        strongClose = close > open and (close - low) / (rangeBar + 0.000001) > 0.66
                      and low < low[1]

        isBearEng   = close < open and close[1] > open[1] and close < open[1] and open > close[1]
        isShootStar = upperWick > body * 2 and lowerWick < body and close < open
        weakClose   = close < open and (high - close) / (rangeBar + 0.000001) > 0.66
                      and high > high[1]

    Returnerer dict med Series for hver feature.
    """
    body       = (close - open_).abs()
    range_bar  = high - low
    upper_wick = high - close.combine(open_, max)
    lower_wick = close.combine(open_, min) - low

    prev_open  = open_.shift(1)
    prev_close = close.shift(1)
    prev_low   = low.shift(1)
    prev_high  = high.shift(1)

    # Bullish patterns
    is_bull_eng = (
        (close > open_)
        & (prev_close < prev_open)
        & (close > prev_open)
        & (open_ < prev_close)
    )
    is_hammer = (
        (lower_wick > body * 2)
        & (upper_wick < body)
        & (close > open_)
    )
    strong_close = (
        (close > open_)
        & ((close - low) / (range_bar + 1e-9) > 0.66)
        & (low < prev_low)
    )

    # Bearish patterns
    is_bear_eng = (
        (close < open_)
        & (prev_close > prev_open)
        & (close < prev_open)
        & (open_ > prev_close)
    )
    is_shoot_star = (
        (upper_wick > body * 2)
        & (lower_wick < body)
        & (close < open_)
    )
    weak_close = (
        (close < open_)
        & ((high - close) / (range_bar + 1e-9) > 0.66)
        & (high > prev_high)
    )

    return {
        "body":          body,
        "range":         range_bar,
        "upper_wick":    upper_wick,
        "lower_wick":    lower_wick,
        "is_bull_eng":   is_bull_eng.fillna(False),
        "is_hammer":     is_hammer.fillna(False),
        "strong_close":  strong_close.fillna(False),
        "is_bear_eng":   is_bear_eng.fillna(False),
        "is_shoot_star": is_shoot_star.fillna(False),
        "weak_close":    weak_close.fillna(False),
    }


# ─────────────────────────────────────────────────────────────────
# Helpers brugt af entry/exit (rolling lowest/highest)
# ─────────────────────────────────────────────────────────────────

def rolling_lowest(series: pd.Series, length: int) -> pd.Series:
    """ta.lowest(series, length) — rullende minimum over de sidste `length` bars."""
    return series.rolling(window=length, min_periods=1).min()


def rolling_highest(series: pd.Series, length: int) -> pd.Series:
    """ta.highest(series, length) — rullende maximum over de sidste `length` bars."""
    return series.rolling(window=length, min_periods=1).max()


def crossover(series: pd.Series, level: float) -> pd.Series:
    """
    ta.crossover(src, level) — True på bar hvor src krydser op gennem level.
        Forrige bar:  src[i-1] <= level
        Denne bar:    src[i]   >  level
    """
    return (series.shift(1) <= level) & (series > level)


def crossunder(series: pd.Series, level: float) -> pd.Series:
    """
    ta.crossunder(src, level) — True på bar hvor src krydser ned gennem level.
        Forrige bar:  src[i-1] >= level
        Denne bar:    src[i]   <  level
    """
    return (series.shift(1) >= level) & (series < level)

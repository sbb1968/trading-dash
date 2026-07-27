#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store_bevaegelser_lib.py
========================
Bibliotek til `analyse_store_bevaegelser.py` — indikatorer, MTF-alignment og
bevaegelses-detektion.

Deles op i fire dele:

  DEL 1  Ekstra indikatorer der IKKE allerede findes i
         strategies/shared/indicators.py:  z-score, ADX, CMF, RVOL,
         Stochastic RSI (VuManChu-varianten) og WaveTrend.
         Alt andet (ema/rma/sma/rsi/atr/macd/vwap/pivots) importeres derfra,
         saa vi har ÉN Pine-matchet kilde til sandhed.

  DEL 2  Cipher B's signal-prikker — WaveTrend-kryds, store cirkler,
         divergens-prikker og guld-prikken.

  DEL 3  MTF: resampling 1-min -> 2m/3m/5m/15m/1h + "previous"-konventionen
         der sikrer at vi aldrig ser en uafsluttet HTF-bar (future leak).

  DEL 4  Bevaegelses-detektion: Metode A (ATR-swing/zigzag paa pivots) og
         Metode B (fremadrettet afkast-taerskel).

KILDE TIL CIPHER B-DEFINITIONERNE
─────────────────────────────────
Selve .pine-filen ligger ikke i repoet. Alle formler, parametre og
prik-betingelser herunder er taget fra `docs_src/market_cipher_b_teknisk.md`,
som er skrevet "læst direkte ud af Pine-scriptet" (VuManChu B Divergences /
VMC Cipher_B) og er projektets egen reference for indikatoren.
Se `PRIK_MAPNING`-noten i DEL 2 for det ene sted hvor dokumentet ikke er
entydigt, og som derfor skal bekraeftes visuelt i TradingView-tjekket.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Genbrug det eksisterende Pine-matchede bibliotek — genopfind ingenting.
from strategies.shared.indicators import (
    ema, sma, rma, rsi, atr, macd,
    vwap_with_bands, pivot_highs, pivot_lows,
    rolling_lowest, rolling_highest,
)


# =============================================================================
# KONSTANTER — indikator-laengder og Cipher B-niveauer
# =============================================================================
# --- Mean-reversion / regime (cockpittet) --------------------------------
Z_LEN            = 30        # z = (close - sma(30)) / population-std(30)
ADX_DI_LEN       = 14        # ADX: DI-laengde
ADX_SMOOTH       = 14        # ADX: udglatning
ADX_TREND_LEVEL  = 25        # regime-taerskel (kun til rapportering)
ATR_LEN          = 14        # ATR(14), Wilder/rma
VWAP_STDEV_MULT  = 1.5       # baand-multiplier (vi bruger kun selve vwap'en)
CMF_LEN          = 20        # Chaikin Money Flow
RVOL_LEN         = 20        # snit af de FOREGAAENDE 20 bars

# --- Oscillatorer (Cipher B) ---------------------------------------------
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RSI_LEN          = 14

# Stochastic RSI — VuManChu-defaults (docs_src/market_cipher_b_teknisk.md §7)
STOCH_LEN        = 14        # Stoch-laengde
STOCH_RSI_LEN    = 14        # RSI-laengde under stochen
STOCH_K_SMOOTH   = 3         # K-udglatning
STOCH_D_SMOOTH   = 3         # D-udglatning
STOCH_USE_LOG    = True      # log-skala til (VuManChu-default)

# WaveTrend — VuManChu-defaults (§2 i teknisk gennemgang)
WT_CHANNEL_LEN   = 9         # esa/de-laengde
WT_AVERAGE_LEN   = 12        # tci-laengde  -> wt1
WT_MA_LEN        = 3         # sma paa wt1  -> wt2

# WaveTrend-niveauer (§2)
WT_OB_LEVEL      =  53.0     # "overkoebt"  = wt2 >= 53
WT_OS_LEVEL      = -53.0     # "oversolgt"  = wt2 <= -53
WT_OS_LEVEL3     = -75.0     # ekstrem-niveau brugt af guld-prikken

# Divergens-graenser (§4)
WT_DIV_OB        =  45.0     # "haard" bearish-graense
WT_DIV_OS        = -65.0     # "haard" bullish-graense
WT_DIV_OB_ADD    =  15.0     # 2. WT-range, bloedere (flere/svagere divergenser)
WT_DIV_OS_ADD    = -40.0
GOLD_RSI_MAX     = 30.0      # RSI ved bunden skal vaere < 30
GOLD_DIP_MIN     = -5.0      # wtLow_prev - wt2 <= -5

# Hvilke divergens-kilder der er slaaet TIL (VuManChu-defaults, §4/§11)
DIV_SHOW_WT      = True      # haard WT-range
DIV_SHOW_WT_ADD  = True      # 2. WT-range (default vist)
DIV_SHOW_STOCH   = False     # default slaaet fra
DIV_SHOW_RSI     = False     # default slaaet fra

# Futures-session: CME-doegnet starter 18:00 ET. +6t goer 18:00 ET til
# midnat, saa .date giver den korrekte CME-handelsdato (VWAP-anchor).
CME_SESSION_SHIFT_H = 6

# MES-specifikt: 1 tick = 0.25 indekspoint. I doede nattetimer kan MES printe
# et dusin identiske barer i traek; saa gaar TR mod 0 og ATR mod 0 med den.
# Alt der DIVIDERER med ATR (vwap_dist_atr, size_atr) eksploderer saa til
# vaerdier i milliard-klassen — det er ikke information, det er division med
# naesten-nul. Under ét tick regnes ATR som degenereret.
ATR_FLOOR = 0.25


# =============================================================================
# DEL 1 — EKSTRA INDIKATORER
# =============================================================================

def zscore(close: pd.Series, length: int = Z_LEN) -> pd.Series:
    """
    z = (close - sma(close, n)) / stdev(close, n)

    Pine's ta.stdev er POPULATIONS-standardafvigelse (biased, ddof=0) — ikke
    pandas' default sample-std. Det er en reel forskel paa ~1.7% ved n=30.
    """
    mid = sma(close, length)
    sd = close.rolling(window=length, min_periods=length).std(ddof=0)
    return (close - mid) / sd.replace(0.0, np.nan)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Pine's ta.tr — foerste bar er high-low (intet forrige close)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr.iloc[0] = (high.iloc[0] - low.iloc[0]) if len(high) else np.nan
    return tr


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        di_len: int = ADX_DI_LEN, adx_smooth: int = ADX_SMOOTH) -> pd.Series:
    """
    Average Directional Index — matcher Pine's ta.dmi(diLen, adxSmooth).

    Pine:
        up      = ta.change(high)
        down    = -ta.change(low)
        plusDM  = up > down and up > 0     ? up   : 0
        minusDM = down > up and down > 0   ? down : 0
        trur    = ta.rma(ta.tr, diLen)
        plus    = 100 * ta.rma(plusDM,  diLen) / trur
        minus   = 100 * ta.rma(minusDM, diLen) / trur
        adx     = 100 * ta.rma(|plus - minus| / (plus + minus), adxSmooth)

    Bemaerk: rma (Wilder), ikke ema — og DI-laengde og ADX-udglatning er to
    separate parametre (begge 14 her).
    """
    up = high.diff()
    down = -low.diff()

    plus_dm = pd.Series(
        np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    trur = rma(true_range(high, low, close), di_len)
    trur_safe = trur.replace(0.0, np.nan)

    plus_di = 100.0 * rma(plus_dm, di_len) / trur_safe
    minus_di = 100.0 * rma(minus_dm, di_len) / trur_safe

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = (plus_di - minus_di).abs() / di_sum
    return 100.0 * rma(dx.fillna(0.0), adx_smooth)


def cmf(high: pd.Series, low: pd.Series, close: pd.Series,
        volume: pd.Series, length: int = CMF_LEN) -> pd.Series:
    """
    Chaikin Money Flow.
        mult = ((close - low) - (high - close)) / (high - low)
        mfv  = mult * volume
        cmf  = sum(mfv, n) / sum(volume, n)

    Naar high == low (nul-range bar) er mult udefineret -> 0, som i Pine
    (der bruger `high - low == 0 ? 0 : ...`).
    """
    rng = (high - low)
    mult = ((close - low) - (high - close)) / rng
    mult = mult.where(rng != 0, 0.0)
    mfv = mult * volume
    vol_sum = volume.rolling(window=length, min_periods=length).sum()
    return mfv.rolling(window=length, min_periods=length).sum() / vol_sum.replace(0.0, np.nan)


def rvol(volume: pd.Series, length: int = RVOL_LEN) -> pd.Series:
    """
    Relativ volumen = volume / snit af de FOREGAAENDE `length` bars.

    Bemaerk .shift(1): den aktuelle bar indgaar IKKE i sit eget
    sammenlignings-snit — ellers ville en volumen-eksplosion delvist
    normalisere sig selv vaek.
    """
    base = sma(volume.shift(1), length)
    return volume / base.replace(0.0, np.nan)


def stoch_rsi(close: pd.Series,
              stoch_len: int = STOCH_LEN,
              rsi_len: int = STOCH_RSI_LEN,
              k_smooth: int = STOCH_K_SMOOTH,
              d_smooth: int = STOCH_D_SMOOTH,
              use_log: bool = STOCH_USE_LOG) -> tuple[pd.Series, pd.Series]:
    """
    Cipher B's "Stoch" — en Stochastic beregnet OVEN PAA RSI (ikke paa prisen).

    VuManChu (f_stochrsi):
        src = use_log ? log(close) : close
        r   = rsi(src, rsi_len)
        k   = sma( stoch(r, r, r, stoch_len), k_smooth )
        d   = sma( k, d_smooth )
    hvor stoch(x, x, x, n) = 100 * (x - lowest(x, n)) / (highest(x, n) - lowest(x, n))

    Vigtigt: `use_log` er TIL som VuManChu-default. RSI af log(pris) er ikke
    helt det samme som RSI af prisen — derfor er den med her.
    Returnerer (k, d). Kolonnen vi gemmer er k.
    """
    src = np.log(close) if use_log else close
    r = rsi(src, rsi_len)

    lo = rolling_lowest(r, stoch_len)
    hi = rolling_highest(r, stoch_len)
    st = 100.0 * (r - lo) / (hi - lo).replace(0.0, np.nan)

    k = sma(st, k_smooth)
    d = sma(k, d_smooth)
    return k, d


def wavetrend(high: pd.Series, low: pd.Series, close: pd.Series,
              channel_len: int = WT_CHANNEL_LEN,
              average_len: int = WT_AVERAGE_LEN,
              ma_len: int = WT_MA_LEN) -> tuple[pd.Series, pd.Series]:
    """
    WaveTrend (LazyBear/VuManChu) — motoren bag Cipher B.

        ap  = hlc3
        esa = ema(ap, channel_len)
        de  = ema(|ap - esa|, channel_len)
        ci  = (ap - esa) / (0.015 * de)
        wt1 = ema(ci, average_len)      # hurtig boelge
        wt2 = sma(wt1, ma_len)          # langsom boelge

    Returnerer (wt1, wt2). wt_diff = wt1 - wt2 er den hvide "Fast WT"-flade,
    som Cipher B misvisende kalder "VWAP".

    Guard: `de` kan blive 0 paa helt flade strækninger -> ci = inf. Vi saetter
    NaN der i stedet, saa den fejl ikke forplanter sig gennem ema'en som inf.
    """
    ap = (high + low + close) / 3.0
    esa = ema(ap, channel_len)
    de = ema((ap - esa).abs(), channel_len)
    ci = (ap - esa) / (0.015 * de.replace(0.0, np.nan))
    wt1 = ema(ci, average_len)
    wt2 = sma(wt1, ma_len)
    return wt1, wt2


def vwap_session(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series,
                 stdev_mult: float = VWAP_STDEV_MULT,
                 shift_hours: int = CME_SESSION_SHIFT_H) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Daglig-ankret VWAP for FUTURES.

    Det eksisterende `vwap_with_bands` ankrer paa `index.date`. For aktier er
    det rigtigt, men CME-doegnet starter 18:00 ET — TradingViews daglige VWAP
    paa MES resetter derfor 18:00 ET, ikke ved midnat. Havde vi ankret paa
    kalenderdatoen, ville VWAP'en vaere forkert i hele aften-sessionen (og
    dermed ogsaa `vwap_dist_atr`, som er en af de 18 parametre).

    Loesning: vi flytter indekset +6 timer i ET-tid, saa 18:00 ET bliver til
    midnat og `.date` giver den rigtige CME-handelsdato. Derefter genbruger vi
    `vwap_with_bands` uaendret og saetter det oprindelige indeks tilbage.
    """
    et = close.index.tz_convert("America/New_York")
    fake_idx = pd.DatetimeIndex(et + pd.Timedelta(hours=shift_hours))

    def _re(s: pd.Series) -> pd.Series:
        out = s.copy()
        out.index = fake_idx
        return out

    vw, up, lo = vwap_with_bands(_re(high), _re(low), _re(close), _re(volume),
                                 stdev_mult=stdev_mult)
    for s in (vw, up, lo):
        s.index = close.index
    return vw, up, lo


# =============================================================================
# DEL 2 — CIPHER B's SIGNAL-PRIKKER
# =============================================================================
#
# PRIK_MAPNING — det ene sted der kraever visuel bekraeftelse
# ──────────────────────────────────────────────────────────
# Specen beder om 7 kategorier: udvandet/almindelig/kraftig i groen og roed,
# plus guld. Cipher B tegner praecis tre distinkte groenne prik-stilarter
# (og tre roede). Ud fra §3 og FAQ'en i market_cipher_b_teknisk.md
# ("Lille = et hvilket som helst kryds. Stor = kryds i et yderpunkt
#  (rigtigt signal). Divergens-prikken = kryds hvor der ogsaa er divergens.")
# mapper vi efter signal-STYRKE:
#
#   udvandet_groen  = almindeligt wt1/wt2-op-kryds uden filter (den lille prik)
#   alm_groen       = divergens-prik  (buySignalDiv)
#   kraftig_groen   = stor cirkel     (buySignal: kryds op MENS wt2 <= -53)
#   ...og spejlvendt for roed. guld = wtGoldBuy.
#
# Falder flere sammen paa samme bar vinder den staerkeste:
#   guld > kraftig > alm > udvandet.
#
# ⚠ Denne rangordning er den eneste antagelse i hele pipelinen der ikke kan
#   udledes entydigt af dokumentationen. Den skal bekraeftes visuelt mod
#   TradingView i fidelitets-tjekket (spec afsnit 6) — hvis Cipher B fx
#   tegner 2.-range-divergensen (den gennemsigtige) som "udvandet" og det
#   almindelige kryds som noget andet, aendres kun `_dot_priority` herunder.

DOT_UDVANDET_GROEN = "udvandet_groen"
DOT_ALM_GROEN      = "alm_groen"
DOT_KRAFTIG_GROEN  = "kraftig_groen"
DOT_UDVANDET_ROED  = "udvandet_roed"
DOT_ALM_ROED       = "alm_roed"
DOT_KRAFTIG_ROED   = "kraftig_roed"
DOT_GULD           = "guld"
DOT_INGEN          = "ingen"

# Staerkest foerst — bruges naar flere prikker falder paa samme bar.
_DOT_PRIORITY = [
    DOT_GULD,
    DOT_KRAFTIG_GROEN, DOT_KRAFTIG_ROED,
    DOT_ALM_GROEN,     DOT_ALM_ROED,
    DOT_UDVANDET_GROEN, DOT_UDVANDET_ROED,
]

# Divergens-prikker bekraeftes 2 barer efter selve toppen/bunden (5-bars
# fraktal). Krydsprikker har ingen forsinkelse.
_DOT_LAG = {
    DOT_GULD: 2,
    DOT_ALM_GROEN: 2, DOT_ALM_ROED: 2,
    DOT_KRAFTIG_GROEN: 0, DOT_KRAFTIG_ROED: 0,
    DOT_UDVANDET_GROEN: 0, DOT_UDVANDET_ROED: 0,
}


def _shift_np(a: np.ndarray, k: int) -> np.ndarray:
    """a[i-k] som numpy-array (NaN i starten). k >= 0."""
    out = np.full(len(a), np.nan, dtype=np.float64)
    if k == 0:
        out[:] = a
    elif k < len(a):
        out[k:] = a[:-k]
    return out


def _ffill_np(a: np.ndarray) -> np.ndarray:
    """Pine's valuewhen(cond, expr, 0) = forward-fill af expr paa cond-barer."""
    return pd.Series(a).ffill().to_numpy()


def find_divergences(src: pd.Series, high: pd.Series, low: pd.Series,
                     top_limit: float, bot_limit: float,
                     use_limits: bool = True) -> dict[str, np.ndarray]:
    """
    VuManChu's f_findDivs — 5-bars fraktaler + regulaer divergens.

    Fraktal (Pine, bekraeftet paa bar i, med CENTRUM paa bar i-2):
        top: src[4] < src[2] and src[3] < src[2] and src[2] > src[1] and src[2] > src[0]
        bot: spejlvendt

    Divergens:
        bearSignal = fractalTop and high[2] > highPrice and src[2] < highPrev
        bullSignal = fractalBot and low[2]  < lowPrice  and src[2] > lowPrev
    hvor *Prev/*Price er vaerdierne fra den FORRIGE fraktal
    (valuewhen(...)[2] — de to bars forskydning goer at "forrige" ikke er
    fraktalen vi lige har bekraeftet).

    Alle returnerede arrays er indekseret paa BEKRAEFTELSES-baren (i), ikke
    paa centrum (i-2). Det er med vilje: bekraeftelses-baren er det tidligste
    tidspunkt hvor signalet faktisk var kendt.
    """
    s = src.to_numpy(dtype=np.float64)
    hi = high.to_numpy(dtype=np.float64)
    lo = low.to_numpy(dtype=np.float64)

    s0, s1, s2, s3, s4 = (_shift_np(s, k) for k in (0, 1, 2, 3, 4))
    hi2 = _shift_np(hi, 2)
    lo2 = _shift_np(lo, 2)

    # NaN-sammenligninger giver False i numpy — praecis som Pine's na-haandtering.
    with np.errstate(invalid="ignore"):
        is_top = (s4 < s2) & (s3 < s2) & (s2 > s1) & (s2 > s0)
        is_bot = (s4 > s2) & (s3 > s2) & (s2 < s1) & (s2 < s0)

        if use_limits:
            top_ok = is_top & (s2 >= top_limit)
            bot_ok = is_bot & (s2 <= bot_limit)
        else:
            top_ok, bot_ok = is_top, is_bot

        frac_top_val = np.where(top_ok, s2, np.nan)
        frac_top_px = np.where(top_ok, hi2, np.nan)
        frac_bot_val = np.where(bot_ok, s2, np.nan)
        frac_bot_px = np.where(bot_ok, lo2, np.nan)

        high_prev = _shift_np(_ffill_np(frac_top_val), 2)
        high_price = _shift_np(_ffill_np(frac_top_px), 2)
        low_prev = _shift_np(_ffill_np(frac_bot_val), 2)
        low_price = _shift_np(_ffill_np(frac_bot_px), 2)

        bear = top_ok & (hi2 > high_price) & (s2 < high_prev)
        bull = bot_ok & (lo2 < low_price) & (s2 > low_prev)

    return {
        "bull": bull, "bear": bear,
        "frac_top": top_ok, "frac_bot": bot_ok,
        "low_prev": low_prev, "high_prev": high_prev,
    }


def cipher_b_dots(df: pd.DataFrame) -> pd.DataFrame:
    """
    Beregn Cipher B's prikker for én timeframe.

    Kraever kolonnerne wt1, wt2, rsi, stoch_k, high, low i `df`.

    Returnerer en DataFrame med samme indeks og:
        dot_type    kategori (str), eller "ingen" — placeret paa den bar hvor
                    prikken var KENDT (bekraeftelses-baren)
        dot_lag     0 eller 2 — hvor mange barer TILBAGE prikken tegnes paa
                    charten (divergenser tegnes med offset -2)

    Skelnen mellem "kendt" og "tegnet" er hele pointen: vi maa kun bruge en
    prik i et start-snapshot hvis den var bekraeftet paa eller foer start-
    baren (ingen future leak), men `bars_to_dot` skal taelle til der hvor
    Soeren rent faktisk SER prikken paa charten.
    """
    wt1 = df["wt1"]
    wt2 = df["wt2"]
    n = len(df)

    # --- Kryds -----------------------------------------------------------
    diff = (wt1 - wt2)
    prev = diff.shift(1)
    cross_up = ((prev <= 0) & (diff > 0)).to_numpy()
    cross_dn = ((prev >= 0) & (diff < 0)).to_numpy()

    oversold = (wt2 <= WT_OS_LEVEL).to_numpy()
    overbought = (wt2 >= WT_OB_LEVEL).to_numpy()

    buy_signal = cross_up & oversold      # stor groen cirkel
    sell_signal = cross_dn & overbought   # stor roed cirkel

    # --- Divergenser -----------------------------------------------------
    wt_div = find_divergences(wt2, df["high"], df["low"],
                              WT_DIV_OB, WT_DIV_OS, use_limits=True)
    wt_div_add = find_divergences(wt2, df["high"], df["low"],
                                  WT_DIV_OB_ADD, WT_DIV_OS_ADD, use_limits=True)
    stoch_div = find_divergences(df["stoch_k"], df["high"], df["low"],
                                 80.0, 20.0, use_limits=False)
    rsi_div = find_divergences(df["rsi"], df["high"], df["low"],
                               60.0, 30.0, use_limits=False)

    off = np.zeros(n, dtype=bool)
    bull_div = (
        (wt_div["bull"] if DIV_SHOW_WT else off)
        | (wt_div_add["bull"] if DIV_SHOW_WT_ADD else off)
        | (stoch_div["bull"] if DIV_SHOW_STOCH else off)
        | (rsi_div["bull"] if DIV_SHOW_RSI else off)
    )
    bear_div = (
        (wt_div["bear"] if DIV_SHOW_WT else off)
        | (wt_div_add["bear"] if DIV_SHOW_WT_ADD else off)
        | (stoch_div["bear"] if DIV_SHOW_STOCH else off)
        | (rsi_div["bear"] if DIV_SHOW_RSI else off)
    )

    # --- Guld-prikken ----------------------------------------------------
    # lastRsi = valuewhen(wtFractalBot, rsi[2], 0)[2] — RSI-vaerdien ved den
    # forrige WT-bund.
    rsi_at_bot = np.where(wt_div["frac_bot"], _shift_np(df["rsi"].to_numpy(), 2), np.nan)
    last_rsi = _shift_np(_ffill_np(rsi_at_bot), 2)

    wt_low_prev = wt_div["low_prev"]
    wt2_np = wt2.to_numpy()

    with np.errstate(invalid="ignore"):
        gold = (
            ((wt_div["bull"] if DIV_SHOW_WT else off)
             | (rsi_div["bull"] if DIV_SHOW_RSI else off))
            & (wt_low_prev <= WT_OS_LEVEL3)
            & (wt2_np > WT_OS_LEVEL3)
            & ((wt_low_prev - wt2_np) <= GOLD_DIP_MIN)
            & (last_rsi < GOLD_RSI_MAX)
        )

    # --- Saml til én kategori pr. bar (staerkeste vinder) ----------------
    masks = {
        DOT_GULD:            gold,
        DOT_KRAFTIG_GROEN:   buy_signal,
        DOT_KRAFTIG_ROED:    sell_signal,
        DOT_ALM_GROEN:       bull_div,
        DOT_ALM_ROED:        bear_div,
        DOT_UDVANDET_GROEN:  cross_up,
        DOT_UDVANDET_ROED:   cross_dn,
    }

    # Sentinel er STRENGEN "ingen", ikke None: None bliver til float NaN naar
    # kolonnen gaar gennem pandas/parquet, og `nan is not None` er True — saa
    # ville hver eneste bar se ud som om der sad en prik paa den.
    dot_type = np.full(n, DOT_INGEN, dtype=object)
    dot_lag = np.zeros(n, dtype=np.int16)
    taken = np.zeros(n, dtype=bool)
    for name in _DOT_PRIORITY:          # staerkeste foerst -> svagere overskriver ikke
        m = np.asarray(masks[name], dtype=bool) & ~taken
        dot_type[m] = name
        dot_lag[m] = _DOT_LAG[name]
        taken |= m

    return pd.DataFrame({"dot_type": dot_type, "dot_lag": dot_lag}, index=df.index)


def nearest_prior_dot(dot_type: np.ndarray, dot_lag: np.ndarray,
                      i: int, lookback: int) -> tuple[str, float]:
    """
    Find den naermeste FOREGAAENDE prik set fra bar `i`.

    Returnerer (kategori, bars_to_dot).
      - Vi scanner kun bekraeftelses-barer j <= i  => ingen future leak.
      - bars_to_dot maales til den bar hvor prikken TEGNES paa charten
        (j - dot_lag), saa tallet svarer til det Soeren taeller i TradingView.
      - Ingen prik inden for `lookback` barer -> ("ingen", NaN).
    """
    lo = max(0, i - lookback + 1)
    for j in range(i, lo - 1, -1):
        t = dot_type[j]
        if t != DOT_INGEN:
            return t, float(i - (j - int(dot_lag[j])))
    return DOT_INGEN, np.nan


# =============================================================================
# DEL 3 — MTF: RESAMPLING + "PREVIOUS"-KONVENTIONEN
# =============================================================================

def to_ns(idx: pd.DatetimeIndex) -> np.ndarray:
    """
    DatetimeIndex -> int64 NANOSEKUNDER siden epoch.

    pandas 3 skiftede default-oploesning fra nanosekunder til mikrosekunder, saa
    `.asi8` returnerer nu tal i indeksets EGEN enhed. Vi laaser den eksplicit,
    saa int64-tidsstempler fra forskellige kilder altid kan sammenlignes og
    konverteres tilbage uden at lande i 1970.
    """
    return idx.as_unit("ns").astype("int64").to_numpy()


# Timeframe-labels -> (pandas-frekvens, laengde i minutter)
TF_SPEC: dict[str, tuple[str, int]] = {
    "1h":  ("60min", 60),
    "15m": ("15min", 15),
    "5m":  ("5min",   5),
    "3m":  ("3min",   3),
    "2m":  ("2min",   2),
}


def resample_ohlcv(df1m: pd.DataFrame, freq: str) -> pd.DataFrame:
    """
    1-min -> hoejere timeframe. Indekset er bar-START (label='left'), praecis
    som TradingView tidsstempler barer.

    Tomme buckets (weekend, vedligeholdelses-pause) droppes — ellers ville
    ema/rma "glide" gennem hundredvis af ikke-eksisterende barer.
    """
    out = df1m.resample(freq, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    })
    return out.dropna(subset=["close"])


def build_indicator_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Beregn HELE indikator-suiten paa én timeframes barer.

    Alt beregnes paa den timeframes egen serie — vi resampler ALDRIG en
    faerdig indikator ned/op. Kolonnen `bar_close` er bar-START + TF-laengde
    og er det der bruges til MTF-alignment (se `align_index`).
    """
    o, h, l, c, v = (bars[x] for x in ("open", "high", "low", "close", "volume"))

    out = pd.DataFrame(index=bars.index)
    out["close"] = c
    out["high"] = h
    out["low"] = l

    # Mean-reversion / regime
    out["z"] = zscore(c, Z_LEN)
    out["adx"] = adx(h, l, c, ADX_DI_LEN, ADX_SMOOTH)
    out["atr"] = atr(h, l, c, ATR_LEN)
    vw, _, _ = vwap_session(h, l, c, v)
    out["vwap"] = vw
    # NaN frem for et astronomisk tal naar ATR er degenereret (< 1 tick).
    out["vwap_dist_atr"] = (c - vw) / out["atr"].where(out["atr"] >= ATR_FLOOR)
    out["cmf"] = cmf(h, l, c, v, CMF_LEN)
    out["rvol"] = rvol(v, RVOL_LEN)

    # Oscillatorer
    m_line, m_sig, m_hist = macd(c, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    out["macd_line"] = m_line
    out["macd_signal"] = m_sig
    out["macd_hist"] = m_hist
    out["rsi"] = rsi(c, RSI_LEN)
    k, _ = stoch_rsi(c)
    out["stoch_k"] = k
    wt1, wt2 = wavetrend(h, l, c)
    out["wt1"] = wt1
    out["wt2"] = wt2
    out["wt_diff"] = wt1 - wt2

    # Cipher B-prikker
    dots = cipher_b_dots(out)
    out["dot_type"] = dots["dot_type"]
    out["dot_lag"] = dots["dot_lag"]
    return out


# De numeriske indikatorer der gemmes pr. TF pr. snapshot-punkt.
SNAPSHOT_NUMERIC = [
    "close", "z", "adx", "atr", "vwap", "vwap_dist_atr", "cmf", "rvol",
    "macd_line", "macd_signal", "macd_hist", "rsi", "stoch_k",
    "wt1", "wt2", "wt_diff",
]


@dataclass
class TimeframeView:
    """En timeframes barer + indikatorer + det der skal til for alignment."""
    label: str
    minutes: int
    feat: pd.DataFrame          # indikator-frame, indekseret paa bar-START
    bar_close_ns: np.ndarray    # bar-START + TF-laengde, som int64 NANOSEKUNDER
    num: np.ndarray             # feat[SNAPSHOT_NUMERIC] som 2D float-array
    dot_type: np.ndarray
    dot_lag: np.ndarray

    @classmethod
    def build(cls, label: str, df1m: pd.DataFrame) -> "TimeframeView":
        freq, minutes = TF_SPEC[label]
        bars = resample_ohlcv(df1m, freq)
        feat = build_indicator_frame(bars)
        bar_close = feat.index + pd.Timedelta(minutes=minutes)
        return cls(
            label=label,
            minutes=minutes,
            feat=feat,
            # .as_unit("ns") er IKKE pynt: pandas 3 bruger mikrosekunder som
            # default-oploesning, saa .asi8 ville give µs. Vi laaser enheden,
            # saa alle int64-tidsstempler i pipelinen er sammenlignelige.
            bar_close_ns=to_ns(bar_close),
            # 2D-array frem for pandas-opslag: der laves ~700.000 raekke-opslag
            # under snapshot-byggeriet, og .iloc pr. celle ville tage timer.
            num=feat[SNAPSHOT_NUMERIC].to_numpy(dtype=np.float64),
            dot_type=feat["dot_type"].to_numpy(dtype=object),
            dot_lag=feat["dot_lag"].to_numpy(),
        )

    def align_index(self, known_at_ns: np.int64) -> int:
        """
        "Previous"-konventionen: find den seneste bar paa DENNE timeframe der
        var HELT AFSLUTTET paa tidspunktet `known_at` (= lukketiden for den
        bar snapshottet tages paa).

        Det er det der forhindrer future leak paa tvaers af timeframes: staar
        vi paa en 15m-bar der lukker 10:15, er den seneste faerdige 1h-bar den
        der lukkede 10:00 — ikke den igangvaerende 10:00-11:00-bar.

        Returnerer -1 hvis ingen bar er afsluttet endnu.
        """
        return int(np.searchsorted(self.bar_close_ns, known_at_ns, side="right")) - 1


# =============================================================================
# DEL 4 — BEVAEGELSES-DETEKTION
# =============================================================================

def roll_segment_id(bar_index: pd.DatetimeIndex,
                    roll_times: list[pd.Timestamp]) -> np.ndarray:
    """
    Nummerér barer efter hvilken futures-kontrakt de tilhoerer.

    Den kontinuerlige MES-serie er RAW-stitched, ikke back-adjusted (se
    `curate_futures_data.stitch_continuous`): ved hver kvartals-rul springer
    prisen med kontraktens carry-spread. Det spring er ikke en markeds-
    bevaegelse, og en "bevaegelse" der spaender over et rul har en opdigtet
    stoerrelse.

    `roll_times` er sidste tidsstempel i hver udloebende kontrakt. To barer
    hoerer til samme segment hvis de har samme segment-id.
    """
    edges = np.array(sorted(to_ns(pd.DatetimeIndex(roll_times))))
    return np.searchsorted(edges, to_ns(bar_index), side="right")


@dataclass
class Event:
    """Én stor bevaegelse. Indeks peger ind i detekterings-timeframens barer."""
    metode: str          # "swing" | "fwd"
    retning: str         # "up" | "down"
    start_idx: int
    end_idx: int
    start_pris: float
    slut_pris: float
    size_pt: float
    size_atr: float


def detect_swings(bars: pd.DataFrame, atr_series: pd.Series,
                  pivot_left: int, pivot_right: int,
                  min_atr_mult: float) -> list[Event]:
    """
    METODE A — ATR-swing / zigzag.

    1. Find pivot-highs/lows (genbrug af det Pine-matchede bibliotek).
    2. Tving skiftevis high/low: to pivots af samme type i traek reduceres til
       den mest ekstreme. Uden det ville "ben" kunne gaa high->high.
    3. Hvert par af nabo-pivots er ét ben. Behold benet hvis dets stoerrelse
       er >= min_atr_mult x ATR MAALT VED BENETS START.

    Bemaerk om pivots: en pivot bekraeftes foerst `pivot_right` barer senere.
    Det paavirker ikke start-snapshottet (det bruger kun data til og med
    start-baren) — men selve DEFINITIONEN af hvor et ben begynder er
    fremadskuende. Det er i orden: benets start/slut/stoerrelse er labelen,
    ikke en feature.
    """
    ph = pivot_highs(bars["high"], pivot_left, pivot_right)
    pl = pivot_lows(bars["low"], pivot_left, pivot_right)

    # Pine returnerer vaerdien paa bekraeftelses-baren (i+right); flyt tilbage
    # til selve pivot-baren.
    ph_c = ph.shift(-pivot_right).to_numpy()
    pl_c = pl.shift(-pivot_right).to_numpy()

    pivots: list[tuple[int, str, float]] = []
    for i in range(len(bars)):
        if not np.isnan(ph_c[i]):
            pivots.append((i, "high", float(ph_c[i])))
        if not np.isnan(pl_c[i]):
            pivots.append((i, "low", float(pl_c[i])))
    pivots.sort(key=lambda p: p[0])

    # Tving alternering
    alt: list[tuple[int, str, float]] = []
    for p in pivots:
        if alt and alt[-1][1] == p[1]:
            keep_new = (p[2] > alt[-1][2]) if p[1] == "high" else (p[2] < alt[-1][2])
            if keep_new:
                alt[-1] = p
            continue
        alt.append(p)

    atr_np = atr_series.to_numpy()
    events: list[Event] = []
    for a, b in zip(alt, alt[1:]):
        i0, t0, p0 = a
        i1, _, p1 = b
        if i1 <= i0:
            continue
        a0 = atr_np[i0]
        if not np.isfinite(a0) or a0 < ATR_FLOOR:
            continue        # degenereret volatilitet — size_atr ville vaere nonsens
        size = abs(p1 - p0)
        if size < min_atr_mult * a0:
            continue
        events.append(Event(
            metode="swing",
            retning="up" if t0 == "low" else "down",
            start_idx=i0, end_idx=i1,
            start_pris=p0, slut_pris=p1,
            size_pt=size, size_atr=size / a0,
        ))
    return events


def detect_forward_moves(bars: pd.DataFrame, atr_series: pd.Series,
                         fwd_n: int, fwd_atr_mult: float) -> list[Event]:
    """
    METODE B — fremadrettet afkast-taerskel.

    For hver bar t kigges `fwd_n` barer frem:
      op-event  hvis max(high[t+1..t+N]) - close[t] >= mult x atr[t]
      ned-event hvis close[t] - min(low[t+1..t+N])  >= mult x atr[t]
    `end_idx` er baren hvor ekstremet naas.

    Overlappende events af samme retning ville ellers taelle den SAMME
    bevaegelse 10-20 gange (alle barer op til toppen udloeser den). Vi loeser
    det grådigt: sortér efter stoerrelse i ATR, behold det stoerste, smid alt
    der overlapper dets [start, slut]-spaend, gentag.
    """
    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    close = bars["close"].to_numpy()
    a = atr_series.to_numpy()
    n = len(bars)

    raw: list[Event] = []
    for t in range(n - 1):
        at = a[t]
        if not np.isfinite(at) or at < ATR_FLOOR:
            continue        # degenereret volatilitet — 2 x ATR ville vaere < 1 tick
        j0, j1 = t + 1, min(t + fwd_n, n - 1)
        if j1 < j0:
            continue
        thr = fwd_atr_mult * at

        w_hi = high[j0:j1 + 1]
        k = int(np.argmax(w_hi))
        up_move = w_hi[k] - close[t]
        if up_move >= thr:
            raw.append(Event("fwd", "up", t, j0 + k, float(close[t]),
                             float(w_hi[k]), float(up_move), float(up_move / at)))

        w_lo = low[j0:j1 + 1]
        k = int(np.argmin(w_lo))
        dn_move = close[t] - w_lo[k]
        if dn_move >= thr:
            raw.append(Event("fwd", "down", t, j0 + k, float(close[t]),
                             float(w_lo[k]), float(dn_move), float(dn_move / at)))

    kept: list[Event] = []
    for retning in ("up", "down"):
        cand = sorted([e for e in raw if e.retning == retning],
                      key=lambda e: -e.size_atr)
        occupied = np.zeros(n, dtype=bool)
        for e in cand:
            if occupied[e.start_idx:e.end_idx + 1].any():
                continue
            occupied[e.start_idx:e.end_idx + 1] = True
            kept.append(e)

    kept.sort(key=lambda e: (e.start_idx, e.retning))
    return kept

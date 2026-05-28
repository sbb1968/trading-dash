"""
strategies/confluence/entry.py
───────────────────────────────
Entry-engine for Konfluens-strategien.

DESIGN-VALG: Pre-compute alle indikatorer ved dagsstart
──────────────────────────────────────────────────────────
I modsætning til MomentumORB (der akkumulerer state bar for bar) er
Konfluens stærkt afhængig af serier (EMA, VWAP, RSI, pivots, candles).
Vi pre-computer derfor alle indikator-værdier for hele dagen i
`reset_for_day(date, context)` og slår op pr. bar i `check_entry()`.

Dette giver:
  ✓ Identisk output som Pine (hele Pine's globale beregning er forudbestemt)
  ✓ Hurtigere backtest (ingen rekursion pr. bar)
  ✓ Mindre risiko for off-by-one mellem live og backtest

Det adskiller sig fra MomentumORB-mønstret men fungerer fint inden for
Strategy-protokollen — context indeholder bare hele indicator-DataFrame'en.

Entry-betingelser (Pine v3, alle skal kunne tjekkes på én bar):
  cond1 = close > htf_ema
  cond2 = not useVWAP or (close > vwap) or (low <= vwap_lower and close > open)
  cond3 = rsiWasOversold and crossover(rsi, rsi_cross_level)
          hvor rsiWasOversold = lowest(rsi, lookback) < oversold
  cond4 = higher_low = last_swing_low > prev_swing_low (begge ikke na)
  cond5 = is_bull_eng or is_hammer or strong_close
  cond6 = vol_spike and close > open
          hvor vol_spike = volume > sma(volume, 20) * vol_mult

DIAGNOSTIK (Lag B): Betingelses-vurderingen er udskilt til evaluate(),
som returnerer en struktureret vurdering MED afvisningsgrund. check_entry()
kalder evaluate() og bygger kun et EntrySignal når status == "signal".
Dermed findes betingelseslogikken ÉT sted og kan ikke drive fra hinanden
mellem entry-beslutning og afvisnings-logging.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, date as date_cls
from typing import Optional

import pandas as pd
import numpy as np

from strategies.base import Bar, EntrySignal
from strategies.confluence.config import (
    ConfluenceVariantConfig,
    SESSION_START_HHMM,
    SESSION_END_HHMM,
    ENTRY_CUTOFF_HHMM,
)
from strategies.confluence.indicators import (
    ema, sma, rsi, atr,
    vwap_with_bands, htf_ema,
    pivot_highs, pivot_lows, pivot_state_track,
    candle_features,
    rolling_lowest, rolling_highest,
    crossover,
)


SESSION_START = dtime(*SESSION_START_HHMM)
SESSION_END   = dtime(*SESSION_END_HHMM)
ENTRY_CUTOFF  = dtime(*ENTRY_CUTOFF_HHMM)


def _config_from_context(context: dict) -> ConfluenceVariantConfig:
    """Hent config fra context; fallback til en standard hvis ikke til stede."""
    cfg = context.get("config")
    if isinstance(cfg, ConfluenceVariantConfig):
        return cfg
    return ConfluenceVariantConfig(name="default")


def precompute_indicators(
    bars_df: pd.DataFrame,
    config: ConfluenceVariantConfig,
) -> pd.DataFrame:
    """
    Beregn ALLE indikatorer for hele bars_df og returnér én DataFrame.

    Input: bars_df med kolonnerne open, high, low, close, volume.
           DatetimeIndex i ET-tidszone.

    Output: bars_df + alle ekstra kolonner brugt af entry og exit.
    """
    if bars_df.empty:
        return bars_df.copy()

    df = bars_df.copy()

    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    # ── Trend / EMA ──────────────────────────────────────────
    df["ema_fast"] = ema(c, config.ema_fast_len)
    df["ema_slow"] = ema(c, config.ema_slow_len)
    df["htf_ema"]  = htf_ema(c, config.htf_timeframe, config.htf_ema_len)

    # ── VWAP ─────────────────────────────────────────────────
    vw, vu, vl = vwap_with_bands(h, l, c, v, config.vwap_band_mult)
    df["vwap"]       = vw
    df["vwap_upper"] = vu
    df["vwap_lower"] = vl

    # ── RSI ──────────────────────────────────────────────────
    df["rsi"] = rsi(c, config.rsi_len)

    # ── ATR ──────────────────────────────────────────────────
    df["atr"] = atr(h, l, c, config.atr_len)

    # ── Volume ───────────────────────────────────────────────
    df["vol_ma"]   = sma(v, config.vol_ma_len)
    df["vol_spike"] = v > (df["vol_ma"] * config.vol_mult)

    # ── Pivots + swing tracking ──────────────────────────────
    piv_h = pivot_highs(h, config.pivot_left, config.pivot_right)
    piv_l = pivot_lows(l,  config.pivot_left, config.pivot_right)

    df["pivot_high"] = piv_h
    df["pivot_low"]  = piv_l

    last_sh, prev_sh = pivot_state_track(piv_h)
    last_sl, prev_sl = pivot_state_track(piv_l)
    df["last_swing_high"] = last_sh
    df["prev_swing_high"] = prev_sh
    df["last_swing_low"]  = last_sl
    df["prev_swing_low"]  = prev_sl

    # higher_low / lower_high — kræver at BEGGE swings er ikke-na
    df["higher_low"] = (
        last_sl.notna() & prev_sl.notna() & (last_sl > prev_sl)
    )
    df["lower_high"] = (
        last_sh.notna() & prev_sh.notna() & (last_sh < prev_sh)
    )

    # ── Candle features ─────────────────────────────────────
    cf = candle_features(o, h, l, c)
    for key, series in cf.items():
        df[f"cf_{key}"] = series

    # Extra helper for exit: close < open (bearish bar)
    df["cf_close_lt_open"] = (c < o)

    # Crossunder af ema_fast — brugt af exit4
    # Pine: close < emaFast AND close[1] >= emaFast[1]
    prev_close    = c.shift(1)
    prev_ema_fast = df["ema_fast"].shift(1)
    df["ema_fast_crossunder"] = (
        (c < df["ema_fast"]) & (prev_close >= prev_ema_fast)
    ).fillna(False)

    # ── RSI helpers (for entry condition 3 og exit condition 1) ──
    rsi_lookback   = max(config.rsi_lookback, 1)
    df["rsi_lowest_lookback"]  = rolling_lowest(df["rsi"], rsi_lookback)
    df["rsi_highest_lookback"] = rolling_highest(df["rsi"], rsi_lookback)

    # crossover op gennem rsi_cross_level → entry
    df["rsi_crossup"]   = crossover(df["rsi"], config.rsi_cross_level)
    # crossover ned gennem rsi_cross_dn → exit
    # crossunder: prev >= level og curr < level
    df["rsi_crossdn"] = (df["rsi"].shift(1) >= config.rsi_cross_dn) & (df["rsi"] < config.rsi_cross_dn)

    # rsiWasOversold/Overbought — har RSI været under/over tærskel i de sidste N bars?
    df["rsi_was_oversold"]   = df["rsi_lowest_lookback"]  < config.rsi_oversold
    df["rsi_was_overbought"] = df["rsi_highest_lookback"] > config.rsi_overbought

    # ── Bearish divergens (til exit condition 5) ─────────────
    # Pine:
    #   var float lastPriceHigh = na, var float prevPriceHigh = na
    #   var float lastRsiAtHigh = na, var float prevRsiAtHigh = na
    #   if not na(pivotHigh):
    #     prevPriceHigh := lastPriceHigh
    #     lastPriceHigh := pivotHigh
    #     prevRsiAtHigh := lastRsiAtHigh
    #     lastRsiAtHigh := rsiVal[pivotRight]
    #   bearDiv = ... (lastPriceHigh > prevPriceHigh AND lastRsiAtHigh < prevRsiAtHigh)
    #
    # Bemærk: rsiVal[pivotRight] = RSI fra pivot-bar'en (pivot_right bars FØR
    # bekræftelses-bar'en). Pine bruger [] historisk operator.
    bear_div_calculated = _compute_bear_div(
        df["pivot_high"], df["rsi"], config.pivot_right
    )
    df["bear_div"] = bear_div_calculated

    return df


def _compute_bear_div(pivot_high_series: pd.Series, rsi_series: pd.Series,
                      pivot_right: int) -> pd.Series:
    """
    Beregn bearish divergens-flag bar for bar.

    Pivot er bekræftet på bar `i`. Pivot-bar'en er `i - pivot_right`.
    RSI-værdien VED pivot-bar'en er `rsi_series.iloc[i - pivot_right]`.

    State holdes på sidste/forrige pris-pivot OG sidste/forrige RSI-ved-pivot.
    bear_div bliver True på de bars hvor begge state-felter sammen viser:
      lastPriceHigh > prevPriceHigh AND lastRsiAtHigh < prevRsiAtHigh
    """
    n = len(pivot_high_series)
    result = pd.Series(False, index=pivot_high_series.index, dtype=bool)

    last_price = np.nan
    prev_price = np.nan
    last_rsi   = np.nan
    prev_rsi   = np.nan

    piv_vals = pivot_high_series.values
    rsi_vals = rsi_series.values

    for i in range(n):
        if not np.isnan(piv_vals[i]):
            # Pivot er bekræftet på bar i; pivot-bar var i - pivot_right
            pivot_bar_idx = i - pivot_right
            rsi_at_pivot = rsi_vals[pivot_bar_idx] if pivot_bar_idx >= 0 else np.nan

            prev_price = last_price
            last_price = piv_vals[i]
            prev_rsi   = last_rsi
            last_rsi   = rsi_at_pivot

        # Sæt bear_div hvis alle 4 felter er ikke-na og kriterierne opfyldt
        if (not np.isnan(last_price) and not np.isnan(prev_price)
                and not np.isnan(last_rsi) and not np.isnan(prev_rsi)):
            if last_price > prev_price and last_rsi < prev_rsi:
                result.iloc[i] = True

    return result


# ─────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────

class ConfluenceEntry:
    """
    Entry-engine for Konfluens-strategien.

    KONTINUERLIG DRIFT (vigtigt — forskel fra MomentumORB):
    ─────────────────────────────────────────────────────────
    Indicator-DataFrame'en dækker HELE bar-perioden (warmup + target),
    ikke kun én dag. Caller bygger den én gang med
    `ConfluenceStrategy.build_session_context()` og giver os referencen
    via load_session_context() / reset_for_day(). Vi laver INGEN
    pr-dags-reset af state — pivots, RSI og swing-tracking er allerede
    forward-kontinuerlige fordi de blev pre-computed på hele serien.

    GENINDGANG efter exit:
    ──────────────────────
    Konfluens tillader genindgang samme dag (Pine scriptet har ingen
    DONE_FOR_DAY-mekanisme). Caller (backtest eller live) skal sikre at
    samme bar ikke åbner og lukker samtidig (normal position-management).
    """

    def __init__(self):
        # ticker → pre-computed indicator df (én DF dækker hele perioden)
        self._df_by_ticker: dict[str, pd.DataFrame] = {}
        # Sidste evaluerede row — sættes af evaluate() når et signal dannes,
        # så check_entry() kan bygge EntrySignal uden at slå rækken op igen.
        self._eval_row: Optional[pd.Series] = None

    def load_session_context(self, context: dict) -> None:
        """
        Indlæs pre-computed indicator-df fra context.

        Context skal indeholde:
          ticker (str)
          ind_df (pd.DataFrame): pre-computed indikatorer fra
                                 build_session_context() — dækker HELE
                                 backtest/live-perioden, ikke kun én dag

        Kaldes ÉN gang pr. session (typisk ved backtest-start eller
        live-algo opstart efter warmup-historik er hentet).
        """
        ticker = context.get("ticker")
        if ticker is None:
            raise ValueError("Context skal indeholde 'ticker'")

        ind_df = context.get("ind_df")
        if ind_df is None or ind_df.empty:
            self._df_by_ticker[ticker] = pd.DataFrame()
            return

        self._df_by_ticker[ticker] = ind_df

    def reset_for_day(self, date, context: dict) -> None:
        """
        PROTOKOL-KOMPATIBILITET: kaldes typisk én gang pr. handelsdag af
        backtest-engines der antager MomentumORB-mønstret.

        For Konfluens er dette stort set en no-op — vi indlæser bare
        context (idempotent, sikkert at kalde flere gange). Indicator-df
        bygges af caller via build_session_context og dækker hele perioden.

        Hvis caller pr. en fejl kalder denne med kun én dags ind_df, så
        vil entry-tjek mod tidlige bars af dagen returnere None
        (indikatorer ikke warmed up).
        """
        self.load_session_context(context)

    
    def evaluate(
        self,
        ticker: str,
        bar: Bar,
        context: dict,
    ) -> dict:
        """Vurdér denne bar og returnér et FULDT diagnostik-dict.

        Returnerer altid et dict med mindst 'status' og 'reason'. Når
        status == 'signal' er også 'score', 'short_form', 'atr', 'ema_fast'
        og 'last_swing_low' sat (alt check_entry skal bruge for at bygge
        EntrySignal — så check_entry behøver ikke genberegne noget).

        status-værdier:
          'signal'    — alle betingelser opfyldt, score >= threshold
          'rejected'  — evalueret men under tærskel / mangler ATR
          'skipped'   — slet ikke evalueret (udenfor session, cutoff,
                        ingen data, ingen bar-række)

        Det er DENNE metode der giver os Lag B+-diagnostikken: vi kan se
        præcis hvilke af de 6 betingelser der manglede, og ved hvilket
        trin en bar faldt fra.
        """
        df = self._df_by_ticker.get(ticker)
        if df is None or df.empty:
            return {"status": "skipped", "reason": "ingen indicator-df"}

        config = _config_from_context(context)

        # Session-filter — Pine: inSession = not na(time(period,"0930-1600","NY"))
        t = bar.time_et
        if not (SESSION_START <= t <= SESSION_END):
            return {"status": "skipped", "reason": f"udenfor session ({t})"}

        # Entry-cutoff — stop nye entries efter ENTRY_CUTOFF (typisk 14:00 ET).
        if t >= ENTRY_CUTOFF:
            return {"status": "skipped",
                    "reason": f"efter entry-cutoff {ENTRY_CUTOFF} ({t})"}

        # Find rækken for denne bar (eksakt match forventes)
        try:
            row = df.loc[bar.timestamp]
        except KeyError:
            return {"status": "skipped",
                    "reason": f"ingen indicator-række for {bar.timestamp}"}
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        close = bar.close

        # ── KONFLUENS-BRICKS (samme logik som før) ───────────
        # cond1: HTF trend
        htf_val = row["htf_ema"]
        cond1 = (not pd.isna(htf_val)) and (close > htf_val)

        # cond2: VWAP
        if not config.use_vwap:
            cond2 = True
        else:
            vw_val  = row["vwap"]
            vwl_val = row["vwap_lower"]
            if pd.isna(vw_val):
                cond2 = False
            else:
                pullback_bull = (
                    not pd.isna(vwl_val)
                    and bar.low <= vwl_val
                    and bar.close > bar.open
                )
                cond2 = (close > vw_val) or pullback_bull

        # cond3: RSI reset + cross-up
        cond3 = bool(row["rsi_was_oversold"]) and bool(row["rsi_crossup"])
        # cond4: Higher low
        cond4 = bool(row["higher_low"])
        # cond5: Reversal candle (bullish)
        cond5 = (
            bool(row["cf_is_bull_eng"])
            or bool(row["cf_is_hammer"])
            or bool(row["cf_strong_close"])
        )
        # cond6: Volume spike + bullish close
        cond6 = bool(row["vol_spike"]) and (bar.close > bar.open)

        score = int(cond1) + int(cond2) + int(cond3) + int(cond4) \
            + int(cond5) + int(cond6)

        short_form = "".join([
            "T" if cond1 else "·",
            "V" if cond2 else "·",
            "R" if cond3 else "·",
            "H" if cond4 else "·",
            "C" if cond5 else "·",
            "L" if cond6 else "·",
        ])

        # Detaljeret reason — hvilke betingelser manglede
        _names = ["HTF", "VWAP", "RSI", "HL", "candle", "vol"]
        _conds = [cond1, cond2, cond3, cond4, cond5, cond6]
        _missing = [n for n, c in zip(_names, _conds) if not c]

        if score < config.entry_threshold:
            return {
                "status":     "rejected",
                "score":      score,
                "short_form": short_form,
                "reason":     (f"score {score}/{config.entry_threshold} — "
                               f"mangler: {', '.join(_missing)}"),
            }

        # Tærskel nået — men ATR skal være gyldig for at kunne sætte stop
        atr_val = row["atr"]
        if pd.isna(atr_val) or atr_val <= 0:
            return {
                "status":     "rejected",
                "score":      score,
                "short_form": short_form,
                "reason":     "score OK men ingen gyldig ATR (kan ikke sætte stop)",
            }

        # ── SIGNAL ───────────────────────────────────────────
        return {
            "status":         "signal",
            "score":          score,
            "short_form":     short_form,
            "reason":         "alle betingelser opfyldt",
            "atr":            float(atr_val),
            "ema_fast":       float(row["ema_fast"]) if not pd.isna(row["ema_fast"]) else None,
            "last_swing_low": float(row["last_swing_low"]) if not pd.isna(row["last_swing_low"]) else None,
        }

    def check_entry(
        self,
        ticker: str,
        bar: Bar,
        context: dict,
        evaluation: Optional[dict] = None,
    ) -> Optional[EntrySignal]:
        """Vurdér om denne bar trigger entry. Returnerer EntrySignal eller None.

        Tynd wrapper om evaluate(): hvis caller allerede har kaldt evaluate()
        kan resultatet sendes med via `evaluation=` (intet dobbeltarbejde).
        Ellers kalder vi det selv. Bevarer bagudkompatibilitet — gamle callere
        der kalder check_entry(ticker, bar, context) virker uændret.
        """
        if evaluation is None:
            evaluation = self.evaluate(ticker, bar, context)

        if evaluation.get("status") != "signal":
            return None

        return EntrySignal(
            ticker=ticker,
            entry_price=bar.close,
            entry_time=bar.timestamp,
            side="long",
            metadata={
                "atr":            evaluation["atr"],
                "entry_score":    evaluation["score"],
                "entry_short":    evaluation["short_form"],
                "ema_fast":       evaluation["ema_fast"],
                "last_swing_low": evaluation["last_swing_low"],
            },
        )

    def get_dataframe(self, ticker: str) -> Optional[pd.DataFrame]:
        """Returnér den pre-computede indicator-df for en ticker. None hvis ingen."""
        return self._df_by_ticker.get(ticker)

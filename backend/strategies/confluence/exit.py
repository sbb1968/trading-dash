"""
strategies/confluence/exit.py
──────────────────────────────
3-lags hybrid exit-engine for Konfluens-strategien.

LAG 1: Hard ATR stop
  Initial stop = entry − atr_sl_mult × ATR(14) ved entry-bar.
  Stop kan KUN bevæge sig opad (ratcheter) gennem trailing.

LAG 2: Trailing stop
  Aktiveres når close ≥ entry + trail_activ_r × R, hvor R = entry − initial_stop.
  Trail-stop beregnes på én af tre måder (config.trail_type):
    - "Swing Low": last_swing_low (fra pivot_lows-tracking)
    - "EMA Fast":  9-EMA på 5m
    - "ATR":       close − trail_atr_mult × ATR(14)
  Trail-stop opdateres bar for bar — stop = max(stop, ny_trail) (kun opad).

LAG 3: Signal-konfluens exit
  Hvis 3+ af 5 bearish konfluens-betingelser → strategy.close("LONG")
  Konkurrerer parallelt med stop. Hvis BÅDE stop og signal udløses i samme bar
  vinder stop (konservativ konvention som i MomentumORB).

PINE-LIGEHED — exit-rækkefølge på hver bar:
─────────────────────────────────────────────
  if position_size > 0:
      activeStop = trailActive ? max(initialStop, trailingStop) : initialStop
      strategy.exit("SL/Trail", stop=activeStop)   # Stop-ordre aktiv hele bar'en
      if exitSignal:
          strategy.close("LONG")                    # Lukker @ close hvis signal

Pine afvikler stop-ordren INTRA-BAR (bar.low <= stop → fyldes ved stop-prisen).
strategy.close fylder ved bar.close. Begge kan fylde samme bar — Pine's
process_orders_on_close=true betyder stop tjekkes først på bar high/low,
derefter close-ordren. Konflikt: stop først.

Backtest-konvention:
  bar.low <= stop  → exit @ stop, reason="stop" eller "trail"
  exit_signal (3+/5) → exit @ bar.close, reason="signal_exit"
  Markedsluk (16:00 ET) → exit @ bar.close, reason="session_close"

EXIT-SIGNAL bricks (Pine):
  exit1: rsiWasOverbought AND crossunder(rsi, rsi_cross_dn)
  exit2: lowerHigh
  exit3: isBearEng OR isShootStar OR weakClose
  exit4: close < emaFast AND close[1] >= emaFast[1]  (crossunder ema_fast)
  exit5: (vol_spike AND close < open) OR bear_div
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from typing import Optional

import pandas as pd
import numpy as np

from strategies.base import Bar, EntrySignal, ExitDecision, Position
from strategies.confluence.config import (
    ConfluenceVariantConfig,
    VARIANTS,
    SESSION_END_HHMM,
)


# ─────────────────────────────────────────────────────────────────
# Konstanter
# ─────────────────────────────────────────────────────────────────

# Force-close 15 min før session-slut (15:45 ET) — matcher MomentumORB
SESSION_FORCE_CLOSE = dtime(15, 45)

# Exit-reasons
REASON_STOP         = "stop"
REASON_TRAIL        = "trail"
REASON_SIGNAL_EXIT  = "signal_exit"
REASON_SESSION_CLOSE = "session_close"


# ─────────────────────────────────────────────────────────────────
# Per-position state
# ─────────────────────────────────────────────────────────────────

@dataclass
class ConfluenceExitState:
    """
    Per-position state for Konfluens-exit.

    Bemærk: vi gemmer initial_stop separat fra current stop. Pine's
    "trailActive ? max(initialStop, trailingStop) : initialStop" mønster
    håndteres ved at current stop = max(initial_stop, trail_stop).
    """
    initial_stop:    float          # Beregnet ved open_position, ændres aldrig
    risk_per_share:  float          # entry − initial_stop, brugt til R-beregning
    current_stop:    float          # = max(initial_stop, trail_stop) når trail aktiv
    trail_stop:      Optional[float] = None
    trail_active:    bool            = False
    atr_at_entry:    float           = 0.0


# ─────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────

class ConfluenceExit:
    """
    Exit-engine for Konfluens-strategien.

    Stateless mellem positioner — al state lever på Position.state.

    NOTE: Konfluens-exit kræver mere kontekst end MomentumORB:
      - last_swing_low for "Swing Low" trail
      - ema_fast for "EMA Fast" trail og exit-bricks 4
      - ATR for "ATR" trail
      - RSI / candle features / bear_div / etc. for signal-exit

    Vi henter disse fra indicator-DataFrame'en via context. Det vil sige
    `check_exit_bar(position, bar, variant_key, context)` har en udvidet
    signatur i forhold til Strategy-protokollen — men det er stadig kompatibelt
    da context kan bære præ-computede indikator-rækker.

    Backtest-engine sender context med indicator_row per bar. Live algo skal
    også beregne en pseudo-row (eller bare bruge enkelte felter).
    """

    def __init__(self, variants: dict[str, ConfluenceVariantConfig] = None):
        self._variants = variants if variants is not None else VARIANTS

    def _config(self, variant_key: str) -> ConfluenceVariantConfig:
        if variant_key not in self._variants:
            raise KeyError(
                f"Ukendt variant {variant_key!r}. "
                f"Tilgængelige: {list(self._variants.keys())}"
            )
        return self._variants[variant_key]

    # ─────────────────────────────────────────────────────────
    # Position-opening
    # ─────────────────────────────────────────────────────────

    def open_position(
        self,
        signal: EntrySignal,
        shares: int,
        variant_key: str,
    ) -> Position:
        """
        Beregn initial stop fra signal.metadata["atr"] og config.atr_sl_mult.

        Pine:
            stopPx = entryPx - atrSLmult * atrVal
        """
        config = self._config(variant_key)

        atr_val = signal.metadata.get("atr")
        if atr_val is None or atr_val <= 0:
            raise ValueError(
                f"EntrySignal mangler gyldig 'atr' i metadata (fik {atr_val!r})"
            )

        initial_stop   = signal.entry_price - config.atr_sl_mult * atr_val
        risk_per_share = signal.entry_price - initial_stop

        if risk_per_share <= 0:
            raise ValueError(
                f"Ugyldig risk_per_share={risk_per_share} "
                f"(entry={signal.entry_price}, stop={initial_stop})"
            )

        state = ConfluenceExitState(
            initial_stop=initial_stop,
            risk_per_share=risk_per_share,
            current_stop=initial_stop,
            trail_stop=None,
            trail_active=False,
            atr_at_entry=atr_val,
        )

        return Position(
            ticker=signal.ticker,
            entry_price=signal.entry_price,
            entry_time=signal.entry_time,
            shares=shares,
            side=signal.side,
            state=state,
            metadata={"variant_key": variant_key, **signal.metadata},
        )

    # ─────────────────────────────────────────────────────────
    # Position-management (state updates)
    # ─────────────────────────────────────────────────────────

    def update(
        self,
        position: Position,
        high_seen: float,
        variant_key: str,
        low_seen: Optional[float] = None,
        ema_fast: Optional[float] = None,
        last_swing_low: Optional[float] = None,
        atr_val: Optional[float] = None,
    ) -> None:
        """
        Opdatér exit-state baseret på dagens udvikling.

        Vi følger Pine's logik:
          currentR = (close - entry) / risk_per_share
          if not trailActive and currentR >= trail_activ_r:
              trailActive := true
          if trailActive:
              newTrail = trailingStop
              if trail_type == "Swing Low" and last_swing_low ikke na:
                  newTrail = max(trailingStop, last_swing_low)
              elif trail_type == "EMA Fast":
                  newTrail = max(trailingStop, ema_fast)
              elif trail_type == "ATR":
                  newTrail = max(trailingStop, close - trail_atr_mult * atr)
              trailingStop = max(trailingStop, newTrail)

        Vi bruger high_seen som "close" (i live = snapshot-pris, i backtest = bar.close).
        Pine bruger close-prisen til at vurdere currentR.

        ⚠ VIGTIG NUANCE: Pine evaluerer trail på close[i], ikke high[i]. Vi
        bruger derfor `high_seen` (som caller skal sende som bar.close i backtest,
        eller close fra snapshot i live) til R-beregning.

        Bare for at undgå tvetydighed: parametret hedder high_seen pga.
        kompatibilitet med MomentumORB-protokollen, men det skal være close.
        """
        config = self._config(variant_key)
        state: ConfluenceExitState = position.state

        if position.side != "long":
            raise NotImplementedError("Konfluens er long-only (Pine v3)")

        # Brug close-prisen (= high_seen som vi har omdøbt mentalt)
        close = high_seen

        # R-beregning: (close - entry) / risk_per_share
        current_R = (close - position.entry_price) / state.risk_per_share

        # ── Stage 1 → trail aktiveres ────────────────────────
        if not state.trail_active and current_R >= config.trail_activ_r:
            state.trail_active = True
            # Initial trail_stop: vi sætter den til initial_stop som baseline
            # (Pine starter trailingStop = initialStop ved entry og bruger
            #  math.max() opad — så trail kan først løfte stop, aldrig sænke)
            state.trail_stop = state.initial_stop

        # ── Trail-vedligehold (kun hvis aktiv) ───────────────
        if state.trail_active:
            candidate = state.trail_stop  # fallback til nuværende

            if config.trail_type == "Swing Low":
                if last_swing_low is not None and not pd.isna(last_swing_low):
                    candidate = max(state.trail_stop, last_swing_low)
            elif config.trail_type == "EMA Fast":
                if ema_fast is not None and not pd.isna(ema_fast):
                    candidate = max(state.trail_stop, ema_fast)
            elif config.trail_type == "ATR":
                if atr_val is not None and not pd.isna(atr_val):
                    atr_trail = close - config.trail_atr_mult * atr_val
                    candidate = max(state.trail_stop, atr_trail)

            # trailingStop ratcheter kun OPAD
            if candidate > state.trail_stop:
                state.trail_stop = candidate

            # current_stop = max(initial_stop, trail_stop) når trail aktiv
            # Det er Pine's "activeStop = math.max(initialStop, trailingStop)"
            state.current_stop = max(state.initial_stop, state.trail_stop)
        # else: current_stop forbliver initial_stop

    # ─────────────────────────────────────────────────────────
    # Exit-tjek
    # ─────────────────────────────────────────────────────────

    def check_exit_bar(
        self,
        position: Position,
        bar: Bar,
        variant_key: str,
        indicator_row: Optional[pd.Series] = None,
    ) -> Optional[ExitDecision]:
        """
        Tjek exit i backtest (OHLC-bar). Returnerer ExitDecision eller None.

        Rækkefølge (Pine-konvention):
          1. Session force-close (15:45 ET) → bar.close
          2. Stop / Trail-stop (bar.low ≤ current_stop) → current_stop
          3. Signal exit (3+/5 exit-bricks) → bar.close

        ⚠ Konflikt-regel: hvis stop OG signal-exit begge rammes samme bar,
        vinder STOP (konservativ — vi antager worst case ved bar-tvetydighed).

        indicator_row: én række fra precompute_indicators(). Hvis None,
        sprunges signal-exit-tjek over (kun stop og force-close vurderes).
        Backtest-engine skal sende denne ind.
        """
        state: ConfluenceExitState = position.state
        config = self._config(variant_key)

        if position.side != "long":
            raise NotImplementedError("Konfluens er long-only")

        # 1. Force-close kl. 15:45 ET — alle long positioner lukkes
        if bar.time_et >= SESSION_FORCE_CLOSE:
            return ExitDecision(exit_price=bar.close, reason=REASON_SESSION_CLOSE)

        # 2. Stop / Trail
        if bar.low <= state.current_stop:
            reason = REASON_TRAIL if state.trail_active else REASON_STOP
            return ExitDecision(exit_price=state.current_stop, reason=reason)

        # 3. Signal-exit (kun hvis vi har indicator-data)
        if indicator_row is not None:
            score, _short = self._evaluate_exit_score(indicator_row, config)
            if score >= config.exit_threshold:
                return ExitDecision(exit_price=bar.close, reason=REASON_SIGNAL_EXIT)

        return None

    def check_exit_live(
        self,
        position: Position,
        current_price: float,
        current_time_et: dtime,
        variant_key: str,
        indicator_row: Optional[pd.Series] = None,
    ) -> Optional[ExitDecision]:
        """
        Live exit-tjek (snapshot-pris).

        Vi har ikke bar.low — kun current_price. Det betyder vi rammer stop
        først når current_price < stop (efter en bar er printet).
        """
        state: ConfluenceExitState = position.state
        config = self._config(variant_key)

        if position.side != "long":
            raise NotImplementedError("Konfluens er long-only")

        if current_time_et >= SESSION_FORCE_CLOSE:
            return ExitDecision(exit_price=current_price, reason=REASON_SESSION_CLOSE)

        if current_price <= state.current_stop:
            reason = REASON_TRAIL if state.trail_active else REASON_STOP
            return ExitDecision(exit_price=state.current_stop, reason=reason)

        if indicator_row is not None:
            score, _short = self._evaluate_exit_score(indicator_row, config)
            if score >= config.exit_threshold:
                return ExitDecision(exit_price=current_price, reason=REASON_SIGNAL_EXIT)

        return None

    # ─────────────────────────────────────────────────────────
    # Signal-exit konfluens (3+/5 bricks)
    # ─────────────────────────────────────────────────────────

    def _evaluate_exit_score(
        self,
        row: pd.Series,
        config: ConfluenceVariantConfig,
    ) -> tuple[int, str]:
        """
        Beregn exit-score 0-5 ud fra én indicator-row.

        Pine:
          exit1 = rsiWasOverbought AND crossunder(rsi, rsi_cross_dn)
          exit2 = lowerHigh
          exit3 = isBearEng OR isShootStar OR weakClose
          exit4 = close < emaFast AND close[1] >= emaFast[1]
          exit5 = (volSpike AND close < open) OR bearDiv
        """
        # exit1 — RSI overbought reversal
        exit1 = bool(row.get("rsi_was_overbought", False)) and bool(row.get("rsi_crossdn", False))

        # exit2 — Lower high
        exit2 = bool(row.get("lower_high", False))

        # exit3 — Bearish candle
        exit3 = (
            bool(row.get("cf_is_bear_eng", False))
            or bool(row.get("cf_is_shoot_star", False))
            or bool(row.get("cf_weak_close", False))
        )

        # exit4 — Crossunder ema_fast: close < emaFast AND close[1] >= emaFast[1]
        # Vi har ikke shift'ed kolonne i row — men precompute_indicators kan
        # tilføje en exit4_crossunder_ema_fast kolonne. Lad os holde det enkelt:
        # vi bruger row['close'] vs row['ema_fast'] og row['close_prev'] vs row['ema_fast_prev']
        # Men de findes ikke i row som standard. Vi tilføjer dem i precompute.
        exit4 = bool(row.get("ema_fast_crossunder", False))

        # exit5 — Vol/divergens
        vol_spike   = bool(row.get("vol_spike", False))
        close_lt_open = bool(row.get("cf_close_lt_open", False))
        bear_div    = bool(row.get("bear_div", False))
        exit5 = (vol_spike and close_lt_open) or bear_div

        score = int(exit1) + int(exit2) + int(exit3) + int(exit4) + int(exit5)
        short = "".join([
            "O" if exit1 else "·",
            "L" if exit2 else "·",
            "C" if exit3 else "·",
            "E" if exit4 else "·",
            "V" if exit5 else "·",
        ])
        return score, short

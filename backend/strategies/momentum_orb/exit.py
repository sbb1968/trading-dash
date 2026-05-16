"""
strategies/momentum_orb/exit.py
────────────────────────────────
3-stadie hybrid exit-engine for MomentumORB.

Stadie 1 (initial):
  Stop:    ORB Mid (med 1% gulv) eller fixed % (baseline)
  Target:  +4%

Stadie 2 (når pris når breakeven_trigger_pct, default +3%):
  Stop:    flyttes op til entry_price (break-even)
  Target:  stadig +4%

Stadie 3 (når pris når trail_activate_pct, default +4%):
  Target:  fjernes (kun trail eller force-close kan lukke)
  Stop:    bliver trailing-stop = highest_high × (1 - trail_distance_pct)

Force-close kl. 10:30 ET overruler alt andet.

Stop "ratcheter" — det kan kun bevæge sig opad, aldrig nedad.

Konflikt-regel (kun backtest): hvis én OHLC-bar både rammer stop og target,
fyrer stop først. Det er konservativt — bedre at antage worst case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from typing import Optional

from strategies.base import Bar, EntrySignal, ExitDecision, Position
from strategies.momentum_orb.config import VariantConfig, VARIANTS


# Absolut force-close-tid — alle åbne positioner SKAL lukkes inden markedet
# lukker kl. 16:00 ET. Vi giver 5 min buffer for at undgå last-second slippage.
# Indtil 15:55 styres positioner kun af stop, target og trail.
TRADE_END_TIME = dtime(15, 55)
# Stage-konstanter
STAGE_INITIAL    = 1
STAGE_BREAKEVEN  = 2
STAGE_TRAILING   = 3

# Exit-årsager — bruges af backtest til at tælle exit-typer
REASON_STOP        = "stop"
REASON_TARGET      = "target"
REASON_TRAIL       = "trail"
REASON_FORCE_CLOSE = "force_close"


@dataclass
class ExitState:
    """
    Den strategi-specifikke state for én åben MomentumORB-position.

    Gemmes på Position.state og opdateres af MomentumORBExit på hver pris-
    observation. Stage transitioner sker her, ikke i caller-koden.
    """
    orb_high: float
    orb_low: float
    orb_mid: float

    stop: float
    target: Optional[float]              # None i stage 3 (kun trail eller force-close lukker)
    highest_high: float
    trail_stop: Optional[float] = None   # None indtil stage 3
    stage: int = STAGE_INITIAL


def _initial_stop(entry_price: float, orb_high: float, orb_low: float,
                  config: VariantConfig) -> float:
    """
    Beregn initial stop baseret på variant:
      fixed_pct: entry × (1 - fixed_stop_pct)
      orb_mid:   max(orb_mid, entry × 0.99)   ← 1% gulv
      orb_low:   max(orb_low, entry × 0.99)   ← 1% gulv
    """
    floor = entry_price * 0.99   # stop er ALDRIG tættere end 1% under entry

    if config.stop_mode == "fixed_pct":
        return entry_price * (1.0 - config.fixed_stop_pct)
    elif config.stop_mode == "orb_mid":
        orb_mid = (orb_high + orb_low) / 2.0
        return max(orb_mid, floor)
    elif config.stop_mode == "orb_low":
        return max(orb_low, floor)
    else:
        raise ValueError(f"Ukendt stop_mode: {config.stop_mode!r}")


class MomentumORBExit:
    """
    Exit-engine for MomentumORB.

    Stateless mellem positioner — al state lever på Position.state.
    Det betyder at den samme engine-instans kan håndtere flere positioner
    samtidigt uden konflikter.
    """

    def __init__(self, variants: dict[str, VariantConfig] = None):
        """variants: mapping af variant-key → VariantConfig. Default = VARIANTS."""
        self._variants = variants if variants is not None else VARIANTS

    def _config(self, variant_key: str) -> VariantConfig:
        if variant_key not in self._variants:
            raise KeyError(f"Ukendt variant {variant_key!r}. "
                          f"Tilgængelige: {list(self._variants.keys())}")
        return self._variants[variant_key]

    def open_position(
        self,
        signal: EntrySignal,
        shares: int,
        variant_key: str,
    ) -> Position:
        """
        Opret Position med ExitState beregnet fra entry-signal.

        Signal.metadata SKAL have orb_high og orb_low — entry-engine sørger
        for dette.
        """
        config = self._config(variant_key)

        orb_high = signal.metadata["orb_high"]
        orb_low  = signal.metadata["orb_low"]
        orb_mid  = (orb_high + orb_low) / 2.0

        stop   = _initial_stop(signal.entry_price, orb_high, orb_low, config)
        target = signal.entry_price * (1.0 + config.target_pct)

        state = ExitState(
            orb_high=orb_high,
            orb_low=orb_low,
            orb_mid=orb_mid,
            stop=stop,
            target=target,
            highest_high=signal.entry_price,
        )

        return Position(
            ticker=signal.ticker,
            entry_price=signal.entry_price,
            entry_time=signal.entry_time,
            shares=shares,
            state=state,
            metadata={"variant_key": variant_key, **signal.metadata},
        )

    def update(
        self,
        position: Position,
        high_seen: float,
        variant_key: str,
    ) -> None:
        """
        Opdatér position-state baseret på højeste pris siden sidst.

        Stage transitions (1 → 2 → 3) sker her. Stop "ratcheter" — sænkes aldrig.
        """
        config = self._config(variant_key)
        state: ExitState = position.state

        # Track highest high (basis for trail)
        if high_seen > state.highest_high:
            state.highest_high = high_seen

        entry = position.entry_price

        # ── Stage 1 → 2: break-even ─────────────────────────
        if (state.stage == STAGE_INITIAL
                and config.breakeven_enabled
                and state.highest_high >= entry * (1.0 + config.breakeven_trigger_pct)):
            # Flyt stop op til entry — KUN hvis det er højere end nuværende
            if entry > state.stop:
                state.stop = entry
            state.stage = STAGE_BREAKEVEN

        # ── Stage 2 → 3: trail aktiveres ────────────────────
        if (state.stage in (STAGE_INITIAL, STAGE_BREAKEVEN)
                and config.trail_enabled
                and state.highest_high >= entry * (1.0 + config.trail_activate_pct)):
            state.target = None    # target fjernes
            state.trail_stop = state.highest_high * (1.0 - config.trail_distance_pct)
            if state.trail_stop > state.stop:
                state.stop = state.trail_stop
            state.stage = STAGE_TRAILING

        # ── Stage 3 vedligehold: trail følger highest_high ──
        if state.stage == STAGE_TRAILING:
            new_trail = state.highest_high * (1.0 - config.trail_distance_pct)
            if new_trail > state.trail_stop:
                state.trail_stop = new_trail
            if state.trail_stop > state.stop:
                state.stop = state.trail_stop

    def check_exit_live(
        self,
        position: Position,
        current_price: float,
        current_time_et: dtime,
        variant_key: str,
    ) -> Optional[ExitDecision]:
        """
        Tjek exit i live (snapshot-pris fra IBKR).

        OBS: caller skal have kaldt update() FØRST.

        Rækkefølge:
          1. Force-close kl. 10:30 — overruler alt
          2. Stop loss (inkl. trail-stop hvis stage 3)
          3. Target (kun hvis ikke None — i stage 3 er det None)
        """
        state: ExitState = position.state

        # NB: force-close kl. 10:30 er fjernet for live algoritmen.
        # Live algoritmen håndterer market close (15:55 ET) separat
        # i hovedloopet i algo_momentum.py. Positioner får lov at
        # udvikle sig efter deres egne exit-regler (stop, target, trail).

        if current_price <= state.stop:
            reason = REASON_TRAIL if state.stage == STAGE_TRAILING else REASON_STOP
            return ExitDecision(exit_price=state.stop, reason=reason)

        if state.target is not None and current_price >= state.target:
            return ExitDecision(exit_price=state.target, reason=REASON_TARGET)

        return None

    def check_exit_bar(
        self,
        position: Position,
        bar: Bar,
        variant_key: str,
    ) -> Optional[ExitDecision]:
        """
        Tjek exit i backtest (OHLC-bar).

        OBS: caller skal have kaldt update(position, bar.high, ...) FØRST.

        Konflikt-regel: hvis både stop og target rammes i samme bar, fyrer
        stop først (konservativ konvention).
        """
        state: ExitState = position.state

        # 1. Force-close
        if bar.time_et >= TRADE_END_TIME:
            return ExitDecision(exit_price=bar.close, reason=REASON_FORCE_CLOSE)

        # 2. Stop (inkl. trail-stop)
        if bar.low <= state.stop:
            reason = REASON_TRAIL if state.stage == STAGE_TRAILING else REASON_STOP
            return ExitDecision(exit_price=state.stop, reason=reason)

        # 3. Target
        if state.target is not None and bar.high >= state.target:
            return ExitDecision(exit_price=state.target, reason=REASON_TARGET)

        return None

"""
strategies/momentum_orb/exit.py
────────────────────────────────
3-stadie hybrid exit-engine for MomentumORB.

Long-positioner (stop UNDER entry, target OVER):
  Stadie 1: stop = ORB Mid (med 1% gulv) eller fixed % | target = +4%
  Stadie 2 (+breakeven_trigger_pct): stop flyttes op til entry
  Stadie 3 (+trail_activate_pct):    target fjernes, trail-stop = highest_high × (1 − trail_distance_pct)
  Stop kan kun bevæge sig OPAD (ratcheter).

Short-positioner (stop OVER entry, target UNDER) — spejlvendt:
  Stadie 1: stop = ORB Mid (med 1% loft) eller fixed % | target = −4%
  Stadie 2 (−breakeven_trigger_pct): stop flyttes ned til entry
  Stadie 3 (−trail_activate_pct):    target fjernes, trail-stop = lowest_low × (1 + trail_distance_pct)
  Stop kan kun bevæge sig NEDAD (ratcheter).

Force-close kl. 15:55 ET overruler alt andet (kun i backtest;
live håndterer market_close i hovedloopet).

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

    For long-positioner bruges `highest_high` til trail-beregning.
    For short-positioner bruges `lowest_low` til trail-beregning (spejlvendt).
    Begge felter eksisterer altid, men kun den relevante for siden opdateres.
    """
    orb_high: float
    orb_low: float
    orb_mid: float

    stop: float
    target: Optional[float]              # None i stage 3 (kun trail eller force-close lukker)
    highest_high: float                  # Bruges af long
    lowest_low: float                    # Bruges af short
    trail_stop: Optional[float] = None   # None indtil stage 3
    stage: int = STAGE_INITIAL


def _initial_stop_long(entry_price: float, orb_high: float, orb_low: float,
                       config: VariantConfig) -> float:
    """
    Long: stop er UNDER entry, capped ved entry × 0.99 (max 1% risiko).

    fixed_pct: entry × (1 - fixed_stop_pct)
    orb_mid:   max(orb_mid, entry × 0.99)   ← 1% gulv (stop kan ikke være > entry*0.99)
    orb_low:   max(orb_low, entry × 0.99)
    """
    floor = entry_price * 0.99   # stop løftes op til floor hvis ORB-niveau er langt under

    if config.stop_mode == "fixed_pct":
        return entry_price * (1.0 - config.fixed_stop_pct)
    elif config.stop_mode == "orb_mid":
        orb_mid = (orb_high + orb_low) / 2.0
        return max(orb_mid, floor)
    elif config.stop_mode == "orb_low":
        return max(orb_low, floor)
    else:
        raise ValueError(f"Ukendt stop_mode: {config.stop_mode!r}")


def _initial_stop_short(entry_price: float, orb_high: float, orb_low: float,
                        config: VariantConfig) -> float:
    """
    Short: stop er OVER entry, capped ved entry × 1.01 (max 1% risiko).

    Spejlvendt af _initial_stop_long:
      fixed_pct: entry × (1 + fixed_stop_pct)
      orb_mid:   min(orb_mid, entry × 1.01)
      orb_low → spejlet til orb_high: min(orb_high, entry × 1.01)
    """
    ceiling = entry_price * 1.01   # stop sænkes hvis ORB-niveau er langt over

    if config.stop_mode == "fixed_pct":
        return entry_price * (1.0 + config.fixed_stop_pct)
    elif config.stop_mode == "orb_mid":
        orb_mid = (orb_high + orb_low) / 2.0
        return min(orb_mid, ceiling)
    elif config.stop_mode == "orb_low":
        # For shorts er orb_high den symmetriske modpart af orb_low-stop'et
        return min(orb_high, ceiling)
    else:
        raise ValueError(f"Ukendt stop_mode: {config.stop_mode!r}")


class MomentumORBExit:
    """
    Exit-engine for MomentumORB.

    Stateless mellem positioner — al state lever på Position.state.
    Det betyder at den samme engine-instans kan håndtere flere positioner
    (long og short side om side) uden konflikter.
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
        for dette. Signal.side ("long"/"short") afgør om stop/target spejlvendes.
        """
        config = self._config(variant_key)

        orb_high = signal.metadata["orb_high"]
        orb_low  = signal.metadata["orb_low"]
        orb_mid  = (orb_high + orb_low) / 2.0
        side     = signal.side

        if side == "long":
            stop   = _initial_stop_long(signal.entry_price, orb_high, orb_low, config)
            target = signal.entry_price * (1.0 + config.target_pct)
        elif side == "short":
            stop   = _initial_stop_short(signal.entry_price, orb_high, orb_low, config)
            target = signal.entry_price * (1.0 - config.target_pct)
        else:
            raise ValueError(f"Ukendt side: {side!r}")

        state = ExitState(
            orb_high=orb_high,
            orb_low=orb_low,
            orb_mid=orb_mid,
            stop=stop,
            target=target,
            highest_high=signal.entry_price,
            lowest_low=signal.entry_price,
        )

        return Position(
            ticker=signal.ticker,
            entry_price=signal.entry_price,
            entry_time=signal.entry_time,
            shares=shares,
            side=side,
            state=state,
            metadata={"variant_key": variant_key, **signal.metadata},
        )

    def update(
        self,
        position: Position,
        high_seen: float,
        variant_key: str,
        low_seen: Optional[float] = None,
    ) -> None:
        """
        Opdatér position-state baseret på pris-observation.

        For long: bruger high_seen til at opdatere highest_high og evt.
                  flytte stop/trail opad.
        For short: bruger low_seen (eller high_seen hvis low_seen=None) til at
                   opdatere lowest_low og evt. flytte stop/trail nedad.

        Caller-konventioner:
          Live (snapshot): kald med kun high_seen = snapshot-prisen.
          Backtest (bar): kald med bar.high og low_seen=bar.low.

        Stage transitions (1 → 2 → 3) sker her. Stop ratcheter (long: kun
        opad; short: kun nedad).
        """
        config = self._config(variant_key)
        state: ExitState = position.state
        entry = position.entry_price
        side = position.side

        # Default low_seen til high_seen hvis caller kun har én pris (snapshot)
        if low_seen is None:
            low_seen = high_seen

        if side == "long":
            # ── Track highest high (basis for trail) ────────────
            if high_seen > state.highest_high:
                state.highest_high = high_seen

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

        elif side == "short":
            # ── Track lowest low (basis for trail) ──────────────
            if low_seen < state.lowest_low:
                state.lowest_low = low_seen

            # ── Stage 1 → 2: break-even ─────────────────────────
            if (state.stage == STAGE_INITIAL
                    and config.breakeven_enabled
                    and state.lowest_low <= entry * (1.0 - config.breakeven_trigger_pct)):
                # Flyt stop ned til entry — KUN hvis det er lavere end nuværende
                if entry < state.stop:
                    state.stop = entry
                state.stage = STAGE_BREAKEVEN

            # ── Stage 2 → 3: trail aktiveres ────────────────────
            if (state.stage in (STAGE_INITIAL, STAGE_BREAKEVEN)
                    and config.trail_enabled
                    and state.lowest_low <= entry * (1.0 - config.trail_activate_pct)):
                state.target = None    # target fjernes
                state.trail_stop = state.lowest_low * (1.0 + config.trail_distance_pct)
                if state.trail_stop < state.stop:
                    state.stop = state.trail_stop
                state.stage = STAGE_TRAILING

            # ── Stage 3 vedligehold: trail følger lowest_low ────
            if state.stage == STAGE_TRAILING:
                new_trail = state.lowest_low * (1.0 + config.trail_distance_pct)
                if new_trail < state.trail_stop:
                    state.trail_stop = new_trail
                if state.trail_stop < state.stop:
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

        Long  hits stop ved current_price ≤ stop, target ved ≥ target.
        Short hits stop ved current_price ≥ stop, target ved ≤ target.
        """
        state: ExitState = position.state
        side = position.side

        # NB: force-close kl. 15:55 ET håndteres af algo_momentum.py for live.

        if side == "long":
            if current_price <= state.stop:
                reason = REASON_TRAIL if state.stage == STAGE_TRAILING else REASON_STOP
                return ExitDecision(exit_price=state.stop, reason=reason)
            if state.target is not None and current_price >= state.target:
                return ExitDecision(exit_price=state.target, reason=REASON_TARGET)
        else:  # short
            if current_price >= state.stop:
                reason = REASON_TRAIL if state.stage == STAGE_TRAILING else REASON_STOP
                return ExitDecision(exit_price=state.stop, reason=reason)
            if state.target is not None and current_price <= state.target:
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

        OBS: caller skal have kaldt update(position, bar.high, key, low_seen=bar.low)
        FØRST. Konflikt-regel: stop først hvis både stop og target rammes.

        Long  hits stop hvis bar.low ≤ stop, target hvis bar.high ≥ target.
        Short hits stop hvis bar.high ≥ stop, target hvis bar.low ≤ target.
        """
        state: ExitState = position.state
        side = position.side

        # 1. Force-close
        if bar.time_et >= TRADE_END_TIME:
            return ExitDecision(exit_price=bar.close, reason=REASON_FORCE_CLOSE)

        if side == "long":
            # 2. Stop (inkl. trail-stop)
            if bar.low <= state.stop:
                reason = REASON_TRAIL if state.stage == STAGE_TRAILING else REASON_STOP
                return ExitDecision(exit_price=state.stop, reason=reason)
            # 3. Target
            if state.target is not None and bar.high >= state.target:
                return ExitDecision(exit_price=state.target, reason=REASON_TARGET)
        else:  # short
            # 2. Stop (over entry — bar.high rammer det)
            if bar.high >= state.stop:
                reason = REASON_TRAIL if state.stage == STAGE_TRAILING else REASON_STOP
                return ExitDecision(exit_price=state.stop, reason=reason)
            # 3. Target (under entry — bar.low rammer det)
            if state.target is not None and bar.low <= state.target:
                return ExitDecision(exit_price=state.target, reason=REASON_TARGET)

        return None

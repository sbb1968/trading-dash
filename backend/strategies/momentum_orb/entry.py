"""
strategies/momentum_orb/entry.py
─────────────────────────────────
Break-and-retest entry-engine for MomentumORB.

State-machine pr. ticker pr. dag:
  waiting           → leder efter første breakout (op ELLER ned)
  breakout_detected → venter på pullback til ORB-niveau (timeout fra config)
  awaiting_retest   → venter på bounce væk fra ORB-niveau
  entered           → position er åbnet, denne engine er færdig for dagen
  done_for_day      → enten entered eller drop, ingen flere forsøg

Side ("long" | "short") lases ved det første breakout og driver derefter
state-overgange (pullback-retning, bounce-retning).

Entry-kriterier (alle fra VariantConfig):
  Long  (altid aktiv):
    1. Pris > ORB High
    2. Volumen ≥ vol_mult × gennemsnit
    3. RSI(14) < rsi_max
    4. Pullback (low ≤ orb_high × 1.001) → bounce (close > orb_high)

  Short (kun hvis config.enable_shorts):
    1. Pris < ORB Low
    2. Volumen ≥ vol_mult × gennemsnit
    3. RSI(14) > 100 − rsi_max   (undgar oversold-bounces, fx rsi > 20)
    4. Pullback (high ≥ orb_low × 0.999) → bounce (close < orb_low)
"""

from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Optional

from strategies.base import Bar, EntrySignal
from strategies.momentum_orb.config import (
    RETEST_TOLERANCE, VariantConfig,
    # Bagudkompat — bruges som fallback hvis config ikke i context
    VOL_MULT, RSI_MAX, RETEST_TIMEOUT_SEC,
)


# State-konstanter
STATE_WAITING            = "waiting"
STATE_BREAKOUT_DETECTED  = "breakout_detected"
STATE_AWAITING_RETEST    = "awaiting_retest"
STATE_DONE_FOR_DAY       = "done_for_day"


def calc_rsi_from_closes(closes: list[float], period: int = 14) -> float:
    """
    RSI(14) baseret på Wilder's smoothing — samme formel som live algo.

    Returnerer 50.0 hvis der er for få data (ingen handel).
    Returnerer 100.0 hvis ingen tab (undgår division by zero).
    """
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = sum(d for d in deltas[-period:] if d > 0) / period
    losses = sum(-d for d in deltas[-period:] if d < 0) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))


def _get_params(context: dict) -> tuple[float, float, int, bool, bool]:
    """
    Hent (vol_mult, rsi_max, retest_timeout_sec, enable_shorts, require_retest)
    fra context.

    Hvis context['config'] er en VariantConfig bruges den. Ellers falder vi
    tilbage paa module-level constants (long-only, require_retest=True for
    bagudkompatibilitet).
    """
    config = context.get("config")
    if isinstance(config, VariantConfig):
        return (
            config.vol_mult,
            config.rsi_max,
            config.retest_timeout_sec,
            config.enable_shorts,
            config.require_retest,
        )
    return float(VOL_MULT), float(RSI_MAX), int(RETEST_TIMEOUT_SEC), False, True


class MomentumORBEntry:
    """
    Entry-engine for MomentumORB.

    Holder state pr. ticker pr. dag — fordi state-machinen er ticker-specifik.
    Side ("long"/"short") lases ved første breakout-detektion. Resten af
    state-overgangene bruger den lasede retning.

    Context-felter der bruges:
      ticker:        str
      orb_high:      float
      orb_low:       float
      avg_vol:       float
      prior_closes:  list[float]  (RSI-start, kan være tom)
      config:        VariantConfig (variant-specifikke parametre + enable_shorts)
    """

    def __init__(self):
        # ticker → state
        self._ticker_state: dict[str, str] = {}
        # ticker → "long" | "short" | None (None før breakout)
        self._ticker_side: dict[str, Optional[str]] = {}
        # ticker → datetime hvornår breakout først blev set
        self._breakout_time: dict[str, datetime] = {}
        # ticker → ekstremum under pullback (low for long, high for short)
        self._retest_extremum: dict[str, float] = {}
        # ticker → liste af closes (til RSI). Backtest akkumulerer som bars kommer.
        self._closes: dict[str, list[float]] = {}

    def reset_for_day(self, date, context: dict) -> None:
        """Nulstil alt for én ny handelsdag."""
        ticker = context.get("ticker")
        if ticker is None:
            raise ValueError("Context skal indeholde 'ticker'")

        self._ticker_state[ticker]   = STATE_WAITING
        self._ticker_side[ticker]    = None
        self._breakout_time.pop(ticker, None)
        self._retest_extremum.pop(ticker, None)
        self._closes[ticker] = list(context.get("prior_closes", []))

    def check_entry(
        self,
        ticker: str,
        bar: Bar,
        context: dict,
    ) -> Optional[EntrySignal]:
        """
        Vurdér om denne bar trigger entry.

        Returnerer EntrySignal hvis entry udløses, ellers None.
        Signal.side fortæller om det er long eller short.
        """
        state = self._ticker_state.get(ticker, STATE_WAITING)

        # Track closes til RSI — opdaterer altid, uanset state
        self._closes.setdefault(ticker, []).append(bar.close)

        # DONE_FOR_DAY → ingen flere forsøg
        if state == STATE_DONE_FOR_DAY:
            return None

        orb_high = context["orb_high"]
        orb_low  = context["orb_low"]
        avg_vol  = context["avg_vol"]
        vol_mult, rsi_max, retest_timeout_sec, enable_shorts, require_retest = _get_params(context)

        vol_ok = avg_vol > 0 and bar.volume >= avg_vol * vol_mult

        # WAITING → led efter første breakout (long først, derefter short)
        if state == STATE_WAITING:
            rsi = calc_rsi_from_closes(self._closes[ticker])

            # Long breakout
            if bar.close > orb_high and vol_ok and rsi < rsi_max:
                if not require_retest:
                    # Direkte-breakout (originalens adfærd): entry MED DET SAMME,
                    # spring BREAKOUT_DETECTED/AWAITING_RETEST helt over.
                    self._ticker_state[ticker] = STATE_DONE_FOR_DAY
                    self._ticker_side[ticker]  = "long"
                    return EntrySignal(
                        ticker=ticker,
                        entry_price=bar.close,
                        entry_time=bar.timestamp,
                        side="long",
                        metadata={"orb_high": orb_high, "orb_low": orb_low},
                    )
                self._ticker_state[ticker]  = STATE_BREAKOUT_DETECTED
                self._ticker_side[ticker]   = "long"
                self._breakout_time[ticker] = bar.timestamp
                return None

            # Short breakdown (kun hvis aktiveret)
            if (enable_shorts
                    and bar.close < orb_low
                    and vol_ok
                    and rsi > (100.0 - rsi_max)):
                if not require_retest:
                    self._ticker_state[ticker] = STATE_DONE_FOR_DAY
                    self._ticker_side[ticker]  = "short"
                    return EntrySignal(
                        ticker=ticker,
                        entry_price=bar.close,
                        entry_time=bar.timestamp,
                        side="short",
                        metadata={"orb_high": orb_high, "orb_low": orb_low},
                    )
                self._ticker_state[ticker]  = STATE_BREAKOUT_DETECTED
                self._ticker_side[ticker]   = "short"
                self._breakout_time[ticker] = bar.timestamp
            return None

        side = self._ticker_side.get(ticker)

        # BREAKOUT_DETECTED → vent på pullback i retning af entry
        if state == STATE_BREAKOUT_DETECTED:
            elapsed = (bar.timestamp - self._breakout_time[ticker]).total_seconds()
            if elapsed > retest_timeout_sec:
                # Timeout — drop, tilbage til WAITING og lad nye breakouts vinde
                self._ticker_state[ticker] = STATE_WAITING
                self._ticker_side[ticker]  = None
                self._breakout_time.pop(ticker, None)
                return None

            if side == "long":
                # Pullback ned til orb_high (tolerance 0.1% over)
                if bar.low <= orb_high * RETEST_TOLERANCE:
                    self._ticker_state[ticker]    = STATE_AWAITING_RETEST
                    self._retest_extremum[ticker] = bar.low
            else:  # short
                # Pullback op til orb_low (tolerance 0.1% under)
                if bar.high >= orb_low * (2.0 - RETEST_TOLERANCE):
                    self._ticker_state[ticker]    = STATE_AWAITING_RETEST
                    self._retest_extremum[ticker] = bar.high
            return None

        # AWAITING_RETEST → vent på bounce væk fra ORB-niveau
        if state == STATE_AWAITING_RETEST:
            if side == "long":
                if bar.low < self._retest_extremum[ticker]:
                    self._retest_extremum[ticker] = bar.low

                # Bounce: close tilbage over orb_high → ENTRY (long)
                if bar.close > orb_high:
                    self._ticker_state[ticker] = STATE_DONE_FOR_DAY
                    return EntrySignal(
                        ticker=ticker,
                        entry_price=bar.close,
                        entry_time=bar.timestamp,
                        side="long",
                        metadata={
                            "orb_high":    orb_high,
                            "orb_low":     orb_low,
                            "retest_low":  self._retest_extremum[ticker],
                        },
                    )
            else:  # short
                if bar.high > self._retest_extremum[ticker]:
                    self._retest_extremum[ticker] = bar.high

                # Bounce: close tilbage under orb_low → ENTRY (short)
                if bar.close < orb_low:
                    self._ticker_state[ticker] = STATE_DONE_FOR_DAY
                    return EntrySignal(
                        ticker=ticker,
                        entry_price=bar.close,
                        entry_time=bar.timestamp,
                        side="short",
                        metadata={
                            "orb_high":    orb_high,
                            "orb_low":     orb_low,
                            "retest_high": self._retest_extremum[ticker],
                        },
                    )
            return None

        return None

    def mark_done(self, ticker: str) -> None:
        """Mark som færdig manuelt — fx hvis position-management overtog."""
        self._ticker_state[ticker] = STATE_DONE_FOR_DAY

    def get_state(self, ticker: str) -> str:
        """Aktuel state for en ticker — bruges af live algo til logging."""
        return self._ticker_state.get(ticker, STATE_WAITING)

    def get_side(self, ticker: str) -> Optional[str]:
        """Aktuel side ("long"/"short"/None) for en ticker."""
        return self._ticker_side.get(ticker)

"""
strategies/momentum_orb/entry.py
─────────────────────────────────
Break-and-retest entry-engine for MomentumORB.

State-machine pr. ticker pr. dag:
  waiting           → leder efter første breakout
  breakout_detected → venter på pullback til ORB-niveau (timeout fra config)
  awaiting_retest   → venter på bounce tilbage over ORB high
  entered           → position er åbnet, denne engine er færdig for dagen
  done_for_day      → enten entered eller drop, ingen flere forsøg

Entry-kriterier (alle fra VariantConfig):
  1. Pris > ORB High (ORB-vindue fra orb_end_minutes i config)
  2. Volumen ≥ vol_mult × gennemsnit
  3. RSI(14) < rsi_max
  4. Break-and-retest bekræftet (pullback + bounce)
  5. Inden for handelsvindue (caller filtrerer)

VIGTIGT: Entry-engine læser nu parametre fra context['config'] hvor config
er en VariantConfig-instans. Dette gør entry-engine variant-aware så
parameter exploration kan teste forskellige værdier.
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


def _get_params(context: dict) -> tuple[float, float, int]:
    """
    Hent (vol_mult, rsi_max, retest_timeout_sec) fra context.

    Hvis context['config'] er en VariantConfig bruges den — det er
    den nye, variant-aware måde. Hvis ikke, falder vi tilbage på
    module-level constants (bagudkompatibel med ældre kald).
    """
    config = context.get("config")
    if isinstance(config, VariantConfig):
        return config.vol_mult, config.rsi_max, config.retest_timeout_sec
    return float(VOL_MULT), float(RSI_MAX), int(RETEST_TIMEOUT_SEC)


class MomentumORBEntry:
    """
    Entry-engine for MomentumORB.

    Holder state pr. ticker pr. dag — fordi state-machinen er ticker-specifik.
    Live algo og backtest skal begge kalde reset_for_day(date, context) før
    første check_entry på en ny dag.

    Context-felter der bruges:
      ticker:        str
      orb_high:      float
      orb_low:       float
      avg_vol:       float
      prior_closes:  list[float]  (RSI-start, kan være tom)
      config:        VariantConfig (ny — variant-specifikke parametre)
    """

    def __init__(self):
        # ticker → state
        self._ticker_state: dict[str, str] = {}
        # ticker → datetime hvornår breakout først blev set
        self._breakout_time: dict[str, datetime] = {}
        # ticker → laveste pris under pullback (kun til bookføring)
        self._retest_low: dict[str, float] = {}
        # ticker → liste af closes (til RSI). Backtest akkumulerer som bars kommer.
        self._closes: dict[str, list[float]] = {}

    def reset_for_day(self, date, context: dict) -> None:
        """Nulstil alt for én ny handelsdag."""
        ticker = context.get("ticker")
        if ticker is None:
            raise ValueError("Context skal indeholde 'ticker'")

        self._ticker_state[ticker]   = STATE_WAITING
        self._breakout_time.pop(ticker, None)
        self._retest_low.pop(ticker, None)
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
        """
        state = self._ticker_state.get(ticker, STATE_WAITING)

        # Track closes til RSI — opdaterer altid, uanset state
        self._closes.setdefault(ticker, []).append(bar.close)

        # DONE_FOR_DAY → ingen flere forsøg
        if state == STATE_DONE_FOR_DAY:
            return None

        orb_high = context["orb_high"]
        avg_vol  = context["avg_vol"]
        vol_mult, rsi_max, retest_timeout_sec = _get_params(context)

        # WAITING → led efter første breakout
        if state == STATE_WAITING:
            rsi = calc_rsi_from_closes(self._closes[ticker])
            if (bar.close > orb_high
                    and bar.volume >= avg_vol * vol_mult
                    and rsi < rsi_max
                    and avg_vol > 0):
                self._ticker_state[ticker]  = STATE_BREAKOUT_DETECTED
                self._breakout_time[ticker] = bar.timestamp
            return None

        # BREAKOUT_DETECTED → vent på pullback
        if state == STATE_BREAKOUT_DETECTED:
            elapsed = (bar.timestamp - self._breakout_time[ticker]).total_seconds()
            if elapsed > retest_timeout_sec:
                # Timeout — drop, tilbage til WAITING
                self._ticker_state[ticker] = STATE_WAITING
                self._breakout_time.pop(ticker, None)
                return None

            # Pullback rammes hvis bar_low ≤ orb_high × tolerance
            if bar.low <= orb_high * RETEST_TOLERANCE:
                self._ticker_state[ticker] = STATE_AWAITING_RETEST
                self._retest_low[ticker]   = bar.low
            return None

        # AWAITING_RETEST → vent på bounce
        if state == STATE_AWAITING_RETEST:
            if bar.low < self._retest_low[ticker]:
                self._retest_low[ticker] = bar.low

            # Bounce: close tilbage over orb_high → ENTRY
            if bar.close > orb_high:
                self._ticker_state[ticker] = STATE_DONE_FOR_DAY
                return EntrySignal(
                    ticker=ticker,
                    entry_price=bar.close,
                    entry_time=bar.timestamp,
                    metadata={
                        "orb_high":   orb_high,
                        "orb_low":    context["orb_low"],
                        "retest_low": self._retest_low[ticker],
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

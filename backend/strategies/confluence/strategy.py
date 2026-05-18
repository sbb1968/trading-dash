"""
strategies/confluence/strategy.py
──────────────────────────────────
Topnivå-klasse for Konfluens-strategien.

Implementerer Strategy-protokollen fra strategies/base.py så live algo og
backtest-engine kan behandle strategien som en black box.

VIGTIG ARKITEKTUR-FORSKEL fra MomentumORB:
═══════════════════════════════════════════
Konfluens-indikatorerne kører KONTINUERLIGT på tværs af handelsdage —
ikke pr. dag som MomentumORB's ORB. Det er fordi Pine-scriptets
indikatorer (HTF EMA, RSI, pivots, swing-tracking, bear-divergens)
er afhængige af langvarig historie. Hvis vi resettede dem pr. dag
ville:
  - Dagens FØRSTE 09:30-bar aldrig kunne triggere et signal
    (intet HTF, ingen pivots, ingen RSI-historie)
  - Higher Low-detektion kun virke fra ~bar 8 hver dag (efter pivots)
  - Bear-divergens være meningsløst (kræver flere swing highs)
Pine bruger 'var'-variabler som persisterer fra script-load til nu —
ofte ugers/månedernes historie.

LØSNING: build_session_context() tager ALLE bars (warmup + target),
pre-computer indikatorer i én lang serie, og lader entry/exit slå op
pr. bar via timestamp. reset_for_day() er en no-op her — den er kun
del af Strategy-protokollen for kompatibilitet med MomentumORB.

Caller (backtest eller live) er ansvarlig for:
  - At hente nok historisk warmup (typisk 5+ handelsdage før target)
  - At filtrere entry-tjek til kun target-perioden (via in_date_range
    parameter — matcher Pine's useStartDate/useEndDate)
"""

from __future__ import annotations

from datetime import datetime, time as dtime, date as date_cls
from typing import Optional

import pandas as pd

from strategies.base import Bar
from strategies.confluence.config import (
    VARIANTS,
    LIVE_VARIANT_KEY,
    ConfluenceVariantConfig,
    SESSION_START_HHMM,
    SESSION_END_HHMM,
)
from strategies.confluence.entry import ConfluenceEntry, precompute_indicators
from strategies.confluence.exit import ConfluenceExit


SESSION_START = dtime(*SESSION_START_HHMM)
SESSION_END   = dtime(*SESSION_END_HHMM)


class ConfluenceStrategy:
    """
    Long-only konfluens-strategi på 5min US equities.

    Bruges af:
      - backtest_confluence.py (IBKR historik over flere uger)
      - algo_confluence.py (live trading, kommer i Trin 3)
    """

    def __init__(self):
        self._entry = ConfluenceEntry()
        self._exit  = ConfluenceExit(VARIANTS)

    # ── Protocol-properties ────────────────────────────────

    @property
    def name(self) -> str:
        return "Konfluens"

    @property
    def description(self) -> str:
        return (
            "Long-only konfluens på US small caps med 4+ af 6 entry-bricks "
            "(HTF EMA, VWAP, RSI reset, Higher Low, reversal candle, vol spike) "
            "og 3-lags hybrid exit (ATR stop + trail + 3+ af 5 signal-bricks)."
        )

    @property
    def variants(self) -> dict:
        return VARIANTS

    @property
    def live_variant_key(self) -> str:
        return LIVE_VARIANT_KEY

    @property
    def entry(self) -> ConfluenceEntry:
        return self._entry

    @property
    def exit(self) -> ConfluenceExit:
        return self._exit

    # ── Session context builder (NY — erstatter build_day_context) ────

    def build_session_context(
        self,
        ticker: str,
        bars: list[Bar],
        config: Optional[ConfluenceVariantConfig] = None,
    ) -> Optional[dict]:
        """
        Byg ÉN context med pre-computed indikatorer over hele bar-perioden.

        bars: ALLE bars i kronologisk rækkefølge — inkluderer både warmup
              (historie før target-dato) og target-perioden. Filtres til
              session (09:30-16:00 ET) før precompute.

        Returnerer None hvis der er for få bars til indikator-warmup.
        """
        if not bars:
            return None

        # Filtrer til session-bars (09:30-16:00 ET)
        session_bars = sorted(
            [b for b in bars if SESSION_START <= b.time_et <= SESSION_END],
            key=lambda b: b.timestamp,
        )

        # Pine's HTF EMA(50) på 15min kræver mindst 50 × 15min = 750 min af bars.
        # Det er ca. 2 handelsdage (78 × 5min bars pr. dag). Vi vil have MERE
        # historie for at indikator-værdier matcher Pine, så minimum er sat
        # til 50 bars men 200+ anbefales.
        if len(session_bars) < 50:
            return None

        if config is None:
            config = VARIANTS[LIVE_VARIANT_KEY]

        # Konvertér til DataFrame med DatetimeIndex
        bars_df = pd.DataFrame({
            "open":   [b.open   for b in session_bars],
            "high":   [b.high   for b in session_bars],
            "low":    [b.low    for b in session_bars],
            "close":  [b.close  for b in session_bars],
            "volume": [b.volume for b in session_bars],
        }, index=pd.DatetimeIndex([b.timestamp for b in session_bars]))

        # Pre-compute alle indikatorer KONTINUERLIGT over hele perioden
        ind_df = precompute_indicators(bars_df, config)

        return {
            "ticker":   ticker,
            "bars_df":  bars_df,
            "ind_df":   ind_df,
            "config":   config,
        }

    # ── build_day_context (legacy — kaldes ikke af Konfluens, men beholdes
    #     for Strategy-protokol-kompatibilitet) ─────────────────────────

    def build_day_context(
        self,
        ticker: str,
        bars: list[Bar],
        config: Optional[ConfluenceVariantConfig] = None,
    ) -> Optional[dict]:
        """
        Alias til build_session_context for protokol-kompatibilitet.

        ADVARSEL: hvis denne kaldes med kun én dags bars, vil de fleste
        indikatorer mangle warmup-historie og strategien vil under-performe.
        Brug build_session_context og send hele warmup+target perioden ind.
        """
        return self.build_session_context(ticker, bars, config)

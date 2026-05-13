"""
strategies/momentum_orb/strategy.py
────────────────────────────────────
Topnivå-klasse for MomentumORB.

Implementerer Strategy-protokollen fra strategies/base.py — så live algo og
backtest-engine kan behandle strategien som en black box.
"""

from __future__ import annotations

from datetime import time as dtime, timedelta
from typing import Optional

from strategies.base import Bar
from strategies.momentum_orb.config import (
    VARIANTS,
    LIVE_VARIANT_KEY,
    VariantConfig,
)
from strategies.momentum_orb.entry import MomentumORBEntry
from strategies.momentum_orb.exit import MomentumORBExit


# ORB-vindue startpunkt og handelsvindue (uændret konstant — det er markedstider)
ORB_START   = dtime(9, 30)
TRADE_START = dtime(9, 45)


def _orb_end_time(orb_end_minutes: int) -> dtime:
    """
    Beregn ORB-slut baseret på orb_end_minutes fra VariantConfig.

    Default: 14 → 09:30 + 14 min = 09:44 (dvs. inkluderer 09:30, 09:35, 09:40 bars)
    """
    total_minutes = 9 * 60 + 30 + orb_end_minutes
    return dtime(total_minutes // 60, total_minutes % 60)


class MomentumORBStrategy:
    """
    Opening Range Breakout med break-and-retest entry og 3-stadie hybrid exit.

    Bruges af:
      - algo_momentum.py (live)  → får entry-signaler bar for bar via IBKR-snapshots
      - backtest engine          → får entry-signaler bar for bar via CSV-data
    """

    def __init__(self):
        self._entry = MomentumORBEntry()
        self._exit  = MomentumORBExit(VARIANTS)

    # ── Protocol-properties ───────────────────────────────────

    @property
    def name(self) -> str:
        return "Momentum ORB"

    @property
    def description(self) -> str:
        return ("Opening Range Breakout på US small caps med break-and-retest "
                "entry og 3-stadie hybrid exit. Kører 09:45-10:30 ET.")

    @property
    def variants(self) -> dict:
        return VARIANTS

    @property
    def live_variant_key(self) -> str:
        return LIVE_VARIANT_KEY

    @property
    def entry(self) -> MomentumORBEntry:
        return self._entry

    @property
    def exit(self) -> MomentumORBExit:
        return self._exit

    # ── Day context builder ───────────────────────────────────

    def build_day_context(
        self,
        ticker: str,
        bars: list[Bar],
        config: Optional[VariantConfig] = None,
    ) -> Optional[dict]:
        """
        Beregn ORB-niveauer og dagsmetrics fra bars for én dag.

        Hvis config er givet, bruges config.orb_end_minutes til ORB-vindue.
        Ellers default: 14 min (09:30-09:44).

        Returnerer dict med ticker, orb_high, orb_low, avg_vol, prior_closes,
        og config (videresendes til entry-engine via context).
        Returnerer None hvis dagen skal springes over.
        """
        # Bestem ORB-slut fra config (eller default 14 min)
        orb_end_min = config.orb_end_minutes if config is not None else 14
        orb_end_time = _orb_end_time(orb_end_min)

        # Filtrer bars til markedstid
        market = [b for b in bars
                  if dtime(9, 30) <= b.time_et <= dtime(16, 0)]
        if len(market) < 8:
            return None

        # ORB-bars (09:30 til orb_end)
        orb_bars = [b for b in market
                    if ORB_START <= b.time_et <= orb_end_time]
        if not orb_bars:
            return None

        orb_high = max(b.high for b in orb_bars)
        orb_low  = min(b.low  for b in orb_bars)

        # Gennemsnitsvolumen — alle dagens bars
        total_vol = sum(b.volume for b in market)
        if total_vol <= 0:
            return None
        avg_vol = total_vol / len(market)

        return {
            "ticker":       ticker,
            "orb_high":     orb_high,
            "orb_low":      orb_low,
            "avg_vol":      avg_vol,
            "prior_closes": [],
            "config":       config,   # Entry-engine læser entry-parametre herfra
            "orb_end_time": orb_end_time,   # Caller kan bruge til at filtrere entry-bars
            "trade_start":  TRADE_START,
        }

"""
strategies/confluence
─────────────────────
Konfluens Strategi v3 — Long-only, US aktier, 5m primær timeframe.

Implementeret 1:1 efter Pine Script v5 referencen i Confluence_Strategy.md.

Entry-logik (4+ af 6 konfluens-betingelser):
  1. HTF trend       — close > 15m EMA(50)
  2. VWAP            — close > VWAP eller pullback under lower band med bullish lukning
  3. RSI reset       — RSI(14) var under 35 i sidste 5 bars OG krydser op gennem 40
  4. Higher Low      — sidste swing-low > forrige (pivots 3/3)
  5. Reversal candle — bullish engulfing, hammer eller strong close
  6. Volume spike    — volume > 1.2× SMA(20) med bullish close

Exit-logik (3-lags hybrid):
  Lag 1 — ATR hard stop:  entry − 1.2 × ATR(14), beregnet ved entry
  Lag 2 — Trailing stop:  aktiveres ved +1R; vælg af Swing Low / EMA Fast / ATR
  Lag 3 — Signal exit:    3+ af 5 exit-konfluens (RSI overbought reversal, lower high,
                          bearish candle, close under fast EMA, vol/divergens)

Sizing: 1% af equity i risiko pr. trade. qty = risk_amount / per_share_risk.

Brug:
  from strategies.confluence import ConfluenceStrategy

  strat = ConfluenceStrategy()
  context = strat.build_day_context(ticker, bars_for_day)
  strat.entry.reset_for_day(date, context)
  for bar in bars:
      signal = strat.entry.check_entry(ticker, bar, context)
      if signal:
          position = strat.exit.open_position(signal, shares, variant_key)
"""

from strategies.confluence.strategy import ConfluenceStrategy
from strategies.confluence.config import (
    VARIANTS,
    LIVE_VARIANT_KEY,
    ConfluenceVariantConfig,
)

__all__ = [
    "ConfluenceStrategy",
    "VARIANTS",
    "LIVE_VARIANT_KEY",
    "ConfluenceVariantConfig",
]
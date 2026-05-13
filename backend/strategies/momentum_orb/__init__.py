"""
strategies/momentum_orb
───────────────────────
Momentum ORB Breakout-strategi.

Brug:
  from strategies.momentum_orb import MomentumORBStrategy

  strat = MomentumORBStrategy()
  context = strat.build_day_context(ticker, bars_for_day)
  strat.entry.reset_for_day(date, context)

  for bar in bars:
      signal = strat.entry.check_entry(ticker, bar, context)
      if signal:
          position = strat.exit.open_position(signal, shares, variant_key)
          # ... senere ved hver pris-opdatering:
          strat.exit.update(position, current_price, variant_key)
          exit_decision = strat.exit.check_exit_live(position, ..., variant_key)
"""

from strategies.momentum_orb.strategy import MomentumORBStrategy
from strategies.momentum_orb.config import (
    VARIANTS,
    LIVE_VARIANT_KEY,
    VariantConfig,
)

__all__ = [
    "MomentumORBStrategy",
    "VARIANTS",
    "LIVE_VARIANT_KEY",
    "VariantConfig",
]

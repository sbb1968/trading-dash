"""
strategies/us_reversion
────────────────────────
US-reversion — long-only mean-reversion på MES i den amerikanske session
(09:30–15:00 ET = 15:30–21:00 dansk).

  15m: bånd ved ±entry_z. En færdig close under det NEDRE bånd ARMERER.
   5m: entry når alle tre bekræftelser holder — to grønne candles med samlet
       stigning ≥ rise_pct, stigende MACD-linje, og (på 15m) stigende CMF.
  exit: stop −stop_pct fra entry · trailing −trail_pct fra højeste close ·
       valgfrit z ≥ +entry_z · tvangsluk 15:00 ET.

rule.py er ENESTE sandhedskilde for beslutningslogikken og deles af
live-wrapperen (algo_us_reversion.py) og backtesten (us_reversion_backtest.py),
så de aldrig kan divergere.

Brug:
  from strategies.us_reversion import UsReversionStrategy
  from strategies.us_reversion import config, rule
"""

from strategies.us_reversion.strategy import UsReversionStrategy
from strategies.us_reversion import config, rule
from strategies.us_reversion.config import VARIANTS, LIVE_VARIANT_KEY

__all__ = ["UsReversionStrategy", "config", "rule", "VARIANTS", "LIVE_VARIANT_KEY"]

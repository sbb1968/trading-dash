"""
strategies/europa_reversion
───────────────────────────
Europa-reversion — mean-reversion på index-micro futures (MES/M2K) i den
europæiske session (02–08 ET). z-score på FÆRDIGE 15-min bars:
  entry |z|≥2.0 (z≥+2→short, z≤−2→long) · exit revert |z|≤0.5 / stop |z|≥3.5
  · tvangsluk ved sessions-slut. Én position pr. instrument.

rule.py er ENESTE sandhedskilde for beslutningslogikken og deles af
live-wrapperen (algo_europa_reversion.py) og backtesten (meanrev_backtest.py),
så de aldrig kan divergere.

Brug:
  from strategies.europa_reversion import EuropaReversionStrategy
  from strategies.europa_reversion import config, rule
"""

from strategies.europa_reversion.strategy import EuropaReversionStrategy
from strategies.europa_reversion import config, rule

__all__ = ["EuropaReversionStrategy", "config", "rule"]

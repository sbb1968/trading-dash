"""
strategies/__init__.py
──────────────────────
Registry over alle tilgængelige strategier.

Når en ny strategi tilføjes:
  1. Opret strategies/<navn>/  med entry.py, exit.py, config.py, strategy.py
  2. Tilføj klassen til STRATEGY_REGISTRY herunder
  3. Live algo og backtest finder den automatisk via key
"""

from strategies.confluence2 import Confluence2Strategy
from strategies.europa_reversion import EuropaReversionStrategy

# Registry: key → Strategy-klasse (ikke instans — instantieres ved brug)
# Konfluens (K1) + Momentum ORB pensioneret og fjernet 2026-06-18.
STRATEGY_REGISTRY = {
    "confluence2":      Confluence2Strategy,
    # Europa-reversion er ikke OHLC-engine-baseret (ren z-score-regel); den
    # backtestes med eureversion_backtest.py.
    "europa_reversion": EuropaReversionStrategy,
}


def get_strategy(key: str):
    """Find en strategi-klasse via dens registry-key. Returnér instans."""
    if key not in STRATEGY_REGISTRY:
        raise KeyError(
            f"Ukendt strategi {key!r}. "
            f"Tilgængelige: {list(STRATEGY_REGISTRY.keys())}"
        )
    return STRATEGY_REGISTRY[key]()


def list_strategies() -> list[str]:
    """Liste af alle registrerede strategi-keys."""
    return list(STRATEGY_REGISTRY.keys())
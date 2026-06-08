"""
strategies/__init__.py
──────────────────────
Registry over alle tilgængelige strategier.

Når en ny strategi tilføjes:
  1. Opret strategies/<navn>/  med entry.py, exit.py, config.py, strategy.py
  2. Tilføj klassen til STRATEGY_REGISTRY herunder
  3. Live algo og backtest finder den automatisk via key
"""

from strategies.momentum_orb import MomentumORBStrategy
from strategies.confluence import ConfluenceStrategy
from strategies.confluence2 import Confluence2Strategy
from strategies.europa_reversion import EuropaReversionStrategy

# Registry: key → Strategy-klasse (ikke instans — instantieres ved brug)
STRATEGY_REGISTRY = {
    "momentum_orb":     MomentumORBStrategy,
    "confluence":       ConfluenceStrategy,
    "confluence2":      Confluence2Strategy,
    # Europa-reversion er ikke OHLC-engine-baseret (ren z-score-regel); den
    # backtestes med meanrev_backtest.py, ikke den generiske backtest_momentum.py.
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
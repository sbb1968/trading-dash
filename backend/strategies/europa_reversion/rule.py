"""
strategies/europa_reversion/rule.py
───────────────────────────────────
Den rene z-score-regel — ENESTE sandhedskilde for beslutningslogikken.

Bruges identisk af live-wrapperen (algo_europa_reversion.py) og backtesten
(eureversion_backtest.py), så de aldrig kan divergere. Ingen IBKR, ingen state —
kun matematik på en liste af closes. Spejler den validerede eureversion_backtest-
regel 1:1 (population-std via pstdev).
"""

from __future__ import annotations

from statistics import pstdev
from typing import Optional

from strategies.europa_reversion.config import ENTRY_Z, EXIT_Z, STOP_Z


def compute_z(closes: list[float]) -> Optional[tuple[float, float]]:
    """
    (z, std) over de seneste closes — eller None hvis std ≤ 0 eller seneste
    close ≤ 0 (intet brugbart signal). std er population-std (pstdev), præcis
    som backtesten, så live og backtest beregner z identisk.
    """
    if len(closes) < 2:
        return None
    ma = sum(closes) / len(closes)
    sd = pstdev(closes)
    if sd <= 0 or closes[-1] <= 0:
        return None
    return (closes[-1] - ma) / sd, sd


def entry_side(z: float, entry_z: float = ENTRY_Z) -> Optional[str]:
    """Hvilken side åbnes ved flad position? z≥+entry_z → short, z≤−entry_z → long."""
    if z >= entry_z:
        return "short"
    if z <= -entry_z:
        return "long"
    return None


def exit_reason(side: str, z: float,
                exit_z: float = EXIT_Z, stop_z: float = STOP_Z) -> Optional[str]:
    """
    Exit-årsag for en åben position, eller None.
      'revert' — tilbage mod middel (|z| ≤ exit_z)
      'stop'   — stræk fortsætter (|z| ≥ stop_z)
    Revert tjekkes før stop (samme rækkefølge som den validerede backtest).
    """
    if side == "long":
        if z >= -exit_z:
            return "revert"
        if z <= -stop_z:
            return "stop"
    else:  # short
        if z <= exit_z:
            return "revert"
        if z >= stop_z:
            return "stop"
    return None


def stop_distance(std: float, entry_z: float = ENTRY_Z, stop_z: float = STOP_Z) -> float:
    """Stop-afstand i prispoint = (stop_z − entry_z) × std — bruges til kontrakt-sizing."""
    return (stop_z - entry_z) * std

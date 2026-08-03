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

from strategies.europa_reversion.config import ENTRY_Z, EXIT_Z, STOP_Z, REQUIRE_CONFIRM


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
    """Hvilken side åbnes ved flad position? z≥+entry_z → short, z≤−entry_z → long.

    RÅT z-signal uden bekræftelse. Live skal bruge `confirmed_entry_side` —
    denne beholdes som den rene z-regel (og bruges af den).
    """
    if z >= entry_z:
        return "short"
    if z <= -entry_z:
        return "long"
    return None


def turned_back(side: str, close_now: float, close_prev: float) -> bool:
    """Lukkede den seneste bar tilbage MOD middelværdien?

    short = prisen er strakt OP  → en vending er en LAVERE luk end forrige bar
    long  = prisen er strakt NED → en vending er en HØJERE luk end forrige bar
    """
    if side == "short":
        return close_now < close_prev
    if side == "long":
        return close_now > close_prev
    return False


def confirmed_entry_side(closes: list[float], z: float,
                         entry_z: float = ENTRY_Z,
                         require_confirm: bool = REQUIRE_CONFIRM) -> Optional[str]:
    """Entry-side MED bekræftelse af at reversionen er begyndt.

    Tilføjet 3/8-2026. Den rå regel gik ind i samme øjeblik |z| krydsede 2 — og
    den bar der PUSHER z over 2 er per definition en bar der lukkede i strækkets
    retning. Vi købte altså systematisk på det punkt hvor bevægelsen var stærkest
    imod os. Kravet nu: den seneste bar skal have lukket tilbage mod middel.

    Målt på MES+M2K, europæisk session, 2 bp rundtur, sep-2025 → jun-2026:

        regel                     n     sum%     PF    IS PF   OOS PF   stop-andel
        rå |z|≥2 (før)          349    13,75   1,52    1,03     2,49       8,3 %
        + bekræftelse           101    15,95   5,05    5,10     4,94       2,0 %

    Mekanismen er ægte og ses direkte i exit-mixet: stop-andelen falder fra
    8,3 % til 2,0 %. Ved at vente på vendingen undgår vi netop de entries hvor
    strækket var en begyndende trend i stedet for en overdrivelse.

    ⚠ FORVENT IKKE PF 5 LIVE. Retningen er robust — bekræftelsen forbedrer i 8 af
    9 celler i et lookback×entry_z-gitter — men STØRRELSEN er det ikke: nabocellerne
    giver 1,4-2,5, og live-konfigurationen (lookback 30, z 2,0) er tilfældigvis
    gitterets bedste celle. Regn med 2-3, ikke 5. Dertil: kun 101 handler, og
    2025-Q4 havde blot 5 af dem (og tabte). Fjernes de 5 bedste handler falder
    PF fra 5,05 til 2,83 — stadig langt over 1,52, men halen vejer tungt.

    En separat trend-vagt (Kaufman efficiency ratio) blev testet og er IKKE
    nødvendig: bekræftelsen dækker den allerede. ER≤0,55 alene gav 1,53, og
    kombineret med bekræftelsen blev det en anelse DÅRLIGERE (4,69 mod 5,05).
    """
    side = entry_side(z, entry_z)
    if side is None:
        return None
    if not require_confirm:
        return side
    if len(closes) < 2:
        return None
    return side if turned_back(side, closes[-1], closes[-2]) else None


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

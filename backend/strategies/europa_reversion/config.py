"""
strategies/europa_reversion/config.py
─────────────────────────────────────
Låste parametre for Europa-reversion (mean-reversion på index-micro futures i
den europæiske session). ÉN konfiguration — bevidst (ingen variant-sweep);
Søren prøver fx LOOKBACK 40 ved at ændre ét tal her.

Disse konstanter er ENESTE sandhedskilde for strategiens parametre: både
live-wrapperen (algo_europa_reversion.py) og backtesten (eureversion_backtest.py)
læser herfra, så live og backtest aldrig kan divergere på parametre.
"""

from __future__ import annotations
from datetime import time as dtime

# ── Session (ET) ──────────────────────────────────────────────
SESSION_START_ET    = dtime(2, 0)    # europæisk session åbner 02:00 ET
SESSION_END_ET      = dtime(8, 0)    # lukker 08:00 ET (= 14:00 dansk)
FORCE_CLOSE_ET      = dtime(7, 55)   # tvangsluk-klokkeslæt (sidste sikre bar før 08:00)
LAST_SESSION_BAR_ET = dtime(7, 45)   # sidste 15-min slot der regnes som "i sessionen"

# ── z-regel ───────────────────────────────────────────────────
LOOKBACK = 30        # bars til MA/std (prøv 40 ved at ændre dette ene tal)
ENTRY_Z  = 2.0       # |z| ≥ ENTRY_Z → entry (z≥+2 short, z≤−2 long)
EXIT_Z   = 0.5       # tilbage mod middel → exit
STOP_Z   = 3.5       # stræk fortsætter → stop

# ── Bars ──────────────────────────────────────────────────────
BAR_SIZE    = "15 mins"
BAR_MINUTES = 15     # afledt af BAR_SIZE — bruges til "er baren færdig?"-tjek

# ── Instrumenter + sizing ─────────────────────────────────────
INSTRUMENTS = ["MES", "M2K"]   # IKKE MNQ (mean-reverter ikke pålideligt — se SPEC)
RISK_PCT    = 0.01             # 1% af konto-equity pr. handel
# Kontrakt-multiplikatorer ($ pr. prispoint). MES og M2K er begge $5/point
# (CME micro). MES bekræftet live: reqPositions-avgCost = pris × 5.
MULTIPLIER = {
    "MES": 5.0,
    "M2K": 5.0,
}

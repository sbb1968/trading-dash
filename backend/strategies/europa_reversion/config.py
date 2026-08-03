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

# Entry-bekraeftelse (3/8-2026). Uden den gik strategien ind i samme oejeblik
# |z| krydsede ENTRY_Z — og den bar der PUSHER z over graensen lukkede per
# definition i straekkets retning. Vi gik altsaa systematisk ind paa det punkt
# hvor bevaegelsen var staerkest IMOD os. Med bekraeftelsen skal den seneste bar
# have lukket tilbage mod middel foer vi handler.
#
# MES+M2K, europaeisk session, 2 bp, sep-2025 -> jun-2026:
#     raa |z|>=2      n=349  sum=+13,75%  PF=1,52  (IS 1,03 / OOS 2,49)  stop-andel 8,3 %
#     + bekraeftelse  n=101  sum=+15,95%  PF=5,05  (IS 5,10 / OOS 4,94)  stop-andel 2,0 %
#
# Faerre handler, MERE afkast, og stop-andelen falder til en fjerdedel — det er
# mekanismen: vi undgaar de entries hvor straekket var en begyndende trend.
# Se rule.confirmed_entry_side for forbeholdene (forvent 2-3 live, ikke 5).
REQUIRE_CONFIRM = True
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
# Loft paa antal kontrakter pr. handel. 1 = Ibens lille test-konto (~$1.400) har ikke
# margin til mere; sammen med gulvet i _size_contracts betyder det ALTID praecis 1
# kontrakt. Haev naar kontoen kan baere det (saa faar risiko/handel effekt igen).
MAX_CONTRACTS = 1

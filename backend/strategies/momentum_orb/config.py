"""
strategies/momentum_orb/config.py
──────────────────────────────────
Variant-konfigurationer for MomentumORB.

En "variant" er en parametriseret udgave af samme strategi. De testes alle i
backtest, og live-algo'en bruger den valgte vinder.

EXIT-FELTER:
  stop_mode:           "fixed_pct" | "orb_mid" | "orb_low"
                       Hvordan beregnes initial stop?
                         - fixed_pct: entry × (1 - fixed_stop_pct)
                         - orb_mid:   max(orb_mid, entry × 0.99)   [1% gulv]
                         - orb_low:   max(orb_low, entry × 0.99)   [1% gulv]
  fixed_stop_pct:      kun for stop_mode="fixed_pct"

  target_pct:          exit-target i stage 1 og 2 (typisk 0.04 = +4%)

  breakeven_enabled:   skal stop flyttes til entry når trigger nås?
  breakeven_trigger_pct: ved hvilken % gevinst aktiveres BE? (default 0.03)

  trail_enabled:       skal trailing stop tage over efter target?
  trail_activate_pct:  ved hvilken % gevinst aktiveres trail? (default 0.04)
  trail_distance_pct:  hvor langt under highest_high skal trail-stop ligge?

ENTRY-FELTER (nye, flyttet fra module-level constants):
  vol_mult:            volumen-multiplikator for breakout (1.5 = 150% af snit)
  rsi_max:             RSI-tærskel for entry (rsi < rsi_max)
  orb_end_minutes:     ORB-vindue-længde fra 09:30 (default 14 = 09:30-09:44)
  retest_timeout_sec:  timeout for pullback efter breakout (default 300 = 5 min)

  Disse er nu variant-specifikke så parameter exploration kan teste forskellige
  kombinationer. De 5 oprindelige varianter bruger de samme default-værdier som
  før, så backtesten skal give identiske tal.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class VariantConfig:
    name: str

    # ── Exit-parametre ─────────────────────────────────────────
    stop_mode: str
    fixed_stop_pct: float = 0.02

    target_pct: float = 0.04

    breakeven_enabled: bool = True
    breakeven_trigger_pct: float = 0.03

    trail_enabled: bool = True
    trail_activate_pct: float = 0.04
    trail_distance_pct: float = 0.015

    # ── Entry-parametre (tidligere module-level konstanter) ────
    vol_mult: float = 1.5            # volumen ≥ vol_mult × gennemsnit
    rsi_max: float = 80.0            # rsi < rsi_max
    orb_end_minutes: int = 14        # ORB-vindue: 09:30 til 09:30 + N minutter
    retest_timeout_sec: int = 300    # max ventetid for pullback efter breakout


# De 5 oprindelige varianter fra opdraget — låst design
# Entry-parametre arver fra default-værdier (vol=1.5, rsi=80, orb=14min, timeout=5min)
VARIANTS: dict[str, VariantConfig] = {
    "baseline": VariantConfig(
        name="Baseline (-2% / +4%, ingen trail)",
        stop_mode="fixed_pct",
        fixed_stop_pct=0.02,
        breakeven_enabled=False,
        trail_enabled=False,
    ),
    "A": VariantConfig(
        name="A: ORB Mid + trail 1.5%",
        stop_mode="orb_mid",
        breakeven_trigger_pct=0.03,
        trail_activate_pct=0.04,
        trail_distance_pct=0.015,
    ),
    "B": VariantConfig(
        name="B: ORB Low + trail 1.5%",
        stop_mode="orb_low",
        breakeven_trigger_pct=0.03,
        trail_activate_pct=0.04,
        trail_distance_pct=0.015,
    ),
    "C": VariantConfig(
        name="C: ORB Mid + trail 2.0%",
        stop_mode="orb_mid",
        breakeven_trigger_pct=0.03,
        trail_activate_pct=0.04,
        trail_distance_pct=0.020,
    ),
    "D": VariantConfig(
        name="D: ORB Mid + BE@2% + trail 1.5%",
        stop_mode="orb_mid",
        breakeven_trigger_pct=0.02,
        trail_activate_pct=0.04,
        trail_distance_pct=0.015,
    ),
    # ── Parameter exploration vinder (2026-05-13) ──────────────
    # Bevist out-of-sample: train +$97 / OOS +$56 mod 11-ticker univers.
    # Strategien: smal target + meget stramt vol-filter.
    # Forventet i live: få trades pr. uge (5x vol_mult er restriktivt).
    "all_winner": VariantConfig(
        name="All-winner: +1% target, vol 5x, ORB Mid stop",
        stop_mode="orb_mid",
        target_pct=0.01,                 # +1% target (lavt og realistisk)
        vol_mult=5.0,                    # KRAFTIGT breakout-filter
        breakeven_enabled=False,         # ingen BE — målet er hurtig profit
        trail_enabled=False,             # ingen trail — target eller force-close
    ),
}

# Aktiv variant for live trading — ændr her efter sweep har vist en vinder
LIVE_VARIANT_KEY = "all_winner"

# ── Strategi-konstanter (IKKE variant-specifikke) ─────────────
# Disse er rammer for hele strategien — ikke noget vi udforsker.
RETEST_TOLERANCE = 1.001     # pullback rammes hvis bar_low ≤ orb_high × 1.001

# ── Bagudkompatible aliaser (deprecates senere) ───────────────
# Hvis nogen kode importerer disse direkte, fungerer det stadig.
# Nye kald bør bruge config.vol_mult osv. i stedet.
VOL_MULT           = 1.5
RSI_MAX            = 80
RETEST_TIMEOUT_SEC = 300

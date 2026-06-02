"""
strategies/confluence2
──────────────────────
Konfluens 2 — impuls-strategi (long-only, US small-caps, 1-min primær timeframe).

Bygget efter erfaringerne fra Konfluens 1, som ventede på bagudskuende
bekræftelser og derfor handlede for sent. K2 reagerer i stedet på selve
impulsen i realtid.

Entry (2 obligatoriske + mindst N af 4 kontekst):
  Obligatorisk:
    V — volumen-spike: volume ≥ vol_mult × forrige bar OG ≥ vol_ma_mult × snit
    B — range/krop:     krop ≥ body_mult × snit-krop
    G — stærk grøn:     grøn close i øverste del af range
  Kontekst (mindst context_threshold):
    E — ikke overudvidet (close ikke for langt over EMA fast)
    K — break af forrige bars high
    R — RSI-momentum (rsi_min ≤ RSI ≤ rsi_max)
    T — bred trend ikke imod (close > EMA slow)

Exit (valgbar arketype pr. variant):
    A impulse_low · B trail_hl · C momentum · D target_r (+ katastrofe-stop)

ADVARSEL: K2 er IKKE valideret. Ingen variant er live-godkendt før en bred
backtest har vist en edge. Værdierne i config.py er startgæt fra én dags analyse.

Brug:
  from strategies.confluence2 import Confluence2Strategy, VARIANTS, LIVE_VARIANT_KEY
"""

from strategies.confluence2.strategy import Confluence2Strategy
from strategies.confluence2.config import (
    VARIANTS,
    LIVE_VARIANT_KEY,
    Confluence2VariantConfig,
)

__all__ = [
    "Confluence2Strategy",
    "VARIANTS",
    "LIVE_VARIANT_KEY",
    "Confluence2VariantConfig",
]
"""
strategies/confluence/config.py
────────────────────────────────
Variant-konfigurationer for Konfluens-strategien.

DEFAULTS MATCHER PINE SCRIPT 1:1
═══════════════════════════════════
Alle default-værdier herunder er KOPIERET direkte fra
Confluence_Strategy.md (Pine Script v5). Når du ændrer en default, skal
du være klar over at backtesten ikke længere kan sammenlignes direkte med
TradingView's resultat.

ENTRY-PARAMETRE:
  entry_threshold:   Min. antal opfyldte entry-betingelser af 6 (default 4)
  htf_timeframe:     Timeframe for HTF EMA-trendfilter (default "15min")
  htf_ema_len:       EMA-længde på HTF (default 50)
  ema_fast_len:      Kort EMA på primær tf (default 9)
  ema_slow_len:      Lang EMA på primær tf (default 21)
  use_vwap:          Aktivér VWAP-konfluens (default True)
  vwap_band_mult:    Std.dev-multiplikator for VWAP-bands (default 1.5)
  rsi_len:           RSI-periode (default 14)
  rsi_oversold:      RSI-tærskel for oversold (default 35)
  rsi_cross_level:   RSI-niveau der skal krydses op for entry (default 40)
  rsi_lookback:      Antal bars hvor RSI skal have været oversold (default 5)
  vol_ma_len:        Volume MA-længde (default 20)
  vol_mult:          Volume spike-multiplikator (default 1.2)
  pivot_left:        Pivot lookback venstre (default 3)
  pivot_right:       Pivot lookback højre (default 3)

EXIT-PARAMETRE:
  exit_threshold:    Min. antal opfyldte exit-betingelser af 5 (default 3)
  atr_len:           ATR-periode (default 14)
  atr_sl_mult:       Hard SL = entry − atr_sl_mult × ATR (default 1.2)
  trail_activ_r:     Trail aktiveres ved +X R (default 1.0)
  trail_type:        "Swing Low" | "EMA Fast" | "ATR" (default "Swing Low")
  trail_atr_mult:    Multiplum hvis trail_type="ATR" (default 1.5)
  rsi_overbought:    RSI overbought-tærskel for exit (default 65)
  rsi_cross_dn:      RSI cross-down niveau for exit (default 60)

RISK:
  risk_percent:      Risk pr. trade i % af equity (default 1.0)

SESSION:
  Pine scriptet bruger 0930-1600 America/New_York session. Vi gør det samme.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfluenceVariantConfig:
    """Konfigurations-objekt for én variant af Konfluens-strategien."""

    name: str

    # ── Entry-tærskel og konfluens-bricks ──────────────────────
    entry_threshold:   int   = 4         # Min. score af 6

    # ── HTF / Trend / EMA ──────────────────────────────────────
    htf_timeframe:     str   = "15min"   # pandas-frekvens streng
    htf_ema_len:       int   = 50
    ema_fast_len:      int   = 9
    ema_slow_len:      int   = 21

    # ── VWAP ───────────────────────────────────────────────────
    use_vwap:          bool  = True
    vwap_band_mult:    float = 1.5

    # ── RSI (entry) ────────────────────────────────────────────
    rsi_len:           int   = 14
    rsi_oversold:      int   = 35
    rsi_cross_level:   int   = 40
    rsi_lookback:      int   = 5

    # ── Volume ─────────────────────────────────────────────────
    vol_ma_len:        int   = 20
    vol_mult:          float = 1.2

    # ── Struktur (pivots) ──────────────────────────────────────
    pivot_left:        int   = 3
    pivot_right:       int   = 3

    # ── Exit-tærskel og exit-konfluens ─────────────────────────
    exit_threshold:    int   = 3         # Min. score af 5

    # ── ATR / Stop / Trail ─────────────────────────────────────
    atr_len:           int   = 14
    atr_sl_mult:       float = 1.2
    trail_activ_r:     float = 1.0       # Trail aktiveres ved +X R
    trail_type:        str   = "Swing Low"  # "Swing Low" | "EMA Fast" | "ATR" | "Percent"
    trail_atr_mult:    float = 1.5
    trail_percent:     float = 0.12      # Kun brugt naar trail_type="Percent". 0.12 = 12%

    # ── RSI (exit) ─────────────────────────────────────────────
    rsi_overbought:    int   = 65
    rsi_cross_dn:      int   = 60

    # ── Risk sizing ────────────────────────────────────────────
    risk_percent:      float = 1.0       # % af equity pr. trade

    # ── Friktion (kun backtest, ikke i Pine) ───────────────────
    # Pine bruger commission 0.05% + slippage 2 ticks. Vi sætter
    # default til Pine's tal her — kan justeres pr. variant.
    commission_pct:    float = 0.05      # % af notional pr. side
    slippage_ticks:    float = 2.0       # antal ticks (mintick = $0.01 antaget)


# ─────────────────────────────────────────────────────────────────
# Varianter
# ─────────────────────────────────────────────────────────────────
# baseline = Pine scriptets eksakte defaults — det er den vi sammenligner
# med TradingView. ÆNDR IKKE noget her uden at vide hvad du gør.
VARIANTS: dict[str, ConfluenceVariantConfig] = {
    "baseline": ConfluenceVariantConfig(
        name="Baseline (Pine 1:1 defaults)",
        # Alle defaults arves
    ),

    # Eksempel-varianter til parameter exploration senere.
    # Disse er IKKE valideret — tilføj først efter baseline er matchet med TV.
    "tight": ConfluenceVariantConfig(
        name="Tight (entry=5, ATR-SL 1.0, EMA Fast trail)",
        entry_threshold=5,
        atr_sl_mult=1.0,
        trail_type="EMA Fast",
    ),
    "loose": ConfluenceVariantConfig(
        name="Loose (entry=3, ATR-SL 1.5, ATR trail)",
        entry_threshold=3,
        atr_sl_mult=1.5,
        trail_type="ATR",
        trail_atr_mult=2.0,
    ),
    # Rene trail-varianter til sammenligning: identisk med baseline paa alt
    # UNDTAGEN trail_type. Bruges til at maale effekten af trail-typen alene
    # uden stoej fra andre parameter-forskelle.
    "baseline_ema": ConfluenceVariantConfig(
        name="Baseline + EMA Fast trail (kun trail aendret)",
        trail_type="EMA Fast",
    ),
    "baseline_atr": ConfluenceVariantConfig(
        name="Baseline + ATR trail 1.5x (kun trail aendret)",
        trail_type="ATR",
        trail_atr_mult=1.5,
    ),
    "percent": ConfluenceVariantConfig(
        name="Percent trail (12% fra hoejeste close)",
        # Alle andre defaults som baseline. Eneste forskel: trail-typen.
        # Bruges til at sammenligne mod baseline (Swing Low) paa momentum
        # spikes hvor swing-low trail er for konservativ.
        trail_type="Percent",
        trail_percent=0.12,
    ),
}


# Aktiv variant for live trading (matcher Pine 1:1 indtil videre)
LIVE_VARIANT_KEY = "baseline"


# ─────────────────────────────────────────────────────────────────
# Universe constraints (matcher ORB-strategiens mønster)
# ─────────────────────────────────────────────────────────────────
# Pris-filter: aktier skal være i denne range ved markeds-åbning.
# $5 minimum filtrerer mange illikvide penny-stocks fra (PIII-type
# zero-range bars er typisk under $5). $50 maksimum holder os væk
# fra large-caps der ikke har det samme momentum-mønster.
UNIVERSE_PRICE_MIN = 5.00
UNIVERSE_PRICE_MAX = 50.00

# Volumen-filter: aktier skal have mindst dette i daglig volumen.
# Matcher ORB's standard og sikrer at vi kun handler likvide aktier
# (ingen zero-range bars, ingen wide bid/ask spreads).
UNIVERSE_MIN_VOLUME = 500_000

# Antal tickers fra TradingView scanner (samme som ORB).
UNIVERSE_TOP_N = 25

# Markedssession — Pine: "0930-1600", "America/New_York"
SESSION_START_HHMM = (9, 30)
SESSION_END_HHMM   = (16, 0)

# Entry-cutoff: efter dette tidspunkt åbnes INGEN nye positioner, men
# eksisterende positioner får lov at fortsætte indtil deres exit (trail/
# signal/session_close).
#
# Begrundelse: Backtest af 15. maj viste klart mønster — strategien
# topper P&L kl. 14:00 ET (08:00 dansk), derefter konsekvent tab:
#   13:00-13:59 ET:  71% win rate, +$177
#   14:00-14:59 ET:  31% win rate, -$502
#   15:00-15:59 ET:  27% win rate, -$651
#
# Begrundelser: lavere likviditet sent på dagen, session-close pres
# beskærer profit på sene entries, færre ægte momentum-aktier tilbage.
# Hvis vi havde stoppet entries kl. 14:00, ville P&L stige fra
# +$2,963 til +$4,117 (+39%).
ENTRY_CUTOFF_HHMM = (14, 0)

# Mintick til slippage-beregning ($0.01 for US equities > $1)
MINTICK = 0.01

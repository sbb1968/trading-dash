"""
strategies/confluence2/config.py
─────────────────────────────────
Variant-konfigurationer for Konfluens 2 (impuls-strategi).

FORSKEL FRA KONFLUENS 1:
  K1 ventede på 4-af-6 BAGUDSKUENDE bekræftelser (trend, higher-low, VWAP) og
  handlede derfor EFTER en bevægelse var sket. K2 reagerer på selve IMPULSEN
  i realtid: en 1-min candle med volumen-spike + range-ekspansion + stærk
  grøn lukning. To obligatoriske impuls-kriterier + mindst N kontekst-kriterier.

TIMEFRAME: 1-min (bekræftet via analyse — fanger bevægelser 5-min midler væk,
  uden at drukne i støj på de testede data).

EXIT: fire arketyper testet i exit-analysen, alle her som valgbare varianter:
  A) impulse_low   — hold til prisen falder under impuls-candlens low (vidt stop)
  B) trail_hl      — trailing higher-low (viste sig for stram på 1-min — med som ref.)
  C) momentum      — exit på rød volumen-candle eller close < EMA(fast)
  D) target_r      — fast target ved target_r × R, med impuls-low som katastrofe-stop

Target-multiplum (target_r) og stop er JUSTERBARE her, så de kan sweepes i
backtest ligesom K1's varianter. Tallene nedenfor er STARTGÆT fra én dags
analyse — de SKAL kalibreres på en bred backtest før de betyder noget.

INGEN af disse værdier er valideret endnu. Dette er et udgangspunkt for test.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Confluence2VariantConfig:
    name: str

    # ── Entry: impuls-kriterier (obligatoriske) ────────────────
    vol_mult: float = 2.0          # volumen ≥ vol_mult × forrige bars volumen
    vol_ma_mult: float = 1.5       # OG volumen ≥ vol_ma_mult × glidende snit
    vol_ma_len: int = 20           # længde på volumen-snit
    body_mult: float = 1.5         # krop ≥ body_mult × glidende snit-krop
    body_ma_len: int = 10          # længde på krop-snit
    close_pos_min: float = 0.60    # close i øverste (1-close_pos_min) af range

    # ── Entry: kontekst-filtre (mindst context_threshold af dem) ──
    context_threshold: int = 2     # hvor mange af de 4 kontekst-kriterier kræves
    ema_fast_len: int = 9          # EMA til "ikke overudvidet" + momentum-exit
    ema_slow_len: int = 20         # langsommere EMA til trend-kontekst
    rsi_len: int = 14
    rsi_min: float = 50.0          # RSI-momentum-vindue: ikke dødt...
    rsi_max: float = 78.0          # ...men heller ikke udmattet
    overext_atr_mult: float = 2.5  # afvis hvis close > ema_fast + dette × ATR (for sent)
    atr_len: int = 14

    # ── Exit ───────────────────────────────────────────────────
    exit_mode: str = "target_r"    # "impulse_low" | "trail_hl" | "momentum" | "target_r"
    target_r: float = 2.0          # for target_r: target = entry + target_r × R
    catastrophe_stop: bool = True  # impuls-low som hård bund (alle modes undtagen rene impulse_low)
    breakeven_r: float | None = None  # hvis sat: flyt stop til entry når high ≥ entry + breakeven_r × R (kun impulse_low)
    confirm_next_bar: bool = False  # hvis True: fyld kun pending entry hvis næste bars open ≥ impuls-close (følge-igennem)

    # ── Rammer ─────────────────────────────────────────────────
    entry_cutoff_hhmm: tuple[int, int] = (15, 0)   # ingen nye entries efter dette (ET)
    force_close_hhmm: tuple[int, int] = (15, 45)   # luk alt (matcher K1/ORB)
    min_warmup_bars: int = 25      # færre 1-min bars end dette → spring over


# ── Varianter: de fire exit-arketyper fra analysen ───────────────
# Entry-parametrene er ens på tværs; KUN exit varierer, så vi isolerer
# exit-effekten i backtest (samme tankegang som K1's trail-varianter).
VARIANTS: dict[str, Confluence2VariantConfig] = {
    "A_impulse_low": Confluence2VariantConfig(
        name="A: Impuls-low stop (vidt, hold-til-stop)",
        exit_mode="impulse_low",
        catastrophe_stop=False,    # impuls-low ER stoppet her
    ),
    "A_be1r": Confluence2VariantConfig(
        name="A+BE: Impuls-low + breakeven efter +1R",
        exit_mode="impulse_low",
        catastrophe_stop=False,
        breakeven_r=1.0,
    ),
    "A_confirm": Confluence2VariantConfig(
        name="A+CONF: Impuls-low + bekræftelse på næste bar",
        exit_mode="impulse_low",
        catastrophe_stop=False,
        confirm_next_bar=True,
    ),
    "B_trail_hl": Confluence2VariantConfig(
        name="B: Trailing higher-low (1-min)",
        exit_mode="trail_hl",
    ),
    "C_momentum": Confluence2VariantConfig(
        name="C: Momentum-exit (rød+vol / <EMA fast)",
        exit_mode="momentum",
    ),
    "D_target_2r": Confluence2VariantConfig(
        name="D: Fast +2R target + impuls-low stop",
        exit_mode="target_r",
        target_r=2.0,
    ),
    # Ekstra target-multipla til sweep (target er den vigtigste parameter)
    "D_target_1_5r": Confluence2VariantConfig(
        name="D1.5: +1.5R target + impuls-low stop",
        exit_mode="target_r",
        target_r=1.5,
    ),
    "D_target_3r": Confluence2VariantConfig(
        name="D3: +3R target + impuls-low stop",
        exit_mode="target_r",
        target_r=3.0,
    ),
}

# Live-variant: A_impulse_low. Valgt efter validering på maj (in-sample,
# portefølje-PF 2,85/3,01) OG april (out-of-sample, PF 1,83/1,69), begge med
# lav drawdown og robust på tværs af fifo/priority signalvalg. De øvrige
# varianter (B/C/D) overlevede ikke slippage + positions-loft og er kun ref.
LIVE_VARIANT_KEY = "A_impulse_low"

# ── Strategi-konstanter (ikke variant-specifikke) ─────────────
SESSION_START_HHMM = (9, 30)
SESSION_END_HHMM   = (16, 0)
MINTICK            = 0.01

# ── Universe-filtre — "Intraday Volatility"-screener ─────────────
# K2 bruger IKKE længere top-gainers (gav elendige kandidater). I stedet
# replikeres Sørens TradingView "Intraday Volatility"-screener: mellem-/large-
# cap aktier med høj ugentlig ATR — likvide navne der bevæger sig nok intraday
# til at impuls-setuppet giver mening. Se fetch_tv_intraday_volatility i
# strategies/confluence/tv_scanner.py (feltnavne verificeret mod TV's API).
UNIVERSE_PRICE_MIN    = 5.0
UNIVERSE_PRICE_MAX    = 50.0
UNIVERSE_MIN_VOLUME   = 500_000               # 30-dages gennemsnitsvolumen
UNIVERSE_TOP_N        = 25
UNIVERSE_MKT_CAP_MIN  = 5_000_000_000         # 5 B
UNIVERSE_MKT_CAP_MAX  = 1_000_000_000_000     # 1 T
UNIVERSE_ATR_PCT_MIN  = 5.0                    # ATR(14) 1W > 5%
UNIVERSE_EXCHANGES    = ["NASDAQ", "NYSE", "AMEX", "CBOE"]  # AMEX = TV's "NYSE Arca"
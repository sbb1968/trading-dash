"""
trade_forensics.py
──────────────────
Bygger struktureret "verden-på-tidspunktet" snapshot ved hver entry og exit.

Kombinerer:
  - Tekniske indikatorer (RSI, MACD, EMA, Bollinger, VWAP) fra historiske bars
  - Tape-aggregeringer (aggressor-ratio, største trade, last 5) fra TapeBuffer
  - Level 2-snapshot (best bid/ask, spread, imbalance) fra TapeBuffer
  - Strategi-kontekst (ORB-niveauer, bar position, breakouts størrelse)

Snapshot gemmes i journal som event_type='trade_forensics'.

Iteration 1 (denne version):
  - VI LOGGER KUN. Ingen filterbeslutning baseret på data.
  - Efter 50+ handler kan vi analysere korrelationer og evt. bygge filter.

Strategier:
  - ORB:        build_entry_snapshot / build_exit_snapshot
  - Konfluens:  build_confluence_entry_snapshot / build_confluence_exit_snapshot

Placering: C:\\Projects\\trading-dash\\backend\\trade_forensics.py
"""

from __future__ import annotations
import logging
import math
from datetime import datetime
from typing import Optional

from indicators import compute_all
from tape_buffer import TapeBuffer

logger = logging.getLogger(__name__)


def _bars_to_dicts(bars) -> list[dict]:
    """
    Konvertér en liste af Bar-objekter (fra strategies.base) til dicts.

    indicators.compute_all forventer dicts med open/high/low/close/volume.
    """
    out: list[dict] = []
    for b in bars:
        # Bar er en dataclass — tilgå felter direkte
        out.append({
            "open":   float(b.open),
            "high":   float(b.high),
            "low":    float(b.low),
            "close":  float(b.close),
            "volume": float(b.volume),
        })
    return out


def _safe_round(v, digits=4):
    """Returnér None hvis v er None/NaN/Inf, ellers round(v, digits)."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, digits)
    except (ValueError, TypeError):
        return None


def _orb_position(price: float, orb_high: float, orb_low: float) -> Optional[float]:
    """
    Hvor er prisen i forhold til ORB-range?

    Returnerer pct: 0% = ved orb_low, 100% = ved orb_high.
    >100% = brudt over ORB high (det vi vil have ved entry).
    """
    rng = orb_high - orb_low
    if rng <= 0:
        return None
    return round((price - orb_low) / rng * 100.0, 2)


def _breakout_strength_pct(price: float, orb_high: float) -> Optional[float]:
    """
    Hvor langt over ORB high er entry-prisen, i procent?

    Et lille tal (0.1%) = svag breakout, kunne være false break.
    Et stort tal (1.5%) = stærk breakout, men måske allerede for sent.
    """
    if orb_high <= 0:
        return None
    return round((price - orb_high) / orb_high * 100.0, 3)


# ═══════════════════════════════════════════════════════════════════
# ORB (MomentumORB) snapshots
# ═══════════════════════════════════════════════════════════════════

def build_entry_snapshot(
    *,
    ticker: str,
    entry_price: float,
    entry_time: datetime,
    shares: int,
    bars: list,                          # list[Bar] — historiske 5-min bars op til nu
    context: dict,                       # day_context fra strategy.build_day_context
    tape_buffer: Optional[TapeBuffer],   # kan være None hvis ikke initialiseret
    variant_name: str,
) -> dict:
    """
    Saml alt vi ved om markedet i øjeblikket hvor vi åbnede positionen.
    """
    bar_dicts = _bars_to_dicts(bars)
    indicators = compute_all(bar_dicts)

    orb_high = context.get("orb_high")
    orb_low  = context.get("orb_low")
    avg_vol  = context.get("avg_vol")

    # Sidste bar's volumen vs. dagens snit
    last_bar_volume = bar_dicts[-1]["volume"] if bar_dicts else None
    rel_vol_last_bar = None
    if last_bar_volume is not None and avg_vol and avg_vol > 0:
        rel_vol_last_bar = round(last_bar_volume / avg_vol, 2)

    setup = {
        "orb_high":               round(orb_high, 4) if orb_high else None,
        "orb_low":                round(orb_low, 4) if orb_low else None,
        "orb_range_pct":          round((orb_high - orb_low) / orb_low * 100.0, 3)
                                  if (orb_high and orb_low and orb_low > 0) else None,
        "orb_position_pct":       _orb_position(entry_price, orb_high, orb_low)
                                  if (orb_high and orb_low) else None,
        "breakout_strength_pct":  _breakout_strength_pct(entry_price, orb_high)
                                  if orb_high else None,
        "avg_volume":             round(avg_vol, 0) if avg_vol else None,
        "last_bar_volume":        round(last_bar_volume, 0) if last_bar_volume else None,
        "rel_vol_last_bar":       rel_vol_last_bar,
    }

    # Tape + depth snapshot
    if tape_buffer is not None:
        tape_snap = tape_buffer.snapshot(ticker, lookback_sec=60)
    else:
        tape_snap = {
            "tape":  {"reason": "tape_buffer_not_available"},
            "depth": {"available": False, "reason": "tape_buffer_not_available"},
        }

    return {
        "phase":         "entry",
        "ticker":        ticker,
        "price":         round(entry_price, 4),
        "shares":        shares,
        "time_et":       entry_time.strftime("%Y-%m-%d %H:%M:%S"),
        "variant":       variant_name,
        "indicators":    indicators,
        "setup":         setup,
        "tape":          tape_snap["tape"],
        "depth":         tape_snap["depth"],
    }


def build_exit_snapshot(
    *,
    ticker: str,
    entry_price: float,
    exit_price: float,
    entry_time: datetime,
    exit_time: datetime,
    shares: int,
    pnl: float,
    reason: str,
    bars: list,
    context: dict,
    tape_buffer: Optional[TapeBuffer],
    variant_name: str,
) -> dict:
    """
    Saml alt vi ved om markedet i øjeblikket hvor vi lukkede positionen.

    Plus afledte metrics om selve handlen (P&L, hold-tid, return-pct).
    """
    bar_dicts = _bars_to_dicts(bars)
    indicators = compute_all(bar_dicts)

    duration_sec = (exit_time - entry_time).total_seconds() if entry_time and exit_time else None
    pnl_pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price > 0 else None

    orb_high = context.get("orb_high")
    orb_low  = context.get("orb_low")

    setup = {
        "orb_high":         round(orb_high, 4) if orb_high else None,
        "orb_low":          round(orb_low, 4) if orb_low else None,
        "orb_position_pct": _orb_position(exit_price, orb_high, orb_low)
                            if (orb_high and orb_low) else None,
    }

    trade_metrics = {
        "entry_price":  round(entry_price, 4),
        "exit_price":   round(exit_price, 4),
        "shares":       shares,
        "pnl":          round(pnl, 2),
        "pnl_pct":      round(pnl_pct, 3) if pnl_pct is not None else None,
        "duration_sec": int(duration_sec) if duration_sec is not None else None,
        "reason":       reason,
    }

    if tape_buffer is not None:
        tape_snap = tape_buffer.snapshot(ticker, lookback_sec=60)
    else:
        tape_snap = {
            "tape":  {"reason": "tape_buffer_not_available"},
            "depth": {"available": False, "reason": "tape_buffer_not_available"},
        }

    return {
        "phase":          "exit",
        "ticker":         ticker,
        "price":          round(exit_price, 4),
        "shares":         shares,
        "time_et":        exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        "variant":        variant_name,
        "indicators":     indicators,
        "setup":          setup,
        "trade_metrics":  trade_metrics,
        "tape":           tape_snap["tape"],
        "depth":          tape_snap["depth"],
    }


# ═══════════════════════════════════════════════════════════════════
# Konfluens snapshots
# ───────────────────────────────────────────────────────────────────
# Konfluens har et helt andet setup end ORB (bricks/score/swing-state
# i stedet for orb_high/orb_low). Vi laver separate funktioner så
# JSON-strukturen passer til strategien.
# ═══════════════════════════════════════════════════════════════════


def _confluence_setup_at_bar(context: dict, timestamp, current_price: float) -> dict:
    """
    Saml Konfluens-specifikt setup ved en given bar.

    context: session_context fra ConfluenceStrategy.build_session_context()
             — indeholder 'ind_df' (DataFrame med indikatorer pr. bar).
    timestamp: bar-timestamp som vi vil slå indikatorer op for.
    current_price: prisen vi handlede på (entry/exit).
    """
    ind_df = context.get("ind_df")
    setup = {
        "ema_fast":          None,
        "ema_slow":          None,
        "rsi":               None,
        "vwap":              None,
        "vwap_distance_pct": None,
        "atr":               None,
        "last_swing_high":   None,
        "last_swing_low":    None,
        "htf_ema":           None,
        "htf_bias":          None,   # "bull" hvis price > htf_ema, ellers "bear"
    }

    if ind_df is None or len(ind_df) == 0:
        return setup

    try:
        row = ind_df.loc[timestamp]
        # Hvis duplikater, tag første
        import pandas as pd
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
    except (KeyError, Exception):
        return setup

    # Slå individuelle felter op — hver kan være NaN
    setup["ema_fast"]        = _safe_round(row.get("ema_fast"))
    setup["ema_slow"]        = _safe_round(row.get("ema_slow"))
    setup["rsi"]             = _safe_round(row.get("rsi"), 2)
    setup["vwap"]            = _safe_round(row.get("vwap"))
    setup["atr"]             = _safe_round(row.get("atr"))
    setup["last_swing_high"] = _safe_round(row.get("last_swing_high"))
    setup["last_swing_low"]  = _safe_round(row.get("last_swing_low"))
    setup["htf_ema"]         = _safe_round(row.get("htf_ema"))

    # VWAP-distance i procent
    vwap_val = setup["vwap"]
    if vwap_val and vwap_val > 0:
        setup["vwap_distance_pct"] = round(
            (current_price - vwap_val) / vwap_val * 100.0, 3
        )

    # HTF-bias
    htf = setup["htf_ema"]
    if htf is not None and htf > 0:
        setup["htf_bias"] = "bull" if current_price > htf else "bear"

    return setup


def build_confluence_entry_snapshot(
    *,
    ticker: str,
    entry_price: float,
    entry_time: datetime,
    shares: int,
    bars: list,
    context: dict,
    tape_buffer: Optional[TapeBuffer],
    variant_name: str,
    entry_score: int,
    entry_bricks: str,
    initial_stop: float,
) -> dict:
    """
    Konfluens entry-snapshot. Adskiller sig fra ORB's `build_entry_snapshot`:
      - Setup indeholder bricks/score/swing-state i stedet for orb_high/low
      - Indikator-værdier slås op fra context['ind_df'] for entry-baren
      - Tape og depth snapshots inkluderes på samme måde
    """
    bar_dicts = _bars_to_dicts(bars)
    indicators = compute_all(bar_dicts)

    # Slå indikator-værdier op for entry-baren specifikt
    setup = _confluence_setup_at_bar(context, entry_time, entry_price)

    # Tilføj entry-specifikke felter
    setup["entry_score"]   = entry_score
    setup["entry_bricks"]  = entry_bricks
    setup["initial_stop"]  = _safe_round(initial_stop)

    # Risk i USD pr. share
    if initial_stop and entry_price > initial_stop > 0:
        setup["risk_per_share"] = round(entry_price - initial_stop, 4)
        setup["risk_pct"]       = round((entry_price - initial_stop) / entry_price * 100.0, 3)
    else:
        setup["risk_per_share"] = None
        setup["risk_pct"]       = None

    # Sidste bar's volumen vs 20-bar gennemsnit
    if bar_dicts:
        last_bar_volume = bar_dicts[-1]["volume"]
        if len(bar_dicts) >= 20:
            avg_vol_20 = sum(b["volume"] for b in bar_dicts[-20:]) / 20.0
            setup["last_bar_volume"]  = round(last_bar_volume, 0)
            setup["avg_vol_20bars"]   = round(avg_vol_20, 0)
            setup["rel_vol_last_bar"] = round(last_bar_volume / avg_vol_20, 2) if avg_vol_20 > 0 else None
        else:
            setup["last_bar_volume"]  = round(last_bar_volume, 0)
            setup["avg_vol_20bars"]   = None
            setup["rel_vol_last_bar"] = None

    # Tape + depth snapshot
    if tape_buffer is not None:
        tape_snap = tape_buffer.snapshot(ticker, lookback_sec=60)
    else:
        tape_snap = {
            "tape":  {"reason": "tape_buffer_not_available"},
            "depth": {"available": False, "reason": "tape_buffer_not_available"},
        }

    return {
        "phase":         "entry",
        "strategy":      "Konfluens",
        "ticker":        ticker,
        "price":         round(entry_price, 4),
        "shares":        shares,
        "time_et":       entry_time.strftime("%Y-%m-%d %H:%M:%S"),
        "variant":       variant_name,
        "indicators":    indicators,
        "setup":         setup,
        "tape":          tape_snap["tape"],
        "depth":         tape_snap["depth"],
    }


def build_confluence_exit_snapshot(
    *,
    ticker: str,
    entry_price: float,
    exit_price: float,
    entry_time: datetime,
    exit_time: datetime,
    shares: int,
    pnl: float,
    reason: str,
    bars: list,
    context: dict,
    tape_buffer: Optional[TapeBuffer],
    variant_name: str,
    entry_score: int,
    entry_bricks: str,
    max_favorable_excursion: Optional[float] = None,
    max_adverse_excursion: Optional[float] = None,
) -> dict:
    """
    Konfluens exit-snapshot. Inkluderer trade-metrics og indikator-status
    ved exit-baren.

    MFE/MAE er optional — hvis Konfluens-algoen tracker dem, kan vi inkludere
    dem her. Ellers None.
    """
    bar_dicts = _bars_to_dicts(bars)
    indicators = compute_all(bar_dicts)

    duration_sec = (exit_time - entry_time).total_seconds() if entry_time and exit_time else None
    pnl_pct = ((exit_price - entry_price) / entry_price * 100.0) if entry_price > 0 else None

    # Setup ved exit-bar
    setup = _confluence_setup_at_bar(context, exit_time, exit_price)
    setup["entry_score_at_open"]  = entry_score
    setup["entry_bricks_at_open"] = entry_bricks

    trade_metrics = {
        "entry_price":             round(entry_price, 4),
        "exit_price":              round(exit_price, 4),
        "shares":                  shares,
        "pnl":                     round(pnl, 2),
        "pnl_pct":                 round(pnl_pct, 3) if pnl_pct is not None else None,
        "duration_sec":            int(duration_sec) if duration_sec is not None else None,
        # duration i 5-min bars (handy for analyse)
        "duration_bars":           int(duration_sec / 300) if duration_sec is not None else None,
        "reason":                  reason,
        "max_favorable_excursion": _safe_round(max_favorable_excursion),
        "max_adverse_excursion":   _safe_round(max_adverse_excursion),
    }

    if tape_buffer is not None:
        tape_snap = tape_buffer.snapshot(ticker, lookback_sec=60)
    else:
        tape_snap = {
            "tape":  {"reason": "tape_buffer_not_available"},
            "depth": {"available": False, "reason": "tape_buffer_not_available"},
        }

    return {
        "phase":          "exit",
        "strategy":       "Konfluens",
        "ticker":         ticker,
        "price":          round(exit_price, 4),
        "shares":         shares,
        "time_et":        exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        "variant":        variant_name,
        "indicators":     indicators,
        "setup":          setup,
        "trade_metrics":  trade_metrics,
        "tape":           tape_snap["tape"],
        "depth":          tape_snap["depth"],
    }

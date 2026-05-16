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

Placering: C:\\Projects\\trading-dash\\backend\\trade_forensics.py
"""

from __future__ import annotations
import logging
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

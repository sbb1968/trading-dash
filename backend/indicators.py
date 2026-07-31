"""
indicators.py
─────────────
Tekniske indikatorer til trade forensics.

Alle funktioner er stateless og tager en liste af bars eller closes som input.
Returnerer None hvis der er for få data — kalderen skal håndtere det.

Indikatorer:
  - RSI(14)
  - MACD(12,26,9)
  - EMA(N) — generisk
  - Bollinger Bands(20, 2σ)
  - VWAP (intraday, fra session-start)

Placering: C:\\Projects\\trading-dash\\backend\\indicators.py
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


# ─────────────────────────────────────────────────────────────────
# EMA — fundament for MACD og andre afledte indikatorer
# ─────────────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> Optional[float]:
    """
    Eksponentielt glidende gennemsnit.

    Beregner EMA på hele serien og returnerer KUN den seneste værdi.
    Returnerer None hvis der er færre end `period` værdier.
    """
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1)
    # Seed med SMA over første `period` værdier
    seed = sum(values[:period]) / period
    ema_val = seed

    for v in values[period:]:
        ema_val = (v - ema_val) * multiplier + ema_val

    return ema_val


def ema_series(values: list[float], period: int) -> list[float]:
    """
    Beregn EMA for hele serien (returnerer en liste af samme længde
    som input, med None-værdier indtil seed er fyldt).

    Bruges af MACD der har brug for hele EMA-serien.
    """
    if len(values) < period:
        return [None] * len(values)  # type: ignore

    out: list[float] = [None] * (period - 1)  # type: ignore
    multiplier = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out.append(seed)

    ema_val = seed
    for v in values[period:]:
        ema_val = (v - ema_val) * multiplier + ema_val
        out.append(ema_val)

    return out


# ─────────────────────────────────────────────────────────────────
# RSI(14) — bevaret kompatibel med strategiens eksisterende RSI
# ─────────────────────────────────────────────────────────────────

def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    Relative Strength Index på `period` bars.

    Bemærk: bruger samme simple-average formel som strategien selv
    (calc_rsi_from_closes, historisk reference) — så vi
    får IDENTISKE tal som strategiens entry-check.

    Returnerer None hvis < period+1 closes.
    """
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = sum(d for d in deltas[-period:] if d > 0) / period
    losses = sum(-d for d in deltas[-period:] if d < 0) / period

    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))


# ─────────────────────────────────────────────────────────────────
# MACD(12, 26, 9)
# ─────────────────────────────────────────────────────────────────

@dataclass
class MACDValue:
    macd: float        # EMA12 - EMA26
    signal: float      # EMA9 af macd-linien
    histogram: float   # macd - signal


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Optional[MACDValue]:
    """
    MACD med klassiske parametre.

    Kræver mindst slow + signal_period bars (35 for default 12/26/9) for
    at have en gyldig signal-linje. Returnerer None ellers.
    """
    if len(closes) < slow + signal_period:
        return None

    ema_fast_series = ema_series(closes, fast)
    ema_slow_series = ema_series(closes, slow)

    # Byg macd-linje (kun hvor begge EMAs er defineret)
    macd_line: list[float] = []
    for f, s in zip(ema_fast_series, ema_slow_series):
        if f is None or s is None:
            continue
        macd_line.append(f - s)

    if len(macd_line) < signal_period:
        return None

    signal_val = ema(macd_line, signal_period)
    if signal_val is None:
        return None

    macd_val = macd_line[-1]
    return MACDValue(
        macd=macd_val,
        signal=signal_val,
        histogram=macd_val - signal_val,
    )


# ─────────────────────────────────────────────────────────────────
# Bollinger Bands (20, 2σ)
# ─────────────────────────────────────────────────────────────────

@dataclass
class BollingerValue:
    upper: float
    middle: float       # SMA20
    lower: float
    width_pct: float    # (upper - lower) / middle * 100 — bånd-bredde i %
    position_pct: float # hvor er prisen i båndet? 0% = lower, 100% = upper


def bollinger(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> Optional[BollingerValue]:
    """
    Bollinger Bands med SMA-middle og ±num_std standardafvigelser.

    position_pct er især interessant for forensics — den fortæller om
    prisen er presset mod toppen (>80%, ofte overkøbt) eller bunden
    (<20%, ofte oversolgt) af båndet.
    """
    if len(closes) < period:
        return None

    window = closes[-period:]
    middle = sum(window) / period

    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5

    upper = middle + num_std * std
    lower = middle - num_std * std

    if middle == 0 or upper == lower:
        return None

    width_pct = (upper - lower) / middle * 100.0
    position_pct = (closes[-1] - lower) / (upper - lower) * 100.0

    return BollingerValue(
        upper=upper,
        middle=middle,
        lower=lower,
        width_pct=width_pct,
        position_pct=position_pct,
    )


# ─────────────────────────────────────────────────────────────────
# VWAP (intraday)
# ─────────────────────────────────────────────────────────────────

def vwap(
    bars: list[dict],
) -> Optional[float]:
    """
    Volume-Weighted Average Price beregnet over alle givne bars.

    bars: liste af dicts med 'high', 'low', 'close', 'volume'.
          Typisk: alle dagens 5-min bars fra session-start til nu.

    Bruger typisk pris = (H + L + C) / 3 (HLC3) som standard for VWAP.

    Returnerer None hvis ingen volumen overhovedet (uden for handelstid).
    """
    if not bars:
        return None

    total_pv = 0.0
    total_v = 0.0
    for b in bars:
        typical = (b["high"] + b["low"] + b["close"]) / 3.0
        v = b["volume"]
        total_pv += typical * v
        total_v += v

    if total_v <= 0:
        return None

    return total_pv / total_v


def vwap_distance_pct(current_price: float, vwap_value: float) -> Optional[float]:
    """
    Prisens afstand fra VWAP i procent.

    Positiv = over VWAP (bullish bias)
    Negativ = under VWAP (bearish bias)
    """
    if vwap_value is None or vwap_value <= 0:
        return None
    return (current_price - vwap_value) / vwap_value * 100.0


# ─────────────────────────────────────────────────────────────────
# Chaikin Money Flow (CMF)
# ─────────────────────────────────────────────────────────────────

def cmf(bars: list[dict], length: int = 20) -> Optional[float]:
    """
    Chaikin Money Flow over de seneste `length` bars.

        mult = ((close − low) − (high − close)) / (high − low)
        mfv  = mult × volume
        cmf  = Σ(mfv, n) / Σ(volume, n)

    Måler om volumen samler sig i toppen eller bunden af hver bars range:
    positiv = akkumulation (køberne lukker barerne højt), negativ = distribution.

    Nul-range bars (high == low) giver en udefineret mult og sættes til 0 —
    samme konvention som Pine (`high - low == 0 ? 0 : ...`) og som pandas-
    udgaven i store_bevaegelser_lib.cmf(), så live, backtest og TradingView
    beregner det ens. Verificeret bit-identisk mod pandas-udgaven i
    test_indicators_cmf.py.

    None når der er for få bars, eller når volumen-summen er nul (ingen
    handel i vinduet → forholdet er udefineret, ikke 0).
    """
    if len(bars) < length:
        return None

    window = bars[-length:]
    mfv_sum = 0.0
    vol_sum = 0.0

    for b in window:
        high  = b["high"]
        low   = b["low"]
        close = b["close"]
        vol   = b["volume"] or 0.0

        rng = high - low
        mult = 0.0 if rng == 0 else ((close - low) - (high - close)) / rng

        mfv_sum += mult * vol
        vol_sum += vol

    if vol_sum == 0:
        return None
    return mfv_sum / vol_sum


# ─────────────────────────────────────────────────────────────────
# Aggregator — kører alle indikatorer på én gang for forensics
# ─────────────────────────────────────────────────────────────────

def compute_all(bars: list[dict]) -> dict:
    """
    Beregn alle indikatorer på én gang fra en liste af bars.

    bars: liste af dicts med keys: open, high, low, close, volume
          (typisk: dagens 5-min bars op til nu, eller backtest-bars).

    Returnerer dict med flade keys klar til JSON-serialisering.
    Manglende/utilstrækkelige data → None for det pågældende felt.
    """
    if not bars:
        return {
            "bars_used": 0,
            "rsi_14": None,
            "macd": None, "macd_signal": None, "macd_hist": None,
            "ema_9": None, "ema_20": None,
            "bb_upper": None, "bb_middle": None, "bb_lower": None,
            "bb_width_pct": None, "bb_position_pct": None,
            "vwap": None, "vwap_distance_pct": None,
            "cmf_20": None,
        }

    closes = [b["close"] for b in bars]
    last_close = closes[-1]

    rsi_val = rsi(closes, 14)
    macd_val = macd(closes)
    ema_9 = ema(closes, 9)
    ema_20 = ema(closes, 20)
    bb = bollinger(closes, 20, 2.0)
    vwap_val = vwap(bars)
    vwap_dist = vwap_distance_pct(last_close, vwap_val) if vwap_val else None
    cmf_val = cmf(bars, 20)

    return {
        "bars_used":         len(bars),
        "rsi_14":            round(rsi_val, 2) if rsi_val is not None else None,
        "macd":              round(macd_val.macd, 4) if macd_val else None,
        "macd_signal":       round(macd_val.signal, 4) if macd_val else None,
        "macd_hist":         round(macd_val.histogram, 4) if macd_val else None,
        "ema_9":             round(ema_9, 4) if ema_9 is not None else None,
        "ema_20":            round(ema_20, 4) if ema_20 is not None else None,
        "bb_upper":          round(bb.upper, 4) if bb else None,
        "bb_middle":         round(bb.middle, 4) if bb else None,
        "bb_lower":          round(bb.lower, 4) if bb else None,
        "bb_width_pct":      round(bb.width_pct, 2) if bb else None,
        "bb_position_pct":   round(bb.position_pct, 2) if bb else None,
        "vwap":              round(vwap_val, 4) if vwap_val is not None else None,
        "vwap_distance_pct": round(vwap_dist, 3) if vwap_dist is not None else None,
        "cmf_20":            round(cmf_val, 4) if cmf_val is not None else None,
    }


# ─────────────────────────────────────────────────────────────────
# Selvtest
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Syntetiske bars — trending op
    test_bars = []
    for i in range(50):
        p = 10.0 + i * 0.05
        test_bars.append({
            "open":   p - 0.02,
            "high":   p + 0.05,
            "low":    p - 0.05,
            "close":  p,
            "volume": 100_000 + i * 1000,
        })

    print("Test af indicators.py — syntetisk trending-op data")
    print("=" * 60)
    result = compute_all(test_bars)
    for k, v in result.items():
        print(f"  {k:>20s}: {v}")

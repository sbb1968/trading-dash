#!/usr/bin/env python3
"""
chart_helper.py — Swing trading chart-helper.

Henter OHLCV for en ticker og udskriver et PASTE-BART tekstblok med:
  - Stoette/modstand-niveauer med beroeringstal (lille/stor)
  - Trend-struktur (HH/HL)
  - Candlestick-moenstre paa de seneste staenger
  - En markering af at chart pattern kraever menneskelig vurdering

Outputtet kopieres direkte ind i swing-rapporten eller gives til Claude.

Brug:
    python chart_helper.py AAPL
    python chart_helper.py AAPL --period 1y --interval 1d

Kraever lokalt netvaerk til Yahoo Finance (yfinance).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:  # gør logikken testbar uden yfinance installeret
    yf = None


# --- Konfiguration (samme knapper vi kan justere i kataloget) ----------------
PIVOT_LOOKBACK = 3        # antal staenger paa hver side for at vaere et svingpunkt
CLUSTER_TOL_PCT = 0.015   # to niveauer regnes som samme zone hvis < 1,5 % fra hinanden
MIN_TOUCHES = 2           # et niveau skal roeres mindst 2 gange for at taelle
STRONG_TOUCHES = 3        # >= 3 beroeringer => "STOR"
RECENCY_BARS = 130        # kun svingpunkter inden for de seneste ~6 mdr taelles med
BAND_PCT = 0.18           # vis kun niveauer inden for +/-18 % af aktuel pris
MAX_PER_SIDE = 3          # hoejst 3 stoette- og 3 modstandsniveauer i rapporten


@dataclass
class Level:
    price: float
    touches: int
    kind: str  # 'support' eller 'resistance'

    @property
    def size(self) -> str:
        return "STOR" if self.touches >= STRONG_TOUCHES else "lille"


# --- Dataindsamling ----------------------------------------------------------
def fetch_data(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    # Daglige bars hentes fra IBKR via data_source.load_bars — SAMME kilde + disk-cache som
    # resten af appen (TRADES-stænger med korrekte candlestick-kroppe; undgår yfinance'
    # split/justerings-forskelle). yfinance bruges kun til intraday-intervaller (som
    # load_bars ikke dækker) og som fallback hvis IBKR ikke svarer.
    if interval in ("1d", "1day", "1 day"):
        try:
            import data_source
            ib_df = data_source.load_bars(ticker)
            if ib_df is not None and not ib_df.empty:
                return ib_df.dropna()
        except Exception:
            pass
    if yf is None:
        raise RuntimeError("yfinance er ikke installeret. Koer: pip install yfinance")
    df = yf.download(ticker, period=period, interval=interval,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    if df.empty:
        raise ValueError(f"Ingen data for {ticker}.")
    return df


# --- Svingpunkter og stoette/modstand ----------------------------------------
def find_pivots(df: pd.DataFrame, lookback: int = PIVOT_LOOKBACK):
    """Find lokale toppe og bunde (svingpunkter)."""
    highs, lows = [], []
    h = df["High"].values
    l = df["Low"].values
    n = len(df)
    for i in range(lookback, n - lookback):
        wh = h[i - lookback:i + lookback + 1]
        wl = l[i - lookback:i + lookback + 1]
        if h[i] == wh.max():
            highs.append((df.index[i], float(h[i])))
        if l[i] == wl.min():
            lows.append((df.index[i], float(l[i])))
    return highs, lows


def cluster_levels(pivots, current_price: float,
                   tol_pct: float = CLUSTER_TOL_PCT):
    """Saml naerliggende svingpunkter til zoner og tael beroeringer."""
    prices = sorted(p for _, p in pivots)
    clusters = []
    for price in prices:
        placed = False
        for c in clusters:
            if abs(price - c["mean"]) / c["mean"] <= tol_pct:
                c["prices"].append(price)
                c["mean"] = sum(c["prices"]) / len(c["prices"])
                placed = True
                break
        if not placed:
            clusters.append({"prices": [price], "mean": price})

    levels = []
    for c in clusters:
        touches = len(c["prices"])
        if touches < MIN_TOUCHES:
            continue
        kind = "resistance" if c["mean"] >= current_price else "support"
        levels.append(Level(price=round(c["mean"], 2), touches=touches, kind=kind))
    return levels


def nearest_levels(levels, current_price: float):
    supports = sorted([lv for lv in levels if lv.kind == "support"],
                      key=lambda x: x.price, reverse=True)
    resistances = sorted([lv for lv in levels if lv.kind == "resistance"],
                         key=lambda x: x.price)
    ns = supports[0] if supports else None
    nr = resistances[0] if resistances else None
    return ns, nr


# --- Trend-struktur ----------------------------------------------------------
def detect_structure(highs, lows, n: int = 3) -> str:
    recent_highs = [p for _, p in highs[-n:]]
    recent_lows = [p for _, p in lows[-n:]]

    def direction(seq):
        if len(seq) < 2:
            return 0
        ups = sum(1 for a, b in zip(seq, seq[1:]) if b > a)
        downs = sum(1 for a, b in zip(seq, seq[1:]) if b < a)
        return ups - downs

    th, tl = direction(recent_highs), direction(recent_lows)
    if th > 0 and tl > 0:
        return "HH/HL (optrend)"
    if th < 0 and tl < 0:
        return "LH/LL (nedtrend)"
    return "sidelaens / blandet"


# --- Candlestick-moenstre (seneste 1-2 staenger) -----------------------------
def detect_candles(df: pd.DataFrame):
    patterns = []
    if len(df) < 2:
        return patterns
    o, h, l, c = (df["Open"].values, df["High"].values,
                  df["Low"].values, df["Close"].values)

    def body(i): return abs(c[i] - o[i])
    def rng(i): return max(h[i] - l[i], 1e-9)
    def upper(i): return h[i] - max(c[i], o[i])
    def lower(i): return min(c[i], o[i]) - l[i]

    r, b = rng(-1), body(-1)
    if b <= 0.1 * r:
        patterns.append(("Doji (ubeslutsomhed)", "seneste"))
    if b > 0 and lower(-1) >= 2 * b and upper(-1) <= 0.3 * r:
        patterns.append(("Hammer / hanging man (lang nedre skygge)", "seneste"))
    if b > 0 and upper(-1) >= 2 * b and lower(-1) <= 0.3 * r:
        patterns.append(("Shooting star / inverted hammer (lang oevre skygge)", "seneste"))

    prev_bull, prev_bear = c[-2] > o[-2], c[-2] < o[-2]
    cur_bull, cur_bear = c[-1] > o[-1], c[-1] < o[-1]
    if cur_bull and prev_bear and c[-1] >= o[-2] and o[-1] <= c[-2]:
        patterns.append(("Bullish engulfing", "seneste 2"))
    if cur_bear and prev_bull and o[-1] >= c[-2] and c[-1] <= o[-2]:
        patterns.append(("Bearish engulfing", "seneste 2"))
    return patterns


# --- Struktureret output (til swing-rapportens JSON) -------------------------
def chart_data_from_df(df: pd.DataFrame, window: int = 120) -> dict:
    """Som report_from_df, men returnerer struktureret data (til UI) i stedet for
    tekst: S/R-niveauer, trend-struktur, candlestick-moenstre, de seneste OHLC-
    staenger, og HH/HL-ankre saa frontend kan tegne en annoteret chart. Chart-
    MOENSTER (flag/H&S/...) er IKKE med - det kraever menneskelig vurdering."""
    current = float(df["Close"].iloc[-1])
    highs, lows = find_pivots(df)
    recency = min(RECENCY_BARS, len(df) - 1)
    cutoff = df.index[-recency]
    rec_highs = [(d, p) for d, p in highs if d >= cutoff]
    rec_lows = [(d, p) for d, p in lows if d >= cutoff]
    all_levels = cluster_levels(rec_highs + rec_lows, current)
    in_band = [lv for lv in all_levels
               if abs(lv.price - current) / current <= BAND_PCT]
    supports = sorted([lv for lv in in_band if lv.kind == "support"],
                      key=lambda x: x.price, reverse=True)[:MAX_PER_SIDE]
    resistances = sorted([lv for lv in in_band if lv.kind == "resistance"],
                         key=lambda x: x.price)[:MAX_PER_SIDE]
    ns = supports[0] if supports else None
    nr = resistances[0] if resistances else None

    structure = detect_structure(highs, lows)
    uptrend = structure.startswith("HH/HL")
    downtrend = structure.startswith("LH/LL")

    # Candle-navn oploeses ud fra trend: shooting star / inverted hammer er samme
    # form (navnet afhaenger af retning); det samme for hammer / hanging man.
    def _resolve(name: str) -> str:
        if "Shooting star / inverted hammer" in name:
            if uptrend:
                return "Shooting star"
            if downtrend:
                return "Inverted hammer"
        if "Hammer / hanging man" in name:
            if uptrend:
                return "Hanging man"
            if downtrend:
                return "Hammer"
        return name
    candles = [{"name": _resolve(name), "when": when}
               for name, when in detect_candles(df)]

    def _t(idx):
        try:
            return pd.Timestamp(idx).strftime("%Y-%m-%d")
        except Exception:
            return str(idx)

    # HH/HL-ankre: seneste svingtop hoejere end den forrige (Higher High) og
    # seneste svingbund hoejere end den forrige (Higher Low). Til at pege paa.
    def _last_higher(points):
        if len(points) < 2:
            return points[-1] if points else None
        for i in range(len(points) - 1, 0, -1):
            if points[i][1] > points[i - 1][1]:
                return points[i]
        return points[-1]

    def _anchor(pt):
        return None if pt is None else {"t": _t(pt[0]), "price": round(float(pt[1]), 2)}

    hh = _anchor(_last_higher(rec_highs)) if rec_highs else None
    hl = _anchor(_last_higher(rec_lows)) if rec_lows else None

    # Seneste ~window staenger til candlesticks.
    win = df.tail(window)
    bars = [{"t": _t(idx),
             "o": round(float(r["Open"]), 2), "h": round(float(r["High"]), 2),
             "l": round(float(r["Low"]), 2), "c": round(float(r["Close"]), 2)}
            for idx, r in win.iterrows()]

    def _lv(lv):
        return {"price": lv.price, "kind": lv.kind, "touches": lv.touches, "size": lv.size}

    levels = sorted(resistances + supports, key=lambda x: x.price, reverse=True)
    return {
        "current_price": round(current, 2),
        "structure": structure,
        "nearest_support": _lv(ns) if ns else None,
        "nearest_resistance": _lv(nr) if nr else None,
        "levels": [_lv(lv) for lv in levels],
        "candles": candles,
        "swing_high": hh,
        "swing_low": hl,
        "bars": bars,
        "pattern_needs_human": True,
    }


# --- Rapport-formatering -----------------------------------------------------
def report_from_df(ticker: str, df: pd.DataFrame) -> str:
    current = float(df["Close"].iloc[-1])
    highs, lows = find_pivots(df)

    # Kun nylige svingpunkter taeller (swing-relevans frem for gammel historik)
    recency = min(RECENCY_BARS, len(df) - 1)
    cutoff = df.index[-recency]
    rec_highs = [(d, p) for d, p in highs if d >= cutoff]
    rec_lows = [(d, p) for d, p in lows if d >= cutoff]

    all_levels = cluster_levels(rec_highs + rec_lows, current)
    # Kun niveauer taet paa prisen, og hoejst MAX_PER_SIDE paa hver side
    in_band = [lv for lv in all_levels
               if abs(lv.price - current) / current <= BAND_PCT]
    supports = sorted([lv for lv in in_band if lv.kind == "support"],
                      key=lambda x: x.price, reverse=True)[:MAX_PER_SIDE]
    resistances = sorted([lv for lv in in_band if lv.kind == "resistance"],
                         key=lambda x: x.price)[:MAX_PER_SIDE]
    levels = resistances + supports
    ns = supports[0] if supports else None
    nr = resistances[0] if resistances else None

    structure = detect_structure(highs, lows)
    candles = detect_candles(df)
    date = pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")

    L = []
    L.append("=" * 60)
    L.append(f" SWING CHART-HELPER — {ticker.upper()} — {date}")
    L.append("=" * 60)
    L.append(f"Aktuel pris: {current:.2f}")
    L.append("")
    L.append("STOETTE / MODSTAND (efter beroeringer, seneste ~6 mdr)")
    if nr:
        L.append(f"  Naermeste modstand: {nr.price:>9.2f}   beroeringer: {nr.touches}   -> {nr.size}")
    else:
        L.append("  Naermeste modstand: (ingen i baandet — pris naer periodens top)")
    if ns:
        L.append(f"  Naermeste stoette:  {ns.price:>9.2f}   beroeringer: {ns.touches}   -> {ns.size}")
    else:
        L.append("  Naermeste stoette:  (ingen i baandet — pris naer periodens bund)")
    L.append("  Relevante niveauer:")
    if levels:
        for lv in sorted(levels, key=lambda x: x.price, reverse=True):
            tag = "R" if lv.kind == "resistance" else "S"
            L.append(f"    {tag}  {lv.price:>9.2f}  ({lv.touches})  {lv.size}")
    else:
        L.append("    (ingen niveauer med nok beroeringer i baandet)")
    L.append("")
    L.append("TREND-STRUKTUR")
    L.append(f"  {structure}")
    L.append("")
    L.append("CANDLESTICK (seneste staenger)")
    if candles:
        for name, when in candles:
            L.append(f"  - {name} ({when})")
    else:
        L.append("  - ingen tydelige moenstre")
    L.append("")
    L.append("CHART PATTERN")
    L.append("  -> KRAEVER MENNESKELIG VURDERING (kig paa TradingView-charten)")
    L.append("     flag / trekant / hoved-skulder / cup&handle / range / ingen")
    L.append("")
    L.append("-" * 60)
    L.append("PASTE TIL CLAUDE: kopier alt ovenstaaende + udfyld chart pattern.")
    L.append("=" * 60)
    return "\n".join(L)


def analyze(ticker: str, period: str = "1y", interval: str = "1d") -> str:
    df = fetch_data(ticker, period, interval)
    return report_from_df(ticker, df)


def main():
    ap = argparse.ArgumentParser(description="Swing trading chart-helper")
    ap.add_argument("ticker", help="fx AAPL")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--interval", default="1d")
    args = ap.parse_args()
    print(analyze(args.ticker, args.period, args.interval))


if __name__ == "__main__":
    main()
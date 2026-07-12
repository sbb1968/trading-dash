#!/usr/bin/env python3
"""
buyhold_trend.py — Buy-and-Hold Lag 4: langsigtet trend & timing (15 % af helheden).

Alt [BEREGNET] fra IBKR-bars (ingen FMP). Tegner det fler-aars billede paa UGE-bars
(+ dag-bars til 200-dag MA): sekulaer trend (pris vs 40-uge/200-dag MA, MA-alignment),
relativ styrke (vs SPY + sektor-ETF), og stabilitet (max drawdown, afkast-vol, afstand
fra ATH). Det er det lag Pine-tvillingen (Fase 4) spejler.

Spejler de oevrige lag (PARAMS/ParamResult/compute_layer/_band_score — GENBRUGT fra
buyhold_fundamental). Bar-hentning GENBRUGER data_source_intraday's generiske
fetch_intraday_bars/fetch_benchmark_bars (live ib, vilkaarlig timeframe/duration).

CLI kraever IBKR forbundet (modsat Fase 1's rene FMP-CLI):
    python buyhold_trend.py KO
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from buyhold_fundamental import ParamResult, compute_layer  # GENBRUG (samme motor)

WEEKLY_DURATION = "5 Y"
DAILY_DURATION = "1 Y"
RS_LOOKBACK_W = 156   # ~3 aar uge-bars til relativ styrke


# ── Rene trend-helpers (testbare uden IBKR) ─────────────────────────────────
def _sma_last(series: pd.Series, n: int):
    s = series.dropna()
    if len(s) < n:
        return None
    return float(s.rolling(n).mean().iloc[-1])


def _max_drawdown(close: pd.Series):
    """Vaerste peak-to-trough % over serien (negativ)."""
    s = close.dropna()
    if len(s) < 2:
        return None
    dd = s / s.cummax() - 1.0
    return float(dd.min() * 100.0)


def _ann_vol(close: pd.Series):
    """Annualiseret afkast-vol fra uge-afkast (%) — std × sqrt(52)."""
    s = close.dropna()
    if len(s) < 8:
        return None
    rets = s.pct_change().dropna()
    if rets.empty:
        return None
    return float(rets.std() * (52 ** 0.5) * 100.0)


def _dist_from_ath(close: pd.Series):
    """Afstand fra all-time-high i serien (%, <= 0)."""
    s = close.dropna()
    if s.empty:
        return None
    return float((s.iloc[-1] / s.max() - 1.0) * 100.0)


def _rs(sym_close: pd.Series, bench_close: pd.Series, lookback: int):
    """Relativ styrke = symbolets afkast − benchmarkets afkast over lookback (%)."""
    a, b = sym_close.dropna(), bench_close.dropna()
    lb = min(lookback, len(a) - 1, len(b) - 1)
    if lb < 4:
        return None
    sa = a.iloc[-1] / a.iloc[-1 - lb] - 1.0
    sb = b.iloc[-1] / b.iloc[-1 - lb] - 1.0
    return float((sa - sb) * 100.0)


def build_trend(weekly: pd.DataFrame, daily: pd.DataFrame,
                spy_weekly: Optional[pd.DataFrame], sector_weekly: Optional[pd.DataFrame]) -> dict:
    """Udled trend-vaerdierne (rent; tager bars, returnerer scorer-input-dict t)."""
    t: dict = {}
    if weekly is None or weekly.empty:
        return t
    wc = weekly["close"]
    price = float(wc.iloc[-1])
    t["price"] = price

    ma40 = _sma_last(wc, 40)
    t["price_vs_40w"] = (price / ma40 - 1.0) * 100.0 if ma40 else None
    if daily is not None and not daily.empty:
        ma200 = _sma_last(daily["close"], 200)
        t["price_vs_200d"] = (price / ma200 - 1.0) * 100.0 if ma200 else None
    ma10, ma30 = _sma_last(wc, 10), _sma_last(wc, 30)
    if ma10 and ma30 and ma40:
        if ma10 > ma30 > ma40:
            t["ma_align"] = 1
        elif ma10 < ma30 < ma40:
            t["ma_align"] = -1
        else:
            t["ma_align"] = 0

    if spy_weekly is not None and not spy_weekly.empty:
        t["rs_index"] = _rs(wc, spy_weekly["close"], RS_LOOKBACK_W)
    if sector_weekly is not None and not sector_weekly.empty:
        t["rs_sector"] = _rs(wc, sector_weekly["close"], RS_LOOKBACK_W)

    t["max_dd"] = _max_drawdown(wc)
    t["ann_vol"] = _ann_vol(wc)
    t["dist_ath"] = _dist_from_ath(wc)
    return t


# ── Scorere (t, price) ───────────────────────────────────────────────────────
from buyhold_fundamental import _band_score   # noqa: E402  (samme band-helper)


def s_price_vs_40w(t, price):
    v = t.get("price_vs_40w")
    if v is None:
        return None
    return (f"{v:+.1f}% vs 40-uge MA",
            _band_score(v, [(-15, -70), (-5, -30), (0, 0), (8, 30), (20, 50)], 60))


def s_price_vs_200d(t, price):
    v = t.get("price_vs_200d")
    if v is None:
        return None
    return (f"{v:+.1f}% vs 200-dag MA",
            _band_score(v, [(-12, -60), (-4, -25), (0, 0), (6, 25), (15, 45)], 55))


def s_ma_align(t, price):
    a = t.get("ma_align")
    if a is None:
        return None
    if a > 0:
        return "Bullish stack (10>30>40u)", 50.0
    if a < 0:
        return "Inverteret (10<30<40u)", -50.0
    return "Blandet MA-stak", 0.0


def s_rs_index(t, price):
    v = t.get("rs_index")
    if v is None:
        return None
    return (f"{v:+.0f}% vs SPY (3y)",
            _band_score(v, [(-30, -60), (-10, -25), (0, 0), (15, 30), (40, 55)], 70))


def s_rs_sector(t, price):
    v = t.get("rs_sector")
    if v is None:
        return None
    return (f"{v:+.0f}% vs sektor (3y)",
            _band_score(v, [(-25, -50), (-8, -20), (0, 0), (12, 25), (30, 45)], 60))


def s_max_dd(t, price):
    v = t.get("max_dd")
    if v is None:
        return None
    return (f"Max drawdown {v:.0f}% (5y)",
            _band_score(v, [(-65, -50), (-50, -25), (-35, 0), (-20, 15), (-10, 25)], 30))


def s_ann_vol(t, price):
    v = t.get("ann_vol")
    if v is None:
        return None
    return (f"Afkast-vol {v:.0f}% (ann.)",
            _band_score(v, [(15, 40), (25, 20), (40, 0), (60, -25)], -50))


def s_dist_ath(t, price):
    """Anti-euforitop (let, ikke-monoton): moderat under ATH = sund trend; LIGE paa ATH
    = mild timing-advarsel for at STARTE; dybt under = braekket trend."""
    v = t.get("dist_ath")
    if v is None:
        return None
    if v >= -3:
        return "Ved ATH (mild timing-advarsel)", -5.0
    if v >= -30:
        return f"{v:.0f}% under ATH (sund pullback)", 10.0
    if v >= -50:
        return f"{v:.0f}% under ATH", -15.0
    return f"{v:.0f}% under ATH (braekket trend)", -40.0


# ── Parameter-register (vaegt = % af Lag 4) ──────────────────────────────────
PARAMS_TREND = [
    # Sekulaer trend ~45 %
    ("price_vs_40w", "Pris vs 40-uge MA", "Sekulaer trend", 0.18, s_price_vs_40w, None),
    ("price_vs_200d", "Pris vs 200-dag MA", "Sekulaer trend", 0.12, s_price_vs_200d, None),
    ("ma_align", "MA-alignment (uge)", "Sekulaer trend", 0.15, s_ma_align, None),
    # Relativ styrke ~30 %
    ("rs_index", "RS vs indeks (3y)", "Relativ styrke", 0.18, s_rs_index, None),
    ("rs_sector", "RS vs sektor (3y)", "Relativ styrke", 0.12, s_rs_sector, None),
    # Stabilitet & nedtur ~25 %
    ("max_dd", "Max drawdown (5y)", "Stabilitet", 0.10, s_max_dd, None),
    ("ann_vol", "Afkast-volatilitet", "Stabilitet", 0.08, s_ann_vol, None),
    ("dist_ath", "Afstand fra ATH", "Stabilitet", 0.07, s_dist_ath, None),
]


def compute_buyhold_trend(weekly, daily, spy_weekly, sector_weekly) -> dict:
    """Lag 4 -> {results, excluded, lag_score}. Mangler bars/benchmark -> faktor ekskluderes."""
    t = build_trend(weekly, daily, spy_weekly, sector_weekly)
    return compute_layer(PARAMS_TREND, t, None)


# ── Bar-hentning (live ib) ───────────────────────────────────────────────────
def buyhold_chart_data(weekly_df, window: int = 260) -> Optional[dict]:
    """Uge-bars + 10/30/40-uge MA-serier + ATH-linje til buy-and-hold-rapportens
    trend-chart. Spejler Pine 'Trend-kontekst'-tvillingen (samme MA'er + ATH).
    window = max antal uge-staenger (260 ~ 5 aar). None hvis for lidt data.
    (200-dag MA UDELADT af chartet — ~40 uger, ville klistre paa 40-uge-linjen.)"""
    if weekly_df is None or len(weekly_df) < 2:
        return None
    df = weekly_df.tail(window)
    close = df["close"]

    def _ma(n):
        # Roll paa HELE serien, klip saa til vinduet -> MA korrekt ved vinduets venstrekant.
        m = weekly_df["close"].rolling(n).mean().tail(len(df))
        return [None if pd.isna(v) else round(float(v), 2) for v in m]

    def _t(idx):
        try:
            return pd.Timestamp(idx).strftime("%Y-%m-%d")
        except Exception:
            return str(idx)

    bars = [{"t": _t(idx),
             "o": round(float(r["open"]), 2), "h": round(float(r["high"]), 2),
             "l": round(float(r["low"]), 2), "c": round(float(r["close"]), 2)}
            for idx, r in df.iterrows()]
    ath = round(float(weekly_df["close"].tail(window).max()), 2)   # ATH (close-basis, matcher faktoren)
    return {
        "bars": bars,
        "ma10": _ma(10), "ma30": _ma(30), "ma40": _ma(40),
        "ath": ath,
        "current_price": round(float(close.iloc[-1]), 2),
    }


async def fetch_trend_bars(ib, symbol: str, sector: Optional[str]):
    """(weekly_5y, daily_1y, spy_weekly_5y, sector_weekly_5y) via de generiske helpers.
    Mangler sektor-ETF-mapping -> sector_weekly None (sektor-RS ekskluderes)."""
    import data_source_intraday as dsi
    import technical_score as tech

    weekly = await dsi.fetch_intraday_bars(ib, symbol, timeframe="1 week", duration=WEEKLY_DURATION)
    daily = await dsi.fetch_intraday_bars(ib, symbol, timeframe="1 day", duration=DAILY_DURATION)
    spy_weekly = await dsi.fetch_benchmark_bars(ib, symbol="SPY", timeframe="1 week", duration=WEEKLY_DURATION)
    sector_weekly = None
    etf = tech.SECTOR_ETF.get(sector) if sector else None
    if etf:
        try:
            sector_weekly = await dsi.fetch_benchmark_bars(ib, symbol=etf, timeframe="1 week", duration=WEEKLY_DURATION)
        except Exception:
            sector_weekly = None
    return weekly, daily, spy_weekly, sector_weekly


# ── CLI ──────────────────────────────────────────────────────────────────────
def _fmt(res) -> str:
    L = [f"  LAG 4-SCORE (trend) {res['lag_score']:+.1f}"]
    last = None
    for r in res["results"]:
        if r.group != last:
            L.append(f"  ({r.group})")
            last = r.group
        L.append(f"    {r.name:<24}{r.raw:<30}{r.signal:>+7.0f} {r.weight*100:>5.1f}% {r.weighted:>+7.1f}")
    if res["excluded"]:
        L.append("    Ekskluderet: " + "; ".join(f"{n} ({w})" for n, w in res["excluded"]))
    return "\n".join(L)


def main() -> int:
    import argparse
    import asyncio
    import os
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Buy-and-Hold Lag 4 (trend) — kraever IBKR")
    ap.add_argument("ticker")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    a = ap.parse_args()

    async def run():
        from ibkr_connect import IBKRConnection
        # Kun sektoren skal bruges (til sektor-ETF). Slå den op via ét let TradingView-kald
        # i stedet for en fuld regnskabs-pull (FMP er død + spild). SECTOR_ETF kender TV's
        # sektor-vokabular. api_key ikke længere nødvendig.
        sector = None
        try:
            from buyhold_fundamental import _tradingview_buyhold_fields
            _f, sector = _tradingview_buyhold_fields(a.ticker.upper())
        except Exception:
            pass
        conn = IBKRConnection(paper_trading=True)
        if not await conn.connect():
            print("❌ Kunne ikke forbinde til IBKR (TWS/Gateway oppe?).")
            return 1
        try:
            weekly, daily, spy, sect = await fetch_trend_bars(conn.ib, a.ticker.upper(), sector)
        finally:
            conn.disconnect()
        print("=" * 78)
        print(f"  BUY-AND-HOLD LAG 4 (langsigtet trend) — {a.ticker.upper()}   sektor: {sector or '—'}")
        print("=" * 78)
        if weekly is None or weekly.empty:
            print("  ❌ Ingen uge-bars fra IBKR.")
            return 1
        print(_fmt(compute_buyhold_trend(weekly, daily, spy, sect)))
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())

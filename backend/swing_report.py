#!/usr/bin/env python3
"""
swing_report.py — Kombinations-motor (capstone).

Binder de tre lag sammen til den endelige swing-egnethedsrapport:
  Samlet = (0.55·Teknisk + 0.20·Fundamental + 0.25·Katalysator) · Gate

- Lag-scores kommer fra technical_score / fundamental_score / catalyst_score
  (alle paa signeret skala -100..+100).
- Gate (handelbarhed, Lag 1) er en 0..1 multiplikator — den bindende
  flaskehals (mindste delfaktor) vaegter tungest.
- Manuelle chart-input (S/R, chart pattern, candlestick) flettes ind i det
  tekniske lag som en overlay paa ~12 % af lagets vaegt.

Udskriver samlet score, de tre lag, gaten, og en PUNKTVIS ANBEFALING med de
staerkeste medvinde/modvinde paa tvaers af alle lag.
"""
from __future__ import annotations

from typing import Optional

LAG_WEIGHTS = {"technical": 0.55, "fundamental": 0.20, "catalyst": 0.25}
MANUAL_TECH_SHARE = 0.12   # manuel chart-overlay = 12 % af det tekniske lag
GATE_STRAF = 40.0          # gate-straf: lav handelbarhed skubber final NEDAD mod Fraraades

LABEL = {"technical": "Teknisk", "fundamental": "Fundamental", "catalyst": "Katalysator"}


# === Handelbarheds-gate (Lag 1) =============================================
def compute_gate(adv_shares: Optional[float] = None,
                 dollar_vol: Optional[float] = None,
                 price: Optional[float] = None,
                 spread_pct: Optional[float] = None,
                 market_cap: Optional[float] = None) -> float:
    """0..1. Mindste delfaktor styrer (bindende flaskehals).

    Likviditets-taersklerne (adv_shares, dollar_vol) skaleres efter market cap
    naar den er kendt; ellers bruges faste standardtaerskler (fallback), saa
    intet braekker hvis cap ikke kan hentes.
    """
    if market_cap is None:
        adv_full, dol_full = 500_000, 20_000_000        # ukendt cap -> standard
    elif market_cap < 300e6:
        adv_full, dol_full = 150_000, 2_000_000         # micro
    elif market_cap < 2e9:
        adv_full, dol_full = 300_000, 6_000_000         # small
    elif market_cap < 10e9:
        adv_full, dol_full = 500_000, 15_000_000        # mid
    else:
        adv_full, dol_full = 750_000, 30_000_000        # large
    f = []
    if adv_shares is not None:
        f.append(min(1.0, adv_shares / adv_full))
    if dollar_vol is not None:
        f.append(min(1.0, dollar_vol / dol_full))
    if price is not None:
        f.append(0.0 if price < 1 else 0.5 if price < 3 else 1.0)
    if spread_pct is not None:
        f.append(max(0.0, 1.0 - spread_pct))             # 0 % = 1, >=1 % = 0
    return round(min(f), 3) if f else 1.0


# === Manuel chart-overlay ===================================================
def manual_overlay(sr: Optional[float] = None,
                   chart_pattern: Optional[float] = None,
                   candlestick: Optional[float] = None) -> Optional[float]:
    """Vægtet snit af de manuelle chart-signaler (-100..+100)."""
    parts = []
    if sr is not None:
        parts.append((0.40, sr))
    if chart_pattern is not None:
        parts.append((0.40, chart_pattern))
    if candlestick is not None:
        parts.append((0.20, candlestick))
    if not parts:
        return None
    tw = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / tw


# === Kombination ============================================================
def combine(layers: dict, gate: float = 1.0,
            manual: Optional[float] = None,
            days_to_earnings: Optional[int] = None) -> dict:
    """layers: {'technical': res, 'fundamental': res, 'catalyst': res} fra compute_*()."""
    tech = layers["technical"]["lag_score"]
    if manual is not None:
        tech = (1 - MANUAL_TECH_SHARE) * tech + MANUAL_TECH_SHARE * manual
    adj = {
        "technical": tech,
        "fundamental": layers["fundamental"]["lag_score"],
        "catalyst": layers["catalyst"]["lag_score"],
    }
    combined = sum(LAG_WEIGHTS[k] * adj[k] for k in LAG_WEIGHTS)
    # Gate som nedadrettet straf (ikke ren multiplikator): bevarer flaskehalsen,
    # men lav handelbarhed skubber ALTID mod Fraraades, aldrig mod neutral.
    gate_straf = (1.0 - gate) * GATE_STRAF
    final = combined * gate - gate_straf
    final = max(-100.0, min(100.0, final))

    drivers = []   # (layer, navn, global_bidrag, signal)
    for layer, res in layers.items():
        for r in res["results"]:
            drivers.append((layer, r.name, LAG_WEIGHTS[layer] * r.weighted, r.signal))

    return {"adj": adj, "combined": combined, "gate": gate, "gate_straf": gate_straf,
            "final": final, "drivers": drivers, "manual": manual,
            "days_to_earnings": days_to_earnings}

def _band(final: float) -> str:
    if final >= 45:
        return "STAERK SWING-KANDIDAT"
    if final >= 25:
        return "EGNET MED FORBEHOLD"
    if final > 0:
        return "NEUTRAL / AFVENT"
    if final > -25:
        return "SVAG"
    return "FRARAADES"


def _lag_band(s: float) -> str:
    if s >= 50:
        return "Staerk"
    if s >= 20:
        return "Medvind"
    if s > -20:
        return "Neutral"
    if s > -50:
        return "Svag"
    return "Fraraades"


def _scale_bar(value: float, boundaries, width: int = 26) -> str:
    """Tekst-skala -100..+100 med baand-grænser (|) og en markør (●)."""
    def idx(v):
        return max(0, min(width - 1, round((v + 100) / 200 * (width - 1))))
    cells = ["-"] * width
    for b in boundaries:
        cells[idx(b)] = "|"
    cells[idx(value)] = "●"
    return "-100 " + "".join(cells) + " +100"


def format_final_report(ticker: str, c: dict) -> str:
    L = []
    L.append("#" * 78)
    L.append(f"  SWING-EGNETHED — {ticker.upper()}")
    L.append("#" * 78)
    L.append("")
    L.append("LAG-SKALA -100..+100:  <=-50 Fraraades | -50..-20 Svag | +-20 Neutral | +20..+50 Medvind | >+50 Staerk")
    L.append("")
    for k in ("technical", "fundamental", "catalyst"):
        s = c["adj"][k]
        L.append(f"  {LABEL[k]:<12}{s:>+6.1f}  [{_lag_band(s):<9}] {_scale_bar(s, [-50, -20, 20, 50])}")
    L.append("")
    L.append(f"  Kombineret (vaegtet):  {c['combined']:>+6.1f}")
    gate = c["gate"]
    gate_note = "" if gate >= 0.7 else "   <-- traekker scoren ned!"
    L.append(f"  Handelbarheds-gate:    {gate:>6.2f}{gate_note}")
    gs = c.get("gate_straf", 0.0)
    if gs > 0.01:
        L.append(f"  Gate-straf:            {-gs:>+6.1f}   (lav handelbarhed)")
    L.append("  " + "-" * 62)
    L.append("  SAMLET-SKALA:  <=-25 Fraraades | -25..0 Svag | 0..+25 Afvent | +25..+45 Egnet | >+45 Staerk")
    L.append(f"  SAMLET EGNETHED:  {c['final']:>+6.1f}  [{_band(c['final'])}]")
    L.append(f"     {_scale_bar(c['final'], [-25, 0, 25, 45])}")
    L.append("")

    # Top-drivere
    drivers = [d for d in c["drivers"]]
    pos = sorted([d for d in drivers if d[2] > 0], key=lambda x: -x[2])[:4]
    neg = sorted([d for d in drivers if d[2] < 0], key=lambda x: x[2])[:4]

    L.append("=" * 78)
    L.append("PUNKTVIS ANBEFALING")
    L.append("=" * 78)
    L.append(f"  • Samlet vurdering: {_band(c['final'])}  ({c['final']:+.1f})")
    if gate < 0.7:
        L.append(f"  • Handelbarhed: BEGRAENSET (gate {gate:.2f}) — likviditet/spread er en hindring")
    else:
        L.append(f"  • Handelbarhed: OK (gate {gate:.2f})")
    L.append("  • Staerkeste medvind:")
    for layer, name, contrib, sig in pos:
        L.append(f"      + {name} [{LABEL[layer]}]  ({contrib:+.1f})")
    L.append("  • Staerkeste modvind:")
    for layer, name, contrib, sig in neg:
        L.append(f"      - {name} [{LABEL[layer]}]  ({contrib:+.1f})")
    d = c.get("days_to_earnings")
    if d is not None and d < 10:
        L.append(f"  • OBS: regnskab om {d} dage — gap-risiko for swing")
    if c["manual"] is None:
        L.append("  • Mangler: chart pattern + candlestick + S/R (kør chart_helper / TradingView")
        L.append("    og flet ind for fuld vurdering — scoren her er auto-lagene alene)")
    else:
        L.append(f"  • Manuelt chart-overlay indregnet: {c['manual']:+.0f}")
    L.append("=" * 78)
    return "\n".join(L)


# === Orkestrering ===========================================================
# Datakilde: 'ibkr' (default) laeser OHLCV fra bars_daily-cachen; 'yfinance'
# er valgfri fallback. Scoring-motorerne er uaendrede uanset kilde.
def _ohlcv(ticker: str, source: str, period: str):
    if source == "ibkr":
        import data_source
        return data_source.load_bars(ticker)
    import technical_score as tech
    return tech._yf_ohlcv(ticker, period)


def run_full(ticker: str, api_key: str, period: str = "1y",
             source: str = "ibkr", manual: Optional[float] = None,
             detailed: bool = True) -> str:
    import technical_score as tech
    import fundamental_score as fund
    import catalyst_score as cat

    df = _ohlcv(ticker, source, period)
    if df is None or df.empty:
        raise ValueError(
            f"Ingen prisdata for {ticker} (kilde={source}). "
            f"Tilfoej tickeren til download-universet, eller brug --source yfinance.")
    price = float(df["Close"].iloc[-1])

    bench = _ohlcv("SPY", source, period)
    if bench is not None and bench.empty:
        bench = None   # SPY ikke i cachen endnu -> relativ styrke ekskluderes pænt

    # Fundamentaler hentes én gang; sektor-feltet genbruges til sektor-ETF'en
    fdict = fund.fetch_fundamentals_fmp(ticker, api_key)
    fund_res = fund.compute_fundamental(fdict, price)

    sector_df, sector_mom = None, None
    etf = tech.SECTOR_ETF.get(fdict.get("sector"))
    if etf:
        sector_df = _ohlcv(etf, source, period)
        if sector_df is not None and not sector_df.empty and len(sector_df) > 63:
            sector_mom = (sector_df["Close"].iloc[-1] / sector_df["Close"].iloc[-64] - 1) * 100
        else:
            sector_df = None

    tech_res = tech.compute_technical(df, benchmark=bench, sector=sector_df)
    cat_dict = cat.fetch_catalyst(ticker, api_key, price, sector_momentum_pct=sector_mom)
    cat_res = cat.compute_catalyst(cat_dict, price)

    adv = float(df["Volume"].tail(50).mean())
    gate = compute_gate(adv_shares=adv, dollar_vol=adv * price, price=price,
                        market_cap=fdict.get("market_cap"))

    c = combine({"technical": tech_res, "fundamental": fund_res, "catalyst": cat_res},
                gate=gate, manual=manual, days_to_earnings=cat_dict.get("days_to_earnings"))
    out = format_final_report(ticker, c)
    if detailed:
        out += "\n\n" + tech.format_technical_report(ticker, tech_res)
        out += "\n\n" + fund.format_fundamental_report(ticker, fund_res)
        out += "\n\n" + cat.format_catalyst_report(ticker, cat_res)
    return out


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser(description="Swing-egnethed (capstone)")
    ap.add_argument("ticker")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    ap.add_argument("--period", default="1y")
    ap.add_argument("--source", default="ibkr", choices=["ibkr", "yfinance"])
    ap.add_argument("--brief", action="store_true", help="kun samlet score + anbefaling")
    ap.add_argument("--sr", type=float, default=None,
                    help="manuel S/R-score -100..+100 (fra TradingView)")
    ap.add_argument("--pattern", type=float, default=None,
                    help="manuel chart-mønster-score -100..+100")
    ap.add_argument("--candle", type=float, default=None,
                    help="manuel candlestick-score -100..+100")
    args = ap.parse_args()
    manual = manual_overlay(sr=args.sr, chart_pattern=args.pattern,
                            candlestick=args.candle)
    print(run_full(args.ticker, args.api_key, args.period, source=args.source,
                   manual=manual, detailed=not args.brief))
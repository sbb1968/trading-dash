#!/usr/bin/env python3
"""
fundamental_score.py — Fundamental lag-scoring (Lag 4, 20 % af helheden).

Samme design som technical_score: motoren er AFKOBLET fra datakilden.
compute_fundamental() tager en dict med raatal + aktuel pris ind og
producerer signaler paa skalaen -100..+100, vaegtet til en lag-score.

Datakilden (FMP primaert) fylder bare dicten via en adapter — se
fetch_fundamentals_fmp() nederst. Saadan kan logikken testes uden API.

Afledte tal beregnes selv fra de bedste dele:
    P/E = pris / EPS(ttm)        PEG = P/E / EPS-vaekst
(jf. kilde-map: "mest paalidelig kilde" for afledte tal = vores formel).

Scoring-kurverne er foerste-udkast-heuristikker til kalibrering. De er
absolutte; en senere forbedring er at goere dem sektor-relative.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def clamp(x, lo=-100.0, hi=100.0):
    return float(max(lo, min(hi, x)))


def _band_score(value, bands, default):
    """bands: liste af (oevre_graense, score), sorteret stigende."""
    if value is None:
        return None
    for hi, score in bands:
        if value <= hi:
            return float(score)
    return float(default)


# === Scorere (input -> signal -100..+100) ===================================
def s_net_margin(f, price):
    return _band_score(f.get("net_margin"),
                       [(0, -80), (5, -20), (10, 10), (20, 40)], 70)


def s_debt_equity(f, price):
    de = f.get("debt_to_equity")
    return _band_score(de, [(0.5, 40), (1.0, 20), (2.0, 0), (3.0, -30)], -60)


def s_fcf_margin(f, price):
    return _band_score(f.get("fcf_margin"),
                       [(0, -60), (5, 10), (10, 40)], 60)


def s_roe(f, price):
    return _band_score(f.get("roe"),
                       [(0, -60), (10, 0), (20, 30)], 60)


def s_eps_growth(f, price):
    return _band_score(f.get("eps_growth"),
                       [(0, -60), (10, 10), (25, 40)], 70)


def s_revenue_growth(f, price):
    return _band_score(f.get("revenue_growth"),
                       [(0, -50), (10, 10), (25, 40)], 60)


def s_pe(f, price):
    eps = f.get("eps_ttm")
    pe = None
    if eps is not None and price is not None and eps > 0:
        pe = price / eps                 # vi beregner selv: pris / EPS
    elif f.get("pe_ttm"):
        pe = f["pe_ttm"]                 # fallback: FMP's færdige P/E
    if pe is None:
        if eps is not None and eps <= 0:
            return "P/E n/a (underskud)", -40.0
        return None
    if pe < 0:                            # negativ P/E (fra pe_ttm) = underskud, ikke "billig"
        return "P/E n/a (underskud)", -40.0
    sig = _band_score(pe, [(15, 20), (30, 10), (50, -10), (80, -30)], -50)
    return f"P/E {pe:.1f}", sig


def s_peg(f, price):
    eps = f.get("eps_ttm")
    g = f.get("eps_growth")
    pe = (price / eps) if (eps and price and eps > 0) else f.get("pe_ttm")
    if pe is None or g is None:
        return None
    if g <= 0:
        # Negativ/nul EPS-vaekst: PEG matematisk meningsloes (negativ PEG ser "billig"
        # ud men betyder faldende indtjening). Vis som roedt flag - trappe efter faldet.
        if g <= -10:
            return f"EPS FALDER {g:+.1f}% - ROEDT FLAG (PEG n/a)", -70.0
        return f"EPS FALDER {g:+.1f}% - advarsel (PEG n/a)", -50.0
    if pe < 0:                            # underskud (negativ P/E) med voksende EPS: PEG udefineret -> udeluk
        return None
    peg = pe / g
    sig = _band_score(peg, [(1, 40), (2, 10), (3, -10)], -30)
    return f"PEG {peg:.2f}", sig


# === Parameter-register (vaegt = andel af det fundamentale lag) =============
# Fra vaegtningsskemaet: gruppe% x parameter-i-gruppe%.
PARAMS = [
    # (key, navn, gruppe, vaegt_af_lag, scorer, raw_fmt)
    ("net_margin", "Profitmarginer", "Kvalitet", 0.120, s_net_margin, "{:.1f}%"),
    ("debt_to_equity", "Gaeld/egenkapital", "Kvalitet", 0.112, s_debt_equity, "{:.2f}"),
    ("fcf_margin", "Free cash flow-margin", "Kvalitet", 0.096, s_fcf_margin, "{:.1f}%"),
    ("roe", "Return on equity", "Kvalitet", 0.072, s_roe, "{:.1f}%"),
    ("eps_growth", "EPS-vaekst", "Vaekst", 0.1925, s_eps_growth, "{:.1f}%"),
    ("revenue_growth", "Omsaetningsvaekst", "Vaekst", 0.1575, s_revenue_growth, "{:.1f}%"),
    ("pe", "P/E", "Vaerdiansaettelse", 0.1375, s_pe, None),
    ("peg", "PEG", "Vaerdiansaettelse", 0.1125, s_peg, None),
]
# Sektor/industri er et kontekstfelt (vaegt 0) — bruges til sektor-ETF i RS.


@dataclass
class ParamResult:
    key: str
    name: str
    group: str
    raw: str
    signal: float
    weight: float
    weighted: float


def compute_fundamental(fundamentals: dict, price: Optional[float] = None):
    """fundamentals: dict med raatal (se nøgler i PARAMS/scorere). price til afledte tal."""
    results, excluded = [], []
    for key, name, group, weight, scorer, fmt in PARAMS:
        try:
            out = scorer(fundamentals, price)
        except Exception as e:
            out = None
            excluded.append((name, f"fejl: {type(e).__name__}"))
        if out is None:
            excluded.append((name, "mangler data"))
            continue
        if isinstance(out, tuple):           # scorer leverede egen raw-tekst
            raw, signal = out
        else:                                 # simpelt signal -> formatér raatal
            signal = out
            val = fundamentals.get(key)
            raw = fmt.format(val) if (fmt and val is not None) else str(val)
        results.append(ParamResult(key, name, group, raw, float(signal), weight, 0.0))

    total_w = sum(r.weight for r in results)
    if total_w > 0:
        for r in results:
            r.weight /= total_w
            r.weighted = r.weight * r.signal
    lag_score = sum(r.weighted for r in results)
    return {"results": results, "excluded": excluded, "lag_score": lag_score}


def _band(score):
    if score >= 50:
        return "Staerk fundamental profil"
    if score >= 20:
        return "Solid / medvind"
    if score > -20:
        return "Neutral / blandet"
    if score > -50:
        return "Svag"
    return "Fraraades (fundamentalt)"


def format_fundamental_report(ticker: str, res: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append(f" FUNDAMENTAL LAG — {ticker.upper()}")
    L.append("=" * 78)
    L.append(f"{'Parameter':<24}{'Vaerdi':<20}{'Bidrag':>9}{'Vaegt':>8}{'Vaegtet':>9}")
    L.append("-" * 78)
    last = None
    for r in res["results"]:
        if r.group != last:
            L.append(f"[{r.group}]")
            last = r.group
        L.append(f"  {r.name:<22}{r.raw:<20}{r.signal:>+8.0f} {r.weight*100:>6.1f}% {r.weighted:>+8.1f}")
    L.append("-" * 78)
    score = res["lag_score"]
    L.append(f"{'FUNDAMENTAL LAG-SCORE':<60}{score:>+8.1f}")
    L.append(f"  -> {_band(score)}")
    if res["excluded"]:
        L.append("")
        L.append("Ekskluderet (mangler data):")
        for name, why in res["excluded"]:
            L.append(f"  - {name} ({why})")
    L.append("=" * 78)
    return "\n".join(L)


# === FMP-adapter ============================================================
# Fylder fundamentals-dicten fra Financial Modeling Prep.
# BEMAERK: verificér de praecise endpoint-stier mod aktuelle FMP-docs
# (FMP er gaaet fra /api/v3 til /stable; felter kan variere). Kraever API-noegle.
def fetch_fundamentals_fmp(symbol: str, api_key: str) -> dict:
    import urllib.request
    import json

    base = "https://financialmodelingprep.com/stable"

    def get(path):
        url = f"{base}/{path}{'&' if '?' in path else '?'}apikey={api_key}"
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode())

    out = {}
    # --- ratios-ttm: nettomargin, gæld/EK, EPS(ttm), FCF-margin (afledt) ---
    try:
        r = _first(get(f"ratios-ttm?symbol={symbol}"))
        if r.get("netProfitMarginTTM") is not None:
            out["net_margin"] = r["netProfitMarginTTM"] * 100      # fraktion -> %
        out["debt_to_equity"] = r.get("debtToEquityRatioTTM")      # ratio, ikke %
        out["eps_ttm"] = r.get("netIncomePerShareTTM")             # EPS (ttm) i $
        ocf_sales = r.get("operatingCashFlowSalesRatioTTM")
        fcf_ocf = r.get("freeCashFlowOperatingCashFlowRatioTTM")
        if ocf_sales is not None and fcf_ocf is not None:
            out["fcf_margin"] = ocf_sales * fcf_ocf * 100          # FCF/omsætning %
        if r.get("priceToEarningsRatioTTM"):
            out["pe_ttm"] = r["priceToEarningsRatioTTM"]           # fallback til P/E
    except Exception:
        pass
    # --- key-metrics-ttm: ROE ---
    try:
        k = _first(get(f"key-metrics-ttm?symbol={symbol}"))
        if k.get("returnOnEquityTTM") is not None:
            out["roe"] = k["returnOnEquityTTM"] * 100              # fraktion -> %
    except Exception:
        pass
    # --- financial-growth: EPS- og omsætningsvækst ---
    try:
        g = _first(get(f"financial-growth?symbol={symbol}&period=annual&limit=1"))
        if g.get("epsgrowth") is not None:
            out["eps_growth"] = g["epsgrowth"] * 100
        if g.get("revenueGrowth") is not None:
            out["revenue_growth"] = g["revenueGrowth"] * 100
    except Exception:
        pass
    # --- profile: sektor (valgfrit; ikke scoret i Lag 4, men bruges andetsteds) ---
    try:
        p = _first(get(f"profile?symbol={symbol}"))
        out["sector"] = p.get("sector")
        out["market_cap"] = p.get("marketCap")
        out["company_name"] = p.get("companyName")
    except Exception:
        pass
    return out


def _first(data):
    """Returnér første record fra et FMP-svar (liste eller dict)."""
    if isinstance(data, list):
        return data[0] if data else {}
    return data if isinstance(data, dict) else {}


def fetch_fundamentals_finnhub(symbol: str) -> dict:
    """
    Fallback-kilde: Finnhub /stock/metric. Genbruger noeglen fra finnhub_news.
    Finnhubs tal er ALLEREDE i procent (modsat FMP), saa de bruges direkte.
    Returnerer kun de felter Finnhub faktisk leverer (fx ikke fcf-margin).
    """
    import urllib.request
    import urllib.parse
    import json
    try:
        from finnhub_news import FINNHUB_API_KEY, FINNHUB_BASE
    except Exception:
        return {}
    q = urllib.parse.urlencode({"symbol": symbol, "metric": "all", "token": FINNHUB_API_KEY})
    try:
        with urllib.request.urlopen(f"{FINNHUB_BASE}/stock/metric?{q}", timeout=20) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return {}
    m = data.get("metric") if isinstance(data, dict) else None
    if not isinstance(m, dict):
        return {}

    def g(*keys):
        for k in keys:
            v = m.get(k)
            if v is not None:
                return v
        return None

    out = {}
    pairs = {
        "net_margin": ("netProfitMarginTTM", "netProfitMarginAnnual"),
        "debt_to_equity": ("totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual",
                           "longTermDebt/equityQuarterly", "longTermDebt/equityAnnual"),
        "roe": ("roeTTM", "roeRfy"),
        "eps_growth": ("epsGrowthTTMYoy",),
        "revenue_growth": ("revenueGrowthTTMYoy",),
        "eps_ttm": ("epsTTM", "epsBasicExclExtraItemsTTM"),
        "pe_ttm": ("peTTM", "peBasicExclExtraTTM"),
    }
    for dst, keys in pairs.items():
        v = g(*keys)
        if v is not None:
            out[dst] = v

    # Free cash flow-margin: Finnhub har intet faerdigt felt, men kan udledes.
    # FCF/indtjening = PE / pfcfShare  ->  fcf_margin = net_margin * (PE / pfcfShare).
    # Kraever alle tre felter; pfcfShare != 0. Negativ FCF gir negativ margin (straffes
    # korrekt af s_fcf_margin's (0,-60)-baand).
    nm = out.get("net_margin")
    pe = g("peTTM", "peBasicExclExtraTTM")
    pfcf = g("pfcfShareTTM", "pfcfShareAnnual")
    if nm is not None and pe is not None and pfcf:
        out["fcf_margin"] = nm * (pe / pfcf)

    return out


def fetch_fundamentals_tradingview(symbol: str) -> dict:
    """PRIMÆR kilde: TradingViews screener (samme som univers/navne — gratis, ingen
    rate-limit, ingen døde FMP-nøgler). Leverer præcis de felter swing scorer på, i samme
    enheder (marginer/vækst i %). Tom dict hvis biblioteket mangler / API'et fejler /
    tickeren ikke findes. `sector` er TV's vokabular ("Technology Services"…) — SECTOR_ETF
    er udvidet til at kende BÅDE TV's og yfinance/FMP's sektor-navne, så det virker direkte."""
    try:
        from tradingview_screener import Query, col
    except Exception:
        return {}
    cols = ['description', 'sector', 'market_cap_basic', 'close',
            'net_margin', 'return_on_equity', 'debt_to_equity',
            'earnings_per_share_diluted_ttm', 'price_earnings_ttm',
            'free_cash_flow_margin_ttm',
            'earnings_per_share_diluted_yoy_growth_ttm', 'total_revenue_yoy_growth_ttm']
    try:
        _, df = (Query().select('name', *cols)
                 .where(col('name') == symbol).limit(5).get_scanner_data())
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    row = None
    for _, r in df.iterrows():
        if str(r.get('name', '')).upper() == symbol.upper():
            row = r
            break
    if row is None:
        return {}

    def num(key):
        v = row.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    out: dict = {}

    def put(dst, key):
        v = num(key)
        if v is not None:
            out[dst] = v

    put("net_margin", "net_margin")
    put("roe", "return_on_equity")
    put("debt_to_equity", "debt_to_equity")
    put("eps_ttm", "earnings_per_share_diluted_ttm")
    put("pe_ttm", "price_earnings_ttm")
    put("fcf_margin", "free_cash_flow_margin_ttm")
    put("eps_growth", "earnings_per_share_diluted_yoy_growth_ttm")
    put("revenue_growth", "total_revenue_yoy_growth_ttm")
    put("market_cap", "market_cap_basic")
    name = row.get("description")
    if name:
        out["company_name"] = str(name)
    sector = row.get("sector")
    if sector:
        out["sector"] = str(sector)
    return out


_META_CACHE: dict = {}


def _yf_meta(symbol: str) -> dict:
    """Firmanavn + sektor via yfinance (ét .info-opslag, cachet). Sektoren bruger yfinance/
    GICS-taxonomien ("Technology", "Financial Services"…) der matcher SECTOR_ETF — modsat
    TradingViews mere granulære sektor-felt. Navne/sektorer ændrer sig ikke → cache."""
    s = symbol.upper()
    if s in _META_CACHE:
        return _META_CACHE[s]
    meta = {"name": None, "sector": None}
    try:
        import yfinance as yf
        info = yf.Ticker(s).info
        meta["name"] = info.get("longName") or info.get("shortName")
        meta["sector"] = info.get("sector")
    except Exception:
        pass
    _META_CACHE[s] = meta
    return meta


def _company_name_yf(symbol: str):
    """Firmanavn via yfinance (bevaret for eksterne kaldere, fx swing_top15). Deler cache
    med _yf_meta."""
    return _yf_meta(symbol)["name"]


def fetch_fundamentals(symbol: str, api_key: str = "") -> dict:
    """
    Kæde med felt-niveau fallback: TradingView PRIMÆR (rig, gratis, konsistent med resten af
    appen — univers/navne/buyhold bruger samme kilde), dernæst Finnhub for de felter TV IKKE
    gav (sjældent). Sektor + firmanavn fra yfinance (ét cachet .info-opslag) — sektoren i den
    taxonomi SECTOR_ETF forventer. FMP er udfaset (død nøgle + 402 på gratis-tier); api_key
    ignoreres nu (bevaret i signaturen for kalder-kompatibilitet).
    """
    f = fetch_fundamentals_tradingview(symbol) or {}
    needed = ("net_margin", "debt_to_equity", "roe", "eps_growth",
              "revenue_growth", "eps_ttm")
    if not all(k in f for k in needed):
        try:
            fh = fetch_fundamentals_finnhub(symbol)
        except Exception:
            fh = {}
        for k, v in fh.items():
            f.setdefault(k, v)   # fyld kun huller
    if not f.get("company_name"):
        f["company_name"] = _company_name_yf(symbol)    # dyb fallback hvis TV mangler navn
    return f


if __name__ == "__main__":
    import argparse
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))  # auto-indlaes backend/.env (kun ved direkte koersel)
    ap = argparse.ArgumentParser(description="Fundamental lag-scoring (swing)")
    ap.add_argument("ticker")
    ap.add_argument("--price", type=float, required=True, help="aktuel pris (fra IBKR)")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    args = ap.parse_args()
    fund = fetch_fundamentals(args.ticker, args.api_key)
    print(format_fundamental_report(args.ticker, compute_fundamental(fund, args.price)))
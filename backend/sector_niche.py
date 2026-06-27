#!/usr/bin/env python3
"""
sector_niche.py — Sektor- & niche-overblik via TradingViews ETF-data
═════════════════════════════════════════════════════════════════════
To-delt overblik til Trading Dash:
  1) De 11 SPDR-sektorer (XLK, XLC, …) med ydeevne NU / 1 uge / 1 måned +
     hver sektors **AUM-andel** af de 11 (summerer til 100 %).
  2) Underinddeling (niche-ETF'er) pr. sektor — samme tal + hver niches
     **AUM-andel inden for sektoren** (summerer til 100 % pr. sektor).
     Samme niche kan optræde i flere sektorer (fx ICLN i Energy + Utilities).

Datakilde: TradingViews RÅ scanner-endpoint (`/america/scan`) med eksplicitte
EXCHANGE:SYMBOL-tickere. Biblioteket `tradingview-screener` kan KUN aktier —
ETF'erne (XLK/SOXX/SPY…) ligger ikke i dets scanner — så vi POST'er selv.
Felter: change (1D %), Perf.W (1W %), Perf.1M (1M %), aum ($), description.

Rent læse-kald (ingen ordrer). Bruges af /sectors/overview i main.py.

    python sector_niche.py            # print overblik (rigtige TV-tal)

Placering: C:\\Projects\\trading_dash\\backend\\sector_niche.py
"""
from __future__ import annotations

import sys
from typing import Optional

import requests

TV_URL = "https://scanner.tradingview.com/america/scan"
TV_COLS = ["name", "close", "change", "Perf.W", "Perf.1M", "aum", "description"]
# Børs-prefikser vi prøver pr. symbol (TV returnerer kun dem der findes → dedup på navn).
EXCHANGE_PREFIXES = ["AMEX", "NASDAQ", "BATS", "NYSE", "CBOE"]
HTTP_TIMEOUT = 20


# ── Taksonomi (fra Sørens TradingView-opsætning) ─────────────────────────────
# Hver niche: (label, [tickers]). FØRSTE ticker er data-kilden; resten vises kun.
SECTORS: list[dict] = [
    {"key": "XLK", "name": "Technology", "emoji": "💻", "niches": [
        ("Semiconductors", ["SOXX", "SMH"]),
        ("S&P Semiconductor (equal-weight)", ["XSD"]),
        ("Quantum Computing & ML", ["QTUM"]),
        ("Software", ["IGV"]),
        ("Cloud Computing", ["SKYY"]),
        ("Robotics & AI", ["BOTZ", "ROBO"]),
        ("AI & Big Data", ["AIQ"]),
        ("Next Gen Internet", ["ARKW"]),
        ("Autonomous Tech", ["ARKQ"]),
        ("Fintech", ["FINX"]),
        ("Blockchain & Fintech", ["KOIN"]),
        ("Future Security / Defense", ["FITE"]),
        ("Data Center & Tower REITs", ["SRVR"]),
        ("Next Gen Connectivity / 5G", ["KNCT"]),
    ]},
    {"key": "XLC", "name": "Communication Services", "emoji": "📡", "niches": [
        ("Quantum Computing & ML", ["QTUM"]),
        ("Cloud Computing", ["SKYY"]),
        ("AI & Big Data", ["AIQ"]),
        ("Next Gen Internet", ["ARKW"]),
        ("Smartphones & Connectivity", ["FONE"]),
        ("Next Gen Connectivity / 5G", ["KNCT"]),
        ("Social Media", ["SOCL"]),
        ("Online Retail", ["ONLN"]),
    ]},
    {"key": "XLY", "name": "Consumer Discretionary", "emoji": "🛍️", "niches": [
        ("Smart Mobility / EVs", ["HAIL"]),
        ("Online Gaming & Sports Betting", ["BETZ"]),
        ("Travel & Tourism", ["AWAY"]),
        ("Online Retail", ["ONLN"]),
        ("Lithium & Battery Tech", ["LIT"]),
    ]},
    {"key": "XLP", "name": "Consumer Staples", "emoji": "🧺", "niches": [
        ("Agribusiness", ["MOO"]),
    ]},
    {"key": "XLE", "name": "Energy", "emoji": "⚡", "niches": [
        ("Oil Services", ["OIH"]),
        ("Oil & Gas Exploration & Production", ["XOP"]),
        ("Natural Gas / Fracking", ["FRAK"]),
        ("Clean Power", ["CNRG"]),
        ("Clean Energy", ["ICLN"]),
        ("Solar Energy", ["TAN"]),
        ("Wind Energy", ["FAN"]),
    ]},
    {"key": "XLF", "name": "Financials", "emoji": "🏛️", "niches": [
        ("Regional Banks", ["KRE"]),
        ("Banks (bredt)", ["KBE"]),
        ("Insurance", ["IAK"]),
        ("Fintech", ["FINX"]),
        ("Blockchain & Fintech", ["KOIN"]),
    ]},
    {"key": "XLV", "name": "Health Care", "emoji": "🩺", "niches": [
        ("Biotech", ["IBB", "XBI"]),
        ("Pharmaceuticals", ["IHE"]),
        ("Health Care Providers", ["IHF"]),
        ("Genomics", ["ARKG"]),
        ("Genomics & Immunology", ["IDNA"]),
    ]},
    {"key": "XLI", "name": "Industrials", "emoji": "🏗️", "niches": [
        ("Final Frontiers (aerospace/defense/space)", ["ROKT"]),
        ("Future Security / Defense", ["FITE"]),
        ("Intelligent Structures / Infrastructure", ["SIMS"]),
        ("Aerospace & Defense", ["ITA"]),
        ("Airlines", ["JETS"]),
        ("Infrastructure", ["PAVE"]),
        ("Robotics & AI", ["BOTZ", "ROBO"]),
        ("Autonomous Tech", ["ARKQ"]),
        ("Smart Mobility / EVs", ["HAIL"]),
        ("Travel & Tourism", ["AWAY"]),
        ("Agribusiness", ["MOO"]),
        ("Clean Power", ["CNRG"]),
        ("Clean Energy", ["ICLN"]),
        ("Solar Energy", ["TAN"]),
        ("Wind Energy", ["FAN"]),
    ]},
    {"key": "XLB", "name": "Materials", "emoji": "⛏️", "niches": [
        ("Lithium & Battery Tech", ["LIT"]),
        ("Metals & Mining", ["PICK"]),
        ("Gold Miners", ["GDX", "GDXJ"]),
        ("Silver Miners", ["SIL"]),
        ("Copper Miners", ["COPX"]),
        ("Agribusiness", ["MOO"]),
    ]},
    {"key": "XLRE", "name": "Real Estate", "emoji": "🏠", "niches": [
        ("Broad REITs", ["VNQ"]),
        ("Residential REITs", ["REZ"]),
        ("Data Center & Tower REITs", ["SRVR"]),
    ]},
    {"key": "XLU", "name": "Utilities", "emoji": "💡", "niches": [
        ("Clean Power", ["CNRG"]),
        ("Clean Energy", ["ICLN"]),
        ("Solar Energy", ["TAN"]),
        ("Wind Energy", ["FAN"]),
    ]},
]

# Ægte tværgående temaer (ingen primær sektor) — vises separat, IKKE i de 11.
CROSS_CUTTING: list[tuple] = [
    ("Kensho New Economies Composite", ["KOMP"]),
    ("Disruptive Innovation", ["ARKK"]),
    ("Cybersecurity", ["HACK", "BUG"]),
]


def _all_data_tickers() -> list[str]:
    """Alle symboler vi skal hente data for (sektor-ETF'er + niche-PRIMÆRtickere + tværgående)."""
    out: set[str] = set()
    for s in SECTORS:
        out.add(s["key"])
        for _label, tks in s["niches"]:
            out.add(tks[0])
    for _label, tks in CROSS_CUTTING:
        out.add(tks[0])
    return sorted(out)


def fetch_tv(symbols: list[str]) -> dict[str, dict]:
    """Hent TV-data for symboler via det rå scanner-endpoint. Returnerer
    {SYMBOL: {close, change, perf_w, perf_m, aum, description}}. Prøver flere
    børs-prefikser pr. symbol; TV returnerer kun dem der findes → dedup på navn.
    Tom dict ved netværks-/API-fejl (kalderen håndterer manglende symboler)."""
    candidates = [f"{ex}:{sym}" for sym in symbols for ex in EXCHANGE_PREFIXES]
    payload = {"symbols": {"tickers": candidates, "query": {"types": []}}, "columns": TV_COLS}
    try:
        r = requests.post(TV_URL, json=payload, timeout=HTTP_TIMEOUT,
                          headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        d = row.get("d") or []
        if len(d) < len(TV_COLS):
            continue
        rec = dict(zip(TV_COLS, d))
        name = (rec.get("name") or "").upper()
        if not name or name in out:
            continue
        out[name] = {
            "close":   _f(rec.get("close")),
            "change":  _f(rec.get("change")),
            "perf_w":  _f(rec.get("Perf.W")),
            "perf_m":  _f(rec.get("Perf.1M")),
            "aum":     _f(rec.get("aum")),
            "description": rec.get("description") or "",
        }
    return out


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _shares(items: list[dict]) -> None:
    """Sæt 'pct' på hvert item = aum-andel af summen (kun items med gyldig aum)."""
    total = sum(it["aum"] for it in items if it.get("aum"))
    for it in items:
        it["pct"] = round(it["aum"] / total * 100, 2) if (total > 0 and it.get("aum")) else None


def build_overview() -> dict:
    """Byg det fulde sektor/niche-overblik med rigtige TV-tal. Ét TV-request."""
    data = fetch_tv(_all_data_tickers())

    def quote(sym: str) -> dict:
        q = data.get(sym.upper(), {})
        return {"change": q.get("change"), "perf_w": q.get("perf_w"),
                "perf_m": q.get("perf_m"), "aum": q.get("aum"),
                "price": q.get("close"), "description": q.get("description", "")}

    sectors_out = []
    for s in SECTORS:
        q = quote(s["key"])
        niches = []
        for label, tks in s["niches"]:
            nq = quote(tks[0])
            niches.append({"ticker": tks[0], "tickers": tks, "label": label,
                           "change": nq["change"], "perf_w": nq["perf_w"],
                           "perf_m": nq["perf_m"], "aum": nq["aum"], "price": nq["price"]})
        _shares(niches)   # niche-andel INDEN FOR sektoren
        sectors_out.append({
            "key": s["key"], "name": s["name"], "emoji": s.get("emoji", ""),
            "change": q["change"], "perf_w": q["perf_w"], "perf_m": q["perf_m"],
            "aum": q["aum"], "price": q["price"], "niches": niches})

    _shares(sectors_out)   # sektor-andel af de 11 (summerer til 100)
    # Sortér efter ydeevne NU (faldende) — som TradingViews sector-ranking.
    sectors_out.sort(key=lambda x: (x["change"] is not None, x["change"] or -999), reverse=True)

    cross = []
    for label, tks in CROSS_CUTTING:
        cq = quote(tks[0])
        cross.append({"ticker": tks[0], "tickers": tks, "label": label,
                      "change": cq["change"], "perf_w": cq["perf_w"],
                      "perf_m": cq["perf_m"], "aum": cq["aum"], "price": cq["price"]})

    missing = sorted(t for t in _all_data_tickers() if t.upper() not in data)
    return {"sectors": sectors_out, "cross_cutting": cross,
            "pct_basis": "aum", "missing": missing, "ok": bool(data)}


# ── CLI ──────────────────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ov = build_overview()
    if not ov["ok"]:
        print("Kunne ikke hente TV-data (netværk/endpoint).")
        return 1

    def pc(v):
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "  n/a"

    print("=" * 78)
    print("  SEKTOR-OVERBLIK (AUM-andel · ydeevne nu / 1U / 1M) — TradingView")
    print("=" * 78)
    print(f"  {'Sektor':<24} {'%':>6} {'Nu':>9} {'1U':>9} {'1M':>9}")
    for s in ov["sectors"]:
        pct = f"{s['pct']:.1f}%" if s.get("pct") is not None else "  n/a"
        print(f"  {s['emoji']} {s['name']:<22.22} {pct:>6} {pc(s['change']):>9} "
              f"{pc(s['perf_w']):>9} {pc(s['perf_m']):>9}")
    print(f"\n  (sektor-%'er summerer til {sum(s['pct'] for s in ov['sectors'] if s.get('pct')):.0f})")

    # Vis ét eksempel på niche-underinddeling
    top = ov["sectors"][0]
    print(f"\n  Niche-underinddeling — {top['name']} ({top['key']}):")
    for n in top["niches"]:
        pct = f"{n['pct']:.1f}%" if n.get("pct") is not None else " n/a"
        print(f"     {'/'.join(n['tickers']):<10} {pct:>6} {pc(n['change']):>9}  {n['label']}")

    if ov["missing"]:
        print(f"\n  Manglede data for: {', '.join(ov['missing'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

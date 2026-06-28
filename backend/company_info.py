#!/usr/bin/env python3
"""
company_info.py — firma-info pr. ticker (Google-knowledge-panel-stil), dagligt cachet.
══════════════════════════════════════════════════════════════════════════════════════
Samler en kompakt firmaprofil til "Firma-info"-vinduet:
  - Finnhub /stock/profile2 : navn, børs, branche, IPO, market cap, aktier udestående,
    logo, website, land (gratis tier; genbruger FINNHUB_API_KEY fra finnhub_news).
  - Wikipedia REST summary  : "Om"-beskrivelsen (gratis, ingen nøgle).
  - yfinance .info (best-effort): medarbejdere, CEO, sektor/industri, beskrivelse-fallback.

Profiler er ~statiske → caches dagligt til company_cache/<TICKER>.json (respekterer
Finnhubs 60-kald/min + Wikipedia). Best-effort: enhver kilde der fejler springes over.

    python company_info.py AAPL        # print profilen (live, cacher)

Placering: C:\\Projects\\trading_dash\\backend\\company_info.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import date
from pathlib import Path

import requests

from finnhub_news import FINNHUB_API_KEY, FINNHUB_BASE

CACHE_DIR = Path(__file__).parent / "company_cache"
HTTP_TIMEOUT = 12
SCHEMA = 3   # bump når output-formen ændres -> gammel cache ignoreres (nye felter vises straks)
UA = {"User-Agent": "TradingDash/1.0 (firma-info)"}

# Selskabs-suffikser der hjælper Wikipedia-opslaget (prøver med og uden).
_SUFFIXES = [" A/S", " ASA", " AB", " Inc.", " Inc", " Corporation", " Corp.", " Corp",
             " Co.", " Company", " Ltd.", " Ltd", " plc", " PLC", " N.V.", " S.A.", " Group"]


def _finnhub_profile(sym: str) -> dict:
    try:
        r = requests.get(f"{FINNHUB_BASE}/stock/profile2",
                         params={"symbol": sym, "token": FINNHUB_API_KEY},
                         timeout=HTTP_TIMEOUT, headers=UA)
        return r.json() or {} if r.status_code == 200 else {}
    except Exception:
        return {}


def _wiki_summary(name: str) -> dict:
    """Wikipedia REST-summary for firmanavnet. Prøver fuldt navn + uden selskabs-suffiks.
    Springer disambiguation over. Tom dict hvis intet rent hit."""
    if not name:
        return {}
    candidates = [name]
    for suf in _SUFFIXES:
        if name.endswith(suf):
            candidates.append(name[: -len(suf)].strip())
    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        title = urllib.parse.quote(cand.replace(" ", "_"))
        try:
            r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                             timeout=HTTP_TIMEOUT, headers=UA)
            if r.status_code != 200:
                continue
            d = r.json()
            if d.get("type") == "disambiguation" or not d.get("extract"):
                continue
            return {"extract": d.get("extract", ""),
                    "url": (((d.get("content_urls") or {}).get("desktop") or {}).get("page", "")),
                    "thumbnail": ((d.get("thumbnail") or {}).get("source", ""))}
        except Exception:
            continue
    return {}


def _num(v):
    """float eller None (filtrerer NaN/uendelig fra)."""
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _yf_bundle(sym: str) -> dict:
    """Ét yfinance-opslag: profil-felter + nøgletal + 4-års omsætning/overskud. Best-effort."""
    out = {"employees": None, "ceo": "", "summary": "", "sector": "", "industry": "",
           "website": "", "country": "", "earnings_date": "", "earnings_date_end": "",
           "stats": {}, "financials": []}
    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        info = t.info or {}
    except Exception:
        return out

    ceo = ""
    for o in (info.get("companyOfficers") or []):
        title = (o.get("title") or "").lower()
        if "chief executive" in title or title == "ceo":
            ceo = o.get("name", "")
            break
    out.update(employees=info.get("fullTimeEmployees"), ceo=ceo,
               summary=info.get("longBusinessSummary", "") or "",
               sector=info.get("sector", "") or "", industry=info.get("industry", "") or "",
               website=info.get("website", "") or "", country=info.get("country", "") or "")
    out["stats"] = {
        "price":         _num(info.get("currentPrice") or info.get("regularMarketPrice")),
        "pe":            _num(info.get("trailingPE")),
        "fwd_pe":        _num(info.get("forwardPE")),
        "pb":            _num(info.get("priceToBook")),
        "eps":           _num(info.get("trailingEps")),
        "div_yield":     _num(info.get("dividendYield")),      # kan være 0.6 (%) eller 0.006 (frac)
        "beta":          _num(info.get("beta")),
        "profit_margin": _num(info.get("profitMargins")),      # fraktion
        "roe":           _num(info.get("returnOnEquity")),     # fraktion
        "rev_growth":    _num(info.get("revenueGrowth")),      # fraktion
        "hi52":          _num(info.get("fiftyTwoWeekHigh")),
        "lo52":          _num(info.get("fiftyTwoWeekLow")),
    }

    # 4-års omsætning + overskud (årlig resultatopgørelse)
    try:
        fin = t.income_stmt
        if fin is None or getattr(fin, "empty", True):
            fin = t.financials
        if fin is not None and not fin.empty:
            def _row(*names):
                for n in names:
                    if n in fin.index:
                        return fin.loc[n]
                return None
            rev = _row("Total Revenue", "TotalRevenue", "Revenue", "Operating Revenue")
            ni = _row("Net Income", "Net Income Common Stockholders", "NetIncome")
            cols = list(fin.columns)[:4][::-1]   # ældste af de 4 først
            for c in cols:
                yr = getattr(c, "year", None) or str(c)[:4]
                out["financials"].append({
                    "year": int(yr) if str(yr).isdigit() else str(yr),
                    "revenue": _num(rev[c]) if rev is not None else None,
                    "net_income": _num(ni[c]) if ni is not None else None,
                })
    except Exception:
        pass

    # Næste earnings-dato (yfinance calendar; 2 datoer = estimeret vindue)
    try:
        cal = t.calendar
        eds = cal.get("Earnings Date") if isinstance(cal, dict) else None
        seq = eds if isinstance(eds, (list, tuple)) else ([eds] if eds else [])
        dates = sorted({(d.isoformat()[:10] if hasattr(d, "isoformat") else str(d)[:10])
                        for d in seq if d})
        if dates:
            out["earnings_date"] = dates[0]
            if len(dates) > 1:
                out["earnings_date_end"] = dates[-1]
    except Exception:
        pass
    return out


def get_company_info(ticker: str, force: bool = False) -> dict:
    """Samlet firma-info for `ticker`. Dagligt cachet til disk. force=True omgår cache."""
    sym = (ticker or "").upper().strip()
    if not sym:
        return {"ticker": "", "ok": False}
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{sym}.json"
    today = date.today().isoformat()
    if not force and cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("_cached_date") == today and d.get("_schema") == SCHEMA:
                return d
        except Exception:
            pass

    prof = _finnhub_profile(sym)
    name = prof.get("name") or sym
    wiki = _wiki_summary(name)
    yf = _yf_bundle(sym)

    out = {
        "ticker":          sym,
        "name":            name,
        "exchange":        prof.get("exchange", ""),
        "industry":        prof.get("finnhubIndustry") or yf.get("industry", ""),
        "sector":          yf.get("sector", ""),
        "country":         prof.get("country") or yf.get("country", ""),
        "currency":        prof.get("currency", ""),
        "ipo":             prof.get("ipo", ""),
        "market_cap_musd": prof.get("marketCapitalization"),   # mio. USD
        "shares_out_m":    prof.get("shareOutstanding"),       # mio. aktier
        "employees":       yf.get("employees"),
        "ceo":             yf.get("ceo", ""),
        "earnings_date":   yf.get("earnings_date", ""),
        "earnings_date_end": yf.get("earnings_date_end", ""),
        "logo":            prof.get("logo", ""),
        "website":         prof.get("weburl") or yf.get("website", ""),
        "description":     wiki.get("extract") or yf.get("summary") or "",
        "desc_source":     "Wikipedia" if wiki.get("extract") else ("yfinance" if yf.get("summary") else ""),
        "wiki_url":        wiki.get("url", ""),
        "thumbnail":       wiki.get("thumbnail", ""),
        "stats":           yf.get("stats", {}),          # nøgletal (P/E, P/B, udbytte, beta, marginer…)
        "financials":      yf.get("financials", []),     # [{year, revenue, net_income}] seneste 4 år
        "ok":              bool(prof or wiki.get("extract") or yf.get("summary")),
        "_cached_date":    today,
        "_schema":         SCHEMA,
    }
    try:
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) < 2:
        print("Brug: python company_info.py <TICKER>")
        return 1
    d = get_company_info(sys.argv[1], force="--force" in sys.argv)
    for k, v in d.items():
        if k == "description":
            print(f"  {k:<16} {str(v)[:120]}{'…' if len(str(v)) > 120 else ''}")
        else:
            print(f"  {k:<16} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

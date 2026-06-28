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
SCHEMA = 5   # bump når output-formen ændres -> gammel cache ignoreres (nye felter vises straks)
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


def _wiki_intro(name: str, lang: str) -> dict:
    """Fuld intro-sektion (længere end REST-summary) via w/api.php extracts. Prøver navn
    med/uden selskabs-suffiks; springer disambiguation over. Tom dict hvis intet rent hit."""
    if not name:
        return {}
    cands = [name]
    for suf in _SUFFIXES:
        if name.endswith(suf):
            cands.append(name[: -len(suf)].strip())
    seen = set()
    for cand in cands:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            r = requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                             params={"action": "query", "prop": "extracts", "exintro": 1,
                                     "explaintext": 1, "redirects": 1, "format": "json",
                                     "titles": cand}, timeout=HTTP_TIMEOUT, headers=UA)
            if r.status_code != 200:
                continue
            pages = ((r.json() or {}).get("query") or {}).get("pages") or {}
            for page in pages.values():
                if page.get("missing") is not None:
                    continue
                extract = (page.get("extract") or "").strip()
                low = extract.lower()
                if not extract or "may refer to" in low or "kan henvise til" in low:
                    continue
                title = page.get("title", cand)
                return {"extract": extract, "lang": lang,
                        "url": f"https://{lang}.wikipedia.org/wiki/"
                               f"{urllib.parse.quote(title.replace(' ', '_'))}"}
        except Exception:
            continue
    return {}


def _trim(text: str, max_sentences: int = 6, max_chars: int = 950) -> str:
    """Normalisér whitespace + kap til ~6 sætninger / max_chars (2-3x længere end en summary)."""
    import re
    text = " ".join((text or "").split())
    if not text:
        return ""
    out = " ".join(re.split(r"(?<=[.!?])\s+", text)[:max_sentences]).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip(",;: ") + "…"
    return out


def _strip_wiki_noise(text: str) -> str:
    """Fjern Wikipedia-vedligeholdelsesbannere/hatnotes (fx 'for faa kildehenvisninger',
    'this article needs additional citations') saa de ikke ender i beskrivelsen."""
    import re
    bad = ("kildehenvisning", "troværdige kilder", "du kan hjælpe", "denne artikel",
           "this article", "additional citations", "may refer to", "kan henvise til")
    sents = re.split(r"(?<=[.!?])\s+", " ".join((text or "").split()))
    keep = [s for s in sents if not any(b in s.lower() for b in bad)]
    return " ".join(keep).strip() or (text or "")


def _danish(text: str, name: str) -> str:
    """Omformulér kildeteksten til en koncis DANSK firmabeskrivelse (3-5 sætninger) via
    Claude Haiku (samme nøgle som hjælpe-assistenten). Best-effort: tom/fejl/ingen nøgle -> ''."""
    if not text:
        return ""
    try:
        from anthropic import Anthropic
        resp = Anthropic().messages.create(
            model="claude-haiku-4-5", max_tokens=500,
            system=("Du skriver en kort, faktuel firmabeskrivelse paa DANSK: 3-5 saetninger der "
                    "klart forklarer hvad firmaet laver og staar for. Kun selve beskrivelsen — "
                    "ingen indledning, ingen kilde-henvisning, ingen markdown."),
            messages=[{"role": "user", "content": f"Firma: {name}\n\nKildetekst:\n{text}"}])
        return "".join(b.text for b in resp.content
                       if getattr(b, "type", None) == "text").strip()
    except Exception:
        return ""


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
    yf = _yf_bundle(sym)
    wiki_da = _wiki_intro(name, "da")                               # dansk hvis artiklen findes
    wiki_en = {} if wiki_da.get("extract") else _wiki_intro(name, "en")

    _src = "Wikipedia" if (wiki_da.get("extract") or wiki_en.get("extract")) else \
           ("yfinance" if yf.get("summary") else "")
    _lang = "dansk" if wiki_da.get("extract") else "engelsk"
    raw = _trim(_strip_wiki_noise(wiki_da.get("extract") or wiki_en.get("extract")
                                  or yf.get("summary") or ""))
    if not raw:
        description, desc_source = "", ""
    else:
        da = _danish(raw, name)   # ALTID via Haiku: ensartet ren dansk i rette længde (3-5 sætn.)
        if da:
            description, desc_source = da, f"{_src} → dansk"
        else:
            description, desc_source = raw, f"{_src} ({_lang})"      # fallback uden oversættelse
    wiki_url = wiki_da.get("url") or wiki_en.get("url") or ""

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
        "description":     description,
        "desc_source":     desc_source,
        "wiki_url":        wiki_url,
        "stats":           yf.get("stats", {}),          # nøgletal (P/E, P/B, udbytte, beta, marginer…)
        "financials":      yf.get("financials", []),     # [{year, revenue, net_income}] seneste 4 år
        "ok":              bool(prof or description or yf.get("summary")),
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

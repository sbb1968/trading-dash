#!/usr/bin/env python3
"""
news_source_bench.py
────────────────────
Empirisk benchmark af gratis-tier small-cap nyhedskilder for Trend Join Long (TJL).

Maal: vaelg TJL's nyhedskilde paa EVIDENS, ikke leverandoerpaastande. Henter nyheder
for de SAMME micro-cap-gappere fra flere kilder, maaler objektiv kvalitet (daekning,
friskhed, katalysator-relevans) og skriver en laesbar per-ticker-rapport vi kan doemme
paa i faellesskab (Soeren + Desktop Claude + VS Code Claude).

Rent diagnosevaerktoej:
  - Roerer IKKE handelsstien, IKKE algo_trendjoin.py, IKKE journalen.
  - Read-only: henter kun nyheder, afgiver INGEN ordrer.
  - Skriver kun til egen output-mappe (news_bench_output/).
  - Koeres paa en WORKSTATION (I/O-tung) — ALDRIG paa algoserveren i handelstiden.

Kilder (pluggable; auto-springer over hvis env-noegle mangler):
  finnhub (kontrol) · alpaca/benzinga · alpha_vantage · marketaux · fmp · yfinance · sec_8k

Brug:
    python news_source_bench.py --tickers SDOT,FUBO,AAPL --window-hours 48
    python news_source_bench.py                       # dagens TV top-gainers
    python news_source_bench.py --file gainers.txt --sources alpaca,finnhub,fmp

Env-noegler (sat dem der haves; resten springes over):
    ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY   (gratis paper-konto)
    ALPHAVANTAGE_API_KEY                        (5/min, 25/dag — stram)
    MARKETAUX_API_KEY                           (100/dag)
    FMP_API_KEY
    FINNHUB_API_KEY                             (valgfri — falder tilbage til indbygget noegle)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

import aiohttp

# ── Stien saa 'finnhub_news' + 'strategies' kan importeres uanset cwd ──
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# GENBRUG eksisterende helpers (ingen drift): sentiment-heuristik + Finnhub-konstanter
from finnhub_news import FINNHUB_BASE, _guess_sentiment  # noqa: E402
from finnhub_news import FINNHUB_API_KEY as _FINNHUB_KEY_BUILTIN  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# ── TJL-universets filtre (spejler algo_trendjoin.py's UNIVERSE_*; holdt lokalt saa
#    dette diagnoseskript ikke traekker hele strategi-modulet + ib_async ind) ──
UNIVERSE_TOP_N      = 25
UNIVERSE_PRICE_MIN  = 3.0
UNIVERSE_PRICE_MAX  = 500.0
UNIVERSE_MIN_VOLUME = 500_000
REQUIRE_ALL_GREEN   = True
NEWS_MAX_AGE_HOURS  = 20    # TJL's frisk-gate — den alder en nyhed skal vaere under

OUTPUT_DIR = _BACKEND_DIR / "news_bench_output"
SEC_CIK_CACHE = OUTPUT_DIR / "_sec_cik_cache.json"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "trading-dash news-bench soeren@brejlborup.dk")

# ── Katalysator-/recap-heuristik (ROUGH startpunkt — forfines efter foerste output) ──
CATALYST_KW = ["fda", "approv", "phase", "trial", "clinical", "contract", "award", "partner",
    "merger", "acqui", "buyout", "takeover", "earnings", "beat", "guidance", "raises", "upgrade",
    "launch", "deal", "agreement", "grant", "patent", "clearance", "results", "milestone",
    "breakthrough", "order", "backlog", "uplist", "positive"]
RECAP_KW = ["top gainer", "biggest mover", "premarket mover", "stocks to watch", "stocks moving",
    "why is", "what to know", "trending", "watchlist", "market today", "movers to watch",
    "stocks that are moving", "best stocks"]

_FAILURES: list[tuple[str, str, str]] = []   # (kilde, ticker, fejl) — vist til sidst, vaelter ikke


# ═══════════════════════════════════════════════════════════════════
# Normaliseret nyheds-record (samme kontrakt paa tvaers af kilder)
# ═══════════════════════════════════════════════════════════════════
@dataclass
class NewsRecord:
    source: str
    ticker: str
    headline: str
    ts_utc: datetime              # tz-aware UTC
    url: str = ""
    sentiment: Optional[str] = None   # kildens egen, hvis den har en
    category: Optional[str] = None    # fx "8-K"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value) -> Optional[datetime]:
    """Robust → tz-aware UTC. Haandterer epoch (s), ISO (m/u Z), 'YYYY-MM-DD HH:MM:SS',
    og Alpha Vantage's 'YYYYMMDDTHHMMSS'. None hvis uparselig."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    # Alpha Vantage: 20260629T143000
    if len(s) == 15 and s[8:9] == "T" and s[:8].isdigit():
        try:
            return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _norm_sentiment(label: Optional[str]) -> Optional[str]:
    """Kildens egen label → bullish/bearish/neutral. None hvis ingen."""
    if not label:
        return None
    low = str(label).lower()
    if "bull" in low or "positive" in low:
        return "bullish"
    if "bear" in low or "negative" in low:
        return "bearish"
    return "neutral"


# ═══════════════════════════════════════════════════════════════════
# Adaptere — async def fetch(session, ticker, start, end) -> list[NewsRecord]
# Hver er FAIL-SAFE: fejl/timeout/ikke-200 → tom liste (+ registreret i _FAILURES).
# Vinduesfilter sker CENTRALT i harness'en, ikke her (fair sammenligning).
# ═══════════════════════════════════════════════════════════════════
async def _get_json(session, url, *, params=None, headers=None):
    async with session.get(url, params=params, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return await resp.json(content_type=None)


async def fetch_finnhub(session, ticker, start, end) -> list[NewsRecord]:
    key = os.environ.get("FINNHUB_API_KEY") or _FINNHUB_KEY_BUILTIN
    params = {"symbol": ticker, "from": start.date().isoformat(),
              "to": end.date().isoformat(), "token": key}
    items = await _get_json(session, f"{FINNHUB_BASE}/company-news", params=params)
    out = []
    for it in (items or []):
        ts = _parse_dt(it.get("datetime"))
        head = (it.get("headline") or "").strip()
        if ts and head:
            out.append(NewsRecord("finnhub", ticker, head, ts,
                                  url=it.get("url", ""), sentiment=None))
    return out


async def fetch_alpaca(session, ticker, start, end) -> list[NewsRecord]:
    kid, sec = os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY")
    headers = {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}
    params = {"symbols": ticker, "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 50, "sort": "desc"}
    data = await _get_json(session, "https://data.alpaca.markets/v1beta1/news",
                           params=params, headers=headers)
    out = []
    for it in (data.get("news", []) if isinstance(data, dict) else []):
        ts = _parse_dt(it.get("created_at"))
        head = (it.get("headline") or "").strip()
        if ts and head:
            out.append(NewsRecord("alpaca", ticker, head, ts,
                                  url=it.get("url", ""), sentiment=None,
                                  category=it.get("source")))
    return out


async def fetch_alpha_vantage(session, ticker, start, end) -> list[NewsRecord]:
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    params = {"function": "NEWS_SENTIMENT", "tickers": ticker, "apikey": key,
              "time_from": start.strftime("%Y%m%dT%H%M"), "sort": "LATEST", "limit": 50}
    data = await _get_json(session, "https://www.alphavantage.co/query", params=params)
    # AV svarer 200 med {"Information": "..."} ved rate-limit — behandl som tomt + advar
    if isinstance(data, dict) and ("Information" in data or "Note" in data):
        raise RuntimeError("rate-limit/info: " + str(data.get("Information") or data.get("Note"))[:80])
    out = []
    for it in (data.get("feed", []) if isinstance(data, dict) else []):
        ts = _parse_dt(it.get("time_published"))
        head = (it.get("title") or "").strip()
        if not (ts and head):
            continue
        # Ticker-specifik sentiment hvis muligt, ellers overall
        sent = it.get("overall_sentiment_label")
        for ts_obj in (it.get("ticker_sentiment") or []):
            if (ts_obj.get("ticker") or "").upper() == ticker.upper():
                sent = ts_obj.get("ticker_sentiment_label") or sent
                break
        out.append(NewsRecord("alpha_vantage", ticker, head, ts,
                              url=it.get("url", ""), sentiment=_norm_sentiment(sent)))
    return out


async def fetch_marketaux(session, ticker, start, end) -> list[NewsRecord]:
    key = os.environ.get("MARKETAUX_API_KEY")
    params = {"symbols": ticker, "filter_entities": "true", "language": "en",
              "published_after": start.strftime("%Y-%m-%dT%H:%M:%S"), "api_token": key}
    data = await _get_json(session, "https://api.marketaux.com/v1/news/all", params=params)
    out = []
    for it in (data.get("data", []) if isinstance(data, dict) else []):
        ts = _parse_dt(it.get("published_at"))
        head = (it.get("title") or "").strip()
        if not (ts and head):
            continue
        score = None
        for ent in (it.get("entities") or []):
            if (ent.get("symbol") or "").upper() == ticker.upper():
                score = ent.get("sentiment_score")
                break
        sent = None
        if isinstance(score, (int, float)):
            sent = "bullish" if score > 0.15 else "bearish" if score < -0.15 else "neutral"
        out.append(NewsRecord("marketaux", ticker, head, ts,
                              url=it.get("url", ""), sentiment=sent))
    return out


async def fetch_fmp(session, ticker, start, end) -> list[NewsRecord]:
    key = os.environ.get("FMP_API_KEY")
    # v3-stien; hvis FMP migrerer til /stable/ kan denne URL skiftes uden andre aendringer
    params = {"tickers": ticker, "limit": 50, "apikey": key}
    data = await _get_json(session, "https://financialmodelingprep.com/api/v3/stock_news",
                           params=params)
    out = []
    for it in (data if isinstance(data, list) else []):
        ts = _parse_dt(it.get("publishedDate"))   # antaget UTC (FMP-tz uspecificeret)
        head = (it.get("title") or "").strip()
        if ts and head:
            out.append(NewsRecord("fmp", ticker, head, ts,
                                  url=it.get("url", ""), sentiment=None,
                                  category=it.get("site")))
    return out


# ── yfinance: SYNKRON → koeres i thread-executor saa event-loopet ikke blokeres ──
def _yf_news_sync(ticker: str) -> list[tuple[str, Optional[datetime], str]]:
    import yfinance as yf
    items = yf.Ticker(ticker).news or []
    out = []
    for it in items:
        content = it.get("content") if isinstance(it.get("content"), dict) else None
        if content:   # nyere schema (news under 'content')
            head = (content.get("title") or "").strip()
            ts = _parse_dt(content.get("pubDate") or content.get("displayTime"))
            url = ((content.get("canonicalUrl") or {}).get("url")
                   or (content.get("clickThroughUrl") or {}).get("url") or "")
        else:          # aeldre schema
            head = (it.get("title") or "").strip()
            ts = _parse_dt(it.get("providerPublishTime"))
            url = it.get("link", "")
        if head:
            out.append((head, ts, url))
    return out


async def fetch_yfinance(session, ticker, start, end) -> list[NewsRecord]:
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: _yf_news_sync(ticker))
    return [NewsRecord("yfinance", ticker, head, ts, url=url)
            for head, ts, url in rows if ts and head]


# ── SEC EDGAR 8-K: regulator-filings (anden signal-type; ingen sentiment) ──
_SEC_CIK_MAP: Optional[dict] = None


async def _sec_cik_map(session) -> dict:
    global _SEC_CIK_MAP
    if _SEC_CIK_MAP is not None:
        return _SEC_CIK_MAP
    if SEC_CIK_CACHE.exists():
        try:
            _SEC_CIK_MAP = json.loads(SEC_CIK_CACHE.read_text(encoding="utf-8"))
            return _SEC_CIK_MAP
        except (ValueError, OSError):
            pass
    data = await _get_json(session, "https://www.sec.gov/files/company_tickers.json",
                           headers={"User-Agent": SEC_USER_AGENT})
    mapping = {}
    for row in (data.values() if isinstance(data, dict) else []):
        tic = (row.get("ticker") or "").upper()
        if tic:
            mapping[tic] = int(row.get("cik_str"))
    _SEC_CIK_MAP = mapping
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        SEC_CIK_CACHE.write_text(json.dumps(mapping), encoding="utf-8")
    except OSError:
        pass
    return mapping


async def fetch_sec_8k(session, ticker, start, end) -> list[NewsRecord]:
    cik = (await _sec_cik_map(session)).get(ticker.upper())
    if not cik:
        return []   # ikke en SEC-registrant (mange micro-caps/OTC mangler) — ikke en fejl
    data = await _get_json(session, f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                           headers={"User-Agent": SEC_USER_AGENT})
    recent = (data.get("filings", {}) or {}).get("recent", {}) if isinstance(data, dict) else {}
    forms = recent.get("form", []) or []
    acc = recent.get("acceptanceDateTime", []) or []
    fdate = recent.get("filingDate", []) or []
    items = recent.get("items", []) or []
    out = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        ts = _parse_dt(acc[i] if i < len(acc) else None) or _parse_dt(fdate[i] if i < len(fdate) else None)
        if not ts:
            continue
        it = items[i] if i < len(items) else ""
        out.append(NewsRecord("sec_8k", ticker, f"8-K: {it}" if it else "8-K filing",
                              ts, category="8-K"))
    return out


# ═══════════════════════════════════════════════════════════════════
# Kilde-registry (env-gating + pacing)
# ═══════════════════════════════════════════════════════════════════
@dataclass
class SourceSpec:
    name: str
    required_env: list[str]          # alle skal vaere sat for at kilden er aktiv
    interval: float                  # sek mellem kald (rate-limit-pacing)
    fetch: Callable[..., Awaitable[list[NewsRecord]]]
    warn_over_tickers: Optional[int] = None   # advar hvis universet er stoerre (fx AV=25/dag)
    needs_pkg: Optional[str] = None  # python-pakke der skal kunne importeres


SOURCES: list[SourceSpec] = [
    SourceSpec("finnhub",       [], 1.1, fetch_finnhub),                         # indbygget noegle
    SourceSpec("alpaca",        ["ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"], 0.4, fetch_alpaca),
    SourceSpec("alpha_vantage", ["ALPHAVANTAGE_API_KEY"], 12.5, fetch_alpha_vantage, warn_over_tickers=25),
    SourceSpec("marketaux",     ["MARKETAUX_API_KEY"], 1.0, fetch_marketaux, warn_over_tickers=100),
    SourceSpec("fmp",           ["FMP_API_KEY"], 0.3, fetch_fmp, warn_over_tickers=250),
    SourceSpec("yfinance",      [], 0.7, fetch_yfinance, needs_pkg="yfinance"),
    SourceSpec("sec_8k",        [], 0.15, fetch_sec_8k),
]


def _source_status(spec: SourceSpec) -> tuple[bool, str]:
    """(aktiv, aarsag-hvis-sprunget-over)."""
    if spec.needs_pkg:
        try:
            __import__(spec.needs_pkg)
        except ImportError:
            return False, f"pakke '{spec.needs_pkg}' ikke installeret"
    missing = [e for e in spec.required_env if not os.environ.get(e)]
    if missing:
        return False, "mangler " + ", ".join(missing)
    return True, ""


# ═══════════════════════════════════════════════════════════════════
# Maal pr. (kilde, ticker) + aggregat pr. kilde
# ═══════════════════════════════════════════════════════════════════
def _rec_sentiment(r: NewsRecord) -> str:
    return r.sentiment or _guess_sentiment(r.headline) or "neutral"


def _is_catalyst(r: NewsRecord) -> bool:
    low = r.headline.lower()
    return any(kw in low for kw in CATALYST_KW)


def _is_recap(r: NewsRecord) -> bool:
    low = r.headline.lower()
    return any(kw in low for kw in RECAP_KW)


@dataclass
class CellMetrics:
    covered: bool = False
    n_articles: int = 0
    newest_age_h: Optional[float] = None
    fresh_le_20h: bool = False
    best_sentiment: Optional[str] = None
    any_catalyst: bool = False
    recap_only: bool = False
    newest_headline: str = ""
    newest_url: str = ""


def _cell_metrics(recs: list[NewsRecord], now: datetime) -> CellMetrics:
    m = CellMetrics()
    if not recs:
        return m
    recs = sorted(recs, key=lambda r: r.ts_utc, reverse=True)
    m.covered = True
    m.n_articles = len(recs)
    newest = recs[0]
    m.newest_age_h = (now - newest.ts_utc).total_seconds() / 3600.0
    m.newest_headline = newest.headline
    m.newest_url = newest.url
    m.fresh_le_20h = any((now - r.ts_utc).total_seconds() / 3600.0 <= NEWS_MAX_AGE_HOURS for r in recs)
    sents = {_rec_sentiment(r) for r in recs}
    m.best_sentiment = "bullish" if "bullish" in sents else "bearish" if "bearish" in sents else "neutral"
    m.any_catalyst = any(_is_catalyst(r) for r in recs)
    m.recap_only = all(_is_recap(r) for r in recs)
    return m


@dataclass
class SourceAgg:
    name: str
    n_tickers: int = 0
    coverage_pct: float = 0.0
    fresh_coverage_pct: float = 0.0
    catalyst_hit_pct: float = 0.0
    recap_noise_pct: float = 0.0
    median_newest_age_h: Optional[float] = None
    mean_articles: float = 0.0


def _aggregate(name: str, cells: list[CellMetrics]) -> SourceAgg:
    n = len(cells)
    agg = SourceAgg(name=name, n_tickers=n)
    if n == 0:
        return agg
    cov = [c for c in cells if c.covered]
    agg.coverage_pct = 100.0 * len(cov) / n
    agg.fresh_coverage_pct = 100.0 * sum(1 for c in cells if c.fresh_le_20h) / n
    agg.catalyst_hit_pct = 100.0 * sum(1 for c in cells if c.any_catalyst) / n
    agg.recap_noise_pct = 100.0 * sum(1 for c in cells if c.covered and c.recap_only) / n
    ages = [c.newest_age_h for c in cov if c.newest_age_h is not None]
    agg.median_newest_age_h = statistics.median(ages) if ages else None
    agg.mean_articles = sum(c.n_articles for c in cells) / n
    return agg


# ═══════════════════════════════════════════════════════════════════
# Harness
# ═══════════════════════════════════════════════════════════════════
async def _run_source(session, spec, tickers, start, end, results):
    by_ticker = {}
    for i, t in enumerate(tickers):
        if i:
            await asyncio.sleep(spec.interval)
        try:
            recs = await spec.fetch(session, t, start, end)
        except Exception as e:
            _FAILURES.append((spec.name, t, f"{type(e).__name__}: {e}"[:120]))
            recs = []
        recs = [r for r in recs if r.ts_utc and start <= r.ts_utc <= end]   # CENTRALT vinduesfilter
        by_ticker[t] = recs
    results[spec.name] = by_ticker
    covered = sum(1 for v in by_ticker.values() if v)
    print(f"   [{spec.name:14s}] faerdig — {covered}/{len(tickers)} tickers daekket")


async def run_benchmark(active, tickers, start, end):
    results: dict[str, dict] = {}
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await asyncio.gather(*(_run_source(session, s, tickers, start, end, results)
                               for s in active))
    return results


# ═══════════════════════════════════════════════════════════════════
# Univers
# ═══════════════════════════════════════════════════════════════════
def resolve_universe(args) -> tuple[list[str], dict[str, Optional[float]], str]:
    """(tickers, change_pct_pr_ticker, kilde-beskrivelse)."""
    if args.tickers:
        tl = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        return tl, {t: None for t in tl}, "manuel liste (--tickers)"
    if args.file:
        lines = Path(args.file).read_text(encoding="utf-8").splitlines()
        tl = [ln.strip().upper() for ln in lines if ln.strip() and not ln.startswith("#")]
        return tl, {t: None for t in tl}, f"fil ({args.file})"
    # Default: dagens TV top-gainers (SAMME univers som TJL)
    from strategies.shared.tv_scanner import fetch_tv_top_gainers
    print("→ Henter dagens TV top-% gainers (samme univers som TJL)...")
    gainers = fetch_tv_top_gainers(
        top_n=args.top_n, price_min=UNIVERSE_PRICE_MIN, price_max=UNIVERSE_PRICE_MAX,
        min_volume=UNIVERSE_MIN_VOLUME, require_all_green=REQUIRE_ALL_GREEN)
    tl = [g[0].upper() for g in gainers]
    change = {g[0].upper(): g[2] for g in gainers}
    return tl, change, "TV top-% gainers"


# ═══════════════════════════════════════════════════════════════════
# Rapport (rigtige aeoeaa — bygget til at kopiere ind i en chat)
# ═══════════════════════════════════════════════════════════════════
def _da(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def write_report(path, *, now, window_h, tickers, change, active_names, skipped,
                 results, cells, aggs):
    L = []
    L.append(f"# Nyhedskilde-benchmark — {now.astimezone().strftime('%Y-%m-%d %H:%M')} "
             f"(vindue {window_h}t, frisk-gate {NEWS_MAX_AGE_HOURS}t)\n")
    L.append(f"**Univers:** {len(tickers)} tickers")
    L.append(f"**Aktive kilder:** {', '.join(active_names) or '(ingen)'}")
    if skipped:
        L.append("**Sprunget over:** " + ", ".join(f"{n} ({why})" for n, why in skipped))
    if _FAILURES:
        L.append(f"**Fejlede kald:** {len(_FAILURES)} (se konsol-output)")
    L.append("")

    # ── Per-kilde resumé ──
    L.append("## Per-kilde resumé\n")
    L.append("| kilde | daekning% | frisk%≤20t | katalysator% | recap-støj% | median alder | snit art |")
    L.append("|---|---|---|---|---|---|---|")
    for a in aggs:
        med = _da(a.median_newest_age_h) + "t" if a.median_newest_age_h is not None else "—"
        L.append(f"| {a.name} | {_da(a.coverage_pct,0)} | {_da(a.fresh_coverage_pct,0)} | "
                 f"{_da(a.catalyst_hit_pct,0)} | {_da(a.recap_noise_pct,0)} | {med} | "
                 f"{_da(a.mean_articles)} |")
    L.append("")
    L.append("> daekning% = andel tickers med ≥1 nyhed i vinduet · frisk%≤20t = andel med ≥1 "
             "nyhed nyere end TJL's gate · katalysator% = andel med ≥1 katalysator-flagget "
             "overskrift · recap-støj% = dækket men kun 'top gainers'-agtige overskrifter.\n")

    # ── Per-ticker ──
    L.append("## Per-ticker\n")
    for t in tickers:
        ch = change.get(t)
        ch_s = f" ({_da(ch)}%)" if isinstance(ch, (int, float)) else ""
        L.append(f"### {t}{ch_s}")
        for name in active_names:
            m = cells[name][t]
            if not m.covered:
                L.append(f"- {name}: 0 art  ← TOM")
                continue
            age = _da(m.newest_age_h) + "t" if m.newest_age_h is not None else "—"
            fresh = "FRISK" if m.fresh_le_20h else f"uden for {NEWS_MAX_AGE_HOURS}t"
            tags = [f"{m.n_articles} art", f"nyeste {age}", fresh, m.best_sentiment or "—"]
            if m.any_catalyst:
                tags.append("KATALYSATOR")
            if m.recap_only:
                tags.append("kun recap")
            head = m.newest_headline.replace("\n", " ").strip()
            if len(head) > 110:
                head = head[:107] + "..."
            L.append(f"- {name}: " + " · ".join(tags) + f' · "{head}"')
        L.append("")

    path.write_text("\n".join(L), encoding="utf-8")


def write_summary_csv(path, aggs):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "n_tickers", "coverage_pct", "fresh_coverage_pct",
                    "catalyst_hit_pct", "recap_noise_pct", "median_newest_age_h", "mean_articles"])
        for a in aggs:
            w.writerow([a.name, a.n_tickers, round(a.coverage_pct, 1),
                        round(a.fresh_coverage_pct, 1), round(a.catalyst_hit_pct, 1),
                        round(a.recap_noise_pct, 1),
                        round(a.median_newest_age_h, 1) if a.median_newest_age_h is not None else "",
                        round(a.mean_articles, 2)])


def write_raw_csv(path, now, results, active_names, tickers):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "ticker", "headline", "ts_utc", "age_h",
                    "sentiment", "fresh_le_20h", "is_catalyst", "is_recap", "url"])
        for name in active_names:
            for t in tickers:
                for r in results[name].get(t, []):
                    age = (now - r.ts_utc).total_seconds() / 3600.0
                    w.writerow([name, t, r.headline, r.ts_utc.isoformat(), round(age, 1),
                                _rec_sentiment(r), int(age <= NEWS_MAX_AGE_HOURS),
                                int(_is_catalyst(r)), int(_is_recap(r)), r.url])


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="Empirisk benchmark af small-cap nyhedskilder for Trend Join Long.")
    ap.add_argument("--tickers", help="komma-liste, fx SDOT,FUBO,AAPL (override af TV-univers)")
    ap.add_argument("--file", help="fil med en ticker pr. linje (override af TV-univers)")
    ap.add_argument("--window-hours", type=int, default=48, dest="window_hours",
                    help="kig-vindue tilbage i timer (default 48)")
    ap.add_argument("--top-n", type=int, default=UNIVERSE_TOP_N, dest="top_n",
                    help="antal TV top-gainers naar intet override er givet (default 25)")
    ap.add_argument("--sources", help="komma-liste der begraenser kilder, fx alpaca,finnhub,fmp")
    args = ap.parse_args()

    now = _now_utc()
    start = now - timedelta(hours=args.window_hours)

    print("=" * 78)
    print("  NEWS SOURCE BENCH — small-cap nyhedskilder for Trend Join Long  ·  READ-ONLY")
    print("=" * 78)

    tickers, change, uni_desc = resolve_universe(args)
    if not tickers:
        print("⚠ Tomt univers — ingen tickers at benchmarke. Stop.")
        return 1
    print(f"→ Univers: {len(tickers)} tickers ({uni_desc}): {', '.join(tickers)}")

    # Aktive vs sprunget-over kilder
    only = {s.strip().lower() for s in args.sources.split(",")} if args.sources else None
    active, skipped = [], []
    for spec in SOURCES:
        if only is not None and spec.name not in only:
            continue
        ok, why = _source_status(spec)
        if ok:
            active.append(spec)
        else:
            skipped.append((spec.name, why))

    print(f"→ Aktive kilder: {', '.join(s.name for s in active) or '(ingen)'}")
    if skipped:
        print("→ Sprunget over: " + ", ".join(f"{n} ({why})" for n, why in skipped))

    # AV-advarsel (25/dag)
    for spec in active:
        if spec.warn_over_tickers and len(tickers) > spec.warn_over_tickers:
            print(f"⚠ {spec.name}: {len(tickers)} tickers > gratis-grænse "
                  f"({spec.warn_over_tickers}/dag) — kald kan blive afvist. Overvej "
                  f"at koere {spec.name} separat paa et mindre saet.")

    if not active:
        print("⚠ Ingen aktive kilder (mangler noegler?). Stop.")
        return 1

    print(f"→ Henter nyheder (vindue {args.window_hours}t, {start.strftime('%Y-%m-%d %H:%M')}"
          f"–{now.strftime('%H:%M')} UTC)...")
    results = asyncio.run(run_benchmark(active, tickers, start, now))

    # Maal
    active_names = [s.name for s in active]
    cells: dict[str, dict[str, CellMetrics]] = {}
    aggs = []
    for name in active_names:
        cells[name] = {t: _cell_metrics(results[name].get(t, []), now) for t in tickers}
        aggs.append(_aggregate(name, [cells[name][t] for t in tickers]))
    aggs.sort(key=lambda a: a.fresh_coverage_pct, reverse=True)   # bedst frisk-daekning oeverst

    # Output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = OUTPUT_DIR / f"news_bench_{stamp}.md"
    sm = OUTPUT_DIR / f"news_bench_{stamp}_summary.csv"
    rw = OUTPUT_DIR / f"news_bench_{stamp}_raw.csv"
    write_report(md, now=now, window_h=args.window_hours, tickers=tickers, change=change,
                 active_names=active_names, skipped=skipped, results=results, cells=cells, aggs=aggs)
    write_summary_csv(sm, aggs)
    write_raw_csv(rw, now, results, active_names, tickers)

    # Konsol-resumé
    print("\n" + "=" * 78)
    print("  RESUMÉ (sorteret efter frisk-daekning ≤20t)")
    print("=" * 78)
    print(f"  {'kilde':15s} {'daekn%':>7s} {'frisk%':>7s} {'katal%':>7s} {'recap%':>7s} {'medAlder':>9s} {'snitArt':>8s}")
    for a in aggs:
        med = f"{a.median_newest_age_h:.1f}t" if a.median_newest_age_h is not None else "—"
        print(f"  {a.name:15s} {a.coverage_pct:6.0f}% {a.fresh_coverage_pct:6.0f}% "
              f"{a.catalyst_hit_pct:6.0f}% {a.recap_noise_pct:6.0f}% {med:>9s} {a.mean_articles:8.1f}")
    if _FAILURES:
        print(f"\n  ⚠ {len(_FAILURES)} fejlede kald:")
        for src, tic, err in _FAILURES[:20]:
            print(f"     {src:14s} {tic:8s} {err}")
        if len(_FAILURES) > 20:
            print(f"     ... (+{len(_FAILURES) - 20} flere)")

    print(f"\n  📄 Rapport:  {md}")
    print(f"  📊 Summary:  {sm}")
    print(f"  📊 Raw:      {rw}")
    print("\n  → Kopiér .md-rapporten ind i en chat for faelles kvalitetsvurdering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

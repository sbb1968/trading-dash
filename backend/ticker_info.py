"""
ticker_info.py
──────────────
Henter og cacher firmanavne for tickers via Finnhub.

Strategi:
    1. Cache i hukommelse — vi kalder kun Finnhub første gang per ticker
    2. "Ukendt" caches også, så vi ikke prøver igen og igen for nye/ugyldige tickers
    3. Persistent disk-cache så navnene overlever uvicorn-genstart

Smart forkortelse:
    "NVIDIA Corporation"          → "NVIDIA"
    "Apple Inc."                  → "Apple"
    "Palantir Technologies Inc."  → "Palantir Technologies"
    "Microsoft Corporation"       → "Microsoft"
    "GameStop Corp."              → "GameStop"
"""

import json
import logging
import re
from pathlib import Path
import aiohttp
import asyncio

logger = logging.getLogger(__name__)

# Genbrug Finnhub-nøglen fra finnhub_news
from finnhub_news import FINNHUB_API_KEY, FINNHUB_BASE

# Persistent cache-fil
CACHE_FILE = Path(__file__).parent / "data" / "ticker_names_cache.json"

# Memory cache: ticker -> name (eller "" hvis ukendt)
_cache: dict[str, str] = {}
_cache_loaded = False
_lock = asyncio.Lock()


# ── Cache-håndtering ──────────────────────────────────────────

def _load_cache():
    """Indlæs cache fra disk én gang."""
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    try:
        if CACHE_FILE.exists():
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            logger.info(f"[TickerInfo] Indlæst {len(_cache)} tickers fra cache")
    except Exception as e:
        logger.warning(f"[TickerInfo] Kunne ikke indlæse cache: {e}")
        _cache = {}
    _cache_loaded = True


def _save_cache():
    """Gem cache til disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[TickerInfo] Kunne ikke gemme cache: {e}")


# ── Smart forkortelse ─────────────────────────────────────────

# Suffixes vi fjerner (case-insensitive). Rækkefølge betyder noget —
# længere først så "Corporation" matches før "Corp".
_SUFFIXES = [
    r",?\s+Corporation\.?$",
    r",?\s+Incorporated\.?$",
    r",?\s+Limited\.?$",
    r",?\s+Company\.?$",
    r",?\s+Holdings?\.?$",
    r",?\s+Group\.?$",
    r",?\s+Corp\.?$",
    r",?\s+Inc\.?$",
    r",?\s+Co\.?$",
    r",?\s+Ltd\.?$",
    r",?\s+LLC\.?$",
    r",?\s+PLC\.?$",
    r",?\s+SA\.?$",
    r",?\s+AG\.?$",
    r",?\s+NV\.?$",
]


def _smart_shorten(name: str, ticker: str = "") -> str:
    """
    Fjern juridiske suffixes så navnet bliver mere læseligt.

    Strip ÉT suffix ad gangen, så vi kan stoppe FØR navnet kollapser til bare
    tickeren: "MARA Holdings, Inc." må blive "MARA Holdings" — IKKE "MARA"
    (det gav "gentagelse af tickeren"). "XP Inc." / "IREN Limited" beholdes
    helt, fordi et strip ville efterlade præcis tickeren.
    """
    if not name:
        return ""

    tk = (ticker or "").upper().strip()
    current = name.strip()
    # Efterstillet "(The)" ("Goldman Sachs Group, Inc. (The)") flyttes/fjernes FØR
    # suffiks-strip, ellers blokerer den for at ", Inc." kan fjernes.
    current = re.sub(r"\s*\(the\)\s*$", "", current, flags=re.IGNORECASE).strip()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            stripped = re.sub(suffix, "", current, flags=re.IGNORECASE).strip()
            if not stripped or stripped == current:
                continue
            if tk and stripped.upper() == tk:
                continue   # dette strip ville efterlade bare tickeren → behold suffikset
            current = stripped
            changed = True
            break

    # Dinglende konjunktion efter suffiks-strip: "Eli Lilly and Company" → "Eli Lilly and"
    # → "Eli Lilly". Kun hvis det ikke kollapser til tickeren.
    trimmed = re.sub(r"\s+(and|&)\s*$", "", current, flags=re.IGNORECASE).strip()
    if trimmed and not (tk and trimmed.upper() == tk):
        current = trimmed

    result = current or name.strip()
    return name.strip() if (tk and result.upper() == tk) else result


# ── Ikke-aktie-instrumenter (futures) ─────────────────────────
# Finnhub har ingen firmaprofil for futures — de ville altid ende som tomt navn.
# Giv dem et læseligt instrument-navn i stedet for en tom celle. Slås op FØR
# netværk, så de aldrig belaster Finnhub/yfinance eller cachen.
_INSTRUMENT_NAMES: dict[str, str] = {
    "MES": "Micro E-mini S&P 500",
    "M2K": "Micro E-mini Russell 2000",
    "MNQ": "Micro E-mini Nasdaq-100",
    "MYM": "Micro E-mini Dow",
    "MGC": "Micro Gold",
    "ES":  "E-mini S&P 500",
    "NQ":  "E-mini Nasdaq-100",
    "RTY": "E-mini Russell 2000",
    "YM":  "E-mini Dow",
}


# ── Kilder (returnerer RÅ navne; forkortelse sker centralt i _resolve_name) ──
#
# Prioritet: TradingView (primær) → Finnhub → yfinance. TradingView er den vi
# allerede har fri, uautentificeret adgang til (samme screener som universet),
# den har rene firmanavne med korrekt kapitalisering (fx "CoStar", "TeraWulf")
# og kan slå op uden per-ticker rate-limit — modsat Finnhubs gratis-tier der
# gav de tilfældige huller. Finnhub/yfinance beholdes kun som dybe fallbacks.

def _fetch_from_tradingview_blocking(ticker: str) -> str:
    """Rå firmanavn fra TradingViews screener (`description`-feltet). "" hvis intet."""
    try:
        from tradingview_screener import Query, col
        _, df = (
            Query()
            .select('name', 'description', 'type')
            .where(col('name') == ticker)
            .limit(10)
            .get_scanner_data()
        )
    except Exception:
        return ""
    if df is None or df.empty:
        return ""
    fallback = ""
    for _, row in df.iterrows():
        if str(row.get('name', '')).upper() != ticker:
            continue
        desc = str(row.get('description') or '').strip()
        if not desc:
            continue
        # Foretræk en almindelig aktie/dr/fond med præcist symbol-match; ellers
        # tag første ikke-tomme beskrivelse (fx futures/andet).
        if str(row.get('type') or '') in ('stock', 'dr', 'fund'):
            return desc
        fallback = fallback or desc
    return fallback


async def _fetch_from_tradingview(ticker: str) -> str:
    """Async-wrapper: kør det blokerende TV-opslag i en executor m. timeout."""
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_from_tradingview_blocking, ticker),
            timeout=10.0,
        )
    except Exception:
        return ""


async def _fetch_from_finnhub(ticker: str) -> tuple[str, bool]:
    """Rå firma-navn fra Finnhub.

    Returnerer (navn, ok):
      ok=True  → Finnhub SVAREDE (HTTP 200 + gyldig JSON). Et tomt navn er så
                 pålideligt ("Finnhub har ingen profil").
      ok=False → TRANSIENT fejl (timeout, 429 opbrugt, non-200, netværk).

    429 (rate-limit) retries med kort backoff.
    """
    url = f"{FINNHUB_BASE}/stock/profile2"
    params = {"symbol": ticker, "token": FINNHUB_API_KEY}

    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 429:
                        logger.info(f"[TickerInfo] {ticker}: 429 rate-limit "
                                    f"(forsøg {attempt+1}/3) — backoff")
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    if resp.status != 200:
                        logger.warning(f"[TickerInfo] {ticker}: HTTP {resp.status}")
                        return "", False
                    data = await resp.json()
        except asyncio.TimeoutError:
            logger.warning(f"[TickerInfo] {ticker}: timeout")
            return "", False
        except Exception as e:
            logger.warning(f"[TickerInfo] {ticker}: {type(e).__name__}: {e}")
            return "", False

        if not isinstance(data, dict) or not data:
            return "", True   # Finnhub svarede, men ingen profil → bekræftet tomt
        return (data.get("name") or "").strip(), True

    return "", False   # opbrugte 429-retries → transient


def _fetch_from_yfinance_blocking(ticker: str) -> str:
    """Rå firma-navn fra yfinance (blokerende). "" hvis intet."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return ""
    return (info.get("shortName") or info.get("longName") or "").strip()


async def _fetch_from_yfinance(ticker: str) -> str:
    """Async-wrapper: kør det blokerende yfinance-opslag i en executor m. timeout."""
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_from_yfinance_blocking, ticker),
            timeout=12.0,
        )
    except Exception:
        return ""


async def _resolve_name(ticker: str) -> tuple[str, bool]:
    """Slå navnet op på tværs af kilder (TradingView → Finnhub → yfinance).
    Returnerer (navn, confirmed):
      confirmed=True  → mindst én kilde svarede → et tomt navn er pålideligt.
      confirmed=False → kun transiente fejl → kalderen må IKKE cache "" endeligt.
    Navnet forkortes centralt her (med ticker-bevidsthed, så det aldrig
    kollapser til bare tickeren).
    """
    if ticker in _INSTRUMENT_NAMES:
        return _INSTRUMENT_NAMES[ticker], True

    # 1) TradingView — primær.
    tv = await _fetch_from_tradingview(ticker)
    if tv:
        return _smart_shorten(tv, ticker), True

    # 2) Finnhub.
    name, ok = await _fetch_from_finnhub(ticker)
    if name:
        return _smart_shorten(name, ticker), True

    # 3) yfinance.
    fb = await _fetch_from_yfinance(ticker)
    if fb:
        return _smart_shorten(fb, ticker), True

    # Intet navn. "" er kun 'bekræftet' hvis Finnhub faktisk svarede (ok=True).
    return "", ok


# ── Public API ────────────────────────────────────────────────

# Svage cache-værdier vi allerede har genforsøgt i DENNE proces. Et "svagt" navn er
# tomt ("") ELLER lig selve tickeren (fx gammel cache MARA→"MARA"). Uden dette sæt
# ville hvert svagt navn blive slået op ved hvert kald. MED det får hvert svagt navn
# præcis ét genforsøg pr. backend-kørsel — nok til at selv-heale forgiftede tomme
# (HIMS/KHC) OG ticker-gentagelser (MARA/XP/IREN) uden at spamme kilderne.
_reresolved: set[str] = set()


def _is_good_name(ticker: str, val) -> bool:
    """Et navn er 'godt' hvis det er ikke-tomt OG ikke bare er tickeren selv."""
    return bool(val) and val.strip().upper() != ticker.upper()


async def get_ticker_name(ticker: str) -> str:
    """
    Returnér det forkortede firmanavn for én ticker.

    Robust cache-semantik:
      - Godt navn i cache (≠ ticker)  → returnér det.
      - Svagt navn ("" eller = ticker) → genforsøg ÉN gang pr. proces (selv-heal).
      - Kilde bekræfter 'intet bedre'  → cache resultatet (spammer ikke igen).
      - KUN transiente fejl            → cache IKKE endeligt → næste kald prøver igen.

    Returnerer "" hvis navnet ikke kan findes — frontend viser så intet.
    """
    if not ticker:
        return ""

    ticker = ticker.upper().strip()
    _load_cache()

    cached = _cache.get(ticker)
    if _is_good_name(ticker, cached):
        # Kør den (evt. forbedrede) forkortelse igen på cache-hittet, så gamle
        # skævheder ("... (The)", dinglende "and") self-healer uden gen-hentning.
        return _smart_shorten(cached, ticker)
    if ticker in _reresolved:          # svagt, men allerede genforsøgt denne proces
        return cached or ""

    async with _lock:
        # Re-check efter lock — en anden coroutine kan have hentet imens.
        cached = _cache.get(ticker)
        if _is_good_name(ticker, cached):
            return _smart_shorten(cached, ticker)
        if ticker in _reresolved:
            return cached or ""

        _reresolved.add(ticker)   # præcis ét genforsøg pr. proces uanset udfald
        name, confirmed = await _resolve_name(ticker)
        if _is_good_name(ticker, name):
            _cache[ticker] = name
            _save_cache()
            return name

        # Fandt intet bedre end det (svage) vi evt. havde. Cache kun et BEKRÆFTET
        # resultat (en kilde svarede) — så en transient fejl ikke fryser et tomt navn.
        if confirmed and cached is None:
            _cache[ticker] = name or ""
            _save_cache()
        return cached or name or ""


async def get_ticker_names(tickers: list[str]) -> dict[str, str]:
    """
    Returnér navne for flere tickers på én gang. Deler den robuste enkelt-opslags-
    logik (get_ticker_name), så cache-semantik, fallback og selv-heal er ens.
    """
    if not tickers:
        return {}
    result: dict[str, str] = {}
    for t in tickers:
        t = t.upper().strip()
        if t and t not in result:
            result[t] = await get_ticker_name(t)
    return result


# ── Selvtest ──────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def test():
        test_tickers = ["NVDA", "AAPL", "TSLA", "PLTR", "GME", "QCLS", "FAKE123"]
        print(f"Henter navne for: {test_tickers}\n")
        names = await get_ticker_names(test_tickers)
        for ticker, name in names.items():
            display = name if name else "(ukendt)"
            print(f"  {ticker:8s} → {display}")

    asyncio.run(test())

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


def _smart_shorten(name: str) -> str:
    """
    Fjern juridiske suffixes så navnet bliver mere læseligt.

    Loop'er fordi nogle navne har flere lag: "Microsoft Corporation Holdings".
    Stopper når der ikke er flere ændringer at lave.
    """
    if not name:
        return ""

    prev = ""
    current = name.strip()
    while prev != current:
        prev = current
        for suffix in _SUFFIXES:
            current = re.sub(suffix, "", current, flags=re.IGNORECASE).strip()

    return current or name  # fall back til oprindelig hvis vi strippede alt


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


# ── Hent fra Finnhub ──────────────────────────────────────────

async def _fetch_from_finnhub(ticker: str) -> tuple[str, bool]:
    """Hent firma-navn fra Finnhub.

    Returnerer (navn, ok):
      ok=True  → Finnhub SVAREDE (HTTP 200 + gyldig JSON). Et tomt navn er så
                 pålideligt ("Finnhub har ingen profil"), og må trygt caches "".
      ok=False → TRANSIENT fejl (timeout, 429 opbrugt, non-200, netværk). Navnet
                 er ukendt, IKKE bekræftet tomt → kalderen må IKKE cache "".

    429 (rate-limit) — den hyppigste årsag til de tilfældige tomme navne når
    frontenden fyrer mange opslag på én gang — retries med kort backoff.
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
        return _smart_shorten((data.get("name") or "").strip()), True

    return "", False   # opbrugte 429-retries → transient


def _fetch_from_yfinance_blocking(ticker: str) -> str:
    """Firma-navn fra yfinance (blokerende). Fallback når Finnhub ikke har navnet —
    yfinance dækker en del navne Finnhubs gratis-tier mangler. "" hvis intet."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return ""
    name = (info.get("shortName") or info.get("longName") or "").strip()
    return _smart_shorten(name)


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
    """Slå navnet op på tværs af kilder. Returnerer (navn, confirmed):
      confirmed=True  → mindst én kilde svarede → et tomt navn er pålideligt (cache "").
      confirmed=False → kun transiente fejl → cache IKKE "" (prøv igen senere).
    """
    if ticker in _INSTRUMENT_NAMES:
        return _INSTRUMENT_NAMES[ticker], True

    name, ok = await _fetch_from_finnhub(ticker)
    if name:
        return name, True

    # Finnhub gav intet navn (bekræftet tomt ELLER transient) — prøv yfinance.
    fb = await _fetch_from_yfinance(ticker)
    if fb:
        return fb, True

    # Stadig intet. "" er kun bekræftet hvis Finnhub faktisk svarede (ok=True).
    return "", ok


# ── Public API ────────────────────────────────────────────────

# Cachede tomme navne ("") vi allerede har genforsøgt i DENNE proces. Uden det ville
# et bekræftet-tomt navn blive slået op ved hvert eneste kald. MED det får hvert tomt
# navn præcis ét genforsøg pr. backend-kørsel — nok til at selv-heale forgiftede tomme
# (fx HIMS/KHC der blev cachet "" pga. en gammel rate-limit) uden at spamme kilderne.
_empty_retried: set[str] = set()


async def get_ticker_name(ticker: str) -> str:
    """
    Returnér det forkortede firmanavn for én ticker.

    Robust cache-semantik:
      - Ikke-tomt navn i cache      → returnér det (permanent).
      - Tomt navn i cache           → genforsøg ÉN gang pr. proces (selv-heal).
      - Kilde bekræfter 'ingen navn' → cache "" (spammer ikke igen).
      - KUN transiente fejl         → cache IKKE "" → næste kald prøver igen.

    Returnerer "" hvis navnet ikke kan findes — frontend viser så intet.
    """
    if not ticker:
        return ""

    ticker = ticker.upper().strip()
    _load_cache()

    cached = _cache.get(ticker)
    if cached:                                    # ikke-tomt → stol på det
        return cached
    if cached == "" and ticker in _empty_retried:  # tomt + allerede genforsøgt → stop
        return ""

    async with _lock:
        # Re-check efter lock — en anden coroutine kan have hentet imens.
        cached = _cache.get(ticker)
        if cached:
            return cached
        if cached == "" and ticker in _empty_retried:
            return ""

        name, confirmed = await _resolve_name(ticker)
        if name:
            _cache[ticker] = name
            _save_cache()
            return name

        # Intet navn fundet.
        if cached == "":
            _empty_retried.add(ticker)   # var allerede tomt → markér genforsøgt
        if confirmed:
            _cache[ticker] = ""          # bekræftet 'ingen profil' → cache tomt
            _empty_retried.add(ticker)
            _save_cache()
        # Ellers (transient, aldrig cachet): cache IKKE → næste kald prøver igen.
        return ""


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

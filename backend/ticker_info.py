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


# ── Hent fra Finnhub ──────────────────────────────────────────

async def _fetch_from_finnhub(ticker: str) -> str:
    """Hent firma-navn fra Finnhub. Returnerer "" hvis ikke fundet."""
    url = f"{FINNHUB_BASE}/stock/profile2"
    params = {"symbol": ticker, "token": FINNHUB_API_KEY}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"[TickerInfo] {ticker}: HTTP {resp.status}")
                    return ""
                data = await resp.json()
    except asyncio.TimeoutError:
        logger.warning(f"[TickerInfo] {ticker}: timeout")
        return ""
    except Exception as e:
        logger.warning(f"[TickerInfo] {ticker}: {type(e).__name__}: {e}")
        return ""

    if not isinstance(data, dict) or not data:
        return ""

    name = data.get("name") or ""
    return _smart_shorten(name.strip())


# ── Public API ────────────────────────────────────────────────

async def get_ticker_name(ticker: str) -> str:
    """
    Returnér det forkortede firmanavn for én ticker.

    Returnerer "" hvis tickeren ikke kan findes — frontend viser så "(ukendt)".
    """
    if not ticker:
        return ""

    ticker = ticker.upper().strip()
    _load_cache()

    if ticker in _cache:
        return _cache[ticker]

    async with _lock:
        # Tjek igen efter lock — en anden coroutine kan have hentet imens
        if ticker in _cache:
            return _cache[ticker]

        name = await _fetch_from_finnhub(ticker)
        _cache[ticker] = name
        _save_cache()
        return name


async def get_ticker_names(tickers: list[str]) -> dict[str, str]:
    """
    Returnér navne for flere tickers på én gang.

    Tickers der allerede er i cache returneres straks.
    Tickers der mangler hentes parallelt fra Finnhub (med 1.1s mellemrum
    for at respektere Finnhub's rate-limit på 60/min).
    """
    if not tickers:
        return {}

    _load_cache()
    result: dict[str, str] = {}
    missing: list[str] = []

    for t in tickers:
        t = t.upper().strip()
        if t in _cache:
            result[t] = _cache[t]
        else:
            missing.append(t)

    if missing:
        logger.info(f"[TickerInfo] Henter {len(missing)} nye tickere fra Finnhub")
        async with _lock:
            for t in missing:
                # Re-check efter lock
                if t in _cache:
                    result[t] = _cache[t]
                    continue
                name = await _fetch_from_finnhub(t)
                _cache[t] = name
                result[t] = name
                await asyncio.sleep(1.1)  # rate-limit
        _save_cache()

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

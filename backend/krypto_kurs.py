"""
krypto_kurs.py — kurser på crypto, hentet fra TradingView
════════════════════════════════════════════════════════════════════════════════
Trading Dash prissætter alt gennem IBKR. **IBKR har ikke `BINANCE:LINKUSDT`** —
symbolet findes kun på TradingView, og Binance-spot handles slet ikke gennem
IBKR. Uden en anden kilde kan tickeren ikke stå i watchlisten.

TradingViews rå scanner-endpoint svarer uden abonnement og uden nøgle, og det er
allerede den kilde `sector_niche.py` bruger til ETF'er. Samme mønster, andet
marked: `/crypto/scan` i stedet for `/america/scan`.

⚠ SYMBOLET SKAL VÆRE DET SAMME SOM I SCREENEREN. `BINANCE:LINKUSDT` er spot på
Binance med USDT som modvaluta — samme symbol CEX-screeneren returnerer, så
niveauer og volumen kan sammenlignes på tværs af de to værktøjer.

    Undgå `CRYPTO:LINKUSD`   — beregnet gennemsnit på tværs af børser, ingen orderbog
    `BINANCE:LINKUSDT.P`     — perpetual. Relevant senere, men funding rate og
                               likvidationspris er nye variable der ikke hører
                               hjemme i en øvefase

⚠ OG DER HANDLES IKKE HERFRA. Modulet leverer kurser, intet andet. Crypto handles
manuelt (TradingView paper eller børs-demo); ordrevejen afviser symbolerne
eksplicit — se `er_krypto` i main.py's ordre-håndtering.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

KOLONNER = ["name", "close", "change", "volume", "high", "low", "open", "description"]
TIMEOUT = 12

# ⚠ EKSPLICIT LISTE, IKKE "indeholder et kolon". `BATS:AAPL` har ogsaa et kolon
# og er ikke crypto — og en regel der matcher bredere end sin paastand, svarer paa
# noget andet end den siger. Skal en boers med, skrives den her.
#
# ⚠ OG DE LIGGER IKKE PAA SAMME ENDPOINT. Boers-parrene (BINANCE:LINKUSDT) svarer
# paa /crypto/scan; TradingViews beregnede maal (CRYPTOCAP:BTC.D, TOTAL3) goer
# ikke — de ligger paa /global/scan. Maalt 11-08: /crypto/scan returnerede NUL
# raekker for BTC.D. Stod de paa samme liste med ét endpoint, ville de tavst
# mangle, og en hvidliste der lover mere end den leverer, er en fejl i sig selv.
TV_ENDPOINT = {
    "krypto": "https://scanner.tradingview.com/crypto/scan",
    "maal":   "https://scanner.tradingview.com/global/scan",
}

BOERS_PAR = {
    "BINANCE", "COINBASE", "KRAKEN", "BITSTAMP", "BYBIT", "OKX", "KUCOIN",
    "BITFINEX", "GEMINI", "HUOBI", "MEXC", "BITGET", "CRYPTO",
}
# Beregnede maal: dominans, samlet markedsvaerdi. Bruges af cockpittets
# crypto-kontekstboks (BTC.D, TOTAL3).
BEREGNEDE = {"CRYPTOCAP"}

KRYPTO_BOERSER = BOERS_PAR | BEREGNEDE

# Kort cache, saa flere kaldere i samme sekund kun giver ét kald udad.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_SEK = 2.0
_laas = asyncio.Lock()


def er_krypto(ticker: str) -> bool:
    """Er dette et crypto-symbol vi selv skal hente?"""
    t = (ticker or "").strip().upper()
    if ":" not in t:
        return False
    return t.split(":", 1)[0] in KRYPTO_BOERSER


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def hent(tickers: list[str]) -> dict[str, dict]:
    """{TICKER: {price, change_percent, volume, high, low, open, navn}}

    ⚠ Ét kald for hele listen. TradingViews endpoint tager mange symboler ad
    gangen, og en løkke med ét kald pr. ticker ville både være langsommere og
    ligne noget der skal rate-limites.

    Symboler der ikke findes, udelades — de kommer ikke tilbage som 0. En kurs
    vi ikke har, er ikke nul.
    """
    onskede = [t.strip().upper() for t in tickers if er_krypto(t)]
    if not onskede:
        return {}

    nu = time.monotonic()
    ud: dict[str, dict] = {}
    mangler = []
    for t in onskede:
        c = _CACHE.get(t)
        if c and nu - c[0] < _CACHE_SEK:
            ud[t] = c[1]
        else:
            mangler.append(t)
    if not mangler:
        return ud

    # Grupper efter hvilket endpoint symbolet bor paa — ét kald pr. gruppe.
    grupper: dict[str, list[str]] = {}
    for t in mangler:
        n = "maal" if t.split(":", 1)[0] in BEREGNEDE else "krypto"
        grupper.setdefault(n, []).append(t)

    raekker: list[dict] = []
    async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as s:
        async def _kald(navn: str, symboler: list[str]):
            payload = {"symbols": {"tickers": symboler, "query": {"types": []}},
                       "columns": KOLONNER}
            try:
                async with s.post(TV_ENDPOINT[navn], json=payload,
                                  headers={"User-Agent": "Mozilla/5.0"}) as r:
                    if r.status != 200:
                        logger.warning(f"[Krypto] {navn}: TradingView svarede "
                                       f"HTTP {r.status}")
                        return []
                    return ((await r.json()) or {}).get("data") or []
            except Exception as e:
                logger.warning(f"[Krypto] {navn}: TradingView svarer ikke "
                               f"({type(e).__name__}: {e})")
                return []

        for svar in await asyncio.gather(
                *(_kald(n, sym) for n, sym in grupper.items())):
            raekker.extend(svar)

    async with _laas:
        for raekke in raekker:
            symbol = (raekke.get("s") or "").upper()
            d = raekke.get("d") or []
            if not symbol or len(d) < len(KOLONNER):
                continue
            rec = dict(zip(KOLONNER, d))
            pris = _f(rec.get("close"))
            if pris is None:
                continue                      # ingen kurs er ikke nul
            post = {
                "price":          pris,
                "change_percent": _f(rec.get("change")),
                "volume":         _f(rec.get("volume")),
                "high":           _f(rec.get("high")),
                "low":            _f(rec.get("low")),
                "open":           _f(rec.get("open")),
                "navn":           rec.get("description") or "",
            }
            _CACHE[symbol] = (time.monotonic(), post)
            ud[symbol] = post
    return ud


async def hent_en(ticker: str) -> Optional[dict]:
    return (await hent([ticker])).get((ticker or "").strip().upper())

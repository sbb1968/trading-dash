"""
finnhub_news.py
───────────────
Henter nyheder fra Finnhub.io og broadcaster dem via WebSocket til Trading Dash.

Finnhub gratis tier:
    - 60 API-kald per minut
    - Data er typisk 15-30 min forsinket (markeret tydeligt i UI)
    - Dækker primært US-aktier

Polling-strategi (kombineret):
    1. General market news hvert 5. minut — kun nyheder MED tickers vises
    2. Company news for fast univers hvert 5. minut — nyheder per ticker

Sentiment-heuristik:
    Finnhub leverer ingen sentiment for gratis tier — vi gætter ud fra
    overskriftens nøgleord (Ross Cameron style: enkle ordlister).
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable
import aiohttp

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict], Awaitable[None]]

FINNHUB_API_KEY = "d83ctd1r01qjsh1l2n10d83ctd1r01qjsh1l2n1g"
FINNHUB_BASE    = "https://finnhub.io/api/v1"

POLL_INTERVAL_SEC   = 300   # 5 minutter — vi vil ikke ramme rate limit
MAX_NEWS_AGE_HOURS  = 24    # ignorer nyheder ældre end 24 timer

# Tickers vi altid poller company news for
# Disse matcher det univers Trading Dash typisk handler på
COMPANY_TICKERS = [
    "NVDA", "TSLA", "AAPL", "AMD", "MSFT", "META", "AMZN", "GOOGL",
    "PLTR", "SOFI", "RIVN", "LCID", "NIO",
    "GME", "AMC", "CLOV", "MVIS", "OCGN", "TLRY", "SNDL", "BBIG", "SPRT",
]

# Heuristik for sentiment baseret på keywords
BULLISH_WORDS = [
    "beats", "beat", "surge", "surges", "soars", "soared", "rally", "rallies",
    "upgrade", "upgraded", "buy rating", "raises guidance", "record high",
    "outperform", "approval", "approved", "wins", "won", "contract",
    "partnership", "acquires", "acquired", "buyback", "dividend increase",
    "breakthrough", "exceeds", "exceeded", "strong", "growth",
]
BEARISH_WORDS = [
    "miss", "misses", "missed", "plunge", "plunges", "falls", "fell",
    "downgrade", "downgraded", "sell rating", "cuts guidance", "lawsuit",
    "underperform", "rejected", "loses", "lost", "warns", "warning",
    "delays", "delayed", "recall", "fraud", "investigation", "probe",
    "bankruptcy", "loss", "losses", "weak", "decline", "declined",
    "halt", "halted", "subpoena",
]


def _guess_sentiment(headline: str) -> str:
    """Simpel keyword-baseret sentiment. Returnerer bullish/bearish/neutral."""
    lower = headline.lower()
    bull_hits = sum(1 for word in BULLISH_WORDS if word in lower)
    bear_hits = sum(1 for word in BEARISH_WORDS if word in lower)

    if bull_hits > bear_hits:
        return "bullish"
    if bear_hits > bull_hits:
        return "bearish"
    return "neutral"


class FinnhubNewsFeed:
    """Poller Finnhub for både general og company news."""

    def __init__(self, broadcast_fn: BroadcastFn):
        self.broadcast = broadcast_fn
        self._running = False
        self._seen_ids: set[int] = set()
        self._next_news_id = 1

    async def start(self):
        """Start polling-loop. Kører indtil .stop() kaldes."""
        self._running = True
        logger.info("[FinnhubNews] Starter — poller hver %d sek", POLL_INTERVAL_SEC)

        # Lille initial-delay så vi ikke spammer ved opstart
        await asyncio.sleep(5)

        # Kør første poll med det samme så News Room ikke står tomt i 5 min
        try:
            await self._poll_all()
        except Exception as e:
            logger.exception(f"[FinnhubNews] Initial poll-fejl: {e}")

        while self._running:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            try:
                await self._poll_all()
            except Exception as e:
                logger.exception(f"[FinnhubNews] Poll-fejl: {e}")

    async def stop(self):
        self._running = False
        logger.info("[FinnhubNews] Stoppet")

    async def _poll_all(self):
        """Kør både general og company news poll i samme runde."""
        async with aiohttp.ClientSession() as session:
            general_count = await self._poll_general(session)
            company_count = await self._poll_company_news(session)

            total = general_count + company_count
            if total > 0:
                logger.info(
                    f"[FinnhubNews] Broadcastede {total} nye nyheder "
                    f"(general: {general_count}, company: {company_count})"
                )

        # Begræns hukommelses-forbrug
        if len(self._seen_ids) > 1000:
            self._seen_ids = set(list(self._seen_ids)[-1000:])

    async def _poll_general(self, session: aiohttp.ClientSession) -> int:
        """Hent general market news. Filtrer dem uden ticker bort."""
        url = f"{FINNHUB_BASE}/news"
        params = {"category": "general", "token": FINNHUB_API_KEY}

        try:
            # Forlænget timeout fra 10 til 30 sek
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(f"[FinnhubNews] General HTTP {resp.status}")
                    return 0
                items = await resp.json()
        except asyncio.TimeoutError:
            logger.warning("[FinnhubNews] General timeout")
            return 0
        except Exception as e:
            logger.warning(f"[FinnhubNews] General request-fejl: {e}")
            return 0

        if not isinstance(items, list):
            return 0

        cutoff = datetime.now().timestamp() - (MAX_NEWS_AGE_HOURS * 3600)
        new_count = 0

        for item in items:
            finnhub_id = item.get("id")
            if not finnhub_id or finnhub_id in self._seen_ids:
                continue

            if item.get("datetime", 0) < cutoff:
                continue

            # Kun nyheder med en ticker
            related = (item.get("related", "") or "").strip()
            if not related:
                self._seen_ids.add(finnhub_id)  # markér som set så vi ikke prøver igen
                continue

            tickers = [t.strip() for t in related.split(",") if t.strip()]
            if not tickers:
                self._seen_ids.add(finnhub_id)
                continue

            sself._seen_ids.add(finnhub_id)
            await self._broadcast_news(item, tickers[0])
            new_count += 1

        return new_count

    async def _poll_company_news(self, session: aiohttp.ClientSession) -> int:
        """Hent company news for hver ticker i COMPANY_TICKERS."""
        # Hent nyheder fra de seneste 2 dage
        to_date   = datetime.now().date()
        from_date = to_date - timedelta(days=2)

        total_new = 0

        for ticker in COMPANY_TICKERS:
            url = f"{FINNHUB_BASE}/company-news"
            params = {
                "symbol": ticker,
                "from":   from_date.isoformat(),
                "to":     to_date.isoformat(),
                "token":  FINNHUB_API_KEY,
            }

            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[FinnhubNews] {ticker}: HTTP {resp.status}")
                        continue
                    items = await resp.json()
            except asyncio.TimeoutError:
                logger.debug(f"[FinnhubNews] {ticker}: timeout")
                continue
            except Exception as e:
                logger.debug(f"[FinnhubNews] {ticker}: {type(e).__name__}: {e}")
                continue

            if not isinstance(items, list):
                continue

            cutoff = datetime.now().timestamp() - (MAX_NEWS_AGE_HOURS * 3600)

            for item in items:
                finnhub_id = item.get("id")
                if not finnhub_id or finnhub_id in self._seen_ids:
                    continue

                if item.get("datetime", 0) < cutoff:
                    continue

                self._seen_ids.add(finnhub_id)
                await self._broadcast_news(item, ticker)
                total_new += 1

            # Lille pause mellem requests så vi ikke rammer rate-limit
            await asyncio.sleep(1.1)

        return total_new

    async def _broadcast_news(self, item: dict, ticker: str):
        """Konverter ét Finnhub-item til Trading Dash format og broadcast det."""
        headline = (item.get("headline", "") or "").strip()
        if not headline:
            return

        source = item.get("source") or "Finnhub"

        try:
            dt = datetime.fromtimestamp(item.get("datetime", 0))
            time_str = dt.strftime("%H:%M")
        except Exception:
            time_str = datetime.now().strftime("%H:%M")

        news_data = {
            "id":        self._next_news_id,
            "ticker":    ticker,
            "headline":  headline,
            "source":    source,
            "time":      time_str,
            "sentiment": _guess_sentiment(headline),
            "timestamp": datetime.now().isoformat(),
            "delayed":   True,
            "url":       item.get("url", "") or "",
        }
        self._next_news_id += 1

        await self.broadcast({"type": "news", "data": news_data})


# ── Selvtest ──────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def fake_broadcast(msg):
        d = msg["data"]
        print(f"  → {d['ticker']:6s} [{d['sentiment']:7s}] {d['time']} · {d['source'][:15]:15s} · {d['headline'][:80]}")

    async def test():
        feed = FinnhubNewsFeed(fake_broadcast)
        print("Henter general + company news fra Finnhub...")
        await feed._poll_all()
        print(f"\nSet {len(feed._seen_ids)} unikke nyheder")

    asyncio.run(test())

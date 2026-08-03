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
import re
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
# Udvidet 3/8-2026. Den oprindelige liste var skrevet til mega-cap-nyheder
# ("beats", "upgrade", "buyback") og ramte naesten aldrig de katalysatorer der
# faktisk driver de micro/small-cap-gappere Trend Join Long jagter — hvor
# nyheden typisk er en FDA-clearance, en kontrakt-tildeling eller en
# fase-3-aflaesning. "NuWellis Announces FDA Clearance of Aquadex" scorede
# neutralt, fordi "clearance" ikke stod paa listen.
#
# Bearish-siden er udvidet med det micro-caps oftest gapper paa i den DAARLIGE
# retning: kapitaludvidelse. En "public offering"/"pricing of" er praecis den
# slags gap der fader — som er hele begrundelsen for at kraeve en katalysator.
BULLISH_WORDS = [
    # regulatorisk / biotek
    "approval", "approved", "approves", "clearance", "cleared", "clears",
    "breakthrough", "fast track", "orphan drug", "priority review",
    "designation", "granted", "grants", "patent",
    "positive results", "positive data", "positive topline", "topline",
    "primary endpoint", "met endpoint", "successful trial",
    # aftaler / selskabshandlinger
    "acquires", "acquired", "acquisition", "merger", "merges", "takeover",
    "buyout", "tender offer", "partnership", "partners", "collaboration",
    "agreement", "signs", "signed", "secures", "secured",
    "contract", "award", "awards", "awarded", "wins", "selected",
    "investment", "funding", "milestone", "stake",
    # regnskab / guidance
    "beat", "beats", "exceeds", "exceeded", "tops", "surpasses",
    "raises guidance", "raises outlook", "raises forecast",
    "upgrade", "upgraded", "buy rating", "outperform",
    "record high", "record revenue", "record quarter",
    "strong", "growth", "profitable", "profitability",
    # kapitalretur / notering
    "buyback", "repurchase", "dividend increase", "authorization", "authorized",
    "uplisting", "uplists", "index inclusion",
    # produkt / momentum
    "launch", "launches", "unveils", "expands", "expansion",
    "surge", "surges", "soars", "soared", "rally", "rallies", "jumps", "spikes",
]
BEARISH_WORDS = [
    # udvanding — den hyppigste micro-cap-gap uden reelt indhold
    "offering", "pricing of", "priced", "dilution", "dilutive",
    "reverse split", "reverse stock split", "at-the-market", "registered direct",
    "warrants", "shelf registration",
    # regnskab / guidance
    "miss", "misses", "missed", "cuts guidance", "cuts outlook", "lowers guidance",
    "downgrade", "downgraded", "sell rating", "underperform",
    "weak", "decline", "declined", "loss", "losses",
    # juridisk / regulatorisk
    "lawsuit", "class action", "fraud", "investigation", "probe", "subpoena",
    "rejected", "rejection", "complete response letter",
    "delisting", "delisted", "noncompliance", "non-compliance", "deficiency",
    "restatement",
    # drift / selskab
    "resigns", "resignation", "steps down", "terminates", "terminated",
    "discontinues", "discontinued", "withdraws", "withdrawn",
    "bankruptcy", "chapter 11", "going concern",
    "loses", "lost", "warns", "warning", "delays", "delayed", "recall",
    # kursbevaegelse
    "plunge", "plunges", "falls", "fell", "drops", "sinks",
    # ⚠ "halt"/"halted" er BEVIDST UDELADT. De stod paa den gamle bearish-liste,
    # men en LULD-halt ledsager lige saa ofte de STOERSTE opture — praecis dem
    # TJL jagter. Med bull > bear-reglen kunne en enkelt halt-overskrift
    # nulstille en aegte katalysator. De nye afvisningsbeskeder viser bull/bear
    # + overskrifterne, saa det kan afgoeres paa data hvis det bliver aktuelt.
]

# Ordgraenser, ikke raa substring. Den gamle matcher brugte `word in lower`, saa
# "won" ramte "Wonder Group", "beat" ramte "beaten" og "loss" ramte "Glossier".
# Laengste foerst, saa "reverse stock split" vinder over "reverse split".
def _byg(ord_liste):
    return re.compile(
        "|".join(rf"(?<!\w){re.escape(w)}(?!\w)"
                 for w in sorted(ord_liste, key=len, reverse=True)),
        re.IGNORECASE,
    )


_BULL_RE = _byg(BULLISH_WORDS)
_BEAR_RE = _byg(BEARISH_WORDS)


def _guess_sentiment(headline: str) -> str:
    """Keyword-baseret sentiment paa ordgraenser. Returnerer bullish/bearish/neutral.

    Taeller DISTINKTE traeftermer (ikke forekomster), saa en overskrift der
    gentager samme ord ikke vejer tungere end én der rammer flere signaler.
    """
    h = headline or ""
    bull_hits = len({m.lower() for m in _BULL_RE.findall(h)})
    bear_hits = len({m.lower() for m in _BEAR_RE.findall(h)})

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

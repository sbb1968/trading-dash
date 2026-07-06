"""
ibkr_live_feed.py
─────────────────
Streamer ægte markedsdata fra IBKR til alle WebSocket-klienter via
den medfølgende broadcast-funktion.

Universe: top gainers fra IBKR scanner + fallback small caps hvis
scanneren er tom (uden for handelstid eller hvis API'et fejler).

Python 3.14 kompatibel — kun async metoder fra ib_async.
"""

import asyncio
import logging
import math
from datetime import datetime
from typing import Awaitable, Callable

from ib_async import Stock

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict], Awaitable[None]]

FALLBACK_UNIVERSE = [
    "AAPL", "TSLA", "NVDA", "AMD", "MSFT",
    "GME", "AMC", "CLOV", "SKLZ", "MVIS",
    "OCGN", "TLRY", "SNDL", "BBIG", "SPRT",
    "PLTR", "SOFI", "RIVN", "LCID", "NIO",
]

UPDATE_INTERVAL_SEC = 1.0
MAX_UNIVERSE_SIZE   = 25


def _is_valid(x) -> bool:
    """ib_async udfylder tom data med NaN — kassér de værdier."""
    if x is None:
        return False
    try:
        return not math.isnan(x)
    except (TypeError, ValueError):
        return False


class IBKRLiveFeed:
    def __init__(self, ibkr_conn, broadcast_fn: BroadcastFn, alert_engine):
        self.conn         = ibkr_conn
        self.broadcast    = broadcast_fn
        self.alert_engine = alert_engine
        self._running     = False
        self._tickers: dict[str, object]      = {}
        self._prev_closes: dict[str, float]   = {}
        self._last_prices: dict[str, float]   = {}

    async def start(self):
        if not self.conn.connected:
            logger.error("[LiveFeed] IBKR ikke forbundet — kan ikke starte feed")
            return

        symbols = await self._build_universe()
        logger.info(f"[LiveFeed] Universe ({len(symbols)}): {symbols}")

        await self._subscribe(symbols)
        await self._fetch_prev_closes(symbols)

        # Vent kort så ib_async får første snapshot fra TWS
        await asyncio.sleep(2.0)

        self._running = True
        logger.info("[LiveFeed] Streaming startet")

        try:
            while self._running:
                await asyncio.sleep(UPDATE_INTERVAL_SEC)
                ticks = self._build_ticks()
                if not ticks:
                    continue
                await self.broadcast({
                    "type":      "ticks",
                    "data":      ticks,
                    "timestamp": datetime.now().isoformat(),
                })
                alerts = self.alert_engine.process_ticks(ticks)
                if alerts:
                    await self.broadcast({"type": "alerts", "data": alerts})
        except asyncio.CancelledError:
            logger.info("[LiveFeed] Annulleret")
            raise
        except Exception as e:
            logger.exception(f"[LiveFeed] Loop-fejl: {e}")

    async def stop(self):
        self._running = False
        for sym, ticker in self._tickers.items():
            try:
                self.conn.ib.cancelMktData(ticker.contract)
            except Exception as e:
                logger.debug(f"[LiveFeed] cancelMktData {sym}: {e}")
        self._tickers.clear()

    # ── Universe ──────────────────────────────────────────────
    async def _build_universe(self) -> list[str]:
        try:
            scanner_results = await self.conn.scan_top_gainers(max_results=20)
            if scanner_results:
                combined = list(dict.fromkeys(scanner_results + FALLBACK_UNIVERSE[:5]))
                return combined[:MAX_UNIVERSE_SIZE]
        except Exception as e:
            logger.warning(f"[LiveFeed] Scanner fejl: {e} — bruger fallback")
        return FALLBACK_UNIVERSE[:MAX_UNIVERSE_SIZE]

    async def add_symbols(self, symbols: list[str]):
        """Tilføj EKSTRA symboler til det levende feed dynamisk (fx watchlist-tickers,
        trin 3). Springer allerede-abonnerede over; best-effort pr. symbol. Deres ticks
        indgår i næste broadcast, så frontendens 'Aktuel pris' begynder at leve."""
        if not self.conn.connected:
            return
        new: list[str] = []
        for sym in symbols:
            s = (sym or "").upper().strip()
            if not s or s in self._tickers:
                continue
            try:
                contract = Stock(s, "SMART", "USD")
                await self.conn.ib.qualifyContractsAsync(contract)
                self._tickers[s] = self.conn.ib.reqMktData(contract, "", False, False)
                new.append(s)
            except Exception as e:
                logger.warning(f"[LiveFeed] watchlist-abonnement {s}: {e}")
        if new:
            await self._fetch_prev_closes(new)
            logger.info(f"[LiveFeed] +{len(new)} watchlist-symbol(er): {new}")

    async def _subscribe(self, symbols: list[str]):
        for sym in symbols:
            try:
                contract = Stock(sym, "SMART", "USD")
                await self.conn.ib.qualifyContractsAsync(contract)
                ticker = self.conn.ib.reqMktData(contract, "", False, False)
                self._tickers[sym] = ticker
            except Exception as e:
                logger.warning(f"[LiveFeed] Abonnement-fejl {sym}: {e}")

    async def _fetch_prev_closes(self, symbols: list[str]):
        for sym in symbols:
            try:
                bars = await self.conn.get_historical_bars(
                    sym, duration="2 D", bar_size="1 day", what_to_show="TRADES"
                )
                if bars:
                    self._prev_closes[sym] = bars[-1]["close"]
            except Exception as e:
                logger.debug(f"[LiveFeed] Prev close fejl {sym}: {e}")

    # ── Tick-bygger ───────────────────────────────────────────
    def _build_ticks(self) -> list[dict]:
        ticks   = []
        now_iso = datetime.now().isoformat()

        for sym, ticker in self._tickers.items():
            price = None
            for candidate in (ticker.last, ticker.close, ticker.marketPrice()):
                if _is_valid(candidate) and candidate > 0:
                    price = candidate
                    break
            if price is None:
                continue

            prev_close = self._prev_closes.get(sym, price)
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

            open_px = ticker.open if _is_valid(ticker.open) else prev_close
            gap_pct = ((open_px - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

            volume     = int(ticker.volume) if _is_valid(ticker.volume) else 0
            prev_price = self._last_prices.get(sym, price)
            self._last_prices[sym] = price

            ticks.append({
                "ticker":         sym,
                "price":          round(price, 2),
                "prev_price":     round(prev_price, 2),
                "change_percent": round(change_pct, 2),
                "volume":         volume,
                "rel_vol_daily":  0.0,
                "rel_vol_5min":   0.0,
                "gap_percent":    round(gap_pct, 2),
                "float":          "—",
                "news":           False,
                "timestamp":      now_iso,
                "bid":            ticker.bid  if _is_valid(ticker.bid)  else None,
                "ask":            ticker.ask  if _is_valid(ticker.ask)  else None,
                "high":           ticker.high if _is_valid(ticker.high) else None,
                "low":            ticker.low  if _is_valid(ticker.low)  else None,
                "open":           round(open_px, 2) if _is_valid(open_px) else None,
                "source":         "live",
            })
        return ticks

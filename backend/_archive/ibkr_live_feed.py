"""
ibkr_live_feed.py
─────────────────
Live markedsdata feed fra IBKR.

Opdateringsfrekvens:
  - Scanner:   hvert 60. sekund via thread executor (blokerer ikke event loop)
  - Snapshots: hvert 5. sekund under handelstid
  - Historisk: closing priser fra gårsdagen uden for handelstid

Placering: C:\\Projects\\trading-dash\\backend\\ibkr_live_feed.py
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SECS = 5
SCAN_INTERVAL_SECS     = 60
MAX_TICKERS            = 25

FALLBACK_UNIVERSE = [
    "AAPL", "TSLA", "NVDA", "AMD", "MSFT",
    "META", "AMZN", "GOOGL", "PLTR", "SOFI",
]

# Thread executor til scanner — kører i separat tråd
_executor = ThreadPoolExecutor(max_workers=1)


class IBKRLiveFeed:

    def __init__(self, conn, broadcast_fn: Callable, alert_engine):
        self.conn         = conn
        self.broadcast_fn = broadcast_fn
        self.alert_engine = alert_engine
        self.running      = False

        self.universe:    list[str]        = list(FALLBACK_UNIVERSE)
        self.prev_prices: dict[str, float] = {}
        self.open_prices: dict[str, float] = {}
        self._first_load: bool             = True

        self._snapshot_task: Optional[asyncio.Task] = None
        self._scan_task:     Optional[asyncio.Task] = None

    # -----------------------------------------------------------------------
    # Start / Stop
    # -----------------------------------------------------------------------

    async def start(self):
        self.running         = True
        print("[LiveFeed] Starter IBKR live feed")
        self._snapshot_task  = asyncio.create_task(self._snapshot_loop())
        self._scan_task      = asyncio.create_task(self._scan_loop())
        while self.running:
            await asyncio.sleep(1)

    def stop(self):
        self.running = False
        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
        print("[LiveFeed] Stoppet")

    # -----------------------------------------------------------------------
    # Scanner loop — kører i thread executor så event loop ikke blokeres
    # -----------------------------------------------------------------------

    async def _scan_loop(self):
        """Opdater universe fra IBKR scanner hvert 60. sekund."""
        print("[LiveFeed] Scanner loop starter")

        # Vent lidt ved opstart så snapshot loop kan nå at starte
        await asyncio.sleep(10)

        while self.running:
            try:
                print("[LiveFeed] Scanner: henter top 25 gainers...")
                loop = asyncio.get_event_loop()

                # Kør scanner i thread executor — blokerer ikke event loop
                new_universe = await loop.run_in_executor(
                    _executor,
                    self._run_scanner_sync
                )

                if new_universe and len(new_universe) >= 5:
                    self.universe = new_universe
                    print(f"[LiveFeed] Scanner: universe opdateret — {', '.join(self.universe[:6])}...")
                else:
                    print(f"[LiveFeed] Scanner: få resultater — beholder eksisterende universe")

            except Exception as e:
                print(f"[LiveFeed] Scanner fejl: {e}")

            await asyncio.sleep(SCAN_INTERVAL_SECS)

    def _run_scanner_sync(self) -> list[str]:
        """
        Kører scanner synkront i en separat tråd.
        Bruger et nyt IB objekt da ib_async ikke er thread-safe.
        """
        import asyncio as _asyncio
        from ib_async import IB, ScannerSubscription
        import random

        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

        async def _scan():
            ib = IB()
            try:
                client_id = random.randint(60, 79)
                await ib.connectAsync(
                    host     = "127.0.0.1",
                    port     = 7497,
                    clientId = client_id,
                    timeout  = 15,
                    readonly = True,
                )

                sub = ScannerSubscription(
                    instrument   = "STK",
                    locationCode = "STK.US.MAJOR",
                    scanCode     = "TOP_PERC_GAIN",
                )

                data = await _asyncio.wait_for(
                    ib.reqScannerDataAsync(sub),
                    timeout=15.0
                )

                tickers = []
                for item in data:
                    symbol = item.contractDetails.contract.symbol
                    if len(symbol) <= 5:
                        tickers.append(symbol)
                    if len(tickers) >= MAX_TICKERS:
                        break

                return tickers

            except Exception as e:
                logger.error(f"[Scanner] Fejl: {e}")
                return []
            finally:
                try:
                    ib.disconnect()
                except Exception:
                    pass

        try:
            return loop.run_until_complete(_scan())
        except Exception as e:
            logger.error(f"[Scanner] Loop fejl: {e}")
            return []
        finally:
            loop.close()

    # -----------------------------------------------------------------------
    # Snapshot loop
    # -----------------------------------------------------------------------

    async def _snapshot_loop(self):
        print("[LiveFeed] Snapshot loop starter")
        while self.running:
            try:
                print(f"[LiveFeed] Henter {len(self.universe)} tickers...")
                ticks = await self._fetch_all_snapshots()
                print(f"[LiveFeed] {len(ticks)} ticks modtaget")

                if ticks:
                    await self.broadcast_fn({
                        "type":      "ticks",
                        "data":      ticks,
                        "timestamp": datetime.now().isoformat(),
                    })

                    if self._first_load:
                        self.alert_engine.process_ticks(ticks)
                        self._first_load = False
                        print("[LiveFeed] Første batch indlæst — alerts aktive fra nu")
                    else:
                        has_live = any(t.get("source") == "live" for t in ticks)
                        if has_live:
                            alerts = self.alert_engine.process_ticks(ticks)
                            if alerts:
                                await self.broadcast_fn({"type": "alerts", "data": alerts})

            except Exception as e:
                print(f"[LiveFeed] Snapshot loop fejl: {e}")

            await asyncio.sleep(SNAPSHOT_INTERVAL_SECS)

    # -----------------------------------------------------------------------
    # Fetch alle snapshots parallelt
    # -----------------------------------------------------------------------

    async def _fetch_all_snapshots(self) -> list[dict]:
        tasks   = [self._fetch_snapshot(ticker) for ticker in self.universe]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]

    # -----------------------------------------------------------------------
    # Fetch snapshot for én ticker
    # -----------------------------------------------------------------------

    async def _fetch_snapshot(self, ticker: str) -> Optional[dict]:
        try:
            snap  = await asyncio.wait_for(
                self.conn.get_snapshot(ticker),
                timeout=4.0
            )
            price = (snap.get("last") or snap.get("close") or 0) if snap else 0

            if not price or price <= 0:
                return await self._fetch_historical(ticker)

            prev_price = self.prev_prices.get(ticker, price)
            open_price = self.open_prices.get(ticker) or snap.get("open") or price

            self.prev_prices[ticker] = price
            if ticker not in self.open_prices and open_price:
                self.open_prices[ticker] = open_price

            change_pct = round((price - prev_price) / prev_price * 100, 2) if prev_price > 0 else 0.0
            prev_close = snap.get("close") or 0
            gap_pct    = round((open_price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0.0
            volume     = int(snap.get("volume") or 0)

            return {
                "ticker":         ticker,
                "price":          round(price, 2),
                "prev_price":     round(prev_price, 2),
                "change_percent": change_pct,
                "volume":         volume,
                "rel_vol_daily":  round(volume / 1_000_000, 2) if volume > 0 else 0.0,
                "rel_vol_5min":   round(volume / 500_000,   2) if volume > 0 else 0.0,
                "gap_percent":    gap_pct,
                "float":          "N/A",
                "bid":            round(snap.get("bid")  or 0, 2),
                "ask":            round(snap.get("ask")  or 0, 2),
                "high":           round(snap.get("high") or price, 2),
                "low":            round(snap.get("low")  or price, 2),
                "open":           round(open_price, 2),
                "news":           False,
                "timestamp":      datetime.now().isoformat(),
                "source":         "live",
            }

        except asyncio.TimeoutError:
            return await self._fetch_historical(ticker)
        except Exception as e:
            logger.debug(f"[LiveFeed] Snapshot fejl {ticker}: {e}")
            return await self._fetch_historical(ticker)

    # -----------------------------------------------------------------------
    # Historisk fallback
    # -----------------------------------------------------------------------

    async def _fetch_historical(self, ticker: str) -> Optional[dict]:
        try:
            bars = None
            for what in ["TRADES", "MIDPOINT"]:
                try:
                    bars = await asyncio.wait_for(
                        self.conn.get_historical_bars(
                            ticker,
                            duration     = "3 D",
                            bar_size     = "1 day",
                            what_to_show = what,
                        ),
                        timeout=8.0
                    )
                    if bars:
                        break
                except Exception:
                    continue

            if not bars:
                return None

            last_bar   = bars[-1]
            price      = last_bar["close"]
            prev_price = bars[-2]["close"] if len(bars) >= 2 else price
            open_price = last_bar["open"]  or price
            volume     = int(last_bar.get("volume") or 0)

            if not price or price <= 0:
                return None

            self.prev_prices[ticker] = price
            if ticker not in self.open_prices:
                self.open_prices[ticker] = open_price

            change_pct = round((price - prev_price) / prev_price * 100, 2) if prev_price > 0 else 0.0
            gap_pct    = round((open_price - prev_price) / prev_price * 100, 2) if prev_price > 0 else 0.0

            return {
                "ticker":         ticker,
                "price":          round(price, 2),
                "prev_price":     round(prev_price, 2),
                "change_percent": change_pct,
                "volume":         volume,
                "rel_vol_daily":  round(volume / 1_000_000, 2) if volume > 0 else 0.0,
                "rel_vol_5min":   0.0,
                "gap_percent":    gap_pct,
                "float":          "N/A",
                "bid":            0.0,
                "ask":            0.0,
                "high":           round(last_bar["high"], 2),
                "low":            round(last_bar["low"],  2),
                "open":           round(open_price, 2),
                "news":           False,
                "timestamp":      datetime.now().isoformat(),
                "source":         "historical",
            }

        except asyncio.TimeoutError:
            logger.debug(f"[LiveFeed] Historisk timeout: {ticker}")
            return None
        except Exception as e:
            logger.debug(f"[LiveFeed] Historisk fejl {ticker}: {e}")
            return None

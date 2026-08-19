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

# Hvor laenge der ventes paa den FOERSTE pris efter abonnementet. En kold TWS er
# et par sekunder om at levere; 2 sekunder (som foer) er for kort til at skelne
# "langsom" fra "faar aldrig noget". Der pollet undervejs, saa en hurtig
# forbindelse ikke straffes -- vi gaar videre i det sekund den foerste pris kommer.
OPSTART_TAALMODIGHED_SEC = 20.0
OPSTART_POLL_SEC = 0.5


def _pris(ticker) -> float | None:
    """Prisen ib_async har lige nu, eller None.

    ⚠ ÉN implementering, brugt baade af opstartskontrollen og af tick-bygningen.
    To kopier ville kunne blive uenige, og saa ville kontrollen maale noget andet
    end loekken bruger -- altsaa vaere blind for praecis den fejl den er sat til
    at fange.
    """
    for kandidat in (ticker.last, ticker.close, ticker.marketPrice()):
        if _is_valid(kandidat) and kandidat > 0:
            return kandidat
    return None


def _is_valid(x) -> bool:
    """ib_async udfylder tom data med NaN — kassér de værdier."""
    if x is None:
        return False
    try:
        return not math.isnan(x)
    except (TypeError, ValueError):
        return False


class IBKRLiveFeed:
    def __init__(self, ibkr_conn, broadcast_fn: BroadcastFn, alert_engine,
                 paa_doedt_feed=None):
        self.conn         = ibkr_conn
        self.broadcast    = broadcast_fn
        self.alert_engine = alert_engine
        # Kaldes hvis feedet viser sig ikke at levere noget. Signatur:
        #   async (hvorfor: str, watchlist_symboler: list[str]) -> bool
        # Returnerer True hvis nogen overtog (fx kursproxyen mod algoserveren).
        self._paa_doedt_feed = paa_doedt_feed
        self._running     = False
        self._tickers: dict[str, object]      = {}
        self._prev_closes: dict[str, float]   = {}
        self._last_prices: dict[str, float]   = {}
        # Kun de symboler WATCHLISTEN har bedt om -- ikke fallback-universet.
        # Skal feedet overdrages, er det disse der betyder noget; at proxy'e 20
        # fallback-tickere ville koste et kald hvert 5. sekund for tal ingen ser.
        self._watchlist_symboler: set[str] = set()

    async def start(self):
        if not self.conn.connected:
            logger.error("[LiveFeed] IBKR ikke forbundet — kan ikke starte feed")
            return

        symbols = await self._build_universe()
        logger.info(f"[LiveFeed] Universe ({len(symbols)}): {symbols}")

        await self._subscribe(symbols)
        await self._fetch_prev_closes(symbols)

        # ── ⚠ KONTROLLEN DER MANGLEDE ───────────────────────────────────────
        # Foer stod her `await asyncio.sleep(2.0)` og derefter "Streaming startet"
        # UBETINGET. Paa Ibens workstation 19-08 gav det:
        #
        #   07:39:45   34 x "No market data during competing live session"
        #   07:39:49   [LiveFeed] Streaming startet
        #
        # Feedet meldte succes efter at hvert eneste abonnement var afvist. Det er
        # husets faste fejlklasse -- en kontrol hvis fejl behandles som en
        # bestaaelse -- og her kostede den mere end en tom kolonne: `_uden_feed()`
        # i main.py indeholder en faerdigbygget kursproxy mod algoserveren, skrevet
        # med netop Ibens maskine som eksempel. Den vej blev aldrig taget, fordi
        # systemet troede den lokale vej lykkedes.
        #
        # `connected` er ikke det samme som "har markedsdata". Paa en konto uden
        # abonnement er de systematisk forskellige.
        med_pris = await self._vent_paa_foerste_pris()
        if self._tickers and med_pris == 0:
            hvorfor = (f"lokalt feed leverede 0 priser af {len(self._tickers)} "
                       f"symboler paa {OPSTART_TAALMODIGHED_SEC:.0f} sekunder")
            if await self._giv_op(hvorfor):
                return
            # ⚠ INGEN AT OVERDRAGE TIL -> BLIV KOERENDE. At lukke feedet ned ville
            # her vaere strengt vaerre: et feed uden priser er ikke daarligere end
            # intet feed, men et lukket feed kan ikke komme sig hvis TWS retter sig.
            logger.warning("[LiveFeed] ... men der er ingen at overdrage til — "
                           "fortsaetter, i haab om at forbindelsen retter sig")

        self._running = True
        logger.info(f"[LiveFeed] Streaming startet — {med_pris} af "
                    f"{len(self._tickers)} symboler har en pris")

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

    async def _vent_paa_foerste_pris(self) -> int:
        """Antal symboler med en brugbar pris. Gaar videre saa snart der er én."""
        ventet = 0.0
        while ventet < OPSTART_TAALMODIGHED_SEC:
            await asyncio.sleep(OPSTART_POLL_SEC)
            ventet += OPSTART_POLL_SEC
            n = self._antal_med_pris()
            if n:
                return n
        return self._antal_med_pris()

    def _antal_med_pris(self) -> int:
        return sum(1 for tk in self._tickers.values() if _pris(tk) is not None)

    async def _giv_op(self, hvorfor: str) -> bool:
        """Meld at feedet ikke leverer. True hvis nogen overtog."""
        logger.error(f"[LiveFeed] {hvorfor} — feedet leverer IKKE data")
        if self._paa_doedt_feed is None:
            return False
        try:
            overtaget = await self._paa_doedt_feed(
                hvorfor, sorted(self._watchlist_symboler))
        except Exception as e:
            logger.exception(f"[LiveFeed] overdragelse fejlede: {e}")
            return False
        if overtaget:
            await self.stop()
            logger.info("[LiveFeed] Overdraget — lokale abonnementer nedlagt")
        return bool(overtaget)

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
                # ⚠ IKKE Stock(s, "SMART"). Futures har ingen aktie-kontrakt: for "MES"
                # gav qualifyContractsAsync ingen fejl men efterlod conId = 0, og
                # reqMktData kastede saa "can't be hashed". Det blev fanget af except
                # nedenfor og endte som EN linje i loggen — MES kom aldrig i feedet.
                # Watchlistens PRIS-kolonne stod alligevel udfyldt (den er den frosne
                # /quote-pris, som ER futures-bevidst), saa det saa ud som om feedet
                # virkede, mens AKTUEL/BEHOLD/UR.P/L var tomme. Ordren blev derefter
                # blokeret af frontendens "ingen live pris"-vagt.
                # _resolve_contract er det faelles punkt der kender begge dele.
                contract = await self.conn._resolve_contract(s)
                self._tickers[s] = self.conn.ib.reqMktData(contract, "", False, False)
                new.append(s)
            except Exception as e:
                logger.warning(f"[LiveFeed] watchlist-abonnement {s}: {e}")
        self._watchlist_symboler.update(
            (s or "").upper().strip() for s in symbols if (s or "").strip())
        if new:
            await self._fetch_prev_closes(new)
            logger.info(f"[LiveFeed] +{len(new)} watchlist-symbol(er): {new}")

    async def _subscribe(self, symbols: list[str]):
        for sym in symbols:
            try:
                # Samme grund som i add_symbols: futures-bevidst resolution.
                contract = await self.conn._resolve_contract(sym)
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
            price = _pris(ticker)          # samme udtraek som opstartskontrollen
            if price is None:
                continue

            prev_close = self._prev_closes.get(sym, price)
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

            open_px = ticker.open if _is_valid(ticker.open) else prev_close
            gap_pct = ((open_px - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

            volume     = int(ticker.volume) if _is_valid(ticker.volume) else 0
            prev_price = self._last_prices.get(sym, price)
            self._last_prices[sym] = price

            # Halt-status fra IBKR (tickType 49): 1 = halted, 2 = paused/volatilitet.
            # NaN/-1 = ikke halted / ikke tilgængelig. Bruges af watchlistens halt-alarm.
            halted_raw = getattr(ticker, "halted", None)
            is_halted  = bool(_is_valid(halted_raw) and halted_raw >= 1)

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
                "halted":         is_halted,
                "source":         "live",
            })
        return ticks

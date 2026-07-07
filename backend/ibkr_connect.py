"""
ibkr_connect.py
───────────────
Forbindelsesmanager til Interactive Brokers via ib_async.

Python 3.14 kompatibel — bruger KUN async metoder.

Kræver TWS med:
    - Enable ActiveX and Socket Clients: ✓
    - Socket port: 7497 (paper trading)
    - Read-Only API: ✗
"""

import asyncio
import logging
from typing import Optional

# Fix Python 3.14 event loop ved import-tid
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock, MarketOrder, LimitOrder

logger = logging.getLogger(__name__)

TWS_HOST       = "127.0.0.1"
TWS_PORT_PAPER = 7497
TWS_PORT_LIVE  = 7496

# ── Futures-understøttelse ────────────────────────────────────
# Symboler her behandles som Future-kontrakter (front-måned) i stedet for
# Stock. Tilføj nye futures-instrumenter ved at registrere deres børs her.
# Multiplikatoren (kontraktstørrelse pr. prispoint) bor i strategien der
# sizer — connection-laget behøver kun børsen for at kvalificere kontrakten.
FUTURES_EXCHANGE = {
    "MES": "CME",   # Micro E-mini S&P 500
    "M2K": "CME",   # Micro E-mini Russell 2000
}

def is_future_symbol(ticker: str) -> bool:
    """True hvis ticker skal handles som en Future-kontrakt (ikke Stock)."""
    return ticker in FUTURES_EXCHANGE


class IBKRConnection:
    """
    Forbindelsesmanager til IBKR.
    Alle metoder er async og skal kaldes med await.
    """

    def __init__(self, paper_trading: bool = True):
        self.ib        = IB()
        self.port      = TWS_PORT_PAPER if paper_trading else TWS_PORT_LIVE
        self.paper     = paper_trading
        # Internt flag: True når vi HAR forsøgt/opnået en forbindelse.
        # Den offentlige `connected`-property kombinerer dette med
        # ib.isConnected() så vi aldrig rapporterer en stale forbindelse
        # som levende (fx når TWS lukker forbindelsen om natten).
        self._connect_attempted = False

        # Cache af kvalificerede front-måned-futures (symbol → qualified
        # Future-kontrakt). Tømmes/genopfriskes ved roll via qualify_future(
        # ..., force_refresh=True) — strategien gør det ved dagsstart.
        self._future_cache: dict = {}

    @property
    def connected(self) -> bool:
        """
        Hurtig (passiv) forbindelsesstatus — afspejler ib_async's interne
        flag for om vi HAR en forbindelse.

        VIGTIGT: Dette flag er PASSIVT. ib.isConnected() returnerer bare
        et internt _apiReady-flag der kun bliver False når biblioteket
        aktivt registrerer en disconnect-event. Hvis TWS lukker mens
        backenden sidder passivt, kan dette hænge på True i et stykke tid.
        Brug is_alive() (aktivt probe) når det er KRITISK at vide om
        forbindelsen reelt lever — fx før algo-start.
        """
        try:
            return self._connect_attempted and self.ib.isConnected()
        except Exception:
            return False

    async def is_alive(self, timeout: float = 3.0) -> bool:
        """
        AKTIV forbindelsestest — sender et rigtigt kald til TWS og ser om
        den svarer. Det er den eneste pålidelige måde at fange en stale
        forbindelse (TWS lukket, men ib_async's flag stadig True).

        Bruger reqCurrentTimeAsync() — et let round-trip der returnerer
        serverens tid. Svarer TWS inden timeout → forbindelsen lever.
        Timeout eller fejl → forbindelsen er reelt død.
        """
        if not self._connect_attempted:
            return False
        try:
            await asyncio.wait_for(self.ib.reqCurrentTimeAsync(), timeout=timeout)
            return True
        except Exception:
            return False

    async def connect(self) -> bool:
        import random
        client_id = random.randint(10, 99)
        try:
            await self.ib.connectAsync(
                host     = TWS_HOST,
                port     = self.port,
                clientId = client_id,
                timeout  = 15,
                readonly = False,
            )
            self._connect_attempted = True
            kind = "PAPER" if self.paper else "LIVE"
            logger.info(f"✅ Forbundet til IBKR ({kind}) på port {self.port}")
            logger.info(f"   Konto: {self.ib.managedAccounts()}")
            return True
        except Exception as e:
            self._connect_attempted = False
            logger.error(f"❌ Forbindelsesfejl: {e}")
            return False

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            logger.info("Afbrudt fra IBKR")
        self._connect_attempted = False

    # ── Futures-kontrakter ────────────────────────────────────
    async def qualify_future(self, symbol: str, force_refresh: bool = False):
        """
        Kvalificér og returnér FRONT-MÅNED-kontrakten for et futures-symbol
        (fx "MES" → den nærmeste ikke-udløbne MES-kontrakt).

        Vi bruger reqContractDetailsAsync på en under-specificeret Future
        (kun symbol+børs) for at få ALLE noterede måneder, og vælger den med
        den nærmeste lastTradeDate ≥ i dag. De returnerede kontrakter er fuldt
        kvalificerede (conId sat), så de kan bruges direkte til ordrer.

        Resultatet caches pr. symbol. Kald med force_refresh=True ved dagsstart
        så vi ruller til en ny front-måned uden genstart. Returnerer None hvis
        kontrakten ikke kan kvalificeres (kalderen må håndtere det).

        BEVIDST: vi kvalificerer en konkret Future — IKKE en ContFuture.
        ContFuture kan bruges til data, men en ordre på en ContFuture udløser
        IBKR-fejl 10339 ("order references a continuous contract").
        """
        if not self.connected:
            return None
        if not force_refresh and symbol in self._future_cache:
            return self._future_cache[symbol]

        try:
            from ib_async import Future
            exchange = FUTURES_EXCHANGE.get(symbol, "CME")
            base = Future(symbol=symbol, exchange=exchange, currency="USD")
            details = await asyncio.wait_for(
                self.ib.reqContractDetailsAsync(base),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.error(f"qualify_future({symbol}): reqContractDetails timeout")
            return None
        except Exception as e:
            logger.error(f"qualify_future({symbol}): {e}")
            return None

        if not details:
            logger.error(f"qualify_future({symbol}): ingen kontrakt-detaljer fra IBKR")
            return None

        import datetime as _dt
        today_str = _dt.datetime.now().strftime("%Y%m%d")
        candidates = []
        for cd in details:
            c = cd.contract
            exp = (c.lastTradeDateOrContractMonth or "").strip()
            if len(exp) == 6:        # "YYYYMM" → sammenlign mod månedens slutning
                exp = exp + "31"
            if len(exp) >= 8 and exp[:8] >= today_str:
                candidates.append((exp[:8], c))

        if not candidates:
            logger.error(f"qualify_future({symbol}): ingen ikke-udløbne kontrakter")
            return None

        candidates.sort(key=lambda x: x[0])   # nærmeste expiry = front-måned
        front = candidates[0][1]
        self._future_cache[symbol] = front
        logger.info(
            f"qualify_future({symbol}) → front-måned "
            f"{front.lastTradeDateOrContractMonth} "
            f"(localSymbol={front.localSymbol}, conId={front.conId})"
        )
        return front

    async def _resolve_contract(self, ticker: str):
        """
        Returnér en KVALIFICERET kontrakt for ticker.

        Futures-symboler (MES/M2K) resolves til front-måned-Future via
        qualify_future; alt andet behandles som en US-aktie (SMART/USD).
        Bruges af get_historical_bars, get_snapshot og place_paper_order så
        futures og aktier deler ét kontrakt-resolutionspunkt.
        """
        if is_future_symbol(ticker):
            fut = await self.qualify_future(ticker)
            if fut is None:
                raise ValueError(f"Kunne ikke kvalificere futures-kontrakt for {ticker}")
            return fut
        contract = Stock(ticker, "SMART", "USD")
        await asyncio.wait_for(
            self.ib.qualifyContractsAsync(contract),
            timeout=10.0,
        )
        return contract

    # ── Konto ─────────────────────────────────────────────────
    def get_account_summary(self) -> dict:
        """Henter konto-oversigt fra cached data."""
        values  = self.ib.accountValues()
        summary = {v.tag: v.value for v in values}
        return {
            "net_liquidation":      float(summary.get("NetLiquidation", 0)),
            "cash_balance":         float(summary.get("CashBalance", 0)),
            "unrealized_pnl":       float(summary.get("UnrealizedPnL", 0)),
            "realized_pnl":         float(summary.get("RealizedPnL", 0)),
            "buying_power":         float(summary.get("BuyingPower", 0)),         # sizing-tallet
            "available_funds":      float(summary.get("AvailableFunds", 0)),
            "excess_liquidity":     float(summary.get("ExcessLiquidity", 0)),
            "maint_margin":         float(summary.get("MaintMarginReq", 0)),
            "gross_position_value": float(summary.get("GrossPositionValue", 0)),
        }

    def get_positions(self) -> list:
        """Henter åbne positioner fra cached data."""
        return [{
            "ticker":   p.contract.symbol,
            "position": p.position,
            "avg_cost": p.avgCost,
        } for p in self.ib.positions()]

    async def get_positions_live(self) -> list:
        """Som get_positions, men LIVE. reqPositions' RETURVAERDI er de FAKTISKE aktuelle
        positioner; ib.positions()-cachen kan beholde et FANTOM efter en reconnect (IBKR
        udelader lukkede positioner -> cachen rydder dem ikke af sig selv). Filtrerer
        desuden net-nul fra. Falder tilbage til cachen ved timeout/fejl."""
        try:
            poss = await asyncio.wait_for(self.ib.reqPositionsAsync(), timeout=5)
        except Exception:
            poss = self.ib.positions()
        return [{
            "ticker":   p.contract.symbol,
            "position": p.position,
            "avg_cost": p.avgCost,
        } for p in (poss or []) if p.position != 0]

    async def get_positions_reliable(self) -> tuple[list, bool]:
        """Som get_positions_live, men returnerer OGSAA en paalideligheds-flag OG falder
        ALDRIG tilbage til den (muligvis kolde/fantom-holdende) cache.

        Returnerer (positioner, reliable):
          reliable=True  -> reqPositions naaede positionEnd; listen er autoritativ
                            (tom liste = kontoen er FAKTISK flad).
          reliable=False -> ikke forbundet, timeout eller fejl -> kalderen MAA IKKE
                            tolke resultatet som 'fladt'.

        Bruges af strategiernes opstarts-reconcile: en absence-baseret luk (fantom/
        stale-journal-sync) maa KUN ske paa et reliable=True read. Ved reliable=False
        springes reconcile over — saa en kold cache lige efter connect aldrig kan faa
        en aegte position til at se 'lukket' ud og dermed forældreløsgøre den."""
        if not self.connected:
            return [], False
        try:
            poss = await asyncio.wait_for(self.ib.reqPositionsAsync(), timeout=5)
        except Exception:
            return [], False   # timeout/fejl -> upaalideligt, IKKE cache-fallback
        out = [{
            "ticker":   p.contract.symbol,
            "position": p.position,
            "avg_cost": p.avgCost,
        } for p in (poss or []) if p.position != 0]
        return out, True

    async def get_open_orders(self) -> list:
        """Aktive (ikke-fyldte/ikke-annullerede) ordrer paa tvaers af ALLE klienter.
        Bruges af reconcile til at undgaa at laegge en DUPLIKAT luk-ordre oven paa en der
        allerede hviler (fx en GTC force-close fra en tidligere session der ikke fyldte).
        Best-effort: timeout/fejl -> falder tilbage paa lokalt kendte openTrades()."""
        ACTIVE = {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted"}
        # VIGTIGT: reqAllOpenOrdersAsync's RETURVAERDI er upaalidelig for ordrer fra ANDRE
        # klienter (kan vaere tom selv om de findes). Vent paa kaldet saa open-order-events
        # lander, og laes derefter ib.openTrades() (akkumuleret state) — den ER paalidelig
        # paa tvaers af klienter (bevist i diag_cogt_orders).
        try:
            await asyncio.wait_for(self.ib.reqAllOpenOrdersAsync(), timeout=5)
        except Exception:
            pass
        try:
            trades = self.ib.openTrades()
        except Exception:
            trades = []
        out = []
        for t in trades or []:
            try:
                st = getattr(t.orderStatus, "status", "")
                if st not in ACTIVE:
                    continue
                rem = getattr(t.orderStatus, "remaining", None)
                out.append({
                    "symbol":    (t.contract.symbol or "").upper(),
                    "action":    (t.order.action or "").upper(),     # BUY/SELL
                    "remaining": float(rem) if rem is not None else 0.0,
                    "status":    st,
                    "orderRef":  getattr(t.order, "orderRef", "") or "",
                })
            except Exception:
                continue
        return out

    async def get_order_outcome(self, order_ref: str) -> str:
        """Slaa udfaldet af en tidligere afgivet ordre op via dens DETERMINISTISKE orderRef.
        Til idempotent reconcile-close. Returnerer EN af:
          'active'            — ordren hviler stadig (openTrades, cross-client)
          'filled'            — en execution m. ref'en findes (account+dag-scoped, overlever genstart)
          'terminal_unfilled' — completed order m. ref'en er Cancelled/Expired/Rejected (ufyldt)
          'not_findable'      — kan ikke fastslaas (KONSERVATIVT default -> kalderen placerer ALDRIG
                                paa dette; den falder tilbage paa positions-korroboration)
        Best-effort: enhver fejl -> 'not_findable'. KRITISK: returnér KUN 'terminal_unfilled' ved
        POSITIV annullerings-bekraeftelse — ellers 'not_findable', saa vi aldrig gen-placerer paa tvivl."""
        if not order_ref or not self.connected:
            return "not_findable"
        # 1) aktiv?
        try:
            for o in await self.get_open_orders():
                if (o.get("orderRef") or "") == order_ref:
                    return "active"
        except Exception:
            pass
        # 2) fyldt? executions baerer orderRef og er account+dag-scoped (overlever genstart)
        try:
            from ib_async import ExecutionFilter
            await asyncio.wait_for(self.ib.reqExecutionsAsync(ExecutionFilter()), timeout=5)
        except Exception:
            pass
        try:
            for f in self.ib.fills():
                if (getattr(f.execution, "orderRef", "") or "") == order_ref:
                    return "filled"
        except Exception:
            pass
        # 3) terminal-ufyldt? completed orders (best-effort; KUN positiv annullering)
        try:
            await asyncio.wait_for(self.ib.reqCompletedOrdersAsync(apiOnly=False), timeout=5)
            TERMINAL = {"Cancelled", "ApiCancelled", "Inactive", "Expired", "Rejected"}
            for t in self.ib.trades():
                if (getattr(t.order, "orderRef", "") or "") != order_ref:
                    continue
                st = getattr(t.orderStatus, "status", "")
                if st == "Filled" or (getattr(t.orderStatus, "filled", 0) or 0) > 0:
                    return "filled"
                if st in TERMINAL:
                    return "terminal_unfilled"
        except Exception:
            pass
        return "not_findable"

    # ── Historiske bars ───────────────────────────────────────
    async def get_historical_bars(
        self,
        ticker:       str,
        duration:     str = "1 D",
        bar_size:     str = "5 mins",
        what_to_show: str = "MIDPOINT",
        use_rth:      Optional[bool] = None,
    ) -> list[dict]:
        """Henter historiske OHLCV bars. Brug MIDPOINT uden for handelstid.

        use_rth: None (default) → True for aktier, False for futures. Futures
        som MES/M2K handler næsten døgnet rundt, og den europæiske session
        (02–08 ET) ligger UDEN for US RTH — useRTH=True ville returnere en tom
        liste der. Send eksplicit True/False for at overstyre auto-valget.

        VIGTIGT: Begge TWS-kald er pakket i asyncio.wait_for med timeout.
        Uden timeout kan reqHistoricalDataAsync hænge for evigt hvis TWS
        modtager anmodningen men aldrig svarer (død data-subscription, TWS
        overbelastet). Da strategi-loopet kalder denne metode sekventielt
        for hver ticker, ville ét hæng fryse HELE strategien (set 2026-05-26:
        konfluens frøs kl. 11:31 og handlede ikke resten af dagen). Med
        timeout bliver et hæng til en TimeoutError → springer denne ticker
        over og fortsætter loopet.
        """
        if not self.connected:
            return []
        try:
            contract = await self._resolve_contract(ticker)
            effective_rth = use_rth if use_rth is not None \
                            else (not is_future_symbol(ticker))
            bars = await asyncio.wait_for(
                self.ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime    = "",
                    durationStr    = duration,
                    barSizeSetting = bar_size,
                    whatToShow     = what_to_show,
                    useRTH         = effective_rth,
                    formatDate     = 1,
                ),
                timeout=15.0,
            )
            return [{
                "datetime": bar.date,
                "open":     bar.open,
                "high":     bar.high,
                "low":      bar.low,
                "close":    bar.close,
                "volume":   bar.volume,
            } for bar in bars]
        except asyncio.TimeoutError:
            logger.warning(
                f"Historical bars TIMEOUT {ticker} — TWS svarede ikke "
                f"inden for tidsgrænsen; springer over denne iteration"
            )
            return []
        except Exception as e:
            logger.error(f"Historical bars fejl {ticker}: {e}")
            return []

    # ── Snapshot ──────────────────────────────────────────────
    async def get_snapshot(self, ticker: str) -> Optional[dict]:
        """Henter realtids snapshot. Kræver aktive market data abonnementer.

        Som get_historical_bars: TWS-kaldene er pakket i asyncio.wait_for,
        så et hængende svar ikke kan fryse en kaldende loop.
        """
        if not self.connected:
            return None
        try:
            contract = await self._resolve_contract(ticker)
            tickers = await asyncio.wait_for(
                self.ib.reqTickersAsync(contract),
                timeout=10.0,
            )
            if not tickers:
                return None
            t = tickers[0]
            return {
                "ticker": ticker,
                "bid":    t.bid,
                "ask":    t.ask,
                "last":   t.last,
                "volume": t.volume,
                "open":   t.open,
                "high":   t.high,
                "low":    t.low,
                "close":  t.close,
            }
        except asyncio.TimeoutError:
            logger.warning(
                f"Snapshot TIMEOUT {ticker} — TWS svarede ikke inden for "
                f"tidsgrænsen"
            )
            return None
        except Exception as e:
            logger.error(f"Snapshot fejl {ticker}: {e}")
            return None

    # ── Scanner ───────────────────────────────────────────────
    async def scan_top_gainers(self, max_results: int = 25) -> list[str]:
        if not self.connected:
            return []
        try:
            from ib_async import ScannerSubscription
            sub = ScannerSubscription(
                instrument   = "STK",
                locationCode = "STK.US.MAJOR",
                scanCode     = "TOP_PERC_GAIN",
            )
            data = await asyncio.wait_for(
                self.ib.reqScannerDataAsync(sub),
                timeout=15.0
            )
            # Filtrer manuelt: pris $1-20, symbol max 5 tegn
            tickers = []
            for item in data:
                contract = item.contractDetails.contract
                symbol   = contract.symbol
                if len(symbol) <= 5:  # Undgå warrants og andre instrumenter
                    tickers.append(symbol)
                if len(tickers) >= max_results:
                    break

            logger.info(f"Scanner fandt {len(tickers)} tickers")
            return tickers
        except asyncio.TimeoutError:
            logger.warning("Scanner timeout")
            return []
        except Exception as e:
            logger.error(f"Scanner fejl: {e}")
            return []
    

    # ── Ordre ─────────────────────────────────────────────────
    async def place_paper_order(
        self,
        ticker:         str,
        action:         str,
        quantity:       float,
        order_type:     str   = "MKT",
        limit_price:    float = 0,
        source:         str   = "",
        await_fill_sec: float = 0,
        order_ref:      Optional[str] = None,
    ) -> Optional[dict]:
        """Sender en ordre til paper trading kontoen.

        source: strateginavn (fx "Momentum ORB"). Saettes som orderRef paa
        ordren, saa fills og ordrehistorik kan spores tilbage til den strategi
        der sendte ordren — afgoerende naar flere strategier deler samme konto.

        order_ref: eksplicit, DETERMINISTISK orderRef (fx reconcile_close_{trade_id}).
        Overstyrer `source` paa ordren, saa en reconcile-close kan slaas entydigt op
        bagefter (idempotens). Falder tilbage paa `source` naar None.
        """
        if not self.connected:
            return None
        if not self.paper:
            raise ValueError("Brug kun place_paper_order på paper trading konto!")
        try:
            # Kontrakt-resolution (inkl. kvalificering) har timeout — selve
            # ordrelægningen (placeOrder) er synkron og røres IKKE, så vi
            # aldrig afbryder en ordre der er på vej igennem. Futures-symboler
            # (MES/M2K) resolves til front-måned-Future via _resolve_contract.
            contract = await self._resolve_contract(ticker)
            order = MarketOrder(action, quantity) if order_type == "MKT" \
                    else LimitOrder(action, quantity, limit_price)
            _ref = order_ref or source
            if _ref:
                order.orderRef = _ref
            trade = self.ib.placeOrder(contract, order)

            # await_fill_sec=0 (default): bevarer hidtidig adfærd — vent 1 sek
            # uanset (entries og alle nuværende kaldere er UÆNDREDE). >0: poll
            # Trade-objektet (opdateres live af ib_async) til ordren er fyldt
            # eller terminal, op til await_fill_sec. Bruges ved lukke-/force-
            # close-ordrer hvor BEKRÆFTET fyldning er nødvendig.
            if await_fill_sec and await_fill_sec > 0:
                _waited = 0.0
                _TERMINAL = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
                while _waited < await_fill_sec:
                    await asyncio.sleep(0.5)
                    _waited += 0.5
                    _st = trade.orderStatus
                    if (_st.filled or 0) >= quantity or _st.status in _TERMINAL:
                        break
            else:
                await asyncio.sleep(1)
            return {
                "ticker":    ticker,
                "action":    action,
                "quantity":  quantity,
                "order_id":  trade.order.orderId,
                "order_ref": trade.order.orderRef,
                "status":    trade.orderStatus.status,
                "filled":    trade.orderStatus.filled,
                "avg_fill":  trade.orderStatus.avgFillPrice,
            } 
        except asyncio.TimeoutError:
            logger.error(
                f"Ordre TIMEOUT {ticker} — kontrakt-kvalificering svarede "
                f"ikke; ordre IKKE sendt"
            )
            return None
        except Exception as e:
            logger.error(f"Ordre fejl {ticker}: {e}")
            return None

    async def what_if_init_margin(
        self,
        ticker:      str,
        action:      str,
        quantity:    float,
        order_type:  str   = "MKT",
        limit_price: float = 0,
    ) -> Optional[float]:
        """IBKR's INITIAL-margin (USD) som DENNE ordre ville binde — via en whatIf-ordre
        (ingen rigtig ordre sendes). KUN til display. Fuldstændig fejl-sikker: enhver
        fejl/timeout → None (kalderen viser så ingen margin). Må ALDRIG kaste videre —
        en margin-forespørgsel må aldrig kunne påvirke en handel."""
        if not self.connected:
            return None
        try:
            contract = await self._resolve_contract(ticker)
            order = MarketOrder(action, quantity) if order_type == "MKT" \
                    else LimitOrder(action, quantity, limit_price)
            state = await asyncio.wait_for(
                self.ib.whatIfOrderAsync(contract, order), timeout=5)
            raw = getattr(state, "initMarginChange", None) if state else None
            if raw in (None, ""):
                return None
            return abs(float(raw))
        except Exception as e:
            logger.warning(f"[whatIf] init-margin kunne ikke beregnes for {ticker}: {e}")
            return None


# ── Singleton ─────────────────────────────────────────────────
_connection: Optional[IBKRConnection] = None

def get_connection(paper_trading: bool = True) -> IBKRConnection:
    global _connection
    if _connection is None:
        _connection = IBKRConnection(paper_trading=paper_trading)
    return _connection


# ── Test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s %(levelname)s %(message)s",
    )

    async def test():
        conn = IBKRConnection(paper_trading=True)
        ok   = await conn.connect()

        if not ok:
            print("\n💡 Tjekliste:")
            print("   1. TWS åben og logget ind på Paper Trading?")
            print("   2. Edit → Global Configuration → API → Settings")
            print("   3. Enable ActiveX and Socket Clients: ✓")
            print("   4. Read-Only API: ✗")
            print("   5. Port: 7497")
            return

        print("\n✅ Forbundet til IBKR Paper Trading!")

        account = conn.get_account_summary()
        print(f"\nKonto:")
        print(f"  Net Liquidation: ${account['net_liquidation']:,.2f}")
        print(f"  Cash Balance:    ${account['cash_balance']:,.2f}")
        print(f"  Urealiseret P&L: ${account['unrealized_pnl']:,.2f}")

        positions = conn.get_positions()
        if positions:
            print(f"\nPositioner:")
            for p in positions:
                print(f"  {p['ticker']:8s} {p['position']:+.0f} @ ${p['avg_cost']:.4f}")

        print("\nHenter AAPL bars...")
        bars = await conn.get_historical_bars("AAPL", duration="1 D", bar_size="5 mins")
        if bars:
            print(f"  ✅ {len(bars)} bars")
            print(f"  Seneste: {bars[-1]}")

        conn.disconnect()
        print("\n✅ ibkr_connect.py klar til brug!")

    asyncio.run(test())

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

    # ── Konto ─────────────────────────────────────────────────
    def get_account_summary(self) -> dict:
        """Henter konto-oversigt fra cached data."""
        values  = self.ib.accountValues()
        summary = {v.tag: v.value for v in values}
        return {
            "net_liquidation": float(summary.get("NetLiquidation", 0)),
            "cash_balance":    float(summary.get("CashBalance", 0)),
            "unrealized_pnl":  float(summary.get("UnrealizedPnL", 0)),
            "realized_pnl":    float(summary.get("RealizedPnL", 0)),
        }

    def get_positions(self) -> list:
        """Henter åbne positioner fra cached data."""
        return [{
            "ticker":   p.contract.symbol,
            "position": p.position,
            "avg_cost": p.avgCost,
        } for p in self.ib.positions()]

    # ── Historiske bars ───────────────────────────────────────
    async def get_historical_bars(
        self,
        ticker:       str,
        duration:     str = "1 D",
        bar_size:     str = "5 mins",
        what_to_show: str = "MIDPOINT",
    ) -> list[dict]:
        """Henter historiske OHLCV bars. Brug MIDPOINT uden for handelstid.

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
            contract = Stock(ticker, "SMART", "USD")
            await asyncio.wait_for(
                self.ib.qualifyContractsAsync(contract),
                timeout=10.0,
            )
            bars = await asyncio.wait_for(
                self.ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime    = "",
                    durationStr    = duration,
                    barSizeSetting = bar_size,
                    whatToShow     = what_to_show,
                    useRTH         = True,
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
            contract = Stock(ticker, "SMART", "USD")
            await asyncio.wait_for(
                self.ib.qualifyContractsAsync(contract),
                timeout=10.0,
            )
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
        ticker:      str,
        action:      str,
        quantity:    float,
        order_type:  str   = "MKT",
        limit_price: float = 0,
        source:      str   = "",
    ) -> Optional[dict]:
        """Sender en ordre til paper trading kontoen.

        source: strateginavn (fx "Momentum ORB"). Saettes som orderRef paa
        ordren, saa fills og ordrehistorik kan spores tilbage til den strategi
        der sendte ordren — afgoerende naar flere strategier deler samme konto.
        """
        if not self.connected:
            return None
        if not self.paper:
            raise ValueError("Brug kun place_paper_order på paper trading konto!")
        try:
            contract = Stock(ticker, "SMART", "USD")
            # Kun kvalificering har timeout — selve ordrelægningen (placeOrder)
            # er synkron og røres IKKE, så vi aldrig afbryder en ordre der er
            # på vej igennem.
            await asyncio.wait_for(
                self.ib.qualifyContractsAsync(contract),
                timeout=10.0,
            )
            order = MarketOrder(action, quantity) if order_type == "MKT" \
                    else LimitOrder(action, quantity, limit_price)
            if source:
                order.orderRef = source
            trade = self.ib.placeOrder(contract, order)
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

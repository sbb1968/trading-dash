"""
algo_momentum.py
────────────────
Momentum ORB Breakout Strategi — arver fra BaseStrategy

Strategi:
  - ORB breakout over første 15 minutters høj (09:30–09:44 ET)
  - Volumen mindst 1.5x gennemsnit
  - RSI(14) < 80
  - Handelsvindue: 09:45–10:30 ET
  - Stop loss: -2%  /  Take profit: +4%

Markedsbetingelser:
  - VIX < 15        → Ingen handel (for roligt)
  - VIX 15-40       → Normal handel (100% position size)
  - VIX > 40        → Reduceret handel (50% position size)
  - A/D ratio < 0.3 → Ingen handel (blodrød dag)
  - A/D ratio < 0.5 → 50% position size
  - SPY gap < -1.5% → Ingen handel
  - SPY volumen < 70% af 20d snit → Ingen handel (tynd dag)

Placering: C:\\Projects\\trading-dash\\backend\\algo_momentum.py
"""

import asyncio
import logging
from datetime import datetime, time as dtime
from collections import defaultdict
from typing import Optional
import pytz

from strategy_base import BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus
from ibkr_connect import IBKRConnection

logger = logging.getLogger(__name__)

# ── Strategi-konstanter ───────────────────────────────────────
STOP_PCT          = 0.02
TARGET_PCT        = 0.04
VOL_MULT          = 1.5
ORB_END           = dtime(9, 44)
TRADE_START       = dtime(9, 45)
TRADE_END         = dtime(10, 30)
CAPITAL_PER_TRADE = 2_500
ET                = pytz.timezone("America/New_York")

# Markedsbetingelser
VIX_MIN           = 15.0
VIX_REDUCED       = 40.0
AD_RATIO_MIN      = 0.3
AD_RATIO_REDUCED  = 0.5
SPY_GAP_MIN       = -1.5
SPY_VOL_MIN_PCT   = 0.70

# Retry
MAX_CONNECT_RETRIES = 3
CONNECT_RETRY_DELAY = 10
MIN_UNIVERSE_SIZE   = 3

FALLBACK_UNIVERSE = [
    "GME", "AMC", "CLOV", "SKLZ", "MVIS",
    "OCGN", "TLRY", "SNDL",
]


class MomentumORB(BaseStrategy):
    """
    Momentum ORB Breakout Strategi.

    Entry-kriterier:
      1. Pris bryder ORB High (første 15 min: 09:30-09:44 ET)
      2. Volumen >= 1.5x gennemsnitlig volumen
      3. RSI(14) < 80
      4. Handelsvindue: 09:45-10:30 ET

    Exit (hvad end kommer først):
      - Stop loss:   -2% fra entry
      - Take profit: +4% fra entry
      - Tidsbaseret: lukker alle positioner kl. 10:30 ET
    """

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn

        # Daglig tilstand
        self.universe:       list[str]        = []
        self.orb_highs:      dict[str, float] = {}

        # State machine per ticker — bestemmer hvor i breakout/retest-cyklus en ticker er
        # Mulige værdier: "waiting", "breakout_detected", "awaiting_retest", "entered", "done_for_day"
        self.ticker_state:   dict[str, str]   = {}

        # Hjælpefelter til retest-detektion
        self.breakout_time:  dict[str, datetime] = {}   # Hvornår blev breakout først set?
        self.retest_low:     dict[str, float]     = {}   # Lavpunkt under pullback (bruges som stop loss)

        self.avg_vols:       dict[str, float] = {}
        self.closes:         dict[str, list]  = defaultdict(list)
        self._position_data: dict[str, dict]  = {}
        self.trades:         list[dict]       = []
        self.total_pnl:      float            = 0.0

        self._position_size_pct: float = 1.0
        self._loop_task: Optional[asyncio.Task] = None

    # -----------------------------------------------------------------------
    # BaseStrategy interface
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Momentum ORB"

    @property
    def description(self) -> str:
        return "Opening Range Breakout på US small caps. Kører 09:45-10:30 ET."

    @property
    def asset_class(self) -> str:
        return "equity"

    async def pre_flight(self) -> tuple[bool, str]:
        checks = []

        self._status("started", "Pre-flight: Tjekker IBKR-forbindelse...")
        if not self.conn.connected:
            return False, "IBKR ikke forbundet"
        checks.append("IBKR forbundet")

        self._status("started", "Pre-flight: Henter konto-data...")
        account = self.conn.get_account_summary()
        if account.get("net_liquidation", 0) <= 0:
            return False, "Ingen konto-data"
        balance = account["net_liquidation"]
        checks.append(f"Konto aktiv — NLV: ${balance:,.2f}")

        if self._risk_manager:
            await self._risk_manager.update_nlv(balance)

        self._status("started", "Pre-flight: Tester datafeed (AAPL)...")
        test_bars = await self.conn.get_historical_bars(
            "AAPL", duration="1 D", bar_size="5 mins", what_to_show="TRADES"
        )
        if not test_bars:
            test_bars = await self.conn.get_historical_bars(
                "AAPL", duration="1 D", bar_size="5 mins", what_to_show="MIDPOINT"
            )
        if not test_bars:
            return False, "Kan ikke hente markedsdata"
        checks.append(f"Datafeed virker — {len(test_bars)} bars")

        # ── Markedsoverblik ──────────────────────────────────
        self._status("started", "Pre-flight: Analyserer markedsforhold...")
        from market_conditions import MarketConditionChecker
        checker    = MarketConditionChecker(self.conn, journal=self._journal)
        conditions = await checker.check()
        self._position_size_pct = conditions.position_size_pct

        # Broadcast detaljeret overblik til frontend
        if self._broadcast_fn:
            self._broadcast_fn(checker.format_detailed(conditions))

        status_msg = checker.format_status_message(conditions)
        checks.append(status_msg)

        if not conditions.skal_handle:
            summary = " | ".join([f"✅ {c}" for c in checks[:-1]])
            self._status("orb_ready",
                         f"Pre-flight OK: {summary}\n"
                         f"🔴 Ingen handel i dag — {status_msg}")
            return True, summary  # Returnerer True men skal_handle=False håndteres i _trading_loop

        summary = " | ".join([f"✅ {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        await asyncio.sleep(1)
        return True, summary

    async def on_start(self) -> None:
        # SAFETY GUARD #4 — undgå dobbelt-start
        if self._loop_task and not self._loop_task.done():
            logger.warning(f"_trading_loop kører allerede — afbryder ny start")
            return

        self._status("started", "Algoritme starter — udfører pre-flight tjek")
        await self._prepare_universe()
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def on_bar(self, ticker: str, bar: dict) -> None:
        pass

    async def on_stop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self._position_data:
            await self._close_all("strategi stoppet")

    # -----------------------------------------------------------------------
    # Status broadcast — kompatibel med LiveAlgo.tsx
    # -----------------------------------------------------------------------

    def _status(self, status: str, message: str):
        msg = {
            "type":      "algo_status",
            "status":    status,
            "message":   message,
            "total_pnl": round(self.total_pnl, 2),
            "positions": self.stats.open_positions,
            "trades":    len(self.trades),
            "time":      datetime.now(ET).strftime("%H:%M:%S"),
        }
        if self._broadcast_fn:
            self._broadcast_fn(msg)
        logger.info(f"[{status}] {message}")

    # -----------------------------------------------------------------------
    # Universe og ORB
    # -----------------------------------------------------------------------

    async def _prepare_universe(self):
        self._status("scanning", "Scanner markedet efter dagens kandidater...")

        for attempt in range(1, 3):
            self.universe = await self.conn.scan_top_gainers(max_results=25)
            if len(self.universe) >= MIN_UNIVERSE_SIZE:
                break
            if attempt < 2:
                self._status("scanning", f"Scanner returnerede få resultater — prøver igen ({attempt}/2)...")
                await asyncio.sleep(5)

        if len(self.universe) < MIN_UNIVERSE_SIZE:
            self.universe = FALLBACK_UNIVERSE
            self._status("universe_ready",
                         f"Scanner tom — bruger fallback universe: {', '.join(self.universe[:6])}")
        else:
            self._status("universe_ready",
                         f"Universe klar ({len(self.universe)} aktier): {', '.join(self.universe[:25])}...")

        await asyncio.sleep(1)

        self._status("loading_orb", f"Henter historiske bars og beregner ORB for {len(self.universe)} aktier...")
        ok_count     = 0
        fail_tickers = []

        for ticker in self.universe:
            bars = await self.conn.get_historical_bars(
                ticker, duration="1 D", bar_size="5 mins", what_to_show="TRADES"
            )
            if not bars:
                fail_tickers.append(ticker)
                continue

            orb_bars = [
                b for b in bars
                if hasattr(b["datetime"], "time")
                and dtime(9, 30) <= b["datetime"].time() <= dtime(9, 44)
            ]
            if orb_bars:
                self.orb_highs[ticker]   = max(b["high"] for b in orb_bars)
                self.ticker_state[ticker] = "waiting"
                self.avg_vols[ticker]  = sum(b["volume"] for b in bars if b["volume"] > 0) / max(len(bars), 1)
                self.closes[ticker]    = [b["close"] for b in bars]
                ok_count += 1
                logger.info(f"  {ticker}: ORB={self.orb_highs[ticker]:.2f}  AvgVol={self.avg_vols[ticker]:.0f}")

        msg = f"ORB klar for {ok_count}/{len(self.universe)} aktier"
        if fail_tickers:
            msg += f" (ingen data: {', '.join(fail_tickers[:3])})"
        self._status("orb_ready", f"✅ {msg} — Klar til handel kl. 09:45 ET (15:45 DK)")

    # -----------------------------------------------------------------------
    # Handels-loop
    # -----------------------------------------------------------------------

    async def _trading_loop(self):
        self._status("trading", "Overvåger markedet — venter på breakouts...")
        consecutive_errors = 0

        self._status("trading", "Overvåger markedet — venter på breakouts...")
        while self.status == StrategyStatus.RUNNING:
            now_et = datetime.now(ET)
            t      = now_et.time()

            if t < TRADE_START:
                self._status("orb_ready", "Venter på handelsvindue — starter kl. 09:45 ET")
                await asyncio.sleep(15)
                continue

            if t >= TRADE_END:
                if self._position_data:
                    self._status("trading", "Handelsdagens slut — lukker alle positioner")
                    await self._close_all("tidsbaseret exit 10:30")
                wins   = sum(1 for tr in self.trades if tr["pnl"] > 0)
                losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                self._status("done",
                             f"✅ Handelsdagen afsluttet | "
                             f"P&L: ${self.total_pnl:+,.2f} | "
                             f"{len(self.trades)} handler ({wins}W/{losses}L)")
                self.status = StrategyStatus.STOPPED
                break

            self._status("trading", f"Overvåger {len(self.universe)} aktier — {datetime.now(ET).strftime('%H:%M:%S')} ET")

            if self._position_size_pct == 0.0:
                self._status("orb_ready", "🔴 Ingen handel i dag — markedsforholdene er ikke til stede")
                await asyncio.sleep(60)
                continue
            try:
                for ticker in self.universe:
                    if self.status != StrategyStatus.RUNNING:
                        break
                    await self._check_ticker(ticker)
                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Fejl i handels-loop: {e}")

                if consecutive_errors >= 3:
                    self._status("trading", f"⚠ {consecutive_errors} fejl — forsøger genforbinding...")
                    reconnected = await self._reconnect()
                    if reconnected:
                        consecutive_errors = 0
                        self._status("trading", "✅ Genforbundet — fortsætter handel")
                    else:
                        self._status("error", "❌ Kunne ikke genforbinde — stopper")
                        self.status = StrategyStatus.ERROR
                        break

            await asyncio.sleep(30)

    # -----------------------------------------------------------------------
    # Genforbinding
    # -----------------------------------------------------------------------

    async def _reconnect(self) -> bool:
        for attempt in range(1, MAX_CONNECT_RETRIES + 1):
            try:
                self.conn.disconnect()
                await asyncio.sleep(2)
                ok = await self.conn.connect()
                if ok:
                    return True
            except Exception as e:
                logger.error(f"Genforbindelsesforsøg {attempt} fejlede: {e}")
            if attempt < MAX_CONNECT_RETRIES:
                await asyncio.sleep(CONNECT_RETRY_DELAY)
        return False

    # -----------------------------------------------------------------------
    # RSI
    # -----------------------------------------------------------------------

    def _rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = sum(d for d in deltas[-period:] if d > 0) / period
        losses = sum(-d for d in deltas[-period:] if d < 0) / period
        if losses == 0:
            return 100.0
        return 100 - (100 / (1 + gains / losses))

    # -----------------------------------------------------------------------
    # Ticker tjek
    # -----------------------------------------------------------------------

    async def _check_ticker(self, ticker: str):
        """
        Break & Retest entry-logik (state machine).

        Hver ticker bevæger sig én vej igennem disse tilstande:
          waiting → breakout_detected → awaiting_retest → entered → done_for_day

        Per ticker per dag tager vi MAKSIMALT én entry. Hvis stop loss → done_for_day.
        Det elimerer spam og matcher pro-traders' tilgang.
        """
        # SAFETY GUARD — handler aldrig hvis position_size_pct er 0
        if self._position_size_pct == 0.0:
            return

        # Hent snapshot
        snap = await self.conn.get_snapshot(ticker)
        if not snap:
            return

        price  = snap.get("last") or 0
        volume = snap.get("volume") or 0
        if price <= 0:
            return

        # PRIS-FILTER — kun small caps $1-20
        if price < 1.0 or price > 20.0:
            return

        # Skip tickers uden ORB beregnet
        if ticker not in self.orb_highs:
            return

        state    = self.ticker_state.get(ticker, "waiting")
        orb_high = self.orb_highs[ticker]

        # ─────────────────────────────────────────────────────────
        # STATE: ENTERED — håndtér exit-logik
        # ─────────────────────────────────────────────────────────
        if state == "entered":
            if ticker not in self._position_data:
                # Position blev lukket eksternt — markér som done
                self.ticker_state[ticker] = "done_for_day"
                return

            pos    = self._position_data[ticker]
            stop   = pos.get("stop_loss", pos["entry_price"] * (1 - STOP_PCT))
            target = pos["entry_price"] * (1 + TARGET_PCT)

            if price <= stop:
                await self._close(ticker, price, "stop loss")
                self.ticker_state[ticker] = "done_for_day"
            elif price >= target:
                await self._close(ticker, price, "take profit")
                self.ticker_state[ticker] = "done_for_day"
            return

        # ─────────────────────────────────────────────────────────
        # STATE: DONE_FOR_DAY — gør intet
        # ─────────────────────────────────────────────────────────
        if state == "done_for_day":
            return

        # ─────────────────────────────────────────────────────────
        # STATE: WAITING — leder efter første breakout
        # ─────────────────────────────────────────────────────────
        if state == "waiting":
            avg_vol = self.avg_vols.get(ticker, 0)
            rsi     = self._rsi(self.closes.get(ticker, []))

            if (price > orb_high
                    and volume >= avg_vol * VOL_MULT
                    and rsi < 80
                    and avg_vol > 0):
                # Breakout detekteret — IKKE entry endnu, vent på retest
                self.ticker_state[ticker]  = "breakout_detected"
                self.breakout_time[ticker] = datetime.now(ET)
                await self._log(f"📊 {ticker}: Breakout detekteret @ ${price:.2f} — venter på retest")
            return

        # ─────────────────────────────────────────────────────────
        # STATE: BREAKOUT_DETECTED — venter på pullback til ORB-niveau
        # ─────────────────────────────────────────────────────────
        if state == "breakout_detected":
            # Timeout — hvis ingen pullback inden for 5 min, drop dette breakout
            elapsed = (datetime.now(ET) - self.breakout_time[ticker]).total_seconds()
            if elapsed > 300:  # 5 minutter
                self.ticker_state[ticker] = "waiting"
                await self._log(f"⏱ {ticker}: Ingen pullback inden 5 min — venter på nyt breakout")
                return

            # Pullback betyder at prisen er TILBAGE PÅ eller UNDER ORB-high
            # (en lille tolerance på 0.1% for at fange pris præcis ved niveauet)
            if price <= orb_high * 1.001:
                self.ticker_state[ticker] = "awaiting_retest"
                self.retest_low[ticker]   = price   # foreløbig low — opdateres mens vi venter
                await self._log(f"📉 {ticker}: Pullback til ${price:.2f} — afventer retest-bekræftelse")
            return

        # ─────────────────────────────────────────────────────────
        # STATE: AWAITING_RETEST — venter på bounce-bekræftelse
        # ─────────────────────────────────────────────────────────
        if state == "awaiting_retest":
            # Track det laveste punkt under pullback — bruges som stop loss
            if price < self.retest_low[ticker]:
                self.retest_low[ticker] = price

            # Bekræftet retest: prisen er bounced TILBAGE OVER ORB-high niveau
            # Det er pro-tilgangen: vent på at "broken resistance bliver ny support"
            if price > orb_high:
                # Stop loss = lige under retest-low (med lille buffer)
                stop_loss = self.retest_low[ticker] * 0.998

                await self._log(
                    f"✅ {ticker}: Retest bekræftet @ ${price:.2f} "
                    f"(low: ${self.retest_low[ticker]:.2f}, stop: ${stop_loss:.2f})"
                )
                await self._open(ticker, price, stop_loss=stop_loss)
                # State sættes til "entered" inde i _open hvis det lykkes
            return

    # -----------------------------------------------------------------------
    # Åbn position
    # -----------------------------------------------------------------------

    async def _open(self, ticker: str, price: float, stop_loss: float = None):
        """
        Åbn long position med dynamisk eller % stop loss.

        Hvis stop_loss er angivet (fra retest-detektion), bruges den.
        Ellers falder vi tilbage på % stop fra entry (gammel adfærd, brugt af _close_all osv.).
        """
        # SAFETY GUARD — sidste linje før ordre afgives
        if self._position_size_pct == 0.0:
            logger.warning(f"BLOKERET: forsøg på at åbne {ticker} med position_size_pct=0")
            return

        # Brug max_position_size fra config hvis den findes, ellers default
        capital_per_trade = getattr(self.config, "max_position_size", CAPITAL_PER_TRADE) or CAPITAL_PER_TRADE
        capital           = capital_per_trade * self._position_size_pct
        shares            = int(capital / price)
        if shares <= 0:
            return

        order = OrderRequest(
            strategy_name=self.name,
            ticker=ticker,
            action="BUY",
            quantity=shares,
            order_type="MKT",
            asset_class="equity",
            reason=f"Break & Retest entry @ ${price:.2f}",
        )

        # Brug request_order kun hvis RiskManager er tilknyttet
        if self._risk_manager:
            approved = await self.request_order(order)
            if not approved:
                return
        
        result = await self.conn.place_paper_order(ticker, "BUY", shares)
        if result:
            # Beregn endelig stop loss: brug retest-baseret hvis sat, ellers fallback til %
            final_stop = stop_loss if stop_loss is not None else price * (1 - STOP_PCT)

            self._position_data[ticker] = {
                "ticker":      ticker,
                "entry_price": price,
                "shares":      shares,
                "entry_time":  datetime.now(ET).strftime("%H:%M:%S"),
                "stop_loss":   final_stop,
            }
            self.record_position_opened(ticker, price, shares, "long")

            # Skift state machine til entered
            self.ticker_state[ticker] = "entered"

            await self._log(
                f"📈 {ticker}: Position åbnet @ ${price:.2f} "
                f"(stop: ${final_stop:.2f}, target: ${price * (1 + TARGET_PCT):.2f})"
            )
            self.record_position_opened(ticker, price, shares, "long")

            if self._broadcast_fn:
                await self._broadcast_fn({
                    "type":   "algo_trade",
                    "action": "buy",
                    "ticker": ticker,
                    "price":  price,
                    "shares": shares,
                    "time":   datetime.now(ET).strftime("%H:%M:%S"),
                })
            self._status("trading",
                         f"📈 Købt {shares} {ticker} @ ${price:.2f} | "
                         f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}")

    # -----------------------------------------------------------------------
    # Luk position
    # -----------------------------------------------------------------------

    async def _close(self, ticker: str, price: float, reason: str):
        if ticker not in self._position_data:
            return

        pos    = self._position_data.pop(ticker)
        shares = pos["shares"]
        pnl    = self.record_position_closed(ticker, price)
        self.total_pnl += pnl

        await self.conn.place_paper_order(ticker, "SELL", shares)

        trade = {
            "ticker":      ticker,
            "entry_price": pos["entry_price"],
            "exit_price":  price,
            "shares":      shares,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2),
            "reason":      reason,
            "entry_time":  pos["entry_time"],
            "exit_time":   datetime.now(ET).strftime("%H:%M:%S"),
        }
        self.trades.append(trade)

        if self._broadcast_fn:
            await self._broadcast_fn({"type": "algo_trade", "action": "sell", **trade})

        emoji = "✅" if pnl > 0 else "❌"
        self._status("trading",
                     f"{emoji} Solgt {shares} {ticker} @ ${price:.2f} "
                     f"${pnl:+.2f} ({reason}) | "
                     f"P&L: ${self.total_pnl:+,.2f}")

    async def _close_all(self, reason: str):
        for ticker in list(self._position_data.keys()):
            snap  = await self.conn.get_snapshot(ticker)
            price = snap["last"] if snap and snap.get("last") \
                    else self._position_data[ticker]["entry_price"]
            await self._close(ticker, price, reason)

    # -----------------------------------------------------------------------
    # Strategy Card
    # -----------------------------------------------------------------------

    def get_strategy_card(self) -> dict:
        return {
            "name":        self.name,
            "description": self.description,
            "asset_class": self.asset_class,
            "instrument":  "US Equities (STK.US.MAJOR)",
            "timeframe":   "Intradag — lukker senest 10:30 ET",
            "entry": [
                "Pris bryder ORB High (første 15 min: 09:30-09:44 ET)",
                f"Volumen >= {VOL_MULT}x gennemsnit",
                "RSI(14) < 80",
                f"Handelsvindue: {TRADE_START.strftime('%H:%M')}-{TRADE_END.strftime('%H:%M')} ET",
            ],
            "exit": [
                f"Stop loss:    -{STOP_PCT*100:.0f}% fra entry",
                f"Take profit:  +{TARGET_PCT*100:.0f}% fra entry",
                f"Tidsbaseret:  {TRADE_END.strftime('%H:%M')} ET (market order)",
            ],
            "market_conditions": [
                f"VIX < {VIX_MIN}          → Ingen handel",
                f"VIX {VIX_MIN}-{VIX_REDUCED}      → Normal (100% position size)",
                f"VIX > {VIX_REDUCED}         → Reduceret (50% position size)",
                f"A/D ratio < {AD_RATIO_MIN}  → Ingen handel (blodrød dag)",
                f"A/D ratio < {AD_RATIO_REDUCED}  → 50% position size",
                f"SPY gap < {SPY_GAP_MIN}%   → Ingen handel",
                f"SPY vol < {SPY_VOL_MIN_PCT*100:.0f}% af 20d → Ingen handel (tynd dag)",
            ],
            "capital": {
                "per_trade":      CAPITAL_PER_TRADE,
                "max_positions":  self.config.max_open_positions,
                "max_daily_loss": self.config.max_daily_loss,
            },
        }


# ── Kør direkte fra terminal ──────────────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("\n🤖 Momentum ORB — Paper Trading")
    print("══════════════════════════════════════")
    print(f"Stop Loss:    -{STOP_PCT*100:.0f}%")
    print(f"Take Profit:  +{TARGET_PCT*100:.0f}%")
    print(f"Volumen:      {VOL_MULT}x gennemsnit")
    print(f"Tidsvindue:   {TRADE_START.strftime('%H:%M')}-{TRADE_END.strftime('%H:%M')} ET")
    print("══════════════════════════════════════\n")

    conn = IBKRConnection(paper_trading=True)
    connected = False

    for attempt in range(1, MAX_CONNECT_RETRIES + 1):
        print(f"Forbinder til IBKR (forsøg {attempt}/{MAX_CONNECT_RETRIES})...")
        ok = await conn.connect()
        if ok:
            connected = True
            break
        if attempt < MAX_CONNECT_RETRIES:
            await asyncio.sleep(CONNECT_RETRY_DELAY)

    if not connected:
        print("\n❌ Kunne ikke forbinde til IBKR.")
        return

    def on_message(msg):
        if msg["type"] == "algo_status":
            print(f"\n[{msg['status'].upper()}] {msg['message']}")
        elif msg["type"] == "algo_trade":
            if msg["action"] == "buy":
                print(f"  📈 KØB  {msg['shares']} {msg['ticker']} @ ${msg['price']:.2f}")
            else:
                pnl = msg.get("pnl", 0)
                print(f"  📉 SÆLG {msg['shares']} {msg['ticker']} @ "
                      f"${msg['exit_price']:.2f}  "
                      f"{'✅' if pnl >= 0 else '❌'} ${pnl:+.2f}  ({msg['reason']})")

    algo = MomentumORB(conn)
    algo._broadcast_fn = on_message

    print("Tryk Ctrl+C for at stoppe\n")
    try:
        await algo.start()
    except KeyboardInterrupt:
        await algo.stop()

    print(f"\nTotal P&L: ${algo.total_pnl:+,.2f}")
    print(f"Handler:   {len(algo.trades)}")
    conn.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

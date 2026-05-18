"""
algo_momentum.py
────────────────
Live Trading Wrapper for MomentumORB-strategien.

Denne fil indeholder INGEN strategi-logik længere — al entry- og exit-
beslutning sker via strategies/momentum_orb/. Denne fil er ansvarlig for:

  - IBKR-forbindelse og market data
  - Universe-scanning og dagens kontekst
  - Position-management (ordrer, fills, kapital)
  - State-broadcast til LiveAlgo.tsx
  - Markedsforhold-tjek (VIX, A/D ratio, SPY)
  - Genforbinding og fejlhåndtering

Når en ny strategi tilføjes (mean reversion, etc.) kan denne fil parameteriseres
til at køre den i stedet — eller vi kan oprette algo_<name>.py som en separat
tynd wrapper.

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

# Ny: strategi-arkitektur
from strategies.momentum_orb import MomentumORBStrategy, VARIANTS, LIVE_VARIANT_KEY
from strategies.momentum_orb.strategy import TRADE_START
from strategies.momentum_orb.exit import TRADE_END_TIME as TRADE_END
from strategies.base import Bar

# Cut-off for NYE entries — efter denne tid handler vi ikke længere på breakouts.
# Eksisterende positioner fortsætter dog indtil TRADE_END (15:55 ET force-close)
# eller indtil stop/target/trail udløses.
ENTRY_END = dtime(11, 0)

# Trade Forensics — logger indikatorer, tape og L2 ved hver entry/exit
from tape_buffer import TapeBuffer
from trade_forensics import build_entry_snapshot, build_exit_snapshot

# Markedet lukker kl. 16:00 ET — vi lukker positioner 5 min før så
# vi undgår closing auction-volatilitet
MARKET_CLOSE = dtime(15, 55)


logger = logging.getLogger(__name__)

# ── Konfiguration ────────────────────────────────────────────
CAPITAL_PER_TRADE = 2_500
ET                = pytz.timezone("America/New_York")

# Markedsbetingelser — bevaret uændret
VIX_MIN           = 15.0
VIX_REDUCED       = 40.0
AD_RATIO_MIN      = 0.3
AD_RATIO_REDUCED  = 0.5
SPY_GAP_MIN       = -1.5
SPY_VOL_MIN_PCT   = 0.70


MAX_CONNECT_RETRIES = 3
CONNECT_RETRY_DELAY = 10
MIN_UNIVERSE_SIZE   = 3

# ── ORB universe-filter (matcher scanner_engine.py defaults) ─────
# ORB er designet til small-cap momentum, derfor lavere pris-range
# end Konfluens ($5-$50). Volume-grænse sikrer likviditet til breakouts.
ORB_UNIVERSE_PRICE_MIN  = 1.0
ORB_UNIVERSE_PRICE_MAX  = 20.0
ORB_UNIVERSE_MIN_VOLUME = 500_000
ORB_UNIVERSE_TOP_N      = 25

FALLBACK_UNIVERSE = [
    "GME", "AMC", "CLOV", "SKLZ", "MVIS",
    "OCGN", "TLRY", "SNDL",
]


class MomentumORB(BaseStrategy):
    """
    Live trading-wrapper for MomentumORB-strategien.

    Denne klasses ansvar:
      - Asynkron loop hvert 30. sekund (IBKR snapshot pr. ticker)
      - Univers-scanning og ORB-beregning
      - Markedsforhold-tjek
      - Position-management (køb/salg ordrer via IBKR)
      - Status-broadcast til frontend

    Selve handelslogikken er DELEGERET til strategies/momentum_orb/.
    """

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn

        # ── Strategi-instans — al beslutningslogik bor her ────
        self._strategy = MomentumORBStrategy()
        self._variant_key = LIVE_VARIANT_KEY

        # ── Trade Forensics ──────────────────────────────────
        # TapeBuffer holder 180 sek tape + depth pr. ticker.
        # Bar-history gemmer historiske 5-min bars pr. ticker for indikatorer.
        # Begge initialiseres lazily ved første brug — kan være None hvis
        # forensics fejler ved opstart.
        self._tape_buffer: Optional[TapeBuffer] = None
        self._bar_history: dict[str, list[Bar]] = {}

        # Universe og dagens kontekst pr.
        self.universe:        list[str]        = []
        self._day_contexts:   dict[str, dict]  = {}     # ticker → day context

        # Position-tracking — bruger strategy.exit's Position-objekter
        from strategies.base import Position
        self._positions:      dict[str, Position] = {}

        # Legacy-felter UI/journal forventer
        self._position_data:  dict[str, dict]  = {}     # ticker → dict (UI-kompat)
        self.trades:          list[dict]       = []
        self.total_pnl:       float            = 0.0

        self._position_size_pct: float = 1.0
        self._loop_task: Optional[asyncio.Task] = None

    # -------------------------------------------------------------
    # BaseStrategy interface
    # -------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._strategy.name

    @property
    def description(self) -> str:
        return self._strategy.description

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

        if self._broadcast_fn:
            self._broadcast_fn(checker.format_detailed(conditions))

        status_msg = checker.format_status_message(conditions)
        checks.append(status_msg)

        if not conditions.skal_handle:
            summary = " | ".join([f"✅ {c}" for c in checks[:-1]])
            self._status("orb_ready",
                         f"Pre-flight OK: {summary}\n"
                         f"🔴 Ingen handel i dag — {status_msg}")
            return True, summary

        summary = " | ".join([f"✅ {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        await asyncio.sleep(1)
        return True, summary

    async def on_start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            logger.warning(f"_trading_loop kører allerede — afbryder ny start")
            return

        variant = VARIANTS[self._variant_key]
        self._status("started",
                     f"Algoritme starter — variant: {variant.name}")
        await self._prepare_universe()
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def on_bar(self, ticker: str, bar: dict) -> None:
        pass

    async def on_stop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self._positions:
            await self._close_all("strategi stoppet")

    # -------------------------------------------------------------
    # Status broadcast
    # -------------------------------------------------------------

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

    # -------------------------------------------------------------
    # Universe og dagskontekst — delegerer til strategien
    # -------------------------------------------------------------

    async def _prepare_universe(self):
        self._status("scanning", "Scanner markedet via TradingView...")

        # Vi bruger TV-screener (samme som Konfluens-strategien) i stedet
        # for IBKR's scanner. IBKR's TOP_PERC_GAIN returnerer en helt anden
        # liste end den Iben ser i sin TradingView screener. TV-screener
        # giver os præcis det univers Iben forventer.
        from strategies.confluence.tv_scanner import fetch_tv_top_gainers
        import asyncio as _asyncio

        for attempt in range(1, 3):
            try:
                loop = _asyncio.get_event_loop()
                results = await _asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: fetch_tv_top_gainers(
                            top_n             = ORB_UNIVERSE_TOP_N,
                            price_min         = ORB_UNIVERSE_PRICE_MIN,
                            price_max         = ORB_UNIVERSE_PRICE_MAX,
                            min_volume        = ORB_UNIVERSE_MIN_VOLUME,
                            require_all_green = True,  # 1D, 1W, 1M alle positive
                        ),
                    ),
                    timeout=15.0,
                )
                self.universe = [symbol for symbol, _, _, _ in results]
            except _asyncio.TimeoutError:
                logger.error("TV-screener timeout")
                self.universe = []
            except Exception as e:
                logger.error(f"TV-screener fejl: {e}")
                self.universe = []

            if len(self.universe) >= MIN_UNIVERSE_SIZE:
                break
            if attempt < 2:
                self._status("scanning",
                             f"Scanner returnerede få resultater — prøver igen ({attempt}/2)...")
                await asyncio.sleep(5)

        if len(self.universe) < MIN_UNIVERSE_SIZE:
            self.universe = FALLBACK_UNIVERSE
            self._status("universe_ready",
                         f"Scanner tom — bruger fallback universe: {', '.join(self.universe[:6])}")
        else:
            self._status("universe_ready",
                         f"Universe klar ({len(self.universe)} aktier, "
                         f"${ORB_UNIVERSE_PRICE_MIN:.0f}-${ORB_UNIVERSE_PRICE_MAX:.0f}, "
                         f"vol >{ORB_UNIVERSE_MIN_VOLUME:,}, alle 3 grønne): "
                         f"{', '.join(self.universe[:25])}")

        await asyncio.sleep(1)

        # Hent historiske bars og lad strategien bygge sin dagskontekst
        self._status("loading_orb",
                     f"Henter historiske bars og beregner kontekst for {len(self.universe)} aktier...")

        ok_count = 0
        fail_tickers = []
        today = datetime.now(ET).date()

        for ticker in self.universe:
            raw_bars = await self.conn.get_historical_bars(
                ticker, duration="1 D", bar_size="5 mins", what_to_show="TRADES"
            )
            if not raw_bars:
                fail_tickers.append(ticker)
                continue

            # Konvertér IBKR-bars til vores Bar-objekter
            bars = [
                Bar(
                    timestamp=b["datetime"] if hasattr(b["datetime"], "tzinfo") and b["datetime"].tzinfo
                              else ET.localize(b["datetime"]),
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=float(b["volume"]),
                )
                for b in raw_bars
                if hasattr(b["datetime"], "time")
            ]

            # Send aktiv variant-config med så ORB-vindue mm. matcher
            active_config = VARIANTS[self._variant_key]
            context = self._strategy.build_day_context(ticker, bars, config=active_config)
            if context is None:
                fail_tickers.append(ticker)
                continue

            self._day_contexts[ticker] = context
            self._strategy.entry.reset_for_day(today, context)
            # Gem bar-history så forensics kan beregne indikatorer ved entry/exit
            self._bar_history[ticker] = list(bars)
            ok_count += 1
            logger.info(
                f"  {ticker}: ORB H={context['orb_high']:.2f} "
                f"L={context['orb_low']:.2f}  AvgVol={context['avg_vol']:.0f}"
            )

        msg = f"Kontekst klar for {ok_count}/{len(self.universe)} aktier"
        if fail_tickers:
            msg += f" (ingen data: {', '.join(fail_tickers[:3])})"
        self._status("orb_ready",
                     f"✅ {msg} — Klar til handel kl. 09:45 ET (15:45 DK)")

        # ── Trade Forensics: start tape/depth subscriptions ───────────
        # Vi tape-subscriber alle tickers og forsøger depth på alle.
        # Depth fejler typisk for de fleste pga. IBKR's 3-samtidige-grænse —
        # det er accepteret og vi fortsætter uden L2 for dem.
        try:
            self._tape_buffer = TapeBuffer(self.conn)
            await self._tape_buffer.start()

            self._status("orb_ready",
                         f"Forensics: subscriber tape + L2 for {len(self.universe)} aktier...")

            tape_ok = depth_ok = depth_failed_count = 0
            for ticker in self.universe:
                result = await self._tape_buffer.subscribe(ticker)
                if result["tape_ok"]:
                    tape_ok += 1
                if result["depth_ok"]:
                    depth_ok += 1
                else:
                    depth_failed_count += 1
                # Lille pause for at undgå IBKR-rate-limits ved mange samtidige subs
                await asyncio.sleep(0.1)

            self._status("orb_ready",
                         f"✅ Forensics klar — Tape: {tape_ok}/{len(self.universe)}  "
                         f"L2: {depth_ok}/{len(self.universe)} "
                         f"({depth_failed_count} fejlede pga. IBKR-grænse)")
        except Exception as e:
            # Forensics-fejl må ALDRIG nedlægge handelsflowet
            logger.exception(f"Forensics setup fejlede — fortsætter uden: {e}")
            self._tape_buffer = None
            self._status("orb_ready",
                         f"⚠ Forensics-setup fejlede ({e}) — algoritmen kører videre uden")

    # -------------------------------------------------------------
    # Trading loop
    # -------------------------------------------------------------

    async def _trading_loop(self):
        self._status("trading", "Overvåger markedet — venter på breakouts...")
        consecutive_errors = 0

        while self.status == StrategyStatus.RUNNING:
            now_et = datetime.now(ET)
            t      = now_et.time()

            if t < TRADE_START:
                self._status("orb_ready", "Venter på handelsvindue — starter kl. 09:45 ET")
                await asyncio.sleep(15)
                continue

            # Efter ENTRY_END (11:00 ET): stop nye entries, men lad
            # eksisterende positioner køre videre til exit-regler triggrer
            # eller markedet lukker (15:55 ET).
            entries_allowed = t < ENTRY_END

            # Markedsluk — luk alle positioner som backup
            if t >= MARKET_CLOSE:
                if self._positions:
                    self._status("trading", "Markedet lukker — lukker alle positioner")
                    await self._close_all("market_close 15:55")
                wins   = sum(1 for tr in self.trades if tr["pnl"] > 0)
                losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                self._status("done",
                             f"✅ Handelsdagen afsluttet | "
                             f"P&L: ${self.total_pnl:+,.2f} | "
                             f"{len(self.trades)} handler ({wins}W/{losses}L)")
                self.status = StrategyStatus.STOPPED
                break

            self._status("trading",
                         f"Overvåger {len(self.universe)} aktier — "
                         f"{datetime.now(ET).strftime('%H:%M:%S')} ET")

            if self._position_size_pct == 0.0:
                self._status("orb_ready",
                             "🔴 Ingen handel i dag — markedsforholdene er ikke til stede")
                await asyncio.sleep(60)
                continue

            try:
                for ticker in self.universe:
                    if self.status != StrategyStatus.RUNNING:
                        break
                    await self._check_ticker(ticker, entries_allowed)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Fejl i handels-loop: {e}")
                if consecutive_errors >= 3:
                    self._status("trading",
                                 f"⚠ {consecutive_errors} fejl — forsøger genforbinding...")
                    reconnected = await self._reconnect()
                    if reconnected:
                        consecutive_errors = 0
                        self._status("trading", "✅ Genforbundet — fortsætter handel")
                    else:
                        self._status("error", "❌ Kunne ikke genforbinde — stopper")
                        self.status = StrategyStatus.ERROR
                        break

            await asyncio.sleep(30)

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

    # -------------------------------------------------------------
    # Ticker check — delegerer til strategiens entry og exit
    # -------------------------------------------------------------

    async def _check_ticker(self, ticker: str, entries_allowed: bool = True):
        """
        Tjek én ticker:
          - Hvis vi har position: opdatér exit-state og tjek exit
          - Ellers: byg en Bar fra snapshot og kald strategy.entry
            (kun hvis entries_allowed=True — dvs. før 10:30 ET)
        """
        if self._position_size_pct == 0.0:
            return

        snap = await self.conn.get_snapshot(ticker)
        if not snap:
            return

        price  = snap.get("last") or 0
        volume = snap.get("volume") or 0
        if price <= 0:
            return

        # Pris-filter — kun small caps $1-20
        if price < 1.0 or price > 20.0:
            return

        context = self._day_contexts.get(ticker)
        if context is None:
            return

        now_et = datetime.now(ET)

        # ── Har vi en åben position? ───────────────────────────
        if ticker in self._positions:
            position = self._positions[ticker]
            # Opdatér state med ny pris (high_seen = price for snapshot)
            self._strategy.exit.update(position, price, self._variant_key)

            # Tjek exit
            exit_decision = self._strategy.exit.check_exit_live(
                position, price, now_et.time(), self._variant_key
            )
            if exit_decision is not None:
                await self._close(ticker, exit_decision.exit_price, exit_decision.reason)
            return

        # ── Ingen position: konvertér snapshot til Bar og kald entry ──
        # Vi laver en "pseudo-bar" baseret på snapshot — strategien forventer
        # OHLC, men i live har vi kun last price. high=low=close=open=price.
        # Entry-engine bruger primært close og volume.
        pseudo_bar = Bar(
            timestamp=now_et,
            open=price, high=price, low=price, close=price,
            volume=volume,
        )

        # Efter 10:30 ET er nye entries deaktiveret — kun eksisterende
        # positioner får lov at fortsætte til exit-reglerne triggrer
        if not entries_allowed:
            return

        signal = self._strategy.entry.check_entry(ticker, pseudo_bar, context)
        if signal is not None:
            await self._open(signal)

    # -------------------------------------------------------------
    # Position open/close — delegerer til strategy.exit
    # -------------------------------------------------------------

    async def _open(self, signal):
        """Åbn position baseret på EntrySignal fra strategy.entry."""
        if self._position_size_pct == 0.0:
            logger.warning(f"BLOKERET: forsøg på at åbne {signal.ticker} med position_size_pct=0")
            return

        capital_per_trade = getattr(self.config, "max_position_size", CAPITAL_PER_TRADE) or CAPITAL_PER_TRADE
        capital  = capital_per_trade * self._position_size_pct
        shares   = int(capital / signal.entry_price)
        if shares <= 0:
            return

        # Long → BUY (køb først). Short → SELL (sælg uden at eje = short på IBKR)
        side   = signal.side
        action = "BUY" if side == "long" else "SELL"

        order = OrderRequest(
            strategy_name=self.name,
            ticker=signal.ticker,
            action=action,
            quantity=shares,
            order_type="MKT",
            asset_class="equity",
            reason=f"Break & Retest {side.upper()} entry @ ${signal.entry_price:.2f}",
        )
        if self._risk_manager:
            approved = await self.request_order(order)
            if not approved:
                return

        result = await self.conn.place_paper_order(signal.ticker, action, shares)
        if not result:
            return

        # Registrer hos OrdersTracker så Ordrer-vinduet viser algoens handler
        try:
            from orders_tracker import get_tracker
            order_id = result.get("order_id")
            if order_id:
                get_tracker().record_placed(
                    order_id=order_id,
                    source=self.name,
                    ticker=signal.ticker,
                    action=action,
                    shares=shares,
                    order_type="MKT",
                )
        except Exception as e:
            logger.warning(f"Kunne ikke registrere ordre hos tracker: {e}")

        # Lad strategien skabe Position med dens egen exit-state
        position = self._strategy.exit.open_position(signal, shares, self._variant_key)
        self._positions[signal.ticker] = position

        # UI-kompatibel struktur (LiveAlgo.tsx forventer dette)
        self._position_data[signal.ticker] = {
            "ticker":      signal.ticker,
            "entry_price": signal.entry_price,
            "shares":      shares,
            "side":        side,
            "entry_time":  signal.entry_time.strftime("%H:%M:%S"),
            "stop_loss":   position.state.stop,
        }
        self.record_position_opened(signal.ticker, signal.entry_price, shares, side)

        variant = VARIANTS[self._variant_key]
        # Long: ▲ entry op-emoji, target over. Short: ▼ entry ned-emoji, target under.
        side_emoji = "📈" if side == "long" else "📉"
        side_label = "LONG" if side == "long" else "SHORT"
        target_str = f"${position.state.target:.2f}" if position.state.target is not None else "—"
        await self._log(
            f"{side_emoji} {signal.ticker} ({side_label}): åbnet @ ${signal.entry_price:.2f} "
            f"(stop: ${position.state.stop:.2f}, target: {target_str}, "
            f"variant: {variant.name})"
        )

        if self._broadcast_fn:
            await self._broadcast_fn({
                "type":   "algo_trade",
                "action": "buy" if side == "long" else "sell_short",
                "side":   side,
                "ticker": signal.ticker,
                "price":  signal.entry_price,
                "shares": shares,
                "time":   signal.entry_time.strftime("%H:%M:%S"),
            })
        verb = "Købt" if side == "long" else "Shortet"
        self._status("trading",
                     f"{side_emoji} {verb} {shares} {signal.ticker} @ ${signal.entry_price:.2f} | "
                     f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}")

        # ── Trade Forensics: log indikatorer, tape og L2 ──────────────
        try:
            bars = self._bar_history.get(signal.ticker, [])
            ctx = self._day_contexts.get(signal.ticker, {})
            snapshot = build_entry_snapshot(
                ticker=signal.ticker,
                entry_price=signal.entry_price,
                entry_time=signal.entry_time,
                shares=shares,
                bars=bars,
                context=ctx,
                tape_buffer=self._tape_buffer,
                variant_name=variant.name,
            )
            if self._journal:
                await self._journal.log_event(
                    source=self.name,
                    event_type="trade_forensics",
                    symbol=signal.ticker,
                    payload=snapshot,
                )
        except Exception as e:
            logger.exception(f"Forensics (entry) for {signal.ticker} fejlede: {e}")

    async def _close(self, ticker: str, price: float, reason: str):
        """Luk position og bogfør trade. Long lukkes med SELL, short med BUY-to-cover."""
        if ticker not in self._positions:
            return

        position = self._positions.pop(ticker)
        pos_data = self._position_data.pop(ticker, None)

        shares = position.shares
        side   = position.side
        pnl    = self.record_position_closed(ticker, price)
        self.total_pnl += pnl

        # Long lukkes med SELL. Short lukkes med BUY (buy-to-cover).
        close_action = "SELL" if side == "long" else "BUY"
        close_result = await self.conn.place_paper_order(ticker, close_action, shares)

        # Registrer luknings-ordren hos OrdersTracker
        try:
            from orders_tracker import get_tracker
            close_order_id = close_result.get("order_id") if close_result else None
            if close_order_id:
                get_tracker().record_placed(
                    order_id=close_order_id,
                    source=self.name,
                    ticker=ticker,
                    action=close_action,
                    shares=shares,
                    order_type="MKT",
                )
        except Exception as e:
            logger.warning(f"Kunne ikke registrere {close_action} hos tracker: {e}")

        # PnL % spejlvendes for short: gevinst hvis exit_price < entry_price
        if side == "long":
            pnl_pct = (price - position.entry_price) / position.entry_price * 100
        else:
            pnl_pct = (position.entry_price - price) / position.entry_price * 100

        trade = {
            "ticker":      ticker,
            "side":        side,
            "entry_price": position.entry_price,
            "exit_price":  price,
            "shares":      shares,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "reason":      reason,
            "entry_time":  position.entry_time.strftime("%H:%M:%S"),
            "exit_time":   datetime.now(ET).strftime("%H:%M:%S"),
        }
        self.trades.append(trade)

        if self._broadcast_fn:
            # action="sell" for long-close (bagudkompat med UI), "buy_cover" for short-close
            close_event = "sell" if side == "long" else "buy_cover"
            await self._broadcast_fn({"type": "algo_trade", "action": close_event, **trade})

        emoji = "✅" if pnl > 0 else "❌"
        verb = "Solgt" if side == "long" else "Covered"
        self._status("trading",
                     f"{emoji} {verb} {shares} {ticker} @ ${price:.2f} "
                     f"${pnl:+.2f} ({reason}) | "
                     f"P&L: ${self.total_pnl:+,.2f}")

        # ── Trade Forensics: log exit-snapshot ─────────────────────────
        try:
            bars = self._bar_history.get(ticker, [])
            ctx = self._day_contexts.get(ticker, {})
            variant = VARIANTS[self._variant_key]
            snapshot = build_exit_snapshot(
                ticker=ticker,
                entry_price=position.entry_price,
                exit_price=price,
                entry_time=position.entry_time,
                exit_time=datetime.now(ET),
                shares=shares,
                pnl=pnl,
                reason=reason,
                bars=bars,
                context=ctx,
                tape_buffer=self._tape_buffer,
                variant_name=variant.name,
            )
            if self._journal:
                await self._journal.log_event(
                    source=self.name,
                    event_type="trade_forensics",
                    symbol=ticker,
                    payload=snapshot,
                )
        except Exception as e:
            logger.exception(f"Forensics (exit) for {ticker} fejlede: {e}")

    async def _close_all(self, reason: str):
        for ticker in list(self._positions.keys()):
            snap  = await self.conn.get_snapshot(ticker)
            price = snap["last"] if snap and snap.get("last") \
                    else self._positions[ticker].entry_price
            await self._close(ticker, price, reason)

    # -------------------------------------------------------------
    # Strategy Card — bevaret for UI
    # -------------------------------------------------------------

    def get_strategy_card(self) -> dict:
        v = VARIANTS[self._variant_key]
        exit_lines = [
            f"Variant aktiv: {v.name}",
            f"Stop mode:    {v.stop_mode}" + (f" ({v.fixed_stop_pct*100:.0f}%)"
                                              if v.stop_mode == 'fixed_pct' else ""),
        ]
        if v.breakeven_enabled:
            exit_lines.append(f"Break-even:   ved +{v.breakeven_trigger_pct*100:.0f}%")
        else:
            exit_lines.append("Break-even:   deaktiveret")
        if v.trail_enabled:
            exit_lines.append(f"Trail:        aktiveres ved +{v.trail_activate_pct*100:.0f}%, "
                              f"{v.trail_distance_pct*100:.1f}% under highest_high")
        else:
            exit_lines.append("Trail:        deaktiveret")
        exit_lines.append(f"Target:       +{v.target_pct*100:.0f}% (fjernes i stage 3)")
        exit_lines.append(f"Force-close:  {MARKET_CLOSE.strftime('%H:%M')} ET (markedsluk)")

        from strategies.momentum_orb.config import VOL_MULT, RSI_MAX

        entry_lines = [
            "LONG: pris bryder ORB High (første 15 min: 09:30-09:44 ET)",
        ]
        if v.enable_shorts:
            entry_lines.append("SHORT: pris bryder ORB Low (spejlvendt af long)")
        entry_lines.extend([
            f"Volumen >= {v.vol_mult}x gennemsnit",
            f"RSI(14) < {v.rsi_max} (long) / > {100 - v.rsi_max:.0f} (short)" if v.enable_shorts
                else f"RSI(14) < {v.rsi_max}",
            "Break & retest bekræftelse",
            f"Entry-vindue: {TRADE_START.strftime('%H:%M')}-{ENTRY_END.strftime('%H:%M')} ET",
        ])

        return {
            "name":        self.name,
            "description": self.description,
            "asset_class": self.asset_class,
            "instrument":  "US Equities (STK.US.MAJOR)",
            "timeframe":   f"Intradag — entries 09:45-{ENTRY_END.strftime('%H:%M')} ET, lukker senest {MARKET_CLOSE.strftime('%H:%M')} ET",
            "entry":       entry_lines,
            "exit": exit_lines,
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
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    variant = VARIANTS[LIVE_VARIANT_KEY]
    print("\n🤖 Momentum ORB — Paper Trading")
    print("══════════════════════════════════════")
    print(f"Aktiv variant: {variant.name}")
    print(f"Stop mode:     {variant.stop_mode}")
    print(f"Entry-vindue:  {TRADE_START.strftime('%H:%M')}-{ENTRY_END.strftime('%H:%M')} ET")
    print(f"Markedsluk:    {MARKET_CLOSE.strftime('%H:%M')} ET")
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
            action = msg["action"]
            if action == "buy":
                print(f"  📈 KØB    {msg['shares']} {msg['ticker']} @ ${msg['price']:.2f}")
            elif action == "sell_short":
                print(f"  📉 SHORT  {msg['shares']} {msg['ticker']} @ ${msg['price']:.2f}")
            elif action in ("sell", "buy_cover"):
                pnl  = msg.get("pnl", 0)
                verb = "SÆLG " if action == "sell" else "COVER"
                print(f"  📉 {verb} {msg['shares']} {msg['ticker']} @ "
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

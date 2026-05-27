"""
algo_confluence.py
──────────────────
Live Trading Wrapper for Konfluens-strategien.

Følger samme arkitektur som algo_momentum.py men adresserer Konfluens'
specifikke krav:

  - KONTINUERLIG INDIKATOR-HISTORIE: Pine-indikatorerne (HTF EMA, RSI,
    VWAP, pivots, swing-state) er afhængige af lang historie.
    Ved opstart henter vi 20 handelsdages 5min bars for hver ticker
    OG bygger ConfluenceStrategy.build_session_context() ÉN gang.
    Derefter appender vi live bars til konteksten efterhånden som de
    færdiggøres.

  - HELE DAGEN: Konfluens trigger fra 09:30 til 15:55 ET (ingen
    ENTRY_END som ORB har). Vi løber bar-loopet hele handelsdagen.

  - 5MIN BARS: Vi venter på færdige 5min bars (ikke snapshots).
    Hvert bar-ankomst trigger ét entry-tjek + opdatering af åbne
    positioner.

  - PRIS-FILTER OG TOP-25: Universe filtreres på $5-$50 åbnings-pris,
    præcis som backtest_confluence.

Denne fil indeholder INGEN strategi-logik — al entry- og exit-beslutning
sker via strategies/confluence/. Denne fil er ansvarlig for:

  - IBKR-forbindelse og bar-streaming
  - Universe-scanning og pris-filtrering
  - Position-management (ordrer, fills, kapital)
  - State-broadcast til LiveAlgo.tsx
  - Markedsforhold-tjek (genbruger MarketConditionChecker)
  - Genforbinding og fejlhåndtering

Placering: C:\\Projects\\trading-dash\\backend\\algo_confluence.py
"""

import asyncio
import logging
from datetime import datetime, timedelta, time as dtime, date as date_cls
from typing import Optional
import pytz

from strategy_base import BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus
from ibkr_connect import IBKRConnection

# Konfluens strategi-arkitektur
from strategies.confluence import (
    ConfluenceStrategy,
    VARIANTS,
    LIVE_VARIANT_KEY,
    ConfluenceVariantConfig,
)
from strategies.confluence.config import (
    SESSION_START_HHMM,
    SESSION_END_HHMM,
    UNIVERSE_PRICE_MIN,
    UNIVERSE_PRICE_MAX,
    UNIVERSE_MIN_VOLUME,
    UNIVERSE_TOP_N,
    MINTICK,
)
from strategies.confluence.exit import (
    REASON_STOP,
    REASON_TRAIL,
    REASON_SIGNAL_EXIT,
    REASON_SESSION_CLOSE,
    SESSION_FORCE_CLOSE,
)
from strategies.base import Bar, Position

# Trade Forensics — logger indikatorer, tape og L2 ved hver entry/exit
from tape_buffer import TapeBuffer
from trade_forensics import (
    build_confluence_entry_snapshot,
    build_confluence_exit_snapshot,
)

logger = logging.getLogger(__name__)

# ── Konstanter ────────────────────────────────────────────────
ET = pytz.timezone("America/New_York")
SESSION_START = dtime(*SESSION_START_HHMM)
SESSION_END   = dtime(*SESSION_END_HHMM)

# Force-close 5 min før session-slut (matcher MomentumORB og strategi-exit)
MARKET_CLOSE = dtime(15, 55)

# Genforbinding
MAX_CONNECT_RETRIES = 3
CONNECT_RETRY_DELAY = 10

# Universe
MIN_UNIVERSE_SIZE = 3   # Færre end dette og vi advarer (men kører videre)

# Warmup: antal handelsdages bars at hente ved opstart for indikator-historie
WARMUP_TRADING_DAYS = 20

# Loop-frekvens: 5min bars i Pine, så vi behøver kun checke hvert 5. minut.
# Vi tjekker dog hvert 30. sek for at fange bar-grænser præcist og for at
# kunne reagere hurtigt på exit-betingelser.
LOOP_SLEEP_SECONDS = 30

# Fallback (ikke brugt i normal drift — kun hvis scanner fejler komplet)
FALLBACK_UNIVERSE: list[str] = []


class ConfluenceLive(BaseStrategy):
    """
    Live trading-wrapper for Konfluens-strategien.

    Følger BaseStrategy-interfacet så StrategyManager kan administrere den
    parallelt med MomentumORB.
    """

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn

        # ── Strategi-instans — al beslutningslogik bor her ────
        self._strategy = ConfluenceStrategy()
        self._variant_key = LIVE_VARIANT_KEY

        # Universe og pre-computed indikator-context pr. ticker
        self.universe:        list[str]                  = []
        self._contexts:       dict[str, dict]            = {}    # ticker → session context
        self._bar_history:    dict[str, list[Bar]]       = {}    # ticker → bars (warmup + live)

        # Position-tracking — bruger strategy.exit's Position-objekter
        self._positions:      dict[str, Position]        = {}

        # Tracker hvilken bar-tid vi sidst har behandlet pr. ticker,
        # så vi ikke behandler samme bar to gange (vi poller hvert 30s
        # men en 5min bar ankommer kun hvert 5. min)
        self._last_bar_processed: dict[str, datetime]    = {}

        # ── Trade Forensics ──────────────────────────────────
        # TapeBuffer holder 180 sek tape + depth pr. ticker.
        # Initialiseres lazily i _prepare_universe — kan være None hvis
        # forensics-setup fejler ved opstart (algoen kører videre uden).
        self._tape_buffer: Optional[TapeBuffer] = None
        # Tracker MFE/MAE pr. position så vi kan logge dem ved exit
        self._mfe: dict[str, float] = {}  # ticker → max favorable excursion (pris)
        self._mae: dict[str, float] = {}  # ticker → max adverse excursion (pris)

        # Legacy-felter UI/journal forventer
        self._position_data:  dict[str, dict]            = {}
        self.trades:          list[dict]                 = []
        self.total_pnl:       float                      = 0.0

        self._position_size_pct: float                   = 1.0
        self._loop_task: Optional[asyncio.Task]          = None

        # Lag C-diagnostik: aggregeret statistik for dagen, bruges til
        # daglig opsummering (peak score pr. aktie, hyppigst manglende
        # betingelse). Nulstilles ved dagsstart.
        self._diag_max_score:     dict[str, int]         = {}
        self._diag_eval_count:    int                    = 0
        self._diag_scored_bars:   int                    = 0
        self._diag_entries:       int                    = 0
        self._diag_missing:       list[int]              = [0, 0, 0, 0, 0, 0]

    # -------------------------------------------------------------
    # BaseStrategy interface — properties
    # -------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._strategy.name      # "Konfluens"

    @property
    def description(self) -> str:
        return self._strategy.description

    @property
    def asset_class(self) -> str:
        return "equity"

    # -------------------------------------------------------------
    # Pre-flight
    # -------------------------------------------------------------

    async def pre_flight(self) -> tuple[bool, str]:
        """
        Tjek at vi kan starte handle:
          1. IBKR forbundet
          2. Konto-data tilgængelig
          3. Datafeed virker (AAPL test-fetch)
          4. Markedsforhold (genbruger MarketConditionChecker)
        """
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

        # Markedsforhold (samme som ORB)
        self._status("started", "Pre-flight: Analyserer markedsforhold...")
        try:
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
        except Exception as e:
            # Markedsforhold-fejl må ikke nedlægge algoen
            logger.exception(f"MarketConditionChecker fejlede: {e}")
            checks.append("Markedsforhold ikke vurderet (fejl ignoreret)")

        summary = " | ".join([f"✅ {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        await asyncio.sleep(1)
        return True, summary

    # -------------------------------------------------------------
    # Start
    # -------------------------------------------------------------

    async def on_start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            logger.warning("_trading_loop kører allerede — afbryder ny start")
            return

        variant = VARIANTS[self._variant_key]
        self._status("started",
                     f"Algoritme starter — variant: {variant.name}")
        await self._prepare_universe()
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def on_bar(self, ticker: str, bar: dict) -> None:
        # Vi bruger ikke event-driven bar-stream — vi poller selv i _trading_loop
        pass

    async def on_stop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self._positions:
            await self._close_all("strategi stoppet")

    # -------------------------------------------------------------
    # Status broadcast — bruger samme format som ORB så LiveAlgo.tsx
    # kan vise begge strategier
    # -------------------------------------------------------------

    def _status(self, status: str, message: str):
        msg = {
            "type":      "algo_status",
            "strategy":  self.name,
            "status":    status,
            "message":   message,
            "total_pnl": round(self.total_pnl, 2),
            "positions": self.stats.open_positions,
            "trades":    len(self.trades),
            "time":      datetime.now(ET).strftime("%H:%M:%S"),
        }
        if self._broadcast_fn:
            self._broadcast_fn(msg)
        logger.info(f"[Konfluens][{status}] {message}")

        # Gem også til journal så pre-flight-trin, universe og status-beskeder
        # kan ses i historik-loggen bagefter (ikke kun live via broadcast).
        # _status er synkron, så vi bruger fire-and-forget (samme mønster som
        # ORB's _status og record_position_opened). Best-effort.
        if self._journal:
            try:
                asyncio.create_task(self._journal.log_event(
                    source     = self.name,
                    event_type = "status",
                    payload    = {"status": status, "message": message},
                ))
            except Exception as e:
                logger.error(f"[{self.name}] _status journal-skriv fejlede: {e}")
    # -------------------------------------------------------------
    # Universe-forberedelse
    # -------------------------------------------------------------

    async def _prepare_universe(self):
        """
        Scan top gainers, filtrer på pris, og byg session-context for hver
        ticker. Det her er den DYRE del af opstart — kan tage 1-3 minutter
        fordi vi henter 20 dages bars pr. ticker.
        """
        self._status("scanning", "Scanner markedet efter dagens kandidater...")

        # Hent top-N fra IBKR scanner MED pris+volumen-filtre.
        # Vi bypass'er self.conn.scan_top_gainers fordi den IBKR-wrapper
        # ikke understøtter abovePrice/belowPrice/aboveVolume — den
        # returnerer rå TOP_PERC_GAIN hvilket er domineret af penny-stocks.
        # Lag C: nulstil dagens diagnostik-aggregering ved dagsstart, så
        # statistikken ikke akkumulerer hvis instansen kører over flere dage.
        self._diag_max_score.clear()
        self._diag_eval_count = 0
        self._diag_scored_bars = 0
        self._diag_entries = 0
        self._diag_missing = [0, 0, 0, 0, 0, 0]
        self.reset_diagnostics()   # nulstiller også Lag B's "kun ændringer"-state

        raw_tickers: list[str] = []
        for attempt in range(1, 3):
            raw_tickers = await self._scan_filtered_gainers(UNIVERSE_TOP_N)
            if len(raw_tickers) >= MIN_UNIVERSE_SIZE:
                break
            if attempt < 2:
                self._status("scanning",
                             f"Scanner returnerede {len(raw_tickers)} — prøver igen...")
                await asyncio.sleep(3)

        if len(raw_tickers) < MIN_UNIVERSE_SIZE:
            if FALLBACK_UNIVERSE:
                self._status("scanning",
                             f"Scanner gav for få — bruger fallback ({len(FALLBACK_UNIVERSE)})")
                raw_tickers = FALLBACK_UNIVERSE[:]
            else:
                self._status("error", "Scanner returnerede 0 tickers — afbryder")
                self.status = StrategyStatus.ERROR
                return

        self._status("scanning",
                     f"Scanner fandt {len(raw_tickers)} tickers fra TradingView "
                     f"(pris ${UNIVERSE_PRICE_MIN}-${UNIVERSE_PRICE_MAX}, vol >{UNIVERSE_MIN_VOLUME:,})")

        # ── Pris-filtrering ──────────────────────────────────
        # Vi henter dagens åbnings-pris for hver ticker. Hvis markedet er åbent
        # bruger vi snapshot (hurtigt). Hvis markedet er lukket falder vi tilbage
        # til seneste daily bar's close-pris (mere robust, virker døgnet rundt).
        # Hver snapshot/bar-call har timeout så vi ikke hænger evigt.
        now_et = datetime.now(ET)
        market_open = SESSION_START <= now_et.time() <= SESSION_END
        SNAPSHOT_TIMEOUT_SEC = 5.0

        passed: list[tuple[str, float]] = []
        snapshot_failures = 0
        for idx, ticker in enumerate(raw_tickers, 1):
            ref_price = None
            try:
                if market_open:
                    # Marked åbent — brug snapshot (hurtig)
                    snap = await asyncio.wait_for(
                        self.conn.get_snapshot(ticker),
                        timeout=SNAPSHOT_TIMEOUT_SEC,
                    )
                    if snap:
                        ref_price = snap.get("open") or snap.get("last")
                else:
                    # Marked lukket — brug seneste daily bar's close som proxy
                    daily_bars = await asyncio.wait_for(
                        self.conn.get_historical_bars(
                            ticker,
                            duration="2 D",
                            bar_size="1 day",
                            what_to_show="TRADES",
                        ),
                        timeout=SNAPSHOT_TIMEOUT_SEC,
                    )
                    if daily_bars:
                        last_bar = daily_bars[-1]
                        ref_price = (
                            last_bar.get("close") if isinstance(last_bar, dict)
                            else last_bar.close
                        )
            except asyncio.TimeoutError:
                logger.warning(f"  [{idx}/{len(raw_tickers)}] {ticker}: pris-lookup timeout")
                snapshot_failures += 1
                continue
            except Exception as e:
                logger.warning(f"  [{idx}/{len(raw_tickers)}] {ticker}: pris-lookup fejl — {e}")
                snapshot_failures += 1
                continue

            if not ref_price or ref_price <= 0:
                logger.debug(f"  [{idx}/{len(raw_tickers)}] {ticker}: ingen pris tilgængelig")
                snapshot_failures += 1
                continue

            if ref_price < UNIVERSE_PRICE_MIN or ref_price > UNIVERSE_PRICE_MAX:
                logger.info(f"  [{idx}/{len(raw_tickers)}] {ticker}: ${ref_price:.2f} "
                            f"udenfor ${UNIVERSE_PRICE_MIN}-${UNIVERSE_PRICE_MAX}")
                continue

            logger.info(f"  [{idx}/{len(raw_tickers)}] {ticker}: ${ref_price:.2f} ✓")
            passed.append((ticker, ref_price))

        if snapshot_failures == len(raw_tickers):
            self._status("error",
                         f"Kunne ikke hente pris for nogen af {len(raw_tickers)} tickers "
                         f"(marked sandsynligvis lukket eller IBKR-problem) — afbryder")
            self.status = StrategyStatus.ERROR
            return

        self.universe = [t for t, _ in passed]
        self._status("scanning",
                     f"{len(self.universe)} tickers passerede pris-filter "
                     f"(${UNIVERSE_PRICE_MIN}-${UNIVERSE_PRICE_MAX})")

        if not self.universe:
            self._status("error", "Ingen tickers efter pris-filter — afbryder")
            self.status = StrategyStatus.ERROR
            return

        # Lag A: universe-logging via den fælles BaseStrategy-metode (arvet).
        # Samme data som før, men skrivningen lever ét sted for alle strategier.
        await self.log_universe(
            self.universe,
            meta = {
                "raw_count":   len(raw_tickers),
                "open_prices": {t: p for t, p in passed},
                "price_min":   UNIVERSE_PRICE_MIN,
                "price_max":   UNIVERSE_PRICE_MAX,
            },
        )

        # ── Hent warmup-historie for hver ticker ──────────────
        config = VARIANTS[self._variant_key]
        target_date = datetime.now(ET).date()
        warmup_start = target_date - timedelta(days=int(WARMUP_TRADING_DAYS * 1.5) + 5)
        # Spring weekender over
        while warmup_start.weekday() >= 5:
            warmup_start -= timedelta(days=1)

        self._status("loading_orb",
                     f"Henter {WARMUP_TRADING_DAYS} dages warmup-historie for "
                     f"{len(self.universe)} tickers... (kan tage 1-2 min)")

        successful = []
        for i, ticker in enumerate(self.universe, 1):
            try:
                bars = await self._fetch_historical_5min_bars(
                    ticker, warmup_start, target_date
                )
            except Exception as e:
                logger.error(f"  {ticker}: warmup-fejl: {e}")
                continue

            if len(bars) < 50:
                logger.warning(f"  {ticker}: kun {len(bars)} bars — springer over")
                continue

            self._bar_history[ticker] = bars

            # Byg session-context (pre-compute alle indikatorer)
            context = self._strategy.build_session_context(ticker, bars, config=config)
            if context is None:
                logger.warning(f"  {ticker}: kunne ikke bygge session-context")
                continue

            self._contexts[ticker] = context
            successful.append(ticker)

            # Progress hvert 5. ticker
            if i % 5 == 0 or i == len(self.universe):
                self._status("loading_orb",
                             f"Warmup hentet for {i}/{len(self.universe)} tickers...")

        self.universe = successful

        # Indlæs context i entry-engine (én gang for alle tickers)
        for ticker in self.universe:
            self._strategy.entry.load_session_context(self._contexts[ticker])

        self._status("orb_ready",
                     f"✅ Universe klar — {len(self.universe)} tickers med fuld indikator-historie")

        # ── Trade Forensics: start tape/depth subscriptions ───────
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
                if result.get("tape_ok"):
                    tape_ok += 1
                if result.get("depth_ok"):
                    depth_ok += 1
                else:
                    depth_failed_count += 1
                # Lille pause for at undgå IBKR-rate-limits
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

    async def _scan_filtered_gainers(self, top_n: int) -> list[str]:
        """
        Hent top-N gainers via TradingView's screener API.

        Vi bruger TV i stedet for IBKR's scanner fordi IBKR's TOP_PERC_GAIN
        returnerer aktier på basis af noget der ikke matcher hvad TV viser.
        TV's API returnerer præcis den liste Iben kan se i sit TradingView
        "US Top Gainers" screener — samme priser, rækkefølge og procent-gain.

        Returnerer liste af symboler (max top_n stk). Tom liste hvis API fejler.
        """
        from strategies.confluence.tv_scanner import fetch_tv_top_gainers
        import asyncio as _asyncio

        # tv_scanner er en blocking-call (requests-baseret), så vi kører den
        # i en thread så vi ikke blokerer event-loopet
        try:
            loop = _asyncio.get_event_loop()
            results = await _asyncio.wait_for(
                loop.run_in_executor(None, fetch_tv_top_gainers, top_n),
                timeout=15.0,
            )
        except _asyncio.TimeoutError:
            logger.error("TV-screener timeout")
            return []
        except Exception as e:
            logger.error(f"TV-screener fejl: {e}")
            return []

        tickers = [symbol for symbol, _, _, _ in results]
        return tickers

    async def _fetch_historical_5min_bars(
        self,
        ticker: str,
        start_date: date_cls,
        end_date: date_cls,
    ) -> list[Bar]:
        """
        Hent 5min bars fra IBKR for én ticker over en periode.

        IBKR's get_historical_bars accepterer ikke end_datetime — den henter
        altid bars op til nu. Vi beregner derfor duration baseret på antal
        kalenderdage i intervallet og henter alt i ét kald.

        For LIVE warmup er det perfekt: vi vil have de seneste N dages bars
        op til nu.
        """
        bars: list[Bar] = []

        # Beregn antal kalenderdage fra start_date til end_date (inkl.)
        days_back = (end_date - start_date).days + 1
        if days_back < 1:
            days_back = 1

        # IBKR's duration-format: "N D" eller "N W"
        # Max 30 dage på 5min bars (IBKR-grænse), så vi capper for sikkerhed
        if days_back > 30:
            days_back = 30
        duration_str = f"{days_back} D"

        try:
            raw_bars = await self.conn.get_historical_bars(
                ticker,
                duration=duration_str,
                bar_size="5 mins",
                what_to_show="TRADES",
            )
        except Exception as e:
            logger.error(f"  {ticker}: get_historical_bars fejlede: {e}")
            return []

        if not raw_bars:
            return []

        for raw in raw_bars:
            # raw er dict fra IBKRConnection.get_historical_bars
            ts = raw.get("datetime") if isinstance(raw, dict) else getattr(raw, "date", None)
            if not isinstance(ts, datetime):
                continue
            if ts.tzinfo is None:
                ts = ET.localize(ts)
            else:
                ts = ts.astimezone(ET)

            o = raw.get("open")   if isinstance(raw, dict) else raw.open
            h = raw.get("high")   if isinstance(raw, dict) else raw.high
            l = raw.get("low")    if isinstance(raw, dict) else raw.low
            c = raw.get("close")  if isinstance(raw, dict) else raw.close
            v = raw.get("volume") if isinstance(raw, dict) else raw.volume

            bars.append(Bar(
                timestamp=ts,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v) if v else 0.0,
            ))

        return bars

    # -------------------------------------------------------------
    # Trading loop
    # -------------------------------------------------------------

    async def _trading_loop(self):
        """
        Hovedloopen. Kører hvert 30. sekund mens strategien er RUNNING.

        Pr. iteration:
          1. Tjek tid (er vi i handelsvinduet?)
          2. For hver ticker: hent seneste 5min bar (kun hvis ny)
          3. Append bar til _bar_history og opdater context
          4. Tjek exit på åbne positioner
          5. Tjek entry på lukkede positioner
        """
        self._status("trading", "Overvåger markedet — venter på 5min bars...")
        consecutive_errors = 0

        while self.status == StrategyStatus.RUNNING:
            now_et = datetime.now(ET)
            t      = now_et.time()

            # Før session-start: vent
            if t < SESSION_START:
                self._status("orb_ready",
                             f"Venter på handelsvindue — starter kl. {SESSION_START.strftime('%H:%M')} ET")
                await asyncio.sleep(LOOP_SLEEP_SECONDS)
                continue

            # Efter session-slut: luk alle positioner og afslut dagen
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

                # Lag C: daglig diagnostik-opsummering via arvet metode.
                _cond_names = ["trend(HTF)", "VWAP", "RSI", "higher-low", "candle", "volumen"]
                _most_missing = None
                if self._diag_scored_bars > 0:
                    _idx = max(range(6), key=lambda i: self._diag_missing[i])
                    _pct = round(self._diag_missing[_idx] / self._diag_scored_bars * 100, 1)
                    _most_missing = f"{_cond_names[_idx]} (manglede i {_pct}% af scorede bars)"
                _peak = max(self._diag_max_score.values()) if self._diag_max_score else None
                await self.log_daily_summary({
                    "universe_size":          len(self._diag_max_score),
                    "evaluations":            self._diag_eval_count,
                    "scored_bars":            self._diag_scored_bars,
                    "entries":                self._diag_entries,
                    "trades":                 len(self.trades),
                    "total_pnl":              round(self.total_pnl, 2),
                    "peak_score":             _peak,
                    "max_score_per_ticker":   dict(self._diag_max_score),
                    "most_missing_condition": _most_missing,
                    "missing_by_condition":   {
                        _cond_names[i]: self._diag_missing[i] for i in range(6)
                    },
                })

                self.status = StrategyStatus.STOPPED
                break

            self._status("trading",
                         f"Overvåger {len(self.universe)} aktier — "
                         f"{now_et.strftime('%H:%M:%S')} ET | "
                         f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}")

            if self._position_size_pct == 0.0:
                self._status("orb_ready",
                             "🔴 Ingen handel i dag — markedsforholdene er ikke til stede")
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
                logger.exception(f"Fejl i handels-loop: {e}")
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

            await asyncio.sleep(LOOP_SLEEP_SECONDS)

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

    async def _check_ticker(self, ticker: str):
        """
        Tjek én ticker pr. loop-iteration:
          1. Hent seneste færdige 5min bar
          2. Hvis ny: append til bar-history og opdater context
          3. Hvis åben position: opdatér state + tjek exit
          4. Hvis ingen position: tjek entry
        """
        if self._position_size_pct == 0.0:
            return

        context = self._contexts.get(ticker)
        if context is None:
            return

        # Hent seneste færdige 5min bar
        new_bar = await self._fetch_latest_bar(ticker)
        if new_bar is None:
            return

        last_processed = self._last_bar_processed.get(ticker)
        if last_processed is not None and new_bar.timestamp <= last_processed:
            # Vi har allerede behandlet denne bar
            return

        # ── Append bar til history og opdater context ─────────
        self._bar_history.setdefault(ticker, []).append(new_bar)
        self._last_bar_processed[ticker] = new_bar.timestamp

        # Genberegn indikatorer (vi pre-computer hele serien igen — det er
        # ikke effektivt men det giver SAMME resultat som backtest)
        # TODO: optimer med inkrementel beregning hvis det bliver flaskehals
        config = VARIANTS[self._variant_key]
        new_context = self._strategy.build_session_context(
            ticker, self._bar_history[ticker], config=config
        )
        if new_context is None:
            return

        self._contexts[ticker] = new_context
        self._strategy.entry.load_session_context(new_context)
        ind_df = new_context["ind_df"]

        # Slå indicator-row op for denne bar
        try:
            row = ind_df.loc[new_bar.timestamp]
            import pandas as pd
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
        except KeyError:
            return

        import pandas as pd
        now_et = datetime.now(ET)

        # ── Hvis åben position: opdatér state og tjek exit ────
        if ticker in self._positions:
            position = self._positions[ticker]

            # Track MFE/MAE for forensics
            if ticker in self._mfe:
                self._mfe[ticker] = max(self._mfe[ticker], new_bar.high)
            if ticker in self._mae:
                self._mae[ticker] = min(self._mae[ticker], new_bar.low)

            ema_fast       = float(row["ema_fast"])       if not pd.isna(row["ema_fast"])       else None
            last_swing_low = float(row["last_swing_low"]) if not pd.isna(row["last_swing_low"]) else None
            atr_val        = float(row["atr"])            if not pd.isna(row["atr"])            else None

            self._strategy.exit.update(
                position=position,
                high_seen=new_bar.close,
                variant_key=self._variant_key,
                low_seen=new_bar.low,
                ema_fast=ema_fast,
                last_swing_low=last_swing_low,
                atr_val=atr_val,
            )

            # Sync live state til trades-tabel — fanger trail-aktivering
            # og ratchet-bevægelser. Konfluens har ingen stage-streng,
            # vi afleder den fra trail_active.
            trade_id = position.metadata.get("trade_id")
            if trade_id and self._journal:
                state = position.state
                stage = "trailing" if state.trail_active else "initial"
                await self._journal.update_trade_state(
                    trade_id     = trade_id,
                    current_stop = state.current_stop,
                    current_stage= stage,
                    trail_stop   = state.trail_stop,
                )

            decision = self._strategy.exit.check_exit_bar(
                position, new_bar, self._variant_key, indicator_row=row
            )
            if decision is not None:
                await self._close(ticker, decision.exit_price, decision.reason)
            return

        # ── Ingen position: vurdér entry ──────────────────────
        # Kald evaluate() én gang. Ved signal genbruges resultatet i
        # check_entry (intet dobbeltarbejde). Ved afvisning logges grunden
        # via den arvede log_rejection_change (Lag B) — som kun skriver når
        # begrundelsen har ÆNDRET sig siden sidste bar for denne ticker.
        # Dette punkt nås kun ved NY bar (sikret af _last_bar_processed
        # tidligere i _check_ticker), så afvisnings-logging sker ikke hver loop.
        evaluation = self._strategy.entry.evaluate(ticker, new_bar, context)

        # Lag C: opsaml diagnostik-statistik (ny-bar-evaluering)
        self._diag_eval_count += 1
        _score = evaluation.get("score")
        _short = evaluation.get("short_form")
        if _score is not None and _short is not None:
            self._diag_scored_bars += 1
            if ticker not in self._diag_max_score or _score > self._diag_max_score[ticker]:
                self._diag_max_score[ticker] = _score
            for _i, _ch in enumerate(_short):
                if _ch == "·":
                    self._diag_missing[_i] += 1

        if evaluation["status"] == "signal":
            signal = self._strategy.entry.check_entry(
                ticker, new_bar, context, evaluation=evaluation
            )
            if signal is not None:
                self._diag_entries += 1
                await self._open(signal)
        else:
            await self.log_rejection_change(ticker, evaluation["reason"])

    async def _fetch_latest_bar(self, ticker: str) -> Optional[Bar]:
        """
        Hent den seneste FÆRDIGE 5min bar.

        Vi spørger om en lille batch (sidste 1-2 timer) og tager seneste bar
        der ligger før eller på nuværende 5min-grænse.
        """
        try:
            bars = await self.conn.get_historical_bars(
                ticker,
                duration="3600 S",   # 1 time
                bar_size="5 mins",
                what_to_show="TRADES",
            )
        except Exception as e:
            logger.warning(f"  {ticker}: kunne ikke hente bar: {e}")
            return None

        if not bars:
            return None

        # Tag seneste bar — IBKR returnerer i kronologisk rækkefølge
        raw = bars[-1]
        ts = raw.get("date") if isinstance(raw, dict) else raw.date
        if not isinstance(ts, datetime):
            return None
        if ts.tzinfo is None:
            ts = ET.localize(ts)
        else:
            ts = ts.astimezone(ET)

        o = raw.get("open")   if isinstance(raw, dict) else raw.open
        h = raw.get("high")   if isinstance(raw, dict) else raw.high
        l = raw.get("low")    if isinstance(raw, dict) else raw.low
        c = raw.get("close")  if isinstance(raw, dict) else raw.close
        v = raw.get("volume") if isinstance(raw, dict) else raw.volume

        return Bar(
            timestamp=ts,
            open=float(o), high=float(h), low=float(l), close=float(c),
            volume=float(v) if v else 0.0,
        )

    # -------------------------------------------------------------
    # Open / Close — ordre-håndtering
    # -------------------------------------------------------------

    async def _open(self, signal):
        """Åbn en long position baseret på entry-signal."""
        if self._position_size_pct == 0.0:
            logger.warning(f"BLOKERET: forsøg på at åbne {signal.ticker} med position_size_pct=0")
            return

        if self.stats.open_positions >= self.config.max_open_positions:
            return

        # ── Sizing ────────────────────────────────────────────
        # Vi bruger max_position_size (samme som ORB: $2500), IKKE 1%-risk
        # som Pine bruger. Beslutning truffet i designgennemgang.
        capital_per_trade = getattr(self.config, "max_position_size", 2500.0)
        capital  = capital_per_trade * self._position_size_pct
        shares   = int(capital / signal.entry_price)
        if shares <= 0:
            return

        order = OrderRequest(
            strategy_name=self.name,
            ticker=signal.ticker,
            action="BUY",
            quantity=shares,
            order_type="MKT",
            asset_class="equity",
            reason=f"Konfluens entry score={signal.metadata.get('entry_score')} "
                   f"bricks={signal.metadata.get('entry_short')}",
        )

        if self._risk_manager:
            approved = await self.request_order(order)
            if not approved:
                return

        result = await self.conn.place_paper_order(signal.ticker, "BUY", shares)
        if not result:
            return

        # Registrer hos OrdersTracker
        try:
            from orders_tracker import get_tracker
            order_id = result.get("order_id")
            if order_id:
                get_tracker().record_placed(
                    order_id=order_id,
                    source=self.name,
                    ticker=signal.ticker,
                    action="BUY",
                    shares=shares,
                    order_type="MKT",
                )
        except Exception as e:
            logger.warning(f"Kunne ikke registrere ordre hos tracker: {e}")

        # Lad strategien skabe Position med dens egen exit-state
        position = self._strategy.exit.open_position(signal, shares, self._variant_key)
        self._positions[signal.ticker] = position

        variant = VARIANTS[self._variant_key]
        score = signal.metadata.get("entry_score", 0)
        bricks = signal.metadata.get("entry_short", "······")

        # UI-kompatibel struktur — også gemmer forensics-metadata
        self._position_data[signal.ticker] = {
            "ticker":       signal.ticker,
            "entry_price":  signal.entry_price,
            "shares":       shares,
            "side":         "long",
            "entry_time":   signal.entry_time.strftime("%H:%M:%S"),
            "stop_loss":    position.state.initial_stop,
            # Forensics-felter (læses ved exit)
            "entry_score":  score,
            "entry_bricks": bricks,
        }
        self.record_position_opened(signal.ticker, signal.entry_price, shares, "long")

        # Initialisér MFE/MAE tracking
        self._mfe[signal.ticker] = signal.entry_price
        self._mae[signal.ticker] = signal.entry_price

        await self._log(
            f"📈 {signal.ticker}: åbnet @ ${signal.entry_price:.2f} "
            f"(stop: ${position.state.initial_stop:.2f}, "
            f"score: {score}/6, bricks: {bricks})"
        )

        # ── Trades-tabel: åbn trade-row ──────────────────────────
        # Konfluens har ikke target eller multi-stage som ORB. Vi gemmer
        # initial_stop som current_stop og stage="initial". Når trail
        # aktiveres opdaterer _check_position_in_bar trade_state.
        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source         = self.name,
                symbol         = signal.ticker,
                side           = "long",
                shares         = shares,
                entry_price    = signal.entry_price,
                entry_time     = signal.entry_time,
                variant        = variant.name,
                entry_reason   = f"Konfluens score={score}/6, bricks={bricks}",
                current_stop   = position.state.initial_stop,
                current_target = None,                 # Konfluens har intet target
                current_stage  = "initial",
                payload        = {"entry_score": score, "entry_bricks": bricks},
            )
            if trade_id:
                position.metadata["trade_id"] = trade_id

        if self._broadcast_fn:
            msg = {
                "type":     "algo_trade",
                "strategy": self.name,
                "action":   "buy",
                "ticker":   signal.ticker,
                "price":    signal.entry_price,
                "shares":   shares,
                "score":    score,
                "bricks":   bricks,
                "time":     signal.entry_time.strftime("%H:%M:%S"),
            }
            await self._broadcast_async(msg)

        self._status("trading",
                     f"📈 {signal.ticker}: købt {shares} @ ${signal.entry_price:.2f} "
                     f"| Bricks: {bricks}")

        # ── Trade Forensics: log indikatorer, tape og L2 ─────────
        try:
            bars = self._bar_history.get(signal.ticker, [])
            ctx  = self._contexts.get(signal.ticker, {})
            snapshot = build_confluence_entry_snapshot(
                ticker         = signal.ticker,
                entry_price    = signal.entry_price,
                entry_time     = signal.entry_time,
                shares         = shares,
                bars           = bars,
                context        = ctx,
                tape_buffer    = self._tape_buffer,
                variant_name   = variant.name,
                entry_score    = score,
                entry_bricks   = bricks,
                initial_stop   = position.state.initial_stop,
            )
            if self._journal:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "trade_forensics",
                    symbol     = signal.ticker,
                    payload    = snapshot,
                )
        except Exception as e:
            logger.exception(f"Forensics (entry) for {signal.ticker} fejlede: {e}")

    async def _close(self, ticker: str, price: float, reason: str):
        """Luk en åben position."""
        if ticker not in self._positions:
            return

        position = self._positions.pop(ticker)
        # Hent forensics-metadata FØR vi pop'er position_data
        pos_data = self._position_data.pop(ticker, None) or {}
        entry_score  = pos_data.get("entry_score", 0)
        entry_bricks = pos_data.get("entry_bricks", "······")

        shares = position.shares
        pnl    = self.record_position_closed(ticker, price)
        self.total_pnl += pnl

        close_result = await self.conn.place_paper_order(ticker, "SELL", shares)

        # Registrer luknings-ordren hos OrdersTracker
        try:
            from orders_tracker import get_tracker
            close_order_id = close_result.get("order_id") if close_result else None
            if close_order_id:
                get_tracker().record_placed(
                    order_id=close_order_id,
                    source=self.name,
                    ticker=ticker,
                    action="SELL",
                    shares=shares,
                    order_type="MKT",
                )
        except Exception as e:
            logger.warning(f"Kunne ikke registrere SELL hos tracker: {e}")

        pnl_pct = (price - position.entry_price) / position.entry_price * 100.0

        trade = {
            "ticker":      ticker,
            "side":        "long",
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

        # ── Trades-tabel: luk trade-row ──────────────────────────
        trade_id = position.metadata.get("trade_id")
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = price,
                exit_time   = datetime.now(ET),
                exit_reason = reason,
                pnl         = pnl,
                payload     = {
                    "max_favorable_excursion": self._mfe.get(ticker),
                    "max_adverse_excursion":   self._mae.get(ticker),
                },
            )

        if self._broadcast_fn:
            msg = {"type": "algo_trade", "strategy": self.name, "action": "sell", **trade}
            await self._broadcast_async(msg)

        emoji = "✅" if pnl > 0 else "❌"
        await self._log(
            f"{emoji} {ticker}: lukket @ ${price:.2f} "
            f"({reason}) | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
        )
        self._status("trading",
                     f"{emoji} {ticker}: solgt @ ${price:.2f} | P&L: ${pnl:+.2f}")

        # ── Trade Forensics: log exit-snapshot ─────────────────
        try:
            bars = self._bar_history.get(ticker, [])
            ctx  = self._contexts.get(ticker, {})
            variant = VARIANTS[self._variant_key]

            # Pop MFE/MAE — slet samtidig
            mfe = self._mfe.pop(ticker, None)
            mae = self._mae.pop(ticker, None)

            snapshot = build_confluence_exit_snapshot(
                ticker                  = ticker,
                entry_price             = position.entry_price,
                exit_price              = price,
                entry_time              = position.entry_time,
                exit_time               = datetime.now(ET),
                shares                  = shares,
                pnl                     = pnl,
                reason                  = reason,
                bars                    = bars,
                context                 = ctx,
                tape_buffer             = self._tape_buffer,
                variant_name            = variant.name,
                entry_score             = entry_score,
                entry_bricks            = entry_bricks,
                max_favorable_excursion = mfe,
                max_adverse_excursion   = mae,
            )
            if self._journal:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "trade_forensics",
                    symbol     = ticker,
                    payload    = snapshot,
                )
        except Exception as e:
            logger.exception(f"Forensics (exit) for {ticker} fejlede: {e}")

    async def _close_all(self, reason: str):
        """Luk alle åbne positioner (typisk ved market close eller stop)."""
        tickers = list(self._positions.keys())
        for ticker in tickers:
            position = self._positions[ticker]
            # Hent seneste pris til close
            snap = await self.conn.get_snapshot(ticker)
            price = (snap.get("last") if snap else None) or position.entry_price
            await self._close(ticker, price, reason)

    # -------------------------------------------------------------
    # Hjælpere
    # -------------------------------------------------------------

    async def _broadcast_async(self, msg: dict):
        """Send broadcast — håndtér både sync og async broadcast_fn."""
        if not self._broadcast_fn:
            return
        import inspect
        if inspect.iscoroutinefunction(self._broadcast_fn):
            await self._broadcast_fn(msg)
        else:
            self._broadcast_fn(msg)

    async def _log(self, message: str, level: str = "info"):
        """Log til journal OG broadcast til UI (Live Log).

        level-parameteren matcher base-klassens signatur. Uden den
        crasher base-klassens kald (pre-flight, nødstop, pause) der
        sender level=. Vi videresender desuden loggen til broadcast_fn
        så Konfluens' beskeder dukker op i Live Log ligesom ORB's.
        """
        full_msg = f"[Konfluens] {message}"
        if level == "error":
            logger.error(full_msg)
        elif level == "warning":
            logger.warning(full_msg)
        else:
            logger.info(full_msg)

        # Journal (uændret adfærd)
        if self._journal:
            try:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "log",
                    payload    = {"message": message, "level": level},
                )
            except Exception:
                pass

        # Broadcast til UI så beskeden vises i Live Log
        if self._broadcast_fn:
            import inspect
            msg = {
                "type":      "strategy_log",
                "strategy":  self.name,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "message":   message,
                "level":     level,
            }
            try:
                if inspect.iscoroutinefunction(self._broadcast_fn):
                    await self._broadcast_fn(msg)
                else:
                    self._broadcast_fn(msg)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────
# Standalone test (ikke brugt af main.py)
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio as _asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    async def main():
        conn = IBKRConnection(paper_trading=True)
        ok = await conn.connect()
        if not ok:
            print("\n💡 Tjekliste:")
            print("   1. TWS åben og logget ind på Paper Trading?")
            print("   2. Edit → Global Configuration → API → Settings")
            print("   3. Port: 7497")
            return

        def on_message(msg):
            if msg["type"] == "algo_status":
                print(f"\n[{msg['status'].upper()}] {msg['message']}")
            elif msg["type"] == "algo_trade":
                action = msg["action"]
                if action == "buy":
                    print(f"  📈 KØB    {msg['shares']} {msg['ticker']} @ ${msg['price']:.2f} "
                          f"[{msg.get('bricks', '······')}]")
                elif action == "sell":
                    pnl = msg.get("pnl", 0)
                    print(f"  📉 SÆLG   {msg['shares']} {msg['ticker']} @ ${msg['exit_price']:.2f}  "
                          f"{'✅' if pnl >= 0 else '❌'} ${pnl:+.2f}  ({msg['reason']})")

        algo = ConfluenceLive(conn, config=StrategyConfig(
            max_loss_per_trade=150.0,
            max_daily_loss=250.0,
            max_open_positions=3,
            max_position_size=2500.0,
        ))
        algo._broadcast_fn = on_message

        ok, summary = await algo.pre_flight()
        if not ok:
            print(f"Pre-flight fejlede: {summary}")
            return

        print("Tryk Ctrl+C for at stoppe\n")
        try:
            algo.status = StrategyStatus.RUNNING
            await algo.on_start()
            # Vent på loop-task
            while algo.status == StrategyStatus.RUNNING:
                await _asyncio.sleep(1)
        except KeyboardInterrupt:
            await algo.on_stop()

        print(f"\nTotal P&L: ${algo.total_pnl:+,.2f}")
        print(f"Handler:   {len(algo.trades)}")
        conn.disconnect()

    _asyncio.run(main())

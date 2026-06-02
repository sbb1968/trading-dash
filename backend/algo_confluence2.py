"""
algo_confluence2.py
───────────────────
Live Trading Wrapper for Konfluens 2 (impuls-strategien).

Strukturelt næsten identisk med algo_confluence.py (ConfluenceLive) — K2 deler
hele live-arkitekturen med K1. De væsentlige forskelle er:

  - 1-MIN BARS (ikke 5-min): K2 reagerer på selve impulsen i realtid, så vi
    venter på færdige 1-min bars og poller hyppigere (LOOP_SLEEP_SECONDS=15).

  - KORT WARMUP: K2's indikatorer (EMA9/20, RSI14, ATR14, vol/krop-snit) kræver
    kun min_warmup_bars=25 1-min bars. IBKR kan ikke levere 20 dages 1-min bars
    i ét kald (grænse ~1-2 dage), så vi henter ~2 dages 1-min bars — rigeligt.

  - IMPULS-ENTRY: 2 obligatoriske impuls-kriterier (volumen-spike + range/grøn)
    + mindst N kontekst-kriterier. Bricks er 7 tegn (V,B,G,E,K,R,T) mod K1's 6.

  - EXIT: live-variant A_impulse_low — hold til prisen falder under impuls-
    candlens low (vidt stop, ingen target). Exit-state har impulse_low /
    target_price / trail_stop (ikke K1's initial_stop / trail_active).

Al strategi-logik bor i strategies/confluence2/. Denne fil styrer IBKR-
forbindelse, universe-scanning, position-management, broadcast og diagnostik —
præcis som ConfluenceLive.

Placering: C:\\Projects\\trading-dash\\backend\\algo_confluence2.py
"""

import asyncio
import logging
from datetime import datetime, timedelta, time as dtime, date as date_cls
from typing import Optional
import pytz

from strategy_base import BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus
from ibkr_connect import IBKRConnection

# Konfluens 2 strategi-arkitektur
from strategies.confluence2 import (
    Confluence2Strategy,
    VARIANTS,
    LIVE_VARIANT_KEY,
)
from strategies.confluence2.config import (
    SESSION_START_HHMM,
    SESSION_END_HHMM,
    MINTICK,
    UNIVERSE_PRICE_MIN,
    UNIVERSE_PRICE_MAX,
    UNIVERSE_MIN_VOLUME,
    UNIVERSE_TOP_N,
)
from strategies.base import Bar, Position

# Exit-reason-konstanter. K2's Confluence2Exit returnerer disse strenge direkte
# (check_exit_bar) — K2 har ikke K1's REASON_*-konstanter, så vi definerer dem
# lokalt for læsbarhed.
REASON_STOP          = "stop"
REASON_TARGET        = "target"
REASON_TRAIL         = "trail"
REASON_SIGNAL_EXIT   = "signal_exit"
REASON_SESSION_CLOSE = "session_close"

# Trade Forensics — logger indikatorer, tape og L2 ved hver entry/exit.
# K2's context har færre indikator-kolonner end K1 (ingen vwap/swing/htf), men
# _confluence_setup_at_bar bruger row.get(...) så de manglende felter bliver
# bare None. Forensics-kald er desuden try/except-omsluttet og kan aldrig
# nedlægge handelsflowet.
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

# Force-close-backstop (sættes pr. instans ud fra variantens force_close_hhmm i
# __init__; dette er default hvis varianten ikke angiver et tidspunkt).
MARKET_CLOSE = dtime(15, 45)

# Genforbinding
MAX_CONNECT_RETRIES = 3
CONNECT_RETRY_DELAY = 10

# Universe
MIN_UNIVERSE_SIZE = 3   # Færre end dette og vi advarer (men kører videre)

# Warmup: K2 bruger 1-min bars. IBKR's 1-min historik er begrænset (~1-2 dage
# pr. kald) og K2's indikatorer kræver kun min_warmup_bars=25, så vi henter
# ~2 dages 1-min bars — rigeligt til EMA20/RSI14/ATR14/vol-snit.
WARMUP_TRADING_DAYS = 2

# Loop-frekvens: 1-min bars, så vi poller hyppigere end K1's 30 sek for at
# fange nye bars hurtigt og reagere prompte på exit-betingelser.
LOOP_SLEEP_SECONDS = 15

# Bar-evaluering logges for hver ny bar pr. ticker. Sæt til et tal for kun at
# logge bars med mindst den score (skruer ned på journal-volumen).
BAR_EVAL_MIN_SCORE = 0

# Heartbeat-interval i sekunder. 300 = hvert 5. minut.
HEARTBEAT_INTERVAL_SEC = 300

# Fallback (ikke brugt i normal drift — kun hvis scanner fejler komplet)
FALLBACK_UNIVERSE: list[str] = []

# Diagnostik-betingelser for K2 (7 bricks: V,B,G,E,K,R,T) i samme rækkefølge
# som Confluence2Entry.evaluate bygger short_form.
COND_NAMES = ["volumen", "krop/range", "stærk grøn",
              "ikke-overext", "break", "rsi-momentum", "trend"]
N_COND = len(COND_NAMES)


class Confluence2Live(BaseStrategy):
    """
    Live trading-wrapper for Konfluens 2 (impuls-strategien).

    Følger BaseStrategy-interfacet så StrategyManager kan administrere den
    parallelt med MomentumORB og Konfluens.
    """

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn

        # ── Strategi-instans — al beslutningslogik bor her ────
        self._strategy = Confluence2Strategy()
        self._variant_key = LIVE_VARIANT_KEY

        # Force-close-backstop fra variantens force_close_hhmm (default 15:45)
        try:
            self._market_close = dtime(*VARIANTS[self._variant_key].force_close_hhmm)
        except Exception:
            self._market_close = MARKET_CLOSE

        # Universe og pre-computed indikator-context pr. ticker
        self.universe:        list[str]                  = []
        self._contexts:       dict[str, dict]            = {}    # ticker → session context
        self._bar_history:    dict[str, list[Bar]]       = {}    # ticker → bars (warmup + live)

        # Position-tracking — bruger strategy.exit's Position-objekter
        self._positions:      dict[str, Position]        = {}

        # Tracker hvilken bar-tid vi sidst har behandlet pr. ticker, så vi ikke
        # behandler samme bar to gange (vi poller hvert 15s men en 1-min bar
        # ankommer kun hvert minut).
        self._last_bar_processed: dict[str, datetime]    = {}

        # ── Trade Forensics ──────────────────────────────────
        self._tape_buffer: Optional[TapeBuffer] = None
        self._mfe: dict[str, float] = {}  # ticker → max favorable excursion (pris)
        self._mae: dict[str, float] = {}  # ticker → max adverse excursion (pris)

        # Legacy-felter UI/journal forventer
        self._position_data:  dict[str, dict]            = {}
        self.trades:          list[dict]                 = []
        self.total_pnl:       float                      = 0.0

        self._position_size_pct: float                   = 1.0
        self._loop_task: Optional[asyncio.Task]          = None

        # Lag C-diagnostik: aggregeret statistik for dagen. K2 har 7 bricks.
        self._diag_max_score:     dict[str, int]         = {}
        self._diag_eval_count:    int                    = 0
        self._diag_scored_bars:   int                    = 0
        self._diag_entries:       int                    = 0
        self._diag_missing:       list[int]              = [0] * N_COND

    # -------------------------------------------------------------
    # BaseStrategy interface — properties
    # -------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._strategy.name      # "Konfluens 2"

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
          3. Datafeed virker (AAPL test-fetch, 1-min)
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
            "AAPL", duration="1 D", bar_size="1 min", what_to_show="TRADES"
        )
        if not test_bars:
            test_bars = await self.conn.get_historical_bars(
                "AAPL", duration="1 D", bar_size="1 min", what_to_show="MIDPOINT"
            )
        if not test_bars:
            return False, "Kan ikke hente markedsdata"
        checks.append(f"Datafeed virker — {len(test_bars)} bars")

        # Markedsforhold (samme som ORB/K1)
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

        # ── Reconciliation: OBSERVÉR afvigelser FØR vi handler ──
        # K2 kører på en delt multistrategi-konto. Derfor lukker vi INTET —
        # en blind flatten kunne lukke en anden strategis position. Vi
        # sammenligner blot journalens forventede nettoposition mod IBKR's
        # faktiske og rapporterer afvigelser.
        await self._reconcile_orphans()

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

    async def _reconcile_orphans(self) -> None:
        """Observér og rapportér afvigelser mellem journal og IBKR ved opstart.

        LUKKER INTET. K2 deler IBKR-konto med de øvrige strategier, så en blind
        flatten ville kunne lukke en anden strategis position. I stedet
        sammenligner vi journalens forventede nettoposition pr. symbol mod
        IBKR's faktiske (via position_ledger) og rapporterer tre slags
        afvigelser:

          - divergence:    journal og IBKR uenige om antal
          - ibkr_only:     IBKR holder noget journalen ikke kender
          - journal_only:  journalen har en åben handel IBKR ikke holder
                           (typisk en ordre der aldrig fyldte)

        Afvigelser kræver manuel vurdering — vi bogfører ikke og lukker ikke.
        Kører best-effort: en fejl her må ikke blokere dagens handel.
        """
        if self.conn is None or not self.conn.connected:
            self._status("started", "Reconciliation sprunget over — IBKR ikke forbundet")
            return

        try:
            ibkr_positions = self.conn.get_positions()
        except Exception as e:
            logger.error(f"[Konfluens 2] reconciliation: kunne ikke hente IBKR-positioner: {e}")
            return

        try:
            import position_ledger as pl
            db_path = self._journal.db_path if self._journal else None
            report = pl.reconcile_against_ibkr(db_path, ibkr_positions)
        except Exception as e:
            logger.error(f"[Konfluens 2] reconciliation: rapport fejlede: {e}")
            return

        n_div  = len(report["divergence"])
        n_ibkr = len(report["ibkr_only"])
        n_jrnl = len(report["journal_only"])

        if not (n_div or n_ibkr or n_jrnl):
            self._status("started", "Reconciliation: journal og IBKR stemmer overens")
            return

        self._status("started",
                     f"⚠ Reconciliation: afvigelser fundet — "
                     f"{n_div} divergens, {n_ibkr} kun-i-IBKR, {n_jrnl} kun-i-journal "
                     f"(LUKKER INTET — kræver manuel vurdering)")

        for d in report["divergence"]:
            await self._log(
                f"🔎 Reconciliation-divergens: {d['symbol']} — "
                f"journal={d['journal']} vs IBKR={d['ibkr']}", level="warning")
        for o in report["ibkr_only"]:
            await self._log(
                f"🔎 Reconciliation: {o['symbol']} holdes i IBKR ({o['ibkr']}) "
                f"men journalen kender den ikke", level="warning")
        for j in report["journal_only"]:
            await self._log(
                f"🔎 Reconciliation: {j['symbol']} står åben i journalen ({j['journal']}) "
                f"men IBKR holder den ikke (fyldte ordren aldrig?)", level="warning")

    # -------------------------------------------------------------
    # Status broadcast — samme format som ORB/K1
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
        logger.info(f"[Konfluens 2][{status}] {message}")

        # Gem også til journal så pre-flight-trin, universe og status-beskeder
        # kan ses i historik-loggen bagefter (ikke kun live via broadcast).
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
        ticker. For K2 er warmup-delen billigere end K1 (kun ~2 dages 1-min
        bars pr. ticker) men scanning + pris-filter er identisk.
        """
        self._status("scanning", "Scanner markedet efter dagens kandidater...")

        # Lag C: nulstil dagens diagnostik-aggregering ved dagsstart.
        self._diag_max_score.clear()
        self._diag_eval_count = 0
        self._diag_scored_bars = 0
        self._diag_entries = 0
        self._diag_missing = [0] * N_COND
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
        now_et = datetime.now(ET)
        market_open = SESSION_START <= now_et.time() <= SESSION_END
        SNAPSHOT_TIMEOUT_SEC = 5.0

        passed: list[tuple[str, float]] = []
        snapshot_failures = 0
        for idx, ticker in enumerate(raw_tickers, 1):
            ref_price = None
            try:
                if market_open:
                    snap = await asyncio.wait_for(
                        self.conn.get_snapshot(ticker),
                        timeout=SNAPSHOT_TIMEOUT_SEC,
                    )
                    if snap:
                        ref_price = snap.get("open") or snap.get("last")
                else:
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

        await self.log_universe(
            self.universe,
            meta = {
                "raw_count":   len(raw_tickers),
                "open_prices": {t: p for t, p in passed},
                "price_min":   UNIVERSE_PRICE_MIN,
                "price_max":   UNIVERSE_PRICE_MAX,
            },
        )

        # ── Hent warmup-historie for hver ticker (1-min) ──────
        config = VARIANTS[self._variant_key]
        target_date = datetime.now(ET).date()
        warmup_start = target_date - timedelta(days=int(WARMUP_TRADING_DAYS * 1.5) + 5)
        while warmup_start.weekday() >= 5:
            warmup_start -= timedelta(days=1)

        self._status("loading_orb",
                     f"Henter {WARMUP_TRADING_DAYS} dages warmup-historie (1-min) for "
                     f"{len(self.universe)} tickers...")

        successful = []
        for i, ticker in enumerate(self.universe, 1):
            try:
                bars = await self._fetch_historical_1min_bars(
                    ticker, warmup_start, target_date
                )
            except Exception as e:
                logger.error(f"  {ticker}: warmup-fejl: {e}")
                continue

            if len(bars) < 50:
                logger.warning(f"  {ticker}: kun {len(bars)} bars — springer over")
                continue

            self._bar_history[ticker] = bars

            context = self._strategy.build_session_context(ticker, bars, config=config)
            if context is None:
                logger.warning(f"  {ticker}: kunne ikke bygge session-context")
                continue

            self._contexts[ticker] = context
            successful.append(ticker)

            if i % 5 == 0 or i == len(self.universe):
                self._status("loading_orb",
                             f"Warmup hentet for {i}/{len(self.universe)} tickers...")

        self.universe = successful

        for ticker in self.universe:
            self._strategy.entry.load_session_context(self._contexts[ticker])

        self._status("orb_ready",
                     f"✅ Universe klar — {len(self.universe)} tickers med fuld indikator-historie")

        # ── Trade Forensics: start tape/depth subscriptions ───────
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
                await asyncio.sleep(0.1)

            self._status("orb_ready",
                         f"✅ Forensics klar — Tape: {tape_ok}/{len(self.universe)}  "
                         f"L2: {depth_ok}/{len(self.universe)} "
                         f"({depth_failed_count} fejlede pga. IBKR-grænse)")
        except Exception as e:
            logger.exception(f"Forensics setup fejlede — fortsætter uden: {e}")
            self._tape_buffer = None
            self._status("orb_ready",
                         f"⚠ Forensics-setup fejlede ({e}) — algoritmen kører videre uden")

    async def _scan_filtered_gainers(self, top_n: int) -> list[str]:
        """
        Hent top-N gainers via TradingView's screener API (samme kilde som K1).
        Returnerer liste af symboler (max top_n stk). Tom liste hvis API fejler.
        """
        from strategies.confluence.tv_scanner import fetch_tv_top_gainers
        import asyncio as _asyncio

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

    async def _fetch_historical_1min_bars(
        self,
        ticker: str,
        start_date: date_cls,
        end_date: date_cls,
    ) -> list[Bar]:
        """
        Hent 1-min bars fra IBKR for én ticker.

        IBKR's 1-min historik er stramt begrænset (typisk 1-2 dage pr. kald),
        og K2 behøver kun ~25+ bars. Vi capper derfor duration til 2 dage —
        rigeligt til at varme alle indikatorer op (EMA20/RSI14/ATR14/vol-snit).
        """
        bars: list[Bar] = []

        days_back = (end_date - start_date).days + 1
        if days_back < 1:
            days_back = 1
        # 1-min: IBKR returnerer ikke pålideligt mere end et par dage pr. kald.
        if days_back > 2:
            days_back = 2
        duration_str = f"{days_back} D"

        try:
            raw_bars = await self.conn.get_historical_bars(
                ticker,
                duration=duration_str,
                bar_size="1 min",
                what_to_show="TRADES",
            )
        except Exception as e:
            logger.error(f"  {ticker}: get_historical_bars fejlede: {e}")
            return []

        if not raw_bars:
            return []

        for raw in raw_bars:
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
        Hovedloopen. Kører hvert LOOP_SLEEP_SECONDS mens strategien er RUNNING.

        Pr. iteration:
          1. Tjek tid (er vi i handelsvinduet?)
          2. For hver ticker: hent seneste 1-min bar (kun hvis ny)
          3. Append bar til _bar_history og opdater context
          4. Tjek exit på åbne positioner
          5. Tjek entry på lukkede positioner
        """
        self._status("trading", "Overvåger markedet — venter på 1-min bars...")
        consecutive_errors = 0
        _shutdown_reason = "unknown"
        _last_heartbeat = datetime.now(ET)

        try:
            while self.status == StrategyStatus.RUNNING:
                now_et = datetime.now(ET)
                t      = now_et.time()

                if t < SESSION_START:
                    self._status("orb_ready",
                                 f"Venter på handelsvindue — starter kl. {SESSION_START.strftime('%H:%M')} ET")
                    await asyncio.sleep(LOOP_SLEEP_SECONDS)
                    continue

                if t >= self._market_close:
                    if self._positions:
                        self._status("trading", "Markedet lukker — lukker alle positioner")
                        await self._close_all(f"market_close {self._market_close.strftime('%H:%M')}")
                    wins   = sum(1 for tr in self.trades if tr["pnl"] > 0)
                    losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                    self._status("done",
                                 f"✅ Handelsdagen afsluttet | "
                                 f"P&L: ${self.total_pnl:+,.2f} | "
                                 f"{len(self.trades)} handler ({wins}W/{losses}L)")

                    await self._write_daily_diagnostics(reason="normal_close")

                    self.status = StrategyStatus.STOPPED
                    break

                self._status("trading",
                             f"Overvåger {len(self.universe)} aktier — "
                             f"{now_et.strftime('%H:%M:%S')} ET | "
                             f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}")

                if (now_et - _last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL_SEC:
                    _last_heartbeat = now_et
                    await self.log_heartbeat({
                        "evaluations":    self._diag_eval_count,
                        "scored_bars":    self._diag_scored_bars,
                        "entries":        self._diag_entries,
                        "open_positions": self.stats.open_positions,
                        "universe_size":  len(self.universe),
                    })

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

        except asyncio.CancelledError:
            _shutdown_reason = "cancelled"
            raise
        except Exception as e:
            _shutdown_reason = f"crash: {type(e).__name__}: {e}"
            logger.exception("_trading_loop crashede")
            raise
        finally:
            await self._write_daily_diagnostics(reason=_shutdown_reason)

    async def _write_daily_diagnostics(self, reason: str) -> None:
        """Skriv daily_diagnostics. Kaldes ved pæn nedlukning OG fra finally i
        _trading_loop, så diagnostik aldrig går tabt. Idempotent."""
        try:
            _most_missing = None
            if self._diag_scored_bars > 0:
                _idx = max(range(N_COND), key=lambda i: self._diag_missing[i])
                _pct = round(self._diag_missing[_idx]
                             / self._diag_scored_bars * 100, 1)
                _most_missing = (f"{COND_NAMES[_idx]} "
                                 f"(manglede i {_pct}% af scorede bars)")
            _peak = (max(self._diag_max_score.values())
                     if self._diag_max_score else None)
            await self.log_daily_summary({
                "shutdown_reason":        reason,
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
                    COND_NAMES[i]: self._diag_missing[i] for i in range(N_COND)
                },
            })
        except Exception as e:
            logger.exception(f"Kunne ikke skrive daily_diagnostics: {e}")

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
          1. Hent seneste færdige 1-min bar
          2. Hvis ny: append til bar-history og opdater context
          3. Hvis åben position: opdatér state + tjek exit
          4. Hvis ingen position: tjek entry
        """
        if self._position_size_pct == 0.0:
            return

        context = self._contexts.get(ticker)
        if context is None:
            return

        new_bar = await self._fetch_latest_bar(ticker)
        if new_bar is None:
            return

        last_processed = self._last_bar_processed.get(ticker)
        if last_processed is not None and new_bar.timestamp <= last_processed:
            return

        # ── Append bar til history og opdater context ─────────
        self._bar_history.setdefault(ticker, []).append(new_bar)
        self._last_bar_processed[ticker] = new_bar.timestamp

        # Genberegn indikatorer over hele serien — samme resultat som backtest.
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

        # ── Hvis åben position: opdatér state og tjek exit ────
        if ticker in self._positions:
            position = self._positions[ticker]

            # Track MFE/MAE for forensics
            if ticker in self._mfe:
                self._mfe[ticker] = max(self._mfe[ticker], new_bar.high)
            if ticker in self._mae:
                self._mae[ticker] = min(self._mae[ticker], new_bar.low)

            # K2's exit.update tager kun low_seen (trail_hl løfter trail-stop til
            # nye higher-lows; impulse_low-mode ignorerer det).
            self._strategy.exit.update(
                position=position,
                high_seen=new_bar.close,
                variant_key=self._variant_key,
                low_seen=new_bar.low,
            )

            # Sync live state til trades-tabel. K2's aktive stop er trail_stop
            # (= impulse_low når der ikke trailes); stage afledes af om trailen
            # er løftet over impuls-low.
            trade_id = position.metadata.get("trade_id")
            if trade_id and self._journal:
                state = position.state
                stage = "trailing" if state.trail_stop > state.impulse_low else "initial"
                await self._journal.update_trade_state(
                    trade_id     = trade_id,
                    current_stop = state.trail_stop,
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
        # Vi evaluerer mod new_context (den netop genbyggede), så impuls-baren
        # rent faktisk har en indikator-række — ellers ville evaluate returnere
        # "skip" på den friske bar.
        evaluation = self._strategy.entry.evaluate(ticker, new_bar, new_context)

        # Lag C: opsaml diagnostik-statistik (ny-bar-evaluering)
        self._diag_eval_count += 1
        _score = evaluation.get("score")
        _short = evaluation.get("short_form")
        if _score is not None and _short is not None:
            self._diag_scored_bars += 1
            if ticker not in self._diag_max_score or _score > self._diag_max_score[ticker]:
                self._diag_max_score[ticker] = _score
            for _i, _ch in enumerate(_short):
                if _i < N_COND and _ch == "·":
                    self._diag_missing[_i] += 1

        # Lag B+: log DENNE bars evaluering.
        _bar_et = new_bar.timestamp.astimezone(ET).strftime("%H:%M") \
            if hasattr(new_bar.timestamp, "astimezone") else str(new_bar.timestamp)
        if _score is None or _score >= BAR_EVAL_MIN_SCORE:
            await self.log_bar_evaluation(
                ticker      = ticker,
                bar_time_et = _bar_et,
                status      = evaluation["status"],
                score       = _score,
                short_form  = _short,
                reason      = evaluation.get("reason"),
            )

        if evaluation["status"] == "signal":
            signal = self._strategy.entry.check_entry(
                ticker, new_bar, new_context, evaluation=evaluation
            )
            if signal is not None:
                self._diag_entries += 1
                await self._open(signal)
        else:
            await self.log_rejection_change(ticker, evaluation["reason"])

    async def _fetch_latest_bar(self, ticker: str) -> Optional[Bar]:
        """
        Hent den seneste FÆRDIGE 1-min bar.

        Vi spørger om en lille batch (sidste time) og tager seneste bar.
        """
        try:
            bars = await self.conn.get_historical_bars(
                ticker,
                duration="3600 S",   # 1 time
                bar_size="1 min",
                what_to_show="TRADES",
            )
        except Exception as e:
            logger.warning(f"  {ticker}: kunne ikke hente bar: {e}")
            return None

        if not bars:
            return None

        raw = bars[-1]
        ts = raw.get("datetime") if isinstance(raw, dict) else raw.date
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
        """Åbn en long position baseret på impuls-entry-signal."""
        if self._position_size_pct == 0.0:
            logger.warning(f"BLOKERET: forsøg på at åbne {signal.ticker} med position_size_pct=0")
            return

        if self.stats.open_positions >= self.config.max_open_positions:
            return

        # ── Sizing ────────────────────────────────────────────
        capital_per_trade = getattr(self.config, "max_position_size", 2500.0)
        capital  = capital_per_trade * self._position_size_pct
        shares   = int(capital / signal.entry_price)
        if shares <= 0:
            return

        # K2's metadata: score (kontekst-hits) og bricks (7-tegns short_form).
        score  = signal.metadata.get("score", 0)
        bricks = signal.metadata.get("bricks", "") or ""

        order = OrderRequest(
            strategy_name=self.name,
            ticker=signal.ticker,
            action="BUY",
            quantity=shares,
            order_type="MKT",
            asset_class="equity",
            reason=f"Konfluens 2 impuls-entry score={score} bricks={bricks}",
        )

        if self._risk_manager:
            approved = await self.request_order(order)
            if not approved:
                return

        result = await self.conn.place_paper_order(signal.ticker, "BUY", shares, source=self.name)
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
        # K2's stop er impuls-candlens low; target_price er sat for target_r-mode
        # (None for live-variant A_impulse_low).
        stop_level = position.state.impulse_low
        target     = position.state.target_price

        self._position_data[signal.ticker] = {
            "ticker":       signal.ticker,
            "entry_price":  signal.entry_price,
            "shares":       shares,
            "side":         "long",
            "entry_time":   signal.entry_time.strftime("%H:%M:%S"),
            "stop_loss":    stop_level,
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
            f"(impuls-low stop: ${stop_level:.2f}, "
            f"kontekst-score: {score}, bricks: {bricks})"
        )

        # ── Trades-tabel: åbn trade-row ──────────────────────────
        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source         = self.name,
                symbol         = signal.ticker,
                side           = "long",
                shares         = shares,
                entry_price    = signal.entry_price,
                entry_time     = signal.entry_time,
                variant        = variant.name,
                entry_reason   = f"Konfluens 2 impuls, score={score}, bricks={bricks}",
                current_stop   = stop_level,
                current_target = target,
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
                initial_stop   = stop_level,
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

        position = self._positions[ticker]
        shares   = position.shares

        # ── Send luknings-ordren FØR vi bogfører noget ──────────────────
        close_result = await self.conn.place_paper_order(ticker, "SELL", shares, source=self.name)
        if not close_result:
            logger.error(
                f"[Konfluens 2] _close({ticker}): SELL-ordre kunne IKKE sendes "
                f"— beholder position åben, ingen journal-close"
            )
            self._status("trading",
                         f"⚠ {ticker}: lukkeordre ikke sendt — position forbliver åben")
            return

        fill = close_result.get("avg_fill")
        if fill and fill > 0:
            price = fill

        position = self._positions.pop(ticker)
        pos_data = self._position_data.pop(ticker, None) or {}
        entry_score  = pos_data.get("entry_score", 0)
        entry_bricks = pos_data.get("entry_bricks", "")

        pnl = self.record_position_closed(ticker, price)
        self.total_pnl += pnl

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
        """Log til journal OG broadcast til UI (Live Log)."""
        full_msg = f"[Konfluens 2] {message}"
        if level == "error":
            logger.error(full_msg)
        elif level == "warning":
            logger.warning(full_msg)
        else:
            logger.info(full_msg)

        if self._journal:
            try:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "log",
                    payload    = {"message": message, "level": level},
                )
            except Exception:
                pass

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
                          f"[{msg.get('bricks', '')}]")
                elif action == "sell":
                    pnl = msg.get("pnl", 0)
                    print(f"  📉 SÆLG   {msg['shares']} {msg['ticker']} @ ${msg['exit_price']:.2f}  "
                          f"{'✅' if pnl >= 0 else '❌'} ${pnl:+.2f}  ({msg['reason']})")

        algo = Confluence2Live(conn, config=StrategyConfig(
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
            while algo.status == StrategyStatus.RUNNING:
                await _asyncio.sleep(1)
        except KeyboardInterrupt:
            await algo.on_stop()

        print(f"\nTotal P&L: ${algo.total_pnl:+,.2f}")
        print(f"Handler:   {len(algo.trades)}")
        conn.disconnect()

    _asyncio.run(main())

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
    UNIVERSE_MKT_CAP_MIN,
    UNIVERSE_MKT_CAP_MAX,
    UNIVERSE_ATR_PCT_MIN,
    UNIVERSE_EXCHANGES,
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

# ── Force-close-robusthed ──────────────────────────────────────
# Ved market-close venter vi på BEKRÆFTET fyldning før vi bogfører en lukning,
# og genforsøger ufyldte. Budgettet skal passe inden for runway'en fra
# force_close (15:45 ET) til markedslukning (16:00 ET) — ~15 min, rigeligt.
CLOSE_FILL_WAIT_SEC      = 8    # sek place_paper_order venter på fyldning af en lukke-ordre
RECONCILE_TIMEOUT_SEC    = 30   # maks sekunder opstarts-reconcile må tage før den springes over
FORCE_CLOSE_MAX_ATTEMPTS = 4    # antal gange _close_all genforsøger ufyldte lukninger
FORCE_CLOSE_RETRY_DELAY  = 4    # sek mellem genforsøg

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

        # K2-specifik state (fælles state — universe, _bar_history,
        # _last_bar_processed, _positions, trades, total_pnl, _loop_task,
        # _tape_buffer, _mfe, _mae — sættes nu i BaseStrategy.__init__).
        self._contexts:       dict[str, dict]            = {}    # ticker → session context
        self._position_data:  dict[str, dict]            = {}    # legacy UI/journal
        self._position_size_pct: float                   = 1.0

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
        # Reconcile er best-effort OG tidsbegrænset: hverken en fejl (try/except i
        # _reconcile_orphans) eller en hang (timeout her) må blokere handelsstarten.
        try:
            await asyncio.wait_for(self._reconcile_orphans(), timeout=RECONCILE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error(f"[Konfluens 2] reconcile-timeout ({RECONCILE_TIMEOUT_SEC}s) "
                         f"— springer over, fortsætter til handel")
            self._status("started", "Reconciliation timeout — fortsætter til handel")

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
        """Best-effort wrapper: ENHVER fejl i reconcile fanges og blokerer ALDRIG
        handelsstarten (float-bug'en 0dd96b8 propagerede tidligere herfra ud af
        on_start og dræbte loopet). Selve logikken bor i _reconcile_orphans_impl."""
        try:
            await self._reconcile_orphans_impl()
        except Exception as e:
            logger.exception(f"[Konfluens 2] reconcile fejlede (best-effort, ignoreret): {e}")
            self._status("started", "Reconciliation sprang fejlet over — fortsætter til handel")

    async def _reconcile_orphans_impl(self) -> None:
        """Scoped, journal-spor-styret oprydning af K2's EGNE spøgelser ved opstart.

        K2 deler IBKR-konto, så vi lukker ALDRIG blindt — en blind flatten kunne
        ramme en anden strategis position. I stedet starter vi fra K2's EGNE åbne
        journal-rows (source=self.name). Ved opstart har K2 endnu ikke åbnet noget
        (self._positions tom, loop'et ikke startet), så enhver åben K2-row er pr.
        definition et levn fra en tidligere session — et spøgelse efter en genstart
        eller en force-close-ordre der aldrig fyldte. Pr. row:

          - IBKR holder samme vej, |net| ≥ vores antal → luk PRÆCIS K2's andel
            (orderRef=self.name), bogfør reconcile_flatten.
          - IBKR flad i symbolet                       → fantom (fyldte nok aldrig):
            bogfør row lukket til entry (nul-P&L), INGEN ordre.
          - IBKR uenig om retning/antal                → inkonsistent (måske anden
            strategi): observe-only, rør den IKKE.

        Til sidst logges IBKR-positioner UDEN K2-journal-spor observe-only (anden
        strategi/manuel) — aldrig rørt.

        Delt-aktiv-sikker: holder to strategier samme ticker, lukker vi kun den
        mængde K2's row angiver (vores andel af nettoet), ikke hele IBKR-nettoet.

        Best-effort: en fejl her må ikke blokere dagens handel.
        """
        if self.conn is None or not self.conn.connected:
            self._status("started", "Reconciliation sprunget over — IBKR ikke forbundet")
            return

        # K2's egne åbne journal-rows = kandidater til oprydning.
        open_rows = []
        try:
            from trade_queries import list_trades
            db = getattr(self._journal, "_db", None) if self._journal else None
            if db is not None:
                open_rows = await list_trades(db, status="open", source=self.name)
        except Exception as e:
            logger.error(f"[Konfluens 2] reconciliation: list_trades fejlede: {e}")
            return

        # IBKR's faktiske positioner pr. ticker (netto).
        try:
            ibkr_positions = self.conn.get_positions()
        except Exception as e:
            logger.error(f"[Konfluens 2] reconciliation: kunne ikke hente IBKR-positioner: {e}")
            return
        ibkr_by_ticker = {
            (p.get("ticker") or "").upper(): (p.get("position") or 0)
            for p in ibkr_positions if p.get("position")
        }

        cleaned = 0
        k2_tickers: set[str] = set()
        for row in open_rows:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            k2_tickers.add(sym)

            shares = int(abs(row.get("shares") or 0))
            side   = (row.get("side") or "").lower()
            sign   = 1 if side == "long" else -1
            if shares <= 0:
                continue

            net = ibkr_by_ticker.get(sym, 0)

            if net == 0:
                # Fantom: journalen åben, IBKR flad → ordren fyldte nok aldrig.
                await self._reconcile_mark_closed(sym, row)
                cleaned += 1
                continue

            net_sign = 1 if net > 0 else -1
            if net_sign != sign or abs(net) < shares:
                # Inkonsistent — rør den IKKE (kan være en anden strategi).
                await self._log(
                    f"🔎 {sym}: K2-journal siger {side} {shares}, IBKR holder "
                    f"{net:+.0f} — inkonsistent, observe-only (rører den IKKE)",
                    level="warning")
                continue

            # Luk PRÆCIS K2's andel, ikke hele nettoet.
            await self._reconcile_close(sym, row, shares, sign)
            cleaned += 1

        # Observability: IBKR-positioner uden K2-journal-spor (anden strategi/manuel).
        for sym, net in ibkr_by_ticker.items():
            if net and sym not in k2_tickers:
                await self._log(
                    f"🔎 {sym} ({net:+.0f}) holdes i IBKR uden K2-journal-spor — "
                    f"observe-only (anden strategi eller manuel handel)", level="warning")

        if cleaned:
            self._status("started",
                         f"Reconciliation: ryddede {cleaned} gammel/gamle K2-position(er) op")
        else:
            self._status("started", "Reconciliation: ingen K2-spøgelser at rydde op")

    async def _reconcile_close(self, sym: str, row: dict, shares: int, sign: int) -> None:
        """Luk K2's andel af et gammelt åbent symbol og bogfør reconcile_flatten.
        Tæller IKKE med i dagens handels-statistik (rører ikke self.trades/self.stats).
        Spejler EUREVERSIONs _reconcile_close, men aktie-P&L (ingen multiplier)."""
        close_action = "SELL" if sign > 0 else "BUY"
        entry = row.get("entry_price") or 0.0
        snap  = await self.conn.get_snapshot(sym)
        exit_price = (snap.get("last") if snap else None) or entry or 0.0

        result = await self.conn.place_paper_order(
            sym, close_action, shares, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC,
        )
        if not result:
            await self._log(
                f"⚠ Kunne ikke lukke gammel position {sym} — luk den manuelt i TWS",
                level="warning")
            return

        # Bekræft fyldning FØR vi bogfører reconcile_flatten. Ufyldt → vi bogfører IKKE
        # (journal-row forbliver åben), så NÆSTE sessions reconcile genforsøger i stedet
        # for at skrive en falsk reconcile_flatten mens IBKR stadig holder positionen.
        filled = result.get("filled") or 0
        if filled < shares:
            await self._log(
                f"⚠ {sym}: reconcile-lukning IKKE bekræftet fyldt "
                f"(status={result.get('status')}, filled={filled}/{shares}) "
                f"— journal-row forbliver åben, luk evt. manuelt i TWS", level="warning")
            return

        fill = result.get("avg_fill")
        if fill and fill > 0:
            exit_price = fill

        pnl = (exit_price - entry) * shares * sign if (entry and exit_price) else 0.0

        trade_id = row.get("trade_id")
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = exit_price,
                exit_time   = datetime.now(ET),
                exit_reason = "reconcile_flatten",
                pnl         = pnl,
                payload     = {"reconcile": True},
            )

        await self._log(
            f"♻ Gammel åben K2-position {sym} ({'long' if sign > 0 else 'short'} "
            f"{shares}) lukket @ ${exit_price:.2f} (reconcile) | P&L: ${pnl:+.2f}")
        self._status("started", f"Gammel åben position {sym} er lukket (reconcile)")

    async def _reconcile_mark_closed(self, sym: str, row: dict) -> None:
        """Journalen har en åben K2-row men IBKR er flad i symbolet → ordren fyldte
        nok aldrig. Bogfør row'en lukket til entry (nul-P&L) så spøgelset forsvinder.
        INGEN ordre sendes (intet at lukke). P&L kan ikke rekonstrueres når IBKR er
        flad — nul er det ærlige 'ved ikke'-default."""
        trade_id = row.get("trade_id")
        entry = row.get("entry_price") or 0.0
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = entry,
                exit_time   = datetime.now(ET),
                exit_reason = "reconcile_phantom",
                pnl         = 0.0,
                payload     = {"reconcile": True, "phantom": True},
            )
        await self._log(
            f"🧹 {sym}: K2-journal stod åben men IBKR er flad — bogført lukket "
            f"(fantom, nul-P&L), ingen ordre sendt", level="warning")

    # -------------------------------------------------------------
    # Status broadcast — samme format som ORB/K1
    # -------------------------------------------------------------

    # _status() er løftet til BaseStrategy (Trin 1).

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
            raw_tickers = await self._scan_volatility_universe(UNIVERSE_TOP_N)
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
                     f"Intraday Volatility (pris ${UNIVERSE_PRICE_MIN:.0f}-${UNIVERSE_PRICE_MAX:.0f}, "
                     f"mkt-cap ${UNIVERSE_MKT_CAP_MIN/1e9:.0f}B-${UNIVERSE_MKT_CAP_MAX/1e12:.0f}T, "
                     f"avg-vol >{UNIVERSE_MIN_VOLUME:,}, ATR-1W >{UNIVERSE_ATR_PCT_MIN:.0f}%)")

        # ── Stol 100% på TV-screeneren — INTET andet-trins IBKR-pris-refilter ──
        # TradingView-queryen har allerede filtreret på pris ($5–50), market cap,
        # børs, 30-dages volumen og ATR. Vi bruger listen direkte (som ORB gør)
        # i stedet for at re-tjekke priser via IBKR-snapshots ved opstart.
        self.universe = list(raw_tickers)
        self._status("scanning",
                     f"{len(self.universe)} tickers fra TradingView Intraday "
                     f"Volatility (stoler 100% på screeneren)")

        if not self.universe:
            self._status("error", "Ingen tickers fra scanneren — afbryder")
            self.status = StrategyStatus.ERROR
            return

        # Vis HELE universet i live-loggen — alle kandidater der potentielt kan
        # handles i dag. Status "universe_ready" gør at frontenden også fanger
        # listen (split på første ":") til universe-visning.
        self._status("universe_ready",
                     f"📋 Dagens univers — {len(self.universe)} aktier der potentielt "
                     f"kan handles: {', '.join(self.universe)}")

        await self.log_universe(
            self.universe,
            meta = {
                "raw_count": len(raw_tickers),
                "price_min": UNIVERSE_PRICE_MIN,
                "price_max": UNIVERSE_PRICE_MAX,
                "source":    "tv_intraday_volatility",
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
                     f"✅ Universe klar — {len(self.universe)} tickers med fuld "
                     f"indikator-historie: {', '.join(self.universe)}")

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

    async def _scan_volatility_universe(self, top_n: int) -> list[str]:
        """
        Hent K2's univers via TradingViews "Intraday Volatility"-screener.

        IKKE længere top-gainers (som gav elendige kandidater) — i stedet
        mellem-/large-cap aktier med høj ugentlig ATR: likvide navne der
        bevæger sig nok intraday til at impuls-setuppet giver mening.
        Filtrene (pris/mkt-cap/børs/avg-vol/ATR) kommer fra confluence2.config
        og matcher Sørens screener. Returnerer symboler sorteret efter seneste
        dagsændring (faldende). Tom liste hvis API fejler.
        """
        from strategies.confluence.tv_scanner import build_volatility_universe
        return await build_volatility_universe(
            top_n       = top_n,
            price_min   = UNIVERSE_PRICE_MIN,
            price_max   = UNIVERSE_PRICE_MAX,
            mkt_cap_min = UNIVERSE_MKT_CAP_MIN,
            mkt_cap_max = UNIVERSE_MKT_CAP_MAX,
            min_avg_vol = UNIVERSE_MIN_VOLUME,
            atr_pct_min = UNIVERSE_ATR_PCT_MIN,
            exchanges   = UNIVERSE_EXCHANGES,
            timeout     = 15.0,
            log_tag     = "Konfluens 2",
        )

    async def _fetch_historical_1min_bars(
        self,
        ticker: str,
        start_date: date_cls,
        end_date: date_cls,
    ) -> list[Bar]:
        """Hent 1-min bars fra IBKR for én ticker. IBKR's 1-min historik er stramt
        begrænset (typisk 1-2 dage pr. kald); vi capper derfor duration til 2 dage."""
        days_back = (end_date - start_date).days + 1
        days_back = max(1, min(days_back, 2))
        return await self._fetch_bars(ticker, duration=f"{days_back} D", bar_size="1 min")

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
                                 f"Venter på handelsvindue — starter kl. {SESSION_START.strftime('%H:%M')} ET",
                                 persist=False)
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
                             f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}",
                             persist=False)

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
                                 "🔴 Ingen handel i dag — markedsforholdene er ikke til stede",
                                 persist=False)
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

    # _reconnect() er løftet til BaseStrategy (Trin 1).

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
                    trade_id      = trade_id,
                    current_stop  = state.trail_stop,
                    current_stage = stage,
                    trail_stop    = state.trail_stop,
                    current_price = new_bar.close,   # seneste pris → live urealiseret P&L i Studio
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
        """Seneste FÆRDIGE 1-min bar (sidste bar i batchen). None hvis ingen."""
        bars = await self._fetch_bars(ticker, duration="3600 S", bar_size="1 min")
        return bars[-1] if bars else None

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
        # await_fill_sec: vent på BEKRÆFTET fyldning (1-sek-vinduet er for kort
        # til tynde aktier/halts).
        close_result = await self.conn.place_paper_order(
            ticker, "SELL", shares, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC,
        )
        if not close_result:
            logger.error(
                f"[Konfluens 2] _close({ticker}): SELL-ordre kunne IKKE sendes "
                f"— beholder position åben, ingen journal-close"
            )
            self._status("trading",
                         f"⚠ {ticker}: lukkeordre ikke sendt — position forbliver åben")
            return

        # ── Bekræft fyldning FØR vi popper + bogfører ───────────────────
        # Hvis ordren ikke er (fuldt) fyldt — halt, tynd likviditet, eller afgivet
        # for sent — bogfører vi IKKE og popper IKKE. Positionen forbliver åben
        # (og åben-i-journal), så _close_all kan genforsøge, og hvad der ikke
        # fyldes fanges af opstarts-oprydningen næste session. Det er netop dét
        # der forhindrer et spøgelse hvor journalen siger 'lukket' mens IBKR
        # stadig holder positionen.
        filled = close_result.get("filled") or 0
        if filled < shares:
            await self._log(
                f"⚠ {ticker}: SELL ikke bekræftet fyldt "
                f"(status={close_result.get('status')}, filled={filled}/{shares}) "
                f"— beholder position åben, genforsøger", level="warning")
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
        """Luk alle åbne positioner med fill-verifikation + genforsøg.

        Hver position lukkes via _close, som nu KUN bogfører ved bekræftet
        fyldning og ellers beholder positionen åben. Vi genforsøger de der ikke
        blev bekræftet lukket, op til FORCE_CLOSE_MAX_ATTEMPTS gange — afgørende
        ved market-close, hvor loop'et bagefter stopper uden flere forsøg. Det
        der STADIG ikke kan lukkes (fx et halt) forbliver åbent i journalen og
        ryddes ved næste opstarts-reconciliation.
        """
        for attempt in range(1, FORCE_CLOSE_MAX_ATTEMPTS + 1):
            tickers = list(self._positions.keys())
            if not tickers:
                return
            for ticker in tickers:
                position = self._positions.get(ticker)
                if position is None:
                    continue
                snap = await self.conn.get_snapshot(ticker)
                price = (snap.get("last") if snap else None) or position.entry_price
                await self._close(ticker, price, reason)

            if not self._positions:
                return
            if attempt < FORCE_CLOSE_MAX_ATTEMPTS:
                await self._log(
                    f"⚠ {len(self._positions)} position(er) ikke bekræftet lukket "
                    f"(forsøg {attempt}/{FORCE_CLOSE_MAX_ATTEMPTS}) — genforsøger om "
                    f"{FORCE_CLOSE_RETRY_DELAY}s", level="warning")
                await asyncio.sleep(FORCE_CLOSE_RETRY_DELAY)

        if self._positions:
            await self._log(
                f"⛔ {len(self._positions)} position(er) kunne IKKE bekræftes lukket "
                f"efter {FORCE_CLOSE_MAX_ATTEMPTS} forsøg — bevaret åbne i journalen, "
                f"ryddes ved næste opstart (luk dem evt. manuelt i TWS nu)",
                level="warning")

    # -------------------------------------------------------------
    # Hjælpere
    # -------------------------------------------------------------

    # _broadcast_async() er løftet til BaseStrategy (Trin 1).

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

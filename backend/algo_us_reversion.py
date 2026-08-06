"""
algo_us_reversion.py
────────────────────
Live-wrapper for US-reversion — long-only mean-reversion på MES i den
amerikanske session (09:30–15:00 ET = 15:30–21:00 dansk).

TO TIDSRAMMER, og det er den strukturelle forskel fra alle øvrige strategier:
  15m  bånd/z (armering), CMF-kriteriet, og Z-exit-varianten
   5m  entry-trigger (to grønne + MACD), stop og trailing

  De to bar-strømme hentes hver for sig. 5m hentes hver loop-runde; 15m kun når
  et kvarter kan være passeret siden sidst — baren ændrer sig alligevel ikke
  imellem, og IBKR har pacing-grænser vi ikke skal spilde på et svar vi kender.

TILSTANDSMASKINEN er det nye her. EUREVERSION er tilstandsløs: hver færdig bar
giver et z, og z alene afgør alt. US-reversion har derimod en ARMERING der lever
mellem bars:

    flad, uarmeret ──(15m close < nedre bånd)──> ARMERET
    ARMERET ──(15m close tilbage inde i båndet)──> uarmeret
    ARMERET ──(5m: to grønne + MACD op + CMF op)──> POSITION
    POSITION ──(stop / upper_z / trail / sessions-slut)──> flad, uarmeret

  Armeringen falder altså bort så snart udvidelsen er ovre. En reversal to timer
  efter et brud er en ANDEN begivenhed og skal have sit eget brud.

Beslutningslogikken bor i strategies/us_reversion/rule.py og deles med
us_reversion_backtest.py. Wrapperen håndterer kun IBKR, sizing, broadcast,
forensik og reconcile.

Placering: C:\\Projects\\trading_dash\\backend\\algo_us_reversion.py
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz

from strategy_base import (
    BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus, ENTRY_FILL_WAIT_SEC)
from ibkr_connect import IBKRConnection
from reconcile_idempotency import (
    RECONCILE_CLOSING, reconcile_close_ref, decide_confirmation, WAIT, FLATTEN, RETRY)
# Delt forensik-opsamling — samme modul K2 og EUREVERSION bruger.
from trade_forensics import (build_entry_snapshot, build_exit_snapshot,
                             bars_to_chart_payload, append_stop_point)
# Live indikator-motor (liste-baseret). SAMME modul backtesten bruger, så MACD
# og CMF ikke kan divergere mellem live og test.
from indicators import macd as macd_of, cmf as cmf_of
# Den kanoniske Bar — samme dataklasse _fetch_bars i BaseStrategy returnerer.
from strategies.base import Bar

# Strategi-logik + parametre bor i pakken (delt sandhedskilde med backtesten).
from strategies.us_reversion import UsReversionStrategy, rule
from strategies.us_reversion.config import (
    SESSION_START_ET, ENTRY_CUTOFF_ET, FORCE_CLOSE_ET, LAST_SESSION_BAR_ET,
    BAR_BAND, BAR_BAND_MINUTES, BAR_TRIG, BAR_TRIG_MINUTES,
    LOOKBACK, CMF_LEN, MACD_FAST, MACD_SLOW, MACD_SIG,
    MACD_WINDOW, MIN_WARMUP_TRIG, MIN_WARMUP_BAND,
    INSTRUMENTS, MULTIPLIER, MAX_CONTRACTS, LIVE_VARIANT_KEY,
)

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

# ── Operationelle konstanter (wrapper-specifikke — IKKE strategi-reglen) ──────
# 5-min bars kræver hyppigere polling end EUREVERSIONs 15-min. 10 sek giver
# prompt reaktion på en netop færdiggjort 5m-bar uden at hamre IBKR.
LOOP_SLEEP_SECONDS = 10

# Hvor ofte 15m-strømmen genhentes. Under et kvarter sker der intet nyt i den,
# så alt derunder er spildte kald; 60 sek giver rigelig margin til at fange en
# ny færdig 15m-bar hurtigt efter den lukker.
BAND_REFETCH_SEC = 60

# Warmup. useRTH=False: vi vil have den fulde elektroniske session med, så
# lookback-vinduet er fyldt allerede ved US-åbning i stedet for at skulle
# opbygges gennem de første timers handel.
WARMUP_TRIG_DURATION = "2 D"      # 5m
WARMUP_BAND_DURATION = "5 D"      # 15m — LOOKBACK+CMF_LEN = 50 bars kræver flere dage
LATEST_TRIG_DURATION = "7200 S"   # 2 timer ≈ 24 5m-bars
LATEST_BAND_DURATION = "14400 S"  # 4 timer ≈ 16 15m-bars

MAX_CONNECT_RETRIES = 3
CONNECT_RETRY_DELAY = 10

HEARTBEAT_INTERVAL_SEC = 300

# Fyldnings-verificeret luk (spejler EUREVERSION/K2).
CLOSE_FILL_WAIT_SEC      = 8
FORCE_CLOSE_MAX_ATTEMPTS = 4
FORCE_CLOSE_RETRY_DELAY  = 4
LATE_CLOSE_MAX_MIN       = 20
RECONCILE_TIMEOUT_SEC    = 30


class UsReversionLive(BaseStrategy):
    """
    Live-wrapper for US-reversion. Følger BaseStrategy-interfacet så
    StrategyManager kan administrere den parallelt med de øvrige strategier.
    """

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None,
                 variant_key: str = LIVE_VARIANT_KEY):
        super().__init__(config)
        self.conn = conn

        # Strategi-facade fra pakken — al beslutningslogik delegeres hertil, så
        # live og backtest deler præcis samme kode.
        self._strategy = UsReversionStrategy(variant_key)
        self._variant_key = variant_key

        self.universe: list[str] = list(INSTRUMENTS)

        # ── Tidsramme nr. 2: 15m-strømmen ──────────────────────
        # _bar_history (fra BaseStrategy) rummer 5m-bars: det er den tidsramme
        # entries og exits sker på, og dermed den Handels-charten skal vise.
        # 15m holdes separat, kun til bånd/z og CMF.
        self._bars15: dict[str, list[Bar]] = {}
        self._last_bar15_processed: dict[str, datetime] = {}
        self._last_band_fetch: dict[str, datetime] = {}

        # ── Armerings-tilstand pr. instrument ──────────────────
        # armed:      har en 15m-close brudt ned gennem det nedre bånd?
        # armed_at:   hvornår (til forensik — hvor længe ventede vi på reversalen?)
        # armed_z:    z på selve brud-baren (til forensik)
        # z / bands:  seneste 15m-værdier, bruges af entry-forensik og Z-exit
        self._armed:    dict[str, bool] = {}
        self._armed_at: dict[str, Optional[datetime]] = {}
        self._armed_z:  dict[str, Optional[float]] = {}
        self._z15:      dict[str, Optional[float]] = {}
        self._sd15:     dict[str, Optional[float]] = {}
        self._cmf_now:  dict[str, Optional[float]] = {}
        self._cmf_prev: dict[str, Optional[float]] = {}

    # -------------------------------------------------------------
    # BaseStrategy interface — properties
    # -------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._strategy.name

    @property
    def description(self) -> str:
        return self._strategy.description

    @property
    def asset_class(self) -> str:
        return self._strategy.asset_class

    @property
    def cfg(self):
        """Den aktive variants parametre."""
        return self._strategy.cfg

    # -------------------------------------------------------------
    # Pre-flight
    # -------------------------------------------------------------

    async def pre_flight(self) -> tuple[bool, str]:
        ok, err, checks = await self._preflight_connection_and_account()
        if not ok:
            return False, err

        # BEGGE tidsrammer testes. En strategi der kan hente 15m men ikke 5m
        # ville armere korrekt og aldrig kunne udløse — den fejl skal fanges her,
        # ikke opdages som mystisk stilhed midt i sessionen.
        self._status("started", "Pre-flight: Tester futures-datafeed (MES, 15-min)...")
        band_bars = await self.conn.get_historical_bars(
            "MES", duration="1 D", bar_size=BAR_BAND, what_to_show="TRADES")
        if not band_bars:
            return False, f"Kan ikke hente {BAR_BAND} futures-bars for MES (bånd-tidsramme)"
        checks.append(f"15-min datafeed virker — {len(band_bars)} MES-bars")

        self._status("started", "Pre-flight: Tester futures-datafeed (MES, 5-min)...")
        trig_bars = await self.conn.get_historical_bars(
            "MES", duration="1 D", bar_size=BAR_TRIG, what_to_show="TRADES")
        if not trig_bars:
            return False, f"Kan ikke hente {BAR_TRIG} futures-bars for MES (trigger-tidsramme)"
        checks.append(f"5-min datafeed virker — {len(trig_bars)} MES-bars")

        checks.append(f"Variant: {self.cfg.name}")

        summary = " | ".join([f"✅ {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        await asyncio.sleep(1)
        return True, summary

    # -------------------------------------------------------------
    # Start / Stop
    # -------------------------------------------------------------

    async def on_start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            logger.warning("[US-reversion] _trading_loop kører allerede — afbryder ny start")
            return

        self._status("started", f"Algoritme starter — US-reversion (MES, {self._variant_key})")

        # Roll: vælg aktuel front-måned. Genopfriskes ved hver start, så vi
        # ruller til en ny kontrakt uden genstart.
        for sym in INSTRUMENTS:
            try:
                fut = await self.conn.qualify_future(sym, force_refresh=True)
                if fut is not None:
                    self._status("started",
                                 f"Front-måned {sym}: {fut.lastTradeDateOrContractMonth} "
                                 f"({fut.localSymbol})")
                else:
                    self._status("started", f"⚠ Kunne ikke kvalificere front-måned for {sym}")
            except Exception as e:
                logger.error(f"[US-reversion] front-måned-valg {sym} fejlede: {e}")

        # Reconciliation: scoped, observe-først. Best-effort OG tidsbegrænset.
        try:
            await asyncio.wait_for(self._reconcile_orphans(), timeout=RECONCILE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error(f"[US-reversion] reconcile-timeout ({RECONCILE_TIMEOUT_SEC}s) "
                         f"— springer over, fortsætter til handel")
            self._status("started", "Reconciliation timeout — fortsætter til handel")

        await self._prepare()
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def on_bar(self, ticker: str, bar: dict) -> None:
        # Event-driven bar-stream bruges ikke — vi poller selv i _trading_loop.
        pass

    async def on_stop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self._positions:
            await self._close_all("strategi stoppet")

    # -------------------------------------------------------------
    # Reconcile — scoped, observe-først
    # -------------------------------------------------------------

    async def _reconcile_orphans(self) -> None:
        """Best-effort wrapper: ENHVER fejl i reconcile fanges og blokerer ALDRIG
        handelsstarten. Selve logikken bor i _reconcile_orphans_impl."""
        try:
            await self._reconcile_orphans_impl()
        except Exception as e:
            logger.exception(f"[US-reversion] reconcile fejlede (best-effort, ignoreret): {e}")
            self._status("started", "Reconciliation sprang fejlet over — fortsætter til handel")

    async def _reconcile_orphans_impl(self) -> None:
        """
        Delt-konto-sikker reconcile ved opstart. To guards i stedet for nul-reconcile:

          1. INSTRUMENT-KLASSE: kun MES er synlig her. Aktie-positioner (K2/BuyTheDip/
             TrendJoin/RelStyrke) er per definition usynlige.
          2. JOURNAL-SPOR: en MES-position lukkes KUN hvis der findes en åben
             journal-row med source=self.name. Ellers OBSERVE-ONLY.

        BEMÆRK — EUREVERSION handler OGSÅ MES, på samme konto. Guard 2 er derfor
        ikke en formalitet her men helt afgørende: uden journal-sporet ville de to
        strategier kunne lukke hinandens positioner. Sessionerne overlapper ikke
        (EUREVERSION slutter 08:00 ET, vi starter 09:30), men et hængende
        EUREVERSION-orphan må vi under ingen omstændigheder røre.

        Best-effort: en fejl her må ikke blokere dagens handel.
        """
        if self.conn is None or not self.conn.connected:
            self._status("started", "Reconciliation sprunget over — IBKR ikke forbundet")
            return

        # Reliable live-read (ikke den kolde cache). KRITISK: begge pas nedenfor er
        # absence-baserede — en tom/degraderet positions-liste ville ellers markere
        # en ægte åben journal-row som "forældet" og lukke den UDEN ordre.
        ibkr_positions, feed_ok = await self.conn.get_positions_reliable()
        if not feed_ok:
            logger.warning("[US-reversion] reconciliation: positions-feed upålideligt "
                           "(tom/timeout ved opstart) — springer over for ikke at "
                           "forældreløsgøre en ægte position")
            self._status("started", "Reconciliation sprunget over — positions-feed upålideligt")
            return

        ours = [p for p in ibkr_positions
                if p.get("ticker") in INSTRUMENTS and p.get("position")]

        if not ours:
            self._status("started",
                         "Reconciliation: ingen gamle futures-positioner i vores instrumenter")
            await self._reconcile_close_stale_journal_rows(ibkr_positions)
            return

        for p in ours:
            sym = p["ticker"]
            qty = p["position"]

            open_rows = []
            try:
                if self._journal is not None and getattr(self._journal, "_db", None) is not None:
                    from trade_queries import list_trades
                    open_rows = await list_trades(
                        self._journal._db, status="open", source=self.name, symbol=sym,
                    )
            except Exception as e:
                logger.error(f"[US-reversion] reconciliation: list_trades fejl for {sym}: {e}")
                open_rows = []

            if open_rows:
                _r = open_rows[0]
                if (_r.get("current_stage") or "") == RECONCILE_CLOSING:
                    await self._reconcile_confirm(sym, qty, _r)
                else:
                    await self._reconcile_close(sym, qty, _r)
            else:
                await self._log(
                    f"🔎 Gammel åben position {sym} ({qty:+.0f}) i IBKR uden journal-spor "
                    f"fra os — observe-only, rører den IKKE (kan være EUREVERSIONs "
                    f"eller en manuel handel)", level="warning")

        await self._reconcile_close_stale_journal_rows(ibkr_positions)

    async def _reconcile_close(self, sym: str, qty: float, row: dict) -> None:
        """Luk en gammel åben position der er ægte vores, og bogfør den med
        exit_reason='reconcile_flatten'. Tæller IKKE med i dagens statistik."""
        side         = "long" if qty > 0 else "short"
        contracts    = int(abs(qty))
        close_action = "SELL" if side == "long" else "BUY"
        mult         = MULTIPLIER.get(sym, 5.0)

        entry = row.get("entry_price") or 0.0
        snap = await self.conn.get_snapshot(sym)
        exit_price = (snap.get("last") if snap else None) or entry or 0.0

        # Undgå DUPLIKAT-lukordre: hviler der allerede en aktiv lukke-ordre på
        # symbolet, så lad DEN lukke positionen.
        _dups = [o for o in await self.conn.get_open_orders()
                 if o["symbol"] == sym.upper() and o["action"] == close_action]
        if _dups:
            await self._log(
                f"⏸ {sym}: en {close_action}-ordre hviler allerede "
                f"({_dups[0]['status']}, rest {_dups[0]['remaining']:.0f}) — lægger IKKE en "
                f"duplikat. Lader den eksisterende lukke; reconcile bekræfter når fyldt.",
                level="warning")
            return

        # Idempotens: durabel markør + deterministisk orderRef FØR vi venter på fyldning.
        _tid = row.get("trade_id")
        _ref = reconcile_close_ref(_tid)
        if _tid and self._journal:
            try:
                await self._journal.update_trade_state(_tid, current_stage=RECONCILE_CLOSING)
            except Exception as e:
                logger.error(f"[US-reversion] kunne ikke sætte reconcile_closing-markør ({sym}): {e}")

        result = await self.conn.place_paper_order(
            sym, close_action, contracts, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC, order_ref=_ref,
        )
        if not result:
            await self._log(
                f"⚠ Kunne ikke lukke gammel position {sym} — luk den manuelt i TWS",
                level="warning")
            return

        filled = result.get("filled") or 0
        if filled < contracts:
            await self._log(
                f"⚠ {sym}: reconcile-lukning IKKE bekræftet fyldt "
                f"(status={result.get('status')}, filled={filled}/{contracts}) "
                f"— journal-row forbliver åben. Luk evt. manuelt i TWS.", level="warning")
            return

        fill = result.get("avg_fill")
        if fill and fill > 0:
            exit_price = fill

        if entry and exit_price:
            pnl = (exit_price - entry) * contracts * mult if side == "long" \
                  else (entry - exit_price) * contracts * mult
        else:
            pnl = 0.0

        trade_id = row.get("trade_id")
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = exit_price,
                exit_time   = datetime.now(ET),
                exit_reason = "reconcile_flatten",
                pnl         = pnl,
                payload     = {"reconcile": True, "multiplier": mult},
            )

        await self._log(
            f"♻ Gammel åben position {sym} ({qty:+.0f}) er lukket @ ${exit_price:.2f} "
            f"(reconcile) | P&L: ${pnl:+.2f}")
        self._status("started", f"Gammel åben position {sym} er lukket (reconcile)")

    async def _reconcile_confirm(self, sym: str, qty: float, row: dict) -> None:
        """Bekræftelses-sti for en row der ALLEREDE er i reconcile_closing.
        Placerer ALDRIG på UKENDT udfald."""
        ref = reconcile_close_ref(row.get("trade_id"))
        position_open = (qty != 0)
        try:
            outcome = await self.conn.get_order_outcome(ref)
        except Exception:
            outcome = "not_findable"
        decision = decide_confirmation(outcome, position_open)
        if decision == WAIT:
            await self._log(f"⏸ {sym}: reconcile-close ({ref}) hviler stadig (udfald={outcome}) — afventer",
                            level="warning")
        elif decision == FLATTEN:
            await self._reconcile_mark_filled(sym, row, outcome)
        elif decision == RETRY:
            await self._log(f"↻ {sym}: reconcile-close bekræftet død-ufyldt (udfald={outcome}), "
                            f"position stadig åben — genafgiver", level="warning")
            await self._reconcile_close(sym, qty, row)
        else:
            await self._log(f"🔎 {sym}: reconcile-close ({ref}) ubekræftet og ikke synlig; position "
                            f"ser stadig åben ud ({qty:+.0f}) — observe-only, afventer", level="warning")

    async def _reconcile_mark_filled(self, sym: str, row: dict, outcome: str) -> None:
        """Tidligere reconcile-close bekræftet fyldt → bogfør row'en lukket. INGEN ny ordre."""
        trade_id = row.get("trade_id")
        entry = row.get("entry_price") or 0.0
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id=trade_id, exit_price=entry, exit_time=datetime.now(ET),
                exit_reason="reconcile_flatten", pnl=0.0,
                payload={"reconcile": True, "confirmed_outcome": outcome})
        await self._log(f"♻ {sym}: tidligere reconcile-close bekræftet ({outcome}) — row bogført "
                        f"lukket (reconcile_flatten, nul-P&L est.)", level="warning")

    async def _reconcile_close_stale_journal_rows(self, ibkr_positions: list) -> None:
        """Luk åbne journal-rows (source=self.name, MES) der IKKE har nogen modsvarende
        IBKR-position. Sender INGEN IBKR-ordre — vi retter kun journalen."""
        if self._journal is None or getattr(self._journal, "_db", None) is None:
            return

        held = {p.get("ticker") for p in ibkr_positions
                if p.get("ticker") in INSTRUMENTS and p.get("position")}

        try:
            from trade_queries import list_trades
            open_rows = await list_trades(self._journal._db, status="open", source=self.name)
        except Exception as e:
            logger.error(f"[US-reversion] reconciliation: list_trades (journal-pas) fejl: {e}")
            return

        for row in open_rows:
            sym = (row.get("symbol") or "").upper()
            if sym not in INSTRUMENTS:
                continue
            if sym in held:
                continue

            trade_id = row.get("trade_id")
            if not trade_id:
                continue

            entry = row.get("entry_price") or 0.0
            exit_price = entry
            try:
                snap = await self.conn.get_snapshot(sym) if self.conn else None
                if snap and snap.get("last"):
                    exit_price = snap.get("last")
            except Exception:
                pass

            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = exit_price,
                exit_time   = datetime.now(ET),
                exit_reason = "reconcile_journal_sync",
                pnl         = 0.0,
                payload     = {"reconcile": True, "journal_only": True,
                               "note": "lukket i journal — ingen IBKR-position (fladt)"},
            )
            await self._log(
                f"♻ Forældet åben journal-row {sym} ({row.get('side','?')}) lukket — "
                f"ingen IBKR-position (journal-sync, ingen ordre sendt)", level="info")
            self._status("started", f"Forældet journal-row {sym} ryddet (ingen IBKR-position)")

    # -------------------------------------------------------------
    # Warmup-forberedelse — BEGGE tidsrammer
    # -------------------------------------------------------------

    async def _prepare(self):
        self._status("loading_orb",
                     f"Henter warmup-historie ({BAR_TRIG} + {BAR_BAND}) for "
                     f"{', '.join(INSTRUMENTS)}...")

        ready = []
        for sym in INSTRUMENTS:
            bars5 = await self._fetch_bars(sym, duration=WARMUP_TRIG_DURATION, bar_size=BAR_TRIG)
            self._bar_history[sym] = bars5
            if bars5:
                self._last_bar_processed[sym] = bars5[-1].timestamp

            bars15 = await self._fetch_bars(sym, duration=WARMUP_BAND_DURATION, bar_size=BAR_BAND)
            self._bars15[sym] = bars15
            if bars15:
                self._last_bar15_processed[sym] = bars15[-1].timestamp
            self._last_band_fetch[sym] = datetime.now(ET)

            # Armering starter altid FRA. En armering fra i går må ikke overleve
            # natten — bruddet skal ske i dagens session for at gælde.
            self._armed[sym]    = False
            self._armed_at[sym] = None
            self._armed_z[sym]  = None

            # Sæt de første 15m-afledte værdier, så entry ikke skal vente et
            # kvarter på sit første CMF-par.
            self._refresh_band_state(sym)

            if bars5 and bars15:
                ready.append(sym)
            if len(bars5) < MIN_WARMUP_TRIG:
                self._status("loading_orb",
                             f"⚠ {sym}: kun {len(bars5)} 5-min warmup-bars "
                             f"(<{MIN_WARMUP_TRIG}) — MACD bliver klar når flere ankommer")
            if len(bars15) < MIN_WARMUP_BAND:
                self._status("loading_orb",
                             f"⚠ {sym}: kun {len(bars15)} 15-min warmup-bars "
                             f"(<{MIN_WARMUP_BAND}) — bånd/CMF bliver klar når flere ankommer")

        await self.log_universe(
            self.universe,
            meta={"session": "amerikansk 09:30-15:00 ET",
                  "bar_trigger": BAR_TRIG, "bar_band": BAR_BAND,
                  "lookback": LOOKBACK, "variant": self._variant_key},
        )

        # Datablind-fix: emit ÉT bar_evaluation pr. instrument for sidste warmup-bar,
        # så watchdog-uret stilles friskt allerede ved start. Kun LOGNING — ingen
        # entry/exit-handling på en warmup-bar.
        for sym in ready:
            hist = self._bar_history.get(sym, [])
            if not hist:
                continue
            last_bar = hist[-1]
            in_session = SESSION_START_ET <= last_bar.timestamp.time() < FORCE_CLOSE_ET
            z = self._z15.get(sym)
            await self.log_bar_evaluation(
                ticker      = sym,
                bar_time_et = last_bar.timestamp.astimezone(ET).strftime("%H:%M"),
                status      = "in_session" if in_session else "out_of_session",
                reason      = f"warmup z={z:+.2f}" if z is not None else "warmup (z endnu ikke klar)",
            )

        self._status("orb_ready",
                     f"✅ Klar — {len(ready)}/{len(INSTRUMENTS)} instrumenter med "
                     f"warmup på begge tidsrammer ({', '.join(INSTRUMENTS)})")

    # -------------------------------------------------------------
    # Trading loop
    # -------------------------------------------------------------

    async def _trading_loop(self):
        self._status("trading", f"Overvåger MES — venter på færdige {BAR_TRIG} bars...")
        consecutive_errors = 0
        _last_heartbeat = datetime.now(ET)

        try:
            while self.loop_skal_koere():
                now_et = datetime.now(ET)
                t = now_et.time()

                if t < SESSION_START_ET:
                    self._status("orb_ready",
                                 f"Venter på US-session — starter kl. "
                                 f"{SESSION_START_ET.strftime('%H:%M')} ET",
                                 persist=False)
                    await asyncio.sleep(LOOP_SLEEP_SECONDS)
                    continue

                # Tvangsluk: når vi passerer FORCE_CLOSE_ET lukker vi alt og
                # afslutter dagen. Holder ALDRIG over sessionen.
                if t >= FORCE_CLOSE_ET:
                    if self._positions:
                        self._status("trading", "Sessions-slut nærmer sig — lukker alle positioner")
                        await self._close_all("session_end")
                    wins   = sum(1 for tr in self.trades if tr["pnl"] > 0)
                    losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                    self._status("done",
                                 f"✅ US-session afsluttet | "
                                 f"P&L: ${self.total_pnl:+,.2f} | "
                                 f"{len(self.trades)} handler ({wins}W/{losses}L)")
                    await self._log_self_stop("Sessions-slut: force-close",
                                              StrategyStatus.STOPPED, "done")
                    break

                armed_txt = "ARMERET" if self._armed.get("MES") else "afventer båndbrud"
                self._status("trading",
                             f"Overvåger MES — {now_et.strftime('%H:%M:%S')} ET | "
                             f"{armed_txt} | Positioner: "
                             f"{self.stats.open_positions}/{self.config.max_open_positions}",
                             persist=False)

                if (now_et - _last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL_SEC:
                    _last_heartbeat = now_et
                    await self.log_heartbeat({
                        "open_positions": self.stats.open_positions,
                        "trades":         len(self.trades),
                        "total_pnl":      round(self.total_pnl, 2),
                        "instruments":    INSTRUMENTS,
                        "armed":          {s: bool(self._armed.get(s)) for s in INSTRUMENTS},
                        "z15":            {s: self._z15.get(s) for s in INSTRUMENTS},
                        "variant":        self._variant_key,
                    })

                try:
                    for sym in INSTRUMENTS:
                        if self.status != StrategyStatus.RUNNING:
                            break
                        # 15m FØRST: armeringen skal være opdateret inden 5m-baren
                        # vurderes, ellers kunne en trigger blive vurderet mod en
                        # forældet armerings-tilstand.
                        await self._check_band(sym)
                        await self._check_trigger(sym)
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    logger.exception(f"[US-reversion] fejl i handels-loop: {e}")
                    if consecutive_errors >= 3:
                        self._status("trading",
                                     f"⚠ {consecutive_errors} fejl — forsøger genforbinding...")
                        if await self._reconnect():
                            consecutive_errors = 0
                            self._status("trading", "✅ Genforbundet — fortsætter handel")
                        else:
                            await self._log_self_stop("IBKR-forbindelse tabt",
                                                      StrategyStatus.ERROR, "error")
                            break

                await asyncio.sleep(LOOP_SLEEP_SECONDS)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[US-reversion] _trading_loop crashede: {e}")
            await self._log_self_stop(
                f"Fejl i strategi-loop: {type(e).__name__}: {str(e)[:80]}",
                StrategyStatus.ERROR, "error")

    # -------------------------------------------------------------
    # 15m — bånd, armering og CMF
    # -------------------------------------------------------------

    def _refresh_band_state(self, sym: str) -> None:
        """Genberegn z, std og CMF-parret ud fra den nuværende 15m-historik.

        CMF beregnes både på det fulde vindue og på vinduet minus sidste bar, så
        'stigende' kan afgøres. Begge kald går gennem den SAMME indicators.cmf
        som backtesten bruger.
        """
        bars15 = self._bars15.get(sym, [])
        if len(bars15) >= LOOKBACK:
            res = rule.compute_z([b.close for b in bars15[-LOOKBACK:]])
            if res is not None:
                self._z15[sym], self._sd15[sym] = res
            else:
                self._z15[sym] = self._sd15[sym] = None
        else:
            self._z15[sym] = self._sd15[sym] = None

        rows = [{"high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                for b in bars15]
        self._cmf_now[sym]  = cmf_of(rows, CMF_LEN)
        self._cmf_prev[sym] = cmf_of(rows[:-1], CMF_LEN) if len(rows) > CMF_LEN else None

    async def _check_band(self, sym: str):
        """Hent nye FÆRDIGE 15m-bars og opdatér armerings-tilstanden.

        Kaldes hver loop-runde, men henter kun fra IBKR hvert BAND_REFETCH_SEC —
        en 15m-bar ændrer sig ikke oftere, så resten ville være spildte kald.
        """
        now = datetime.now(ET)
        last_fetch = self._last_band_fetch.get(sym)
        if last_fetch is not None and (now - last_fetch).total_seconds() < BAND_REFETCH_SEC:
            return
        self._last_band_fetch[sym] = now

        parsed = await self._fetch_bars(sym, duration=LATEST_BAND_DURATION, bar_size=BAR_BAND)
        if not parsed:
            return

        last = self._last_bar15_processed.get(sym)
        finished = [b for b in parsed
                    if now >= b.timestamp + timedelta(minutes=BAR_BAND_MINUTES)]
        if last is not None:
            finished = [b for b in finished if b.timestamp > last]
        if not finished:
            return

        hist = self._bars15.setdefault(sym, [])
        for bar in finished:
            hist.append(bar)
            self._last_bar15_processed[sym] = bar.timestamp
            self._refresh_band_state(sym)
            await self._update_arming(sym, bar)

        # Hold hukommelsen i ave.
        keep = max(LOOKBACK, CMF_LEN) * 8
        if len(hist) > keep:
            self._bars15[sym] = hist[-keep:]

    async def _update_arming(self, sym: str, bar15: Bar):
        """Armér/afarmér ud fra den netop færdiggjorte 15m-bar."""
        z = self._z15.get(sym)
        if z is None:
            return

        # En åben position blokerer ikke armerings-logikken, men armeringen er
        # irrelevant mens vi er i markedet — den nulstilles ved exit.
        was_armed = bool(self._armed.get(sym))

        if self._strategy.is_break_below(z):
            if not was_armed:
                self._armed[sym]    = True
                self._armed_at[sym] = bar15.timestamp
                self._armed_z[sym]  = z
                await self._log(
                    f"🎯 {sym}: ARMERET — 15m lukkede under nedre bånd "
                    f"(z={z:+.2f}, close=${bar15.close:.2f}). Venter nu på 5m-reversal.")
                self._status("trading", f"🎯 {sym}: armeret (z={z:+.2f}) — venter på reversal")
        elif was_armed and self._strategy.is_back_inside(z):
            self._armed[sym]    = False
            self._armed_at[sym] = None
            self._armed_z[sym]  = None
            await self._log(
                f"↩ {sym}: afarmeret — 15m lukkede tilbage inde i båndet (z={z:+.2f}) "
                f"uden at en reversal nåede at udløse. Udvidelsen er forbi.")

    # -------------------------------------------------------------
    # 5m — trigger, exit
    # -------------------------------------------------------------

    async def _check_trigger(self, sym: str):
        """
        Hent nye FÆRDIGE 5m-bars og evaluér dem.

        Bar-dedup (K2-lektionen): en bar er FÆRDIG først når dens sluttidspunkt
        er passeret. Vi appender og evaluerer kun færdige bars vi ikke har set
        før — aldrig den stadig-formende aktuelle bar.
        """
        parsed = await self._fetch_bars(sym, duration=LATEST_TRIG_DURATION, bar_size=BAR_TRIG)
        if not parsed:
            return

        now = datetime.now(ET)
        last = self._last_bar_processed.get(sym)

        finished = [b for b in parsed
                    if now >= b.timestamp + timedelta(minutes=BAR_TRIG_MINUTES)]
        if last is not None:
            finished = [b for b in finished if b.timestamp > last]
        if not finished:
            return

        hist = self._bar_history.setdefault(sym, [])
        for bar in finished:
            hist.append(bar)
            self._last_bar_processed[sym] = bar.timestamp
            await self._evaluate_bar(sym, bar)

        if len(hist) > MIN_WARMUP_TRIG * 8:
            self._bar_history[sym] = hist[-MIN_WARMUP_TRIG * 8:]

    async def _evaluate_bar(self, sym: str, bar: Bar):
        """Kør reglen på én netop-færdiggjort 5m-bar."""
        bar_t = bar.timestamp.time()
        in_session = SESSION_START_ET <= bar_t < FORCE_CLOSE_ET
        z = self._z15.get(sym)

        await self.log_bar_evaluation(
            ticker      = sym,
            bar_time_et = bar.timestamp.astimezone(ET).strftime("%H:%M"),
            status      = "in_session" if in_session else "out_of_session",
            reason      = (f"z15={z:+.2f} " if z is not None else "z15=n/a ")
                          + ("armeret" if self._armed.get(sym) else "uarmeret"),
        )

        # ── Åben position: tjek exit ──
        if sym in self._positions:
            pos = self._positions[sym]

            if sym in self._mfe:
                self._mfe[sym] = max(self._mfe[sym], bar.high)
            if sym in self._mae:
                self._mae[sym] = min(self._mae[sym], bar.low)

            # HH sporer CLOSES (ikke highs) — Sørens specifikation.
            pos["hh_close"] = rule.update_hh(pos["hh_close"], bar.close)
            pos["last_z"] = z

            trade_id = pos.get("trade_id")
            if trade_id and self._journal:
                await self._journal.update_trade_state(trade_id=trade_id, current_price=bar.close)

            # Bar-baseret tvangsluk-backstop: sessionens sidste bar lukker altid.
            if bar_t >= LAST_SESSION_BAR_ET:
                await self._close(sym, bar.close, "session_end", z)
                return

            reason = self._strategy.check_exit(
                entry_price = pos["entry_price"],
                hh_close    = pos["hh_close"],
                last_close  = bar.close,
                z           = z,
            )
            if reason:
                await self._close(sym, bar.close, reason, z)
            return

        # ── Flad: vurdér entry ──
        if not in_session:
            return
        if not self._armed.get(sym):
            return
        # Entry-cutoff: en frisk position tæt på sessions-slut ville blive
        # tvangslukket inden den fik en chance.
        if bar_t >= ENTRY_CUTOFF_ET or datetime.now(ET).time() >= ENTRY_CUTOFF_ET:
            return
        if self.stats.open_positions >= self.config.max_open_positions:
            return

        hist = self._bar_history.get(sym, [])
        if len(hist) < MIN_WARMUP_TRIG:
            return

        # PRÆCIS MACD_WINDOW bars til begge beregninger. Vinduet skal være fast og
        # ens med backtesten (se config.MACD_WINDOW), og "forrige" skal have samme
        # længde som "nu" — ellers sammenlignes 150 bars med 149.
        w = [b.close for b in hist[-(MACD_WINDOW + 1):]]
        m_now  = macd_of(w[1:],  MACD_FAST, MACD_SLOW, MACD_SIG)
        m_prev = macd_of(w[:-1], MACD_FAST, MACD_SLOW, MACD_SIG)

        bars5_rows = [{"open": b.open, "close": b.close} for b in hist[-2:]]
        ok, detaljer = self._strategy.check_entry(
            bars5     = bars5_rows,
            macd_now  = m_now.macd if m_now else None,
            macd_prev = m_prev.macd if m_prev else None,
            cmf_now   = self._cmf_now.get(sym),
            cmf_prev  = self._cmf_prev.get(sym),
        )
        if not ok:
            await self._log_entry_afvist(sym, bar, z, detaljer)
            return

        await self._open(sym, bar, z, detaljer)

    async def _log_entry_afvist(self, sym: str, bar: Bar, z, detaljer: dict) -> None:
        """Hvorfor gik den ARMEREDE strategi ikke ind paa denne bar?

        `bar_evaluation` siger kun "z15=-2,45 armeret". Naar den saa IKKE handler,
        kan man ikke se hvilket af de tre bekraeftelses-kriterier der holdt igen —
        og saa kan man heller ikke skelne "den venter fornuftigt paa vendingen" fra
        "et kriterium er saa stramt at det aldrig opfyldes".

        check_entry regner allerede detaljerne ud (og dens egen docstring siger at
        de er til netop dette); de blev bare kasseret af `if not ok: return`.

        EGEN event_type frem for en rigere bar_evaluation-tekst: den tidlige
        bar_evaluation stiller watchdog-uret, saa den maa hverken flyttes eller
        udsendes to gange. Fyrer KUN naar armeret og afvist — ingen stoej paa de
        bars hvor der intet er at forklare.

        Best-effort: en manglende logline maa aldrig kunne vaelte handelsflowet.
        """
        if not self._journal:
            return
        try:
            def flag(navn, ok_):
                return f"{navn}{'✓' if ok_ else '✗'}"
            r  = detaljer.get("rise_pct")
            kr = detaljer.get("rise_krav")
            # rise_pct er None naar de to 5m-bars IKKE begge er groenne — ikke naar
            # maalingen fejlede. "n/a" ville skjule netop den oplysning man leder
            # efter: at vendingen slet ikke er begyndt endnu.
            stigning = (f"stigning {r:.3f}%/{kr:.2f}%" if r is not None
                        else "ikke to groenne 5m-bars")
            tekst = " · ".join([
                stigning,
                flag("macd", detaljer.get("ok_macd")),
                flag("cmf",  detaljer.get("ok_cmf")),
            ])
            mangler = [n for n, k in (("stigning", "ok_rise"), ("macd", "ok_macd"),
                                      ("cmf", "ok_cmf")) if not detaljer.get(k)]
            await self._journal.log_event(
                source     = self.name,
                event_type = "entry_afvist",
                symbol     = sym,
                payload    = {
                    "bar_time_et": bar.timestamp.astimezone(ET).strftime("%H:%M"),
                    "z15":         round(z, 2) if z is not None else None,
                    "mangler":     mangler,       # hvilke kriterier der holdt igen
                    "kort":        tekst,         # menneskelaesbar én-linjer
                    **detaljer,                   # de raa tal, til senere analyse
                },
            )
        except Exception as e:
            # ⚠ IKKE self.name her. Den er en property der slaar op i self._strategy,
            # og findes den ikke, kaster fejlhaandteringen selv — saa en best-effort
            # logline kan vaelte bar-evalueringen. Fanget af testen, som bygger sin
            # double med __new__.
            logger.warning(f"[US-reversion] kunne ikke logge entry_afvist for {sym}: {e}")

    # -------------------------------------------------------------
    # Sizing — altid præcis 1 kontrakt
    # -------------------------------------------------------------

    def _size_contracts(self, sym: str, entry_price: float) -> tuple[int, float, float]:
        """
        (antal_kontrakter, stop_afstand_point, per_kontrakt_risiko).

        Modsat EUREVERSION er stoppet her en fast PROCENT af entry-prisen, ikke
        et multiplum af std. Antallet er altid præcis 1 (MAX_CONTRACTS) — det er
        Sørens eksplicitte krav, og kontoen har alligevel ikke margin til mere.
        """
        mult = MULTIPLIER.get(sym, 5.0)
        stop_dist = entry_price * (self.cfg.stop_pct / 100.0)
        per_contract_risk = stop_dist * mult
        return MAX_CONTRACTS, stop_dist, per_contract_risk

    # -------------------------------------------------------------
    # Open / Close
    # -------------------------------------------------------------

    async def _open(self, sym: str, bar: Bar, z: Optional[float], detaljer: dict):
        if self.stats.open_positions >= self.config.max_open_positions:
            return

        side = "long"          # US-reversion er long-only
        action = "BUY"
        mult = MULTIPLIER.get(sym, 5.0)
        entry_price = bar.close

        contracts, stop_dist, per_contract_risk = self._size_contracts(sym, entry_price)

        # IBKR's faktiske initial-margin denne ordre binder (KUN display, fejl-sikker).
        init_margin = await self.conn.what_if_init_margin(sym, action, contracts)

        order = OrderRequest(
            strategy_name=self.name,
            ticker=sym,
            action=action,
            quantity=contracts,
            order_type="MKT",
            asset_class="futures",
            reason=f"US-reversion long-entry (z15={z:+.2f})" if z is not None
                   else "US-reversion long-entry",
        )

        if self._risk_manager:
            approved = await self.request_order(order)
            if not approved:
                return

        result = await self.conn.place_paper_order(
            sym, action, contracts, source=self.name,
            await_fill_sec=ENTRY_FILL_WAIT_SEC)
        # Bogfør KUN hvad IBKR bekræfter fyldt — ellers bygger vi en fantom-position.
        filled_qty = await self._entry_fill_qty(result, sym, contracts)
        if filled_qty <= 0:
            return
        contracts = int(filled_qty)

        fill = result.get("avg_fill")
        if fill and fill > 0:
            entry_price = fill

        entry_time = datetime.now(ET)
        stop_price = rule.stop_price(entry_price, self.cfg)
        reserved   = contracts * 10.0

        # Stop-trajektorie (fast procent-stop → ét punkt = flad step-linje).
        stop_traj: list = []
        append_stop_point(stop_traj, entry_time, stop_price, None)

        self._positions[sym] = {
            "side":        side,
            "entry_price": entry_price,
            "contracts":   contracts,
            "multiplier":  mult,
            "entry_time":  entry_time,
            "stop_price":  stop_price,
            # HH starter ved ENTRY-prisen, ikke ved næste close — et øjeblikkeligt
            # dyk må ikke sænke referencen og gøre trailing-stoppet meningsløst løst.
            "hh_close":    entry_price,
            "reserved":    reserved,
            "init_margin": init_margin,
            "armed_at":    self._armed_at.get(sym),
            "armed_z":     self._armed_z.get(sym),
            "trade_id":    None,
            "stop_traj":   stop_traj,
            "last_z":      z,
        }
        self.stats.open_positions = len(self._positions)

        # Armeringen er brugt op — nulstil, så et nyt brud kræves til næste handel.
        self._armed[sym]    = False
        self._armed_at[sym] = None
        self._armed_z[sym]  = None

        # OrdersTracker
        try:
            from orders_tracker import get_tracker
            order_id = result.get("order_id")
            if order_id:
                get_tracker().record_placed(
                    order_id=order_id, source=self.name, ticker=sym,
                    action=action, shares=contracts, order_type="MKT",
                )
        except Exception as e:
            logger.warning(f"[US-reversion] kunne ikke registrere ordre hos tracker: {e}")

        # Bånd-kontekst fra 15m (samme form som EUREVERSION, så de to reversions-
        # strategier kan sammenlignes direkte i journalen bagefter).
        sd = self._sd15.get(sym)
        bars15 = self._bars15.get(sym, [])
        band = self._strategy.bands([b.close for b in bars15[-LOOKBACK:]]) \
               if len(bars15) >= LOOKBACK else None
        mean, lower_band, upper_band = band if band else (None, None, None)

        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source        = self.name,
                symbol        = sym,
                side          = side,
                shares        = contracts,
                entry_price   = entry_price,
                entry_time    = entry_time,
                entry_reason  = (f"US-reversion long (z15={z:+.2f}, "
                                 f"stigning {detaljer.get('rise_pct')}%)" if z is not None
                                 else "US-reversion long"),
                current_stop  = stop_price,
                current_stage = "initial",
                payload       = {
                    "variant":           self._variant_key,
                    "entry_z":           round(z, 4) if z is not None else None,
                    "armed_z":           round(self._armed_z.get(sym), 4)
                                         if self._armed_z.get(sym) is not None else None,
                    "std":               round(sd, 4) if sd is not None else None,
                    "mean":              round(mean, 4) if mean is not None else None,
                    "upper_band":        round(upper_band, 4) if upper_band is not None else None,
                    "lower_band":        round(lower_band, 4) if lower_band is not None else None,
                    "multiplier":        mult,
                    "stop_distance_pts": round(stop_dist, 4),
                    "stop_pct":          self.cfg.stop_pct,
                    "trail_pct":         self.cfg.trail_pct,
                    "contracts":         contracts,
                    "init_margin":       round(init_margin, 2) if init_margin is not None else None,
                    "trigger":           detaljer,
                },
            )
            if trade_id:
                self._positions[sym]["trade_id"] = trade_id

        self._mfe[sym] = entry_price
        self._mae[sym] = entry_price

        # Entry-forensik via den delte builder → samme CSV-kolonner som K2.
        # FAIL-SAFE: forensik må ALDRIG vælte handlen.
        try:
            snap = build_entry_snapshot(
                ticker       = sym,
                entry_price  = entry_price,
                entry_time   = entry_time,
                shares       = contracts,
                bars         = self._bar_history.get(sym, []),
                context      = {},
                tape_buffer  = None,
                variant_name = f"{self.name} [{self._variant_key}]",
            )
            snap["reversion"] = {
                "variant":           self._variant_key,
                "entry_z":           round(z, 4) if z is not None else None,
                "armed_z":           self._armed_z.get(sym),
                "mean":              round(mean, 4) if mean is not None else None,
                "std":               round(sd, 4) if sd is not None else None,
                "upper_band":        round(upper_band, 4) if upper_band is not None else None,
                "lower_band":        round(lower_band, 4) if lower_band is not None else None,
                "stop_price":        round(stop_price, 4),
                "stop_distance_pts": round(stop_dist, 4),
                "contracts":         contracts,
            }
            # Trigger-blokken: hvert delkriterium hver for sig, så "hvorfor NETOP
            # denne bar?" kan besvares præcist bagefter.
            snap["trigger"] = detaljer
            if self._journal:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "trade_forensics",
                    symbol     = sym,
                    payload    = snap,
                )
        except Exception as e:
            logger.warning(f"[US-reversion] entry-forensik fejlede for {sym}: {e}")

        if self._broadcast_fn:
            await self._broadcast_async({
                "type":     "algo_trade",
                "strategy": self.name,
                "action":   "buy",
                "ticker":   sym,
                "price":    entry_price,
                "shares":   contracts,
                "time":     entry_time.strftime("%H:%M:%S"),
            })

        await self._log(
            f"📈 {sym}: LONG {contracts} kontrakt(er) @ ${entry_price:.2f} "
            f"(stop ${stop_price:.2f} = −{self.cfg.stop_pct}%, "
            f"stigning {detaljer.get('rise_pct')}%)")
        self._status("trading", f"📈 {sym}: LONG {contracts}× @ ${entry_price:.2f}")

    async def _close(self, sym: str, price: float, reason: str, z: float = None):
        pos = self._positions.get(sym)
        if pos is None:
            return

        side      = pos["side"]
        contracts = pos["contracts"]
        mult      = pos["multiplier"]
        entry     = pos["entry_price"]

        close_action = "SELL"   # long-only

        # Send luknings-ordren FØR vi bogfører noget.
        result = await self.conn.place_paper_order(
            sym, close_action, contracts, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC,
        )
        if not result:
            logger.error(f"[US-reversion] _close({sym}): lukke-ordre kunne IKKE sendes "
                         f"— beholder position åben")
            self._status("trading", f"⚠ {sym}: lukkeordre ikke sendt — position forbliver åben")
            return

        # Bekræft fyldning FØR vi popper + bogfører. Ufyldt → positionen forbliver
        # åben (og åben-i-journal), så næste bar / _close_all kan genforsøge.
        filled = result.get("filled") or 0
        if filled < contracts:
            # Spørg IBKR FØR næste bar gen-afgiver — ellers lukkes en position der
            # allerede er lukket, og residualet bliver en ejerløs modsat position.
            still = await self._ibkr_still_holds(sym, side, contracts)
            if still is False:
                await self._log(
                    f"ℹ {sym}: lukkeordre var ubekræftet, men IBKR er flad — ordren "
                    f"fyldte alligevel. Bogfører lukningen (gen-afgiver IKKE).",
                    level="warning")
            else:
                _why = result.get("reject_reason")
                logger.warning(f"[US-reversion] _close({sym}): lukke-ordre IKKE bekræftet "
                               f"(status={result.get('status')}, filled={filled}/{contracts}"
                               + (f", IBKR: {_why}" if _why else "") + ")")
                self._status("trading",
                             f"⚠ {sym}: lukkeordre ikke bekræftet ({_why or result.get('status')})"
                             + (" — position åben, genforsøger" if still
                                else " — feed upålideligt, gen-afgiver IKKE"))
                return

        fill = result.get("avg_fill")
        if fill and fill > 0:
            price = fill

        self._positions.pop(sym, None)
        self.stats.open_positions = len(self._positions)

        # Efter en exit kræves et NYT båndbrud før næste handel.
        self._armed[sym]    = False
        self._armed_at[sym] = None
        self._armed_z[sym]  = None

        # Dollar-P&L med multiplikator (long-only).
        pnl = (price - entry) * contracts * mult

        self.total_pnl += pnl
        self.stats.trades_today  += 1
        self.stats.pnl_today     += pnl
        self.stats.last_trade_time = datetime.now(ET).strftime("%H:%M:%S")
        if pnl > 0:
            self.stats.wins_today += 1
        else:
            self.stats.losses_today += 1

        if self._risk_manager:
            self._risk_manager.release_exposure(self.name, sym, pos.get("reserved", contracts * 10.0))
            asyncio.create_task(self._risk_manager.record_pnl(self.name, pnl))

        try:
            from orders_tracker import get_tracker
            close_order_id = result.get("order_id")
            if close_order_id:
                get_tracker().record_placed(
                    order_id=close_order_id, source=self.name, ticker=sym,
                    action=close_action, shares=contracts, order_type="MKT",
                )
        except Exception as e:
            logger.warning(f"[US-reversion] kunne ikke registrere lukke-ordre hos tracker: {e}")

        pnl_pct = ((price - entry) / entry * 100.0) if entry else 0.0

        trade = {
            "ticker":      sym,
            "side":        side,
            "entry_price": entry,
            "exit_price":  price,
            "shares":      contracts,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "reason":      reason,
            "entry_time":  pos["entry_time"].strftime("%H:%M:%S"),
            "exit_time":   datetime.now(ET).strftime("%H:%M:%S"),
        }
        self.trades.append(trade)

        mfe = self._mfe.pop(sym, None)
        mae = self._mae.pop(sym, None)

        try:
            chart_bars = bars_to_chart_payload(self._bar_history.get(sym, []),
                                               entry_time=pos.get("entry_time"))
        except Exception as e:
            logger.warning(f"[US-reversion] chart_bars-snapshot fejlede for {sym}: {e}")
            chart_bars = []
        stop_traj = pos.get("stop_traj", [])

        trade_id = pos.get("trade_id")
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = price,
                exit_time   = datetime.now(ET),
                exit_reason = reason,
                pnl         = pnl,
                payload     = {
                    "variant":                 self._variant_key,
                    "exit_z":                  round(z, 4) if z is not None else None,
                    "hh_close":                round(pos.get("hh_close", entry), 4),
                    "multiplier":              mult,
                    "contracts":               contracts,
                    "max_favorable_excursion": round(mfe, 4) if mfe is not None else None,
                    "max_adverse_excursion":   round(mae, 4) if mae is not None else None,
                    "chart_bars":              chart_bars,
                    "stop_trajectory":         stop_traj,
                },
            )

        try:
            snap = build_exit_snapshot(
                ticker       = sym,
                entry_price  = entry,
                exit_price   = price,
                entry_time   = pos["entry_time"],
                exit_time    = datetime.now(ET),
                shares       = contracts,
                pnl          = pnl,
                reason       = reason,
                bars         = self._bar_history.get(sym, []),
                context      = {},
                tape_buffer  = None,
                variant_name = f"{self.name} [{self._variant_key}]",
            )
            snap["reversion"] = {
                "variant":  self._variant_key,
                "exit_z":   round(z, 4) if z is not None else None,
                "hh_close": round(pos.get("hh_close", entry), 4),
            }
            if self._journal:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "trade_forensics",
                    symbol     = sym,
                    payload    = snap,
                )
        except Exception as e:
            logger.warning(f"[US-reversion] exit-forensik fejlede for {sym}: {e}")

        if self._broadcast_fn:
            await self._broadcast_async({
                "type":     "algo_trade",
                "strategy": self.name,
                "action":   "sell",
                **trade,
            })

        emoji = "✅" if pnl > 0 else "❌"
        await self._log(
            f"{emoji} {sym}: lukket @ ${price:.2f} ({reason}) | "
            f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
        self._status("trading", f"{emoji} {sym}: lukket @ ${price:.2f} | P&L: ${pnl:+.2f}")

    async def _close_all(self, reason: str):
        """Luk alle åbne positioner (sessions-slut eller stop), fyldnings-verificeret.

        FASE 1 — hurtige genforsøg: op til FORCE_CLOSE_MAX_ATTEMPTS forsøg med
        FORCE_CLOSE_RETRY_DELAY imellem. Dækker tynd likviditet.

        FASE 2 — feed-genoprettelses-vindue: IBKR's data-farm-drop ved sessions-slut
        forsinker simulerede ordrer i typisk minutter. I stedet for at lade positionen
        hænge natten over bliver vi ved — FEED-GATED — i op til LATE_CLOSE_MAX_MIN.

        Hvad der STADIG ikke kan lukkes forbliver åbent + åbent-i-journal;
        opstarts-reconcile fanger det næste session som sidste net."""
        # ── Fase 1: hurtige genforsøg ──
        for attempt in range(1, FORCE_CLOSE_MAX_ATTEMPTS + 1):
            if not self._positions:
                break
            for sym in list(self._positions.keys()):
                pos = self._positions[sym]
                snap = await self.conn.get_snapshot(sym)
                price = (snap.get("last") if snap else None) or pos["entry_price"]
                await self._close(sym, price, reason, pos.get("last_z"))
            if self._positions and attempt < FORCE_CLOSE_MAX_ATTEMPTS:
                await asyncio.sleep(FORCE_CLOSE_RETRY_DELAY)

        if not self._positions:
            return

        # ── Fase 2: feed-gated genoprettelses-vindue ──
        self._status("trading",
                     f"⚠ {', '.join(self._positions)}: ikke lukket i første forsøg — "
                     f"venter på datafeed i op til {LATE_CLOSE_MAX_MIN} min")
        deadline = datetime.now(ET) + timedelta(minutes=LATE_CLOSE_MAX_MIN)
        while self._positions and datetime.now(ET) < deadline:
            if self.status not in (StrategyStatus.RUNNING, StrategyStatus.STOPPED):
                break
            for sym in list(self._positions.keys()):
                pos = self._positions[sym]
                snap = await self.conn.get_snapshot(sym)
                price = snap.get("last") if snap else None
                if not price:
                    continue   # feed nede — forsøg ikke en luk uden kurs
                await self._close(sym, price, reason, pos.get("last_z"))
            if self._positions:
                await asyncio.sleep(FORCE_CLOSE_RETRY_DELAY)

        if self._positions:
            self._status("trading",
                         f"⚠ {', '.join(self._positions)}: ikke lukket inden for "
                         f"{LATE_CLOSE_MAX_MIN} min (datafeed nede) — forbliver åben, "
                         f"opstarts-reconcile fanger den næste session")

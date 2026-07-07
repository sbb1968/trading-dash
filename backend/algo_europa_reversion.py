"""
algo_europa_reversion.py
────────────────────────
Live trading-wrapper for "Europa-reversion" — mean-reversion på index-micro
futures (MES/M2K) i den EUROPÆISKE session (02:00–08:00 ET ≈ 08:00–14:00 dansk).

FØRSTE futures-strategi i systemet. Strukturelt en forenklet Confluence2Live:
samme BaseStrategy-livscyklus (pre_flight → on_start → _trading_loop → on_stop)
og samme broadcast/journal-mønster, men:

  - INGEN universe-scanning: universet er fast (MES, M2K).
  - INGEN TradingView/IBKR-gainer-scanner, ingen pris-filter.
  - INGEN trade-forensics/tape/L2.
  - SELVSTÆNDIG z-score-regel (ingen strategies/-pakke; den validerede
    backtest bor i eureversion_backtest.py — vi spejler dens z-matematik 1:1).
  - KONTRAKT-baseret sizing (futures), ikke shares (§2).
  - SCOPED, observe-først reconcile (§5) — delt konto-sikker.

Den validerede regel (LÅST — se SPEC):
  z = (close − MA(LOOKBACK)) / std(LOOKBACK)  på FÆRDIGE 15-min bars.
  Entry (kun i sessionen, kun hvis flad): |z| ≥ ENTRY_Z.
     z ≥ +ENTRY_Z → SHORT · z ≤ −ENTRY_Z → LONG.
  Exit (hvad end først): revert |z| ≤ EXIT_Z · stop |z| ≥ STOP_Z · sessions-slut.
  Én position pr. instrument ad gangen.

Placering: C:\\Projects\\trading_dash\\backend\\algo_europa_reversion.py
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from math import floor
from typing import Optional

import pytz

from strategy_base import BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus
from ibkr_connect import IBKRConnection
from reconcile_idempotency import (
    RECONCILE_CLOSING, reconcile_close_ref, decide_confirmation, WAIT, FLATTEN, RETRY)
# Delt forensik-opsamling — samme modul K2 bruger. De generiske builders beregner
# indikatorerne (RSI/MACD/BB/VWAP/EMA) via compute_all og samler tape/depth,
# strategi-agnostisk. EUREVERSION lægger sin egen bånd/z-blok oveni.
from trade_forensics import build_entry_snapshot, build_exit_snapshot

# Strategi-logik + parametre bor i pakken (delt sandhedskilde med backtesten
# eureversion_backtest.py, så live og backtest aldrig divergerer). Wrapperen
# håndterer kun IBKR, sizing, broadcast og reconcile.
from strategies.europa_reversion import EuropaReversionStrategy, rule
from strategies.europa_reversion.config import (
    SESSION_START_ET, SESSION_END_ET, FORCE_CLOSE_ET, LAST_SESSION_BAR_ET,
    LOOKBACK, ENTRY_Z, BAR_SIZE, BAR_MINUTES, INSTRUMENTS, RISK_PCT, MULTIPLIER,
)

logger = logging.getLogger(__name__)

# ── Tidszone ──────────────────────────────────────────────────
ET = pytz.timezone("America/New_York")

# ── Operationelle konstanter (wrapper-specifikke — IKKE strategi-reglen,
#    som bor i strategies/europa_reversion/) ──────────────────────────────
# Loop-frekvens. 15-min bars, så vi behøver ikke polle hyppigt; 20 sek giver
# prompt reaktion uden at hamre IBKR.
LOOP_SLEEP_SECONDS = 20

# Warmup: hent rigeligt 15-min historie (useRTH=False — EU-session ligger uden
# for US RTH) så vi har ≥ LOOKBACK bars klar ved sessions-start.
WARMUP_DURATION       = "3 D"
LATEST_FETCH_DURATION = "14400 S"   # 4 timer ≈ 16 bars — nok til at finde nye færdige bars

# Genforbinding (spejler K2)
MAX_CONNECT_RETRIES = 3
CONNECT_RETRY_DELAY = 10

HEARTBEAT_INTERVAL_SEC = 300

# Fyldnings-verificeret luk (spejler K2 331f898). place_paper_order venter på
# bekræftet fyldning af lukke-ordrer; ufyldt → vi popper IKKE positionen.
CLOSE_FILL_WAIT_SEC      = 8    # sek place_paper_order venter på fyldning af en lukke-ordre
FORCE_CLOSE_MAX_ATTEMPTS = 4    # antal gange _close_all genforsøger ufyldte lukninger
FORCE_CLOSE_RETRY_DELAY  = 4    # sek mellem _close_all-genforsøg (fase 1 + 2)
LATE_CLOSE_MAX_MIN       = 20   # min fase 2 venter (feed-gated) på at datafeedet kommer tilbage,
                                # så hængende positioner flades ud i stedet for at hænge til næste session
RECONCILE_TIMEOUT_SEC    = 30   # maks sekunder opstarts-reconcile må tage før den springes over


@dataclass
class Bar:
    timestamp: datetime   # tz-aware ET
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float


class EuropaReversionLive(BaseStrategy):
    """
    Live-wrapper for Europa-reversion. Følger BaseStrategy-interfacet så
    StrategyManager kan administrere den parallelt med de øvrige strategier.
    """

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn

        # Strategi-facade fra pakken — al beslutningslogik (z-regel) delegeres
        # hertil, så live og backtest deler præcis samme kode.
        self._strategy = EuropaReversionStrategy()

        # Fast univers (override af base-default []). Resten af den fælles state
        # (_bar_history, _last_bar_processed, _positions, trades, total_pnl,
        # _loop_task, _mfe, _mae, _tape_buffer) sættes nu i BaseStrategy.__init__.
        # Positions-dict pr. instrument:
        #   {side, entry_price, contracts, multiplier, entry_time,
        #    stop_price, std, reserved, trade_id}
        self.universe: list[str] = list(INSTRUMENTS)

    # -------------------------------------------------------------
    # BaseStrategy interface — properties
    # -------------------------------------------------------------

    # Navn/beskrivelse/aktivklasse delegeres til pakke-strategien (én kilde).
    @property
    def name(self) -> str:
        return self._strategy.name

    @property
    def description(self) -> str:
        return self._strategy.description

    @property
    def asset_class(self) -> str:
        return self._strategy.asset_class

    # -------------------------------------------------------------
    # Pre-flight (§1)
    # -------------------------------------------------------------

    async def pre_flight(self) -> tuple[bool, str]:
        ok, err, checks = await self._preflight_connection_and_account()
        if not ok:
            return False, err

        self._status("started", "Pre-flight: Tester futures-datafeed (MES, 15-min)...")
        test_bars = await self.conn.get_historical_bars(
            "MES", duration="1 D", bar_size=BAR_SIZE, what_to_show="TRADES",
        )
        if not test_bars:
            return False, "Kan ikke hente 15-min futures-bars for MES"
        checks.append(f"Futures-datafeed virker — {len(test_bars)} MES-bars")

        summary = " | ".join([f"✅ {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        await asyncio.sleep(1)
        return True, summary

    # -------------------------------------------------------------
    # Start / Stop
    # -------------------------------------------------------------

    async def on_start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            logger.warning("[Europa-reversion] _trading_loop kører allerede — afbryder ny start")
            return

        self._status("started", "Algoritme starter — Europa-reversion (MES/M2K)")

        # ── Roll: vælg aktuel front-måned for hvert instrument ──
        # Genopfrisk cachen ved hver start/dag, så vi ruller til en ny
        # kontrakt uden genstart.
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
                logger.error(f"[Europa-reversion] front-måned-valg {sym} fejlede: {e}")

        # ── Reconciliation: scoped, observe-først (§5) ──
        # Best-effort OG tidsbegrænset: hverken fejl eller hang må blokere starten.
        try:
            await asyncio.wait_for(self._reconcile_orphans(), timeout=RECONCILE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error(f"[Europa-reversion] reconcile-timeout ({RECONCILE_TIMEOUT_SEC}s) "
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
    # Reconcile — scoped, observe-først (§5)
    # -------------------------------------------------------------

    async def _reconcile_orphans(self) -> None:
        """Best-effort wrapper: ENHVER fejl i reconcile fanges og blokerer ALDRIG
        handelsstarten. Selve logikken bor i _reconcile_orphans_impl."""
        try:
            await self._reconcile_orphans_impl()
        except Exception as e:
            logger.exception(f"[Europa-reversion] reconcile fejlede (best-effort, ignoreret): {e}")
            self._status("started", "Reconciliation sprang fejlet over — fortsætter til handel")

    async def _reconcile_orphans_impl(self) -> None:
        """
        Delt-konto-sikker reconcile ved opstart.

        To guards i stedet for nul-reconcile:
          1. INSTRUMENT-KLASSE: kun positioner i VORES futures-symboler
             (MES/M2K) er overhovedet synlige her — aktie-positioner (ORB/K2)
             er per definition usynlige. Vandtæt mod at røre en anden strategis
             position.
          2. JOURNAL-SPOR: en futures-position i vores instrument lukkes KUN
             hvis der findes en åben journal-row med source=self.name for
             symbolet (= ægte vores, hvor en lukke-ordre aldrig fyldte).
             Ellers OBSERVE-ONLY: log advarsel, rør den IKKE (kan være manuel
             handel).

        Best-effort: en fejl her må ikke blokere dagens handel.
        """
        if self.conn is None or not self.conn.connected:
            self._status("started", "Reconciliation sprunget over — IBKR ikke forbundet")
            return

        # Reliable live-read (ikke den kolde cache). KRITISK her: begge pas nedenfor
        # (IBKR-løkken OG _reconcile_close_stale_journal_rows) er absence-baserede —
        # en tom/degraderet positions-liste ved opstart ville ellers markere en ægte
        # åben MES/M2K-journal-row som "forældet" og lukke den UDEN ordre → den ægte
        # position bliver forældreløs (præcis MES-fejlen). reliable=False → spring ALT over.
        ibkr_positions, feed_ok = await self.conn.get_positions_reliable()
        if not feed_ok:
            logger.warning("[Europa-reversion] reconciliation: positions-feed upålideligt "
                           "(tom/timeout ved opstart) — springer over for ikke at "
                           "forældreløsgøre en ægte position")
            self._status("started", "Reconciliation sprunget over — positions-feed upålideligt")
            return

        # Guard 1: instrument-klasse — KUN vores futures-symboler.
        ours = [p for p in ibkr_positions
                if p.get("ticker") in INSTRUMENTS and p.get("position")]

        if not ours:
            self._status("started",
                         "Reconciliation: ingen gamle futures-positioner i vores instrumenter")
            # FALD IKKE igennem til IBKR-løkken (den er tom), men HOP til journal-passet
            # nedenfor: forældede åbne journal-rows uden IBKR-position skal stadig lukkes.
            await self._reconcile_close_stale_journal_rows(ibkr_positions)
            return

        for p in ours:
            sym = p["ticker"]
            qty = p["position"]

            # Guard 2: journal-spor — har VI en åben handel for symbolet?
            open_rows = []
            try:
                if self._journal is not None and getattr(self._journal, "_db", None) is not None:
                    from trade_queries import list_trades
                    open_rows = await list_trades(
                        self._journal._db, status="open", source=self.name, symbol=sym,
                    )
            except Exception as e:
                logger.error(f"[Europa-reversion] reconciliation: list_trades fejl for {sym}: {e}")
                open_rows = []

            if open_rows:
                _r = open_rows[0]
                # Idempotens-gate (lukker Del C): allerede en reconcile-close afgivet for
                # row'en? → bekraeftelses-sti, ALDRIG blind gen-placering paa stale feed.
                if (_r.get("current_stage") or "") == RECONCILE_CLOSING:
                    await self._reconcile_confirm(sym, qty, _r)
                else:
                    # Ægte vores (lukke-ordre fyldte aldrig) → luk + bogfør.
                    await self._reconcile_close(sym, qty, _r)
            else:
                # Observe-only: aldrig blind-flatte.
                await self._log(
                    f"🔎 Gammel åben position {sym} ({qty:+.0f}) i IBKR uden journal-spor "
                    f"fra os — observe-only, rører den IKKE", level="warning")

        # ── Journal-pas: luk forældede åbne rows UDEN modsvarende IBKR-position ──
        # (IBKR er sandheden; en åben journal-row uden position betyder positionen
        # blev lukket manuelt/eksternt eller en lukke-ordre fyldte i IBKR men ikke i
        # journalen. Vi sender INGEN ordre — der er intet at lukke i IBKR — vi retter
        # kun journalen så den matcher.)
        await self._reconcile_close_stale_journal_rows(ibkr_positions)

    async def _reconcile_close(self, sym: str, qty: float, row: dict) -> None:
        """Luk en gammel åben position der er ægte vores, og bogfør den med
        exit_reason='reconcile_flatten' (spejler K2's bogføring). Tæller IKKE
        med i dagens handels-statistik (vi rører ikke self.trades/self.stats)."""
        side         = "long" if qty > 0 else "short"
        contracts    = int(abs(qty))
        close_action = "SELL" if side == "long" else "BUY"
        mult         = MULTIPLIER.get(sym, 5.0)

        entry = row.get("entry_price") or 0.0
        snap = await self.conn.get_snapshot(sym)
        exit_price = (snap.get("last") if snap else None) or entry or 0.0

        # Undgaa DUPLIKAT-lukordre: hviler der allerede en aktiv {close_action}-ordre paa
        # symbolet (fx en GTC force-close fra en tidligere session der ikke fyldte), saa lad
        # DEN lukke positionen — laeg ikke endnu en (ellers dobbelt-luk ved aabningen). Row
        # forbliver aaben; naeste reconcile bekraefter naar den eksisterende er fyldt.
        _dups = [o for o in await self.conn.get_open_orders()
                 if o["symbol"] == sym.upper() and o["action"] == close_action]
        if _dups:
            await self._log(
                f"⏸ {sym}: en {close_action}-ordre hviler allerede "
                f"({_dups[0]['status']}, rest {_dups[0]['remaining']:.0f}) — laegger IKKE en "
                f"duplikat. Lader den eksisterende lukke; reconcile bekraefter naar fyldt.",
                level="warning")
            return

        # Idempotens: durabel markoer + deterministisk orderRef FOER vi venter paa fyldning.
        _tid = row.get("trade_id")
        _ref = reconcile_close_ref(_tid)
        if _tid and self._journal:
            try:
                await self._journal.update_trade_state(_tid, current_stage=RECONCILE_CLOSING)
            except Exception as e:
                logger.error(f"[Europa-reversion] kunne ikke saette reconcile_closing-markoer ({sym}): {e}")

        result = await self.conn.place_paper_order(
            sym, close_action, contracts, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC, order_ref=_ref,
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
        if filled < contracts:
            await self._log(
                f"⚠ {sym}: reconcile-lukning IKKE bekræftet fyldt "
                f"(status={result.get('status')}, filled={filled}/{contracts}) "
                f"— journal-row forbliver åben (markedet kan være lukket: en MKT-ordre "
                f"fylder først ved åbning). Luk evt. manuelt i TWS.", level="warning")
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
        """Bekraeftelses-sti for en row der ALLEREDE er i reconcile_closing (delt, ren
        beslutning via decide_confirmation). Placerer ALDRIG paa UKENDT udfald (Del C).
        Kaldes kun fra 'ours'-loopet, dvs. IBKR viser en position (qty != 0)."""
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
            await self._log(f"↻ {sym}: reconcile-close bekraeftet doed-ufyldt (udfald={outcome}), "
                            f"position stadig aaben — genafgiver", level="warning")
            await self._reconcile_close(sym, qty, row)
        else:
            await self._log(f"🔎 {sym}: reconcile-close ({ref}) ubekraeftet og ikke synlig; position "
                            f"ser stadig aaben ud ({qty:+.0f}) — observe-only, afventer", level="warning")

    async def _reconcile_mark_filled(self, sym: str, row: dict, outcome: str) -> None:
        """Tidligere reconcile-close bekraeftet fyldt (eller position flad) → bogfoer row'en
        lukket (reconcile_flatten, nul-P&L est.). INGEN ny ordre."""
        trade_id = row.get("trade_id")
        entry = row.get("entry_price") or 0.0
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id=trade_id, exit_price=entry, exit_time=datetime.now(ET),
                exit_reason="reconcile_flatten", pnl=0.0,
                payload={"reconcile": True, "confirmed_outcome": outcome})
        await self._log(f"♻ {sym}: tidligere reconcile-close bekraeftet ({outcome}) — row bogfoert "
                        f"lukket (reconcile_flatten, nul-P&L est.)", level="warning")

    async def _reconcile_close_stale_journal_rows(self, ibkr_positions: list) -> None:
        """Luk åbne journal-rows (source=self.name, MES/M2K) der IKKE har nogen
        modsvarende IBKR-position. Sender INGEN IBKR-ordre — positionen er allerede
        væk i IBKR; vi retter kun journalen så den matcher virkeligheden.

        Dette er det modsatte pas af IBKR-løkken: dér lukker vi IBKR-positioner der
        har journal-spor; her lukker vi journal-rows der IKKE har IBKR-spor. Tilsammen
        holder de journal og IBKR i sync uanset hvilken side der drev fra.

        Tæller IKKE med i dagens handels-statistik (rører ikke self.trades/self.stats).
        """
        if self._journal is None or getattr(self._journal, "_db", None) is None:
            return

        # Symboler vi FAKTISK har en position i hos IBKR (disse rows er IKKE forældede
        # — de håndteres af IBKR-løkken / det normale exit-flow).
        held = {p.get("ticker") for p in ibkr_positions
                if p.get("ticker") in INSTRUMENTS and p.get("position")}

        try:
            from trade_queries import list_trades
            open_rows = await list_trades(
                self._journal._db, status="open", source=self.name,
            )
        except Exception as e:
            logger.error(f"[Europa-reversion] reconciliation: list_trades (journal-pas) fejl: {e}")
            return

        for row in open_rows:
            sym = (row.get("symbol") or "").upper()
            # Kun vores instrumenter, og kun rows UDEN modsvarende IBKR-position.
            if sym not in INSTRUMENTS:
                continue
            if sym in held:
                continue   # ægte åben position → lad det normale flow håndtere den

            trade_id = row.get("trade_id")
            if not trade_id:
                continue

            # Luk journal-rowen så den matcher IBKR (fladt). Ingen IBKR-ordre.
            # exit_price = sidste kendte (snapshot hvis muligt, ellers entry) — kun til
            # bogføring; P&L kan ikke beregnes pålideligt uden en faktisk fill, så vi
            # bogfører 0 og markerer årsagen tydeligt.
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
            self._status("started",
                         f"Forældet journal-row {sym} ryddet (ingen IBKR-position)")

    # -------------------------------------------------------------
    # Status broadcast
    # -------------------------------------------------------------

    # _status() er løftet til BaseStrategy (Trin 1).

    # -------------------------------------------------------------
    # Warmup-forberedelse
    # -------------------------------------------------------------

    async def _prepare(self):
        self._status("loading_orb",
                     f"Henter warmup-historie (15-min) for {', '.join(INSTRUMENTS)}...")

        ready = []
        for sym in INSTRUMENTS:
            bars = await self._fetch_warmup_bars(sym)
            self._bar_history[sym] = bars
            if bars:
                # Sæt sidst-behandlede til seneste warmup-bar, så vi ikke
                # genbehandler den. Live-loopet appender først når en NYERE
                # færdig bar ankommer.
                self._last_bar_processed[sym] = bars[-1].timestamp
                ready.append(sym)
            if len(bars) < LOOKBACK:
                self._status("loading_orb",
                             f"⚠ {sym}: kun {len(bars)} warmup-bars (<{LOOKBACK}) — "
                             f"z bliver klar når flere bars ankommer")

        await self.log_universe(
            self.universe,
            meta={"session": "europæisk 02-08 ET", "bar_size": BAR_SIZE, "lookback": LOOKBACK},
        )

        # Datablind-fix: emit ÉT bar_evaluation pr. instrument for sidste warmup-bar,
        # så watchdog-uret stilles friskt allerede ved start. Uden dette ser watchdog
        # nattens gamle bar_evaluation (~13 t) og fyrer falsk "EUREVERSION DATABLIND"
        # fra graceen udløber (~20 min inde) til første LIVE bar lander (~30 min inde).
        # Vi LOGGER kun denne ene evaluering — ingen exit/entry-handling på en warmup-bar.
        for sym in ready:
            hist = self._bar_history.get(sym, [])
            if len(hist) < LOOKBACK:
                continue
            res = rule.compute_z([b.close for b in hist[-LOOKBACK:]])
            if res is None:
                continue
            z, sd = res
            last_bar = hist[-1]
            in_session = SESSION_START_ET <= last_bar.timestamp.time() < SESSION_END_ET
            await self.log_bar_evaluation(
                ticker      = sym,
                bar_time_et = last_bar.timestamp.astimezone(ET).strftime("%H:%M")
                              if hasattr(last_bar.timestamp, "astimezone") else str(last_bar.timestamp),
                status      = "in_session" if in_session else "out_of_session",
                reason      = f"warmup z={z:+.2f} sd={sd:.2f}",
            )

        self._status("orb_ready",
                     f"✅ Klar — {len(ready)}/{len(INSTRUMENTS)} instrumenter med "
                     f"warmup-historie ({', '.join(INSTRUMENTS)})")

    async def _fetch_warmup_bars(self, sym: str) -> list[Bar]:
        return await self._fetch_bars(sym, duration=WARMUP_DURATION, bar_size=BAR_SIZE)

    # -------------------------------------------------------------
    # Trading loop
    # -------------------------------------------------------------

    async def _trading_loop(self):
        self._status("trading", "Overvåger MES/M2K — venter på færdige 15-min bars...")
        consecutive_errors = 0
        _last_heartbeat = datetime.now(ET)

        try:
            while self.status == StrategyStatus.RUNNING:
                now_et = datetime.now(ET)
                t = now_et.time()

                if t < SESSION_START_ET:
                    self._status("orb_ready",
                                 f"Venter på EU-session — starter kl. "
                                 f"{SESSION_START_ET.strftime('%H:%M')} ET",
                                 persist=False)
                    await asyncio.sleep(LOOP_SLEEP_SECONDS)
                    continue

                # Tvangsluk (tidsbaseret): når vi passerer FORCE_CLOSE_ET lukker
                # vi alt og afslutter dagen. Holder ALDRIG over sessionen.
                if t >= FORCE_CLOSE_ET:
                    if self._positions:
                        self._status("trading", "Sessions-slut nærmer sig — lukker alle positioner")
                        await self._close_all("session_end")
                    wins   = sum(1 for tr in self.trades if tr["pnl"] > 0)
                    losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                    self._status("done",
                                 f"✅ EU-session afsluttet | "
                                 f"P&L: ${self.total_pnl:+,.2f} | "
                                 f"{len(self.trades)} handler ({wins}W/{losses}L)")
                    await self._log_self_stop("Sessions-slut: force-close",
                                              StrategyStatus.STOPPED, "done")
                    break

                self._status("trading",
                             f"Overvåger {len(INSTRUMENTS)} instrumenter — "
                             f"{now_et.strftime('%H:%M:%S')} ET | "
                             f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}",
                             persist=False)

                if (now_et - _last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL_SEC:
                    _last_heartbeat = now_et
                    await self.log_heartbeat({
                        "open_positions": self.stats.open_positions,
                        "trades":         len(self.trades),
                        "total_pnl":      round(self.total_pnl, 2),
                        "instruments":    INSTRUMENTS,
                    })

                try:
                    for sym in INSTRUMENTS:
                        if self.status != StrategyStatus.RUNNING:
                            break
                        await self._check_instrument(sym)
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    logger.exception(f"[Europa-reversion] fejl i handels-loop: {e}")
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
            logger.exception(f"[Europa-reversion] _trading_loop crashede: {e}")
            await self._log_self_stop(
                f"Fejl i strategi-loop: {type(e).__name__}: {str(e)[:80]}",
                StrategyStatus.ERROR, "error")

    # _reconnect() er løftet til BaseStrategy (Trin 1).

    # -------------------------------------------------------------
    # Instrument-check — bar-dedup + z-regel (spejler backtest)
    # -------------------------------------------------------------

    async def _check_instrument(self, sym: str):
        """
        Hent nye FÆRDIGE bars, append dem til historien og evaluér z-reglen.

        Bar-dedup (den kritiske K2-lektion): en bar er FÆRDIG først når dens
        sluttidspunkt (start + BAR_MINUTES) er passeret. Vi appender og
        evaluerer kun færdige bars vi ikke har set før — aldrig den stadig-
        formende aktuelle bar. Appender ALLE nye færdige bars (ikke kun den
        seneste), så historien forbliver sammenhængende selv efter et udfald.
        """
        parsed = await self._fetch_recent_bars(sym)
        if not parsed:
            return

        now = datetime.now(ET)
        last = self._last_bar_processed.get(sym)

        finished = [b for b in parsed if now >= b.timestamp + timedelta(minutes=BAR_MINUTES)]
        if last is not None:
            finished = [b for b in finished if b.timestamp > last]
        if not finished:
            return

        hist = self._bar_history.setdefault(sym, [])
        for bar in finished:
            hist.append(bar)
            self._last_bar_processed[sym] = bar.timestamp
            if len(hist) >= LOOKBACK:
                res = rule.compute_z([b.close for b in hist[-LOOKBACK:]])
                if res is not None:
                    z, sd = res
                    await self._evaluate_bar(sym, bar, z, sd)

        # Hold hukommelsen i ave (vi behøver kun de seneste LOOKBACK closes).
        if len(hist) > LOOKBACK * 8:
            self._bar_history[sym] = hist[-LOOKBACK * 8:]

    async def _fetch_recent_bars(self, sym: str) -> list[Bar]:
        return await self._fetch_bars(sym, duration=LATEST_FETCH_DURATION, bar_size=BAR_SIZE)

    async def _evaluate_bar(self, sym: str, bar: Bar, z: float, sd: float):
        """Kør z-reglen på én netop-færdiggjort bar (spejler run_backtest)."""
        bar_t = bar.timestamp.time()
        in_session = SESSION_START_ET <= bar_t < SESSION_END_ET

        # Lag B+: log DENNE bars evaluering — gør EUREVERSION synlig for datablind-
        # forensik/watchdog (emitterede før INTET bar_evaluation). Ét event pr. færdig
        # bar = datablind-signalet: fyrer når data flyder, stopper når feedet dør.
        await self.log_bar_evaluation(
            ticker      = sym,
            bar_time_et = bar.timestamp.astimezone(ET).strftime("%H:%M")
                          if hasattr(bar.timestamp, "astimezone") else str(bar.timestamp),
            status      = "in_session" if in_session else "out_of_session",
            reason      = f"z={z:+.2f} sd={sd:.2f}",
        )

        # ── Åben position: tjek exit ──
        if sym in self._positions:
            pos = self._positions[sym]
            side = pos["side"]

            # MFE/MAE-tracking (spejler K2) + gem seneste z, så session_end-luk
            # via _close_all også kan registrere exit_z (var tom før).
            if sym in self._mfe:
                self._mfe[sym] = max(self._mfe[sym], bar.high)
            if sym in self._mae:
                self._mae[sym] = min(self._mae[sym], bar.low)
            pos["last_z"] = z

            # Skriv seneste pris til trades-tabel → live urealiseret P&L i Studio
            # (uafhængigt af IBKR-snapshottet).
            trade_id = pos.get("trade_id")
            if trade_id and self._journal:
                await self._journal.update_trade_state(trade_id=trade_id, current_price=bar.close)

            # Bar-baseret tvangsluk-backstop: sessionens sidste bar lukker altid.
            if bar_t >= LAST_SESSION_BAR_ET:
                await self._close(sym, bar.close, "session_end", z)
                return

            # Exit-beslutning fra den delte regel (revert/stop), samme som backtest.
            reason = rule.exit_reason(side, z)
            if reason:
                await self._close(sym, bar.close, reason, z)
            return

        # ── Flad: vurdér entry (kun i sessionen, ikke tæt på sessions-slut) ──
        if not in_session:
            return
        if datetime.now(ET).time() >= FORCE_CLOSE_ET:
            return
        if bar_t >= LAST_SESSION_BAR_ET:
            return
        if self.stats.open_positions >= self.config.max_open_positions:
            return

        # Entry-beslutning fra den delte regel (None hvis |z| < ENTRY_Z).
        side = rule.entry_side(z)
        if side is None:
            return
        await self._open(sym, side, bar, sd, z)

    # -------------------------------------------------------------
    # Sizing (§2) — kontrakt-baseret, 1% risiko
    # -------------------------------------------------------------

    def _size_contracts(self, sym: str, sd: float) -> tuple[int, float, float]:
        """
        Returnér (antal_kontrakter, stop_afstand_point, per_kontrakt_risiko).
        antal_kontrakter == 0 → spring handlen over (konto for lille til denne
        bars stop-afstand).
        """
        mult = MULTIPLIER.get(sym, 5.0)

        # Risiko-dollars = 1% af konto-equity; fallback til per-trade-grænsen
        # hvis konto-equity ikke kan hentes.
        account = self.conn.get_account_summary()
        equity = account.get("net_liquidation", 0) or 0
        risk_dollars = RISK_PCT * equity if equity > 0 else self.config.max_loss_per_trade

        stop_dist = rule.stop_distance(sd)           # (STOP_Z − ENTRY_Z) × std, i prispoint
        per_contract_risk = stop_dist * mult
        if per_contract_risk <= 0:
            return 0, stop_dist, per_contract_risk

        by_risk = floor(risk_dollars / per_contract_risk)
        by_cap  = floor(self.config.max_loss_per_trade / per_contract_risk)
        contracts = min(by_risk, by_cap)

        if contracts < 1:
            # Sikkerhedsgulv: handl 1 kontrakt KUN hvis 1-kontrakts-risiko er
            # inden for per-trade-grænsen; ellers spring over (ærligere end at
            # overtrade en for lille konto).
            if per_contract_risk <= self.config.max_loss_per_trade:
                contracts = 1
            else:
                contracts = 0

        return contracts, stop_dist, per_contract_risk

    # -------------------------------------------------------------
    # Open / Close — ordre-håndtering
    # -------------------------------------------------------------

    async def _open(self, sym: str, side: str, bar: Bar, sd: float, z: float):
        if self.stats.open_positions >= self.config.max_open_positions:
            return

        contracts, stop_dist, per_contract_risk = self._size_contracts(sym, sd)
        if contracts < 1:
            await self._log(
                f"⏭ {sym}: springer {side}-entry over — stop-afstand for stor til "
                f"kontoen (1-kontrakts-risiko ${per_contract_risk:,.0f} > "
                f"per-trade-grænse ${self.config.max_loss_per_trade:,.0f})", level="warning")
            return

        action = "BUY" if side == "long" else "SELL"
        mult   = MULTIPLIER.get(sym, 5.0)

        # IBKR's faktiske initial-margin denne ordre binder (KUN display, fejl-sikker → None).
        init_margin = await self.conn.what_if_init_margin(sym, action, contracts)

        order = OrderRequest(
            strategy_name=self.name,
            ticker=sym,
            action=action,
            quantity=contracts,
            order_type="MKT",
            asset_class="futures",
            reason=f"Europa-reversion {side} z-entry ({contracts} kontrakt(er))",
        )

        if self._risk_manager:
            approved = await self.request_order(order)
            if not approved:
                return

        result = await self.conn.place_paper_order(sym, action, contracts, source=self.name)
        if not result:
            self._status("trading", f"⚠ {sym}: entry-ordre kunne ikke sendes")
            return

        entry_price = bar.close
        fill = result.get("avg_fill")
        if fill and fill > 0:
            entry_price = fill

        entry_time = datetime.now(ET)
        # Approksimeret stop-pris (z-stop oversat til pris) til journal-visning.
        stop_price = entry_price - stop_dist if side == "long" else entry_price + stop_dist
        reserved   = contracts * 10.0   # spejler RiskManager's estimated_value for MKT

        self._positions[sym] = {
            "side":        side,
            "entry_price": entry_price,
            "contracts":   contracts,
            "multiplier":  mult,
            "entry_time":  entry_time,
            "stop_price":  stop_price,
            "std":         sd,
            "reserved":    reserved,
            "init_margin": init_margin,
            "trade_id":    None,
        }
        self.stats.open_positions = len(self._positions)

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
            logger.warning(f"[Europa-reversion] kunne ikke registrere ordre hos tracker: {e}")

        # Bånd-kontekst (reversionens pendant til K2's indikatorer):
        # z = (close − mean)/sd  ⇒  mean = close − z·sd.
        mean       = entry_price - z * sd
        upper_band = mean + ENTRY_Z * sd
        lower_band = mean - ENTRY_Z * sd

        # Trades-tabel
        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source        = self.name,
                symbol        = sym,
                side          = side,
                shares        = contracts,
                entry_price   = entry_price,
                entry_time    = entry_time,
                entry_reason  = f"Europa-reversion {side} (z={z:+.2f})",
                current_stop  = stop_price,
                current_stage = "initial",
                payload       = {
                    "entry_z":           round(z, 4),
                    "std":               round(sd, 4),
                    "mean":              round(mean, 4),
                    "upper_band":        round(upper_band, 4),
                    "lower_band":        round(lower_band, 4),
                    "multiplier":        mult,
                    "stop_distance_pts": round(stop_dist, 4),
                    "contracts":         contracts,
                    "init_margin":       round(init_margin, 2) if init_margin is not None else None,
                },
            )
            if trade_id:
                self._positions[sym]["trade_id"] = trade_id

        # MFE/MAE-tracking start (spejler K2).
        self._mfe[sym] = entry_price
        self._mae[sym] = entry_price

        # Entry-forensik via den delte builder → samme CSV-kolonner som K2
        # (entry_indicators_*, entry_tape_*, entry_depth_*). Vi tilføjer en
        # reversion-blok (bånd/z) som EUREVERSIONs pendant til K2's setup.
        # FAIL-SAFE: forensik må ALDRIG vælte handlen (spejler journalens regel).
        try:
            snap = build_entry_snapshot(
                ticker       = sym,
                entry_price  = entry_price,
                entry_time   = entry_time,
                shares       = contracts,
                bars         = self._bar_history.get(sym, []),
                context      = {},            # ingen ORB-kontekst for en reverter
                tape_buffer  = None,          # ingen tape/depth (se note nedenfor)
                variant_name = self.name,
            )
            snap["reversion"] = {
                "entry_z":           round(z, 4),
                "mean":              round(mean, 4),
                "std":               round(sd, 4),
                "upper_band":        round(upper_band, 4),
                "lower_band":        round(lower_band, 4),
                "stop_price":        round(stop_price, 4),
                "stop_distance_pts": round(stop_dist, 4),
                "contracts":         contracts,
            }
            if self._journal:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "trade_forensics",
                    symbol     = sym,
                    payload    = snap,
                )
        except Exception as e:
            logger.warning(f"[Europa-reversion] entry-forensik fejlede for {sym}: {e}")

        # Broadcast (LiveAlgo: "buy" = long-åbning, "sell_short" = short-åbning)
        if self._broadcast_fn:
            await self._broadcast_async({
                "type":     "algo_trade",
                "strategy": self.name,
                "action":   "buy" if side == "long" else "sell_short",
                "ticker":   sym,
                "price":    entry_price,
                "shares":   contracts,
                "time":     entry_time.strftime("%H:%M:%S"),
            })

        verb = "LONG" if side == "long" else "SHORT"
        await self._log(
            f"📈 {sym}: {verb} {contracts} kontrakt(er) @ ${entry_price:.2f} "
            f"(stop ≈ ${stop_price:.2f}, std={sd:.2f})")
        self._status("trading",
                     f"📈 {sym}: {verb} {contracts}× @ ${entry_price:.2f}")

    async def _close(self, sym: str, price: float, reason: str, z: float = None):
        # z = z-værdi ved exit (None ved ikke-regel-luk, fx _close_all).
        pos = self._positions.get(sym)
        if pos is None:
            return

        side      = pos["side"]
        contracts = pos["contracts"]
        mult      = pos["multiplier"]
        entry     = pos["entry_price"]

        close_action = "SELL" if side == "long" else "BUY"

        # Send luknings-ordren FØR vi bogfører noget.
        result = await self.conn.place_paper_order(
            sym, close_action, contracts, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC,
        )
        if not result:
            logger.error(f"[Europa-reversion] _close({sym}): lukke-ordre kunne IKKE sendes "
                         f"— beholder position åben")
            self._status("trading", f"⚠ {sym}: lukkeordre ikke sendt — position forbliver åben")
            return

        # Bekræft fyldning FØR vi popper + bogfører. Ufyldt (halt / tynd likviditet /
        # afgivet for sent) → vi popper IKKE og bogfører IKKE; positionen forbliver
        # åben (og åben-i-journal), så næste bar / _close_all kan genforsøge. Netop dét
        # forhindrer spøgelset hvor journalen siger 'lukket' mens IBKR holder positionen.
        filled = result.get("filled") or 0
        if filled < contracts:
            logger.warning(f"[Europa-reversion] _close({sym}): lukke-ordre IKKE bekræftet fyldt "
                           f"(status={result.get('status')}, filled={filled}/{contracts}) "
                           f"— beholder position åben, genforsøger")
            self._status("trading",
                         f"⚠ {sym}: lukkeordre ikke bekræftet fyldt "
                         f"(status={result.get('status')}, filled={filled}/{contracts}) "
                         f"— position forbliver åben")
            return

        fill = result.get("avg_fill")
        if fill and fill > 0:
            price = fill

        self._positions.pop(sym, None)
        self.stats.open_positions = len(self._positions)

        # Dollar-P&L med multiplikator (KAN ikke bruge base.record_position_closed
        # — den ganger ikke med kontrakt-multiplikatoren).
        if side == "long":
            pnl = (price - entry) * contracts * mult
        else:
            pnl = (entry - price) * contracts * mult

        self.total_pnl += pnl
        self.stats.trades_today  += 1
        self.stats.pnl_today     += pnl
        self.stats.last_trade_time = datetime.now(ET).strftime("%H:%M:%S")
        if pnl > 0:
            self.stats.wins_today += 1
        else:
            self.stats.losses_today += 1

        # Risk-manager: frigør eksponering + bogfør P&L (spejler base-flowet,
        # men med den korrekte futures-P&L).
        if self._risk_manager:
            self._risk_manager.release_exposure(self.name, sym, pos.get("reserved", contracts * 10.0))
            asyncio.create_task(self._risk_manager.record_pnl(self.name, pnl))

        # OrdersTracker
        try:
            from orders_tracker import get_tracker
            close_order_id = result.get("order_id")
            if close_order_id:
                get_tracker().record_placed(
                    order_id=close_order_id, source=self.name, ticker=sym,
                    action=close_action, shares=contracts, order_type="MKT",
                )
        except Exception as e:
            logger.warning(f"[Europa-reversion] kunne ikke registrere lukke-ordre hos tracker: {e}")

        pnl_pct = ((price - entry) / entry * 100.0) * (1 if side == "long" else -1) if entry else 0.0

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

        # MFE/MAE pop (spejler K2) — gemmes i payload (samme tp_*_excursion-
        # kolonner som K2) og i exit-forensikken.
        mfe = self._mfe.pop(sym, None)
        mae = self._mae.pop(sym, None)

        # Trades-tabel
        trade_id = pos.get("trade_id")
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = price,
                exit_time   = datetime.now(ET),
                exit_reason = reason,
                pnl         = pnl,
                payload     = {
                    "exit_z":                  round(z, 4) if z is not None else None,
                    "multiplier":              mult,
                    "contracts":               contracts,
                    "max_favorable_excursion": round(mfe, 4) if mfe is not None else None,
                    "max_adverse_excursion":   round(mae, 4) if mae is not None else None,
                },
            )

        # Exit-forensik via den delte builder → samme CSV-kolonner som K2
        # (exit_indicators_*, exit_tape_*, exit_trade_metrics_*). FAIL-SAFE.
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
                variant_name = self.name,
            )
            snap["reversion"] = {"exit_z": round(z, 4) if z is not None else None}
            if self._journal:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "trade_forensics",
                    symbol     = sym,
                    payload    = snap,
                )
        except Exception as e:
            logger.warning(f"[Europa-reversion] exit-forensik fejlede for {sym}: {e}")

        # Broadcast (LiveAlgo: "sell" = long-luk, "buy_cover" = short-luk)
        if self._broadcast_fn:
            await self._broadcast_async({
                "type":     "algo_trade",
                "strategy": self.name,
                "action":   "sell" if side == "long" else "buy_cover",
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

        FASE 2 — feed-genoprettelses-vindue (mod IBKR data-farm-drop ved sessions-slut):
        IBKR's daglige farm-drop forsinker simulerede ordrer i typisk minutter. I stedet
        for at lade futures hænge åbne natten over til næste sessions reconcile, bliver vi
        ved — FEED-GATED — i op til LATE_CLOSE_MAX_MIN: vi forsøger kun en luk når der er en
        kurs (snapshot), og flader ud så snart feedet er tilbage. Kun self._positions
        (vores), via _close (popper + bogfører kun ved bekræftet fyldning), afbrydelig
        (status-tjek), tidsbegrænset.

        Hvad der STADIG ikke kan lukkes (feed nede hele vinduet) forbliver åbent + åbent-i-
        journal; opstarts-reconcile (_reconcile_orphans_impl, UÆNDRET) fanger det næste
        session som sidste net."""
        # ── Fase 1: hurtige genforsøg ──
        for attempt in range(1, FORCE_CLOSE_MAX_ATTEMPTS + 1):
            if not self._positions:
                break
            for sym in list(self._positions.keys()):
                pos = self._positions[sym]
                snap = await self.conn.get_snapshot(sym)
                price = (snap.get("last") if snap else None) or pos["entry_price"]
                # Giv seneste kendte z videre (sat i _evaluate_bar), så session_end-luk
                # også får exit_z. Var None før → tp_exit_z tom i CSV'en.
                await self._close(sym, price, reason, pos.get("last_z"))
            if self._positions and attempt < FORCE_CLOSE_MAX_ATTEMPTS:
                logger.warning(f"[Europa-reversion] _close_all: {len(self._positions)} position(er) "
                               f"stadig åben efter forsøg {attempt}/{FORCE_CLOSE_MAX_ATTEMPTS} "
                               f"— genforsøger om {FORCE_CLOSE_RETRY_DELAY}s")
                await asyncio.sleep(FORCE_CLOSE_RETRY_DELAY)

        # ── Fase 2: feed-gated genoprettelses-luk ──
        if self._positions:
            deadline = datetime.now(ET) + timedelta(minutes=LATE_CLOSE_MAX_MIN)
            feed_down_logged = False
            while (self._positions and datetime.now(ET) < deadline
                   and self.status == StrategyStatus.RUNNING):
                for sym in list(self._positions.keys()):
                    pos  = self._positions[sym]
                    snap = await self.conn.get_snapshot(sym)
                    last = snap.get("last") if snap else None
                    if not last:
                        # Feed nede: en luk kan ikke fylde uden kurs. Log årsagen ÉN gang
                        # tydeligt i live-loggen — undgå at spamme "ikke bekræftet fyldt".
                        if not feed_down_logged:
                            await self._log(
                                f"⚠ Datafeed nede (ingen kurs) — kan ikke lukke "
                                f"{', '.join(self._positions)}. Venter på genoprettelse, "
                                f"genforsøger hvert {FORCE_CLOSE_RETRY_DELAY}s i op til "
                                f"{LATE_CLOSE_MAX_MIN} min.", level="warning")
                            self._status("trading",
                                f"⚠ Datafeed nede — venter på kurs for at lukke "
                                f"{', '.join(self._positions)} ({len(self._positions)} åben)")
                            feed_down_logged = True
                        continue
                    await self._close(sym, last, reason, pos.get("last_z"))
                if self._positions and datetime.now(ET) < deadline:
                    await asyncio.sleep(FORCE_CLOSE_RETRY_DELAY)

            if not self._positions and feed_down_logged:
                await self._log("✅ Datafeed tilbage — hængende positioner fladet ud", level="info")

        # ── Endeligt: stadig åben → opstarts-reconcile (uændret) fanger den ──
        if self._positions:
            still = ", ".join(self._positions.keys())
            logger.error(f"[Europa-reversion] _close_all: kunne IKKE lukke {still} inden for "
                         f"{LATE_CLOSE_MAX_MIN} min (datafeed nede/ufyldt hele vinduet) — "
                         f"forbliver åben (i journal); opstarts-reconcile fanger den næste session")
            self._status("trading",
                         f"⚠ {still}: ikke lukket inden for {LATE_CLOSE_MAX_MIN} min "
                         f"(datafeed nede) — forbliver åben, opstarts-reconcile fanger den næste session")

    # -------------------------------------------------------------
    # Hjælper
    # -------------------------------------------------------------

    # _broadcast_async() er løftet til BaseStrategy (Trin 1).

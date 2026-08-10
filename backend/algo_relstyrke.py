"""
algo_relstyrke.py
─────────────────
Live trading-wrapper for "Relativ Styrke" — tvaersnitlig relativ styrke, long-only.
EN beslutning pr. dag: ved T=09:45 ET ranges dagens scanner-univers efter morgen-relativ-
styrke (early_rs), og der gaas LONG i top-3 (equal weight). Positionerne holdes til EOD
force-close 15:51 ET. INGEN continuous rescan, INGEN news-gate, INGEN target/stop.

Bygger paa en bestaaet OFFLINE-backtest (cross_sectional_rs_backtest.py, spor D). Scoren
early_rs = (pris_ved_T - open_0930) / open_0930, entry = naeste bar's open efter T, exit =
EOD. Denne fil matcher den mekanik 1:1 saa live == backtest. De FIRE plateau-parametre
(T=09:45, K=3, score=early_rs, exit=eod) er FROSNE — aendres ALDRIG uden ny backtest.

VIGTIGT (edge-natur): strategien er LONG-only, saa dens raa P&L indeholder markeds- og
small-cap-beta. Selve edgen er SELECTION ALPHA = (snit top-K) - (snit hele universet), som
IKKE maales her men i den akkumulerede shadow-eval (relstyrke_shadow_eval.py). Live-wrapperen
handler top-K OG emitterer hele tvaersnittet som shadow-event, saa alphaen kan doemmes offline.

Strukturelt spejler den BuyTheDipLive (samme robuste maskineri): scoped reconcile (roerer kun
egne journal-rows, source="Relativ Styrke"), bekraeftet force-close m. genforsoeg, throttlet
status, eksplicitte forensik-hooks. Univers-helperen genbruges fra TrendJoin (fetch_tv_top_
gainers). Bevidste forskelle mod BuyTheDip: (1) EEN beslutning ved T (ingen streaming-entry),
(2) ingen exit-regel foer EOD (holder bare), (3) notional-baseret equal-weight-sizing.

PAPER. MANUEL start (IKKE i scheduleren — auto-starter aldrig). Instans-guard: algoserveren.
Navn/source = "Relativ Styrke" overalt (backend, orderRef, journal, events, Studio-meta).

Placering: C:\\Projects\\trading_dash\\backend\\algo_relstyrke.py
"""

import asyncio
import logging
from datetime import datetime, time as dtime
from typing import Optional

import pytz

from strategy_base import (
    BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus, ENTRY_FILL_WAIT_SEC)
from ibkr_connect import IBKRConnection
from reconcile_idempotency import (
    RECONCILE_CLOSING, reconcile_close_ref, decide_confirmation, WAIT, FLATTEN, RETRY)
from strategies.base import Bar
from trade_forensics import (build_entry_snapshot, build_exit_snapshot,
                             bars_to_chart_payload, append_stop_point)

logger = logging.getLogger(__name__)

# ── Tidszone + session ─────────────────────────────────────────
ET             = pytz.timezone("America/New_York")
SESSION_START  = dtime(9, 30)     # US RTH-aabning (open_0930 laases her)

# ── FROSNE plateau-parametre (aendres ALDRIG uden ny backtest) ──
SOURCE            = "Relativ Styrke"
DECISION_ET       = dtime(9, 45)  # beslutningstid T — score-cutoff: early_rs bruger bars <= 09:45
# BAR-PARITET (jf. relstyrke_parity.py KOERSEL 1+2): IBKR stempler en bar paa dens AABNINGSTID,
# saa 09:45-baren er foerst KOMPLET kl. 09:46:00. Backtestens price_T = 09:45-barens close. Fyrede
# vi ved 09:45:xx var 09:45-baren ikke lukket, og live vil se 09:44-close -> anden top-3 end backtesten
# (divergerede paa 16/43 dage). Vi fyrer derfor beslutningen naar 09:45-baren er komplet (09:46), saa
# _early_rs (filter <= 09:45) faar 09:45-close == backtesten. AENDRER IKKE T/K/score/exit — kun bar-timing.
DECISION_FIRE_ET  = dtime(9, 46)  # fyrings-tid: efter 09:45-baren er lukket (== backtest-paritet)
FORCE_CLOSE_ET    = dtime(15, 30) # tvangsluk — 30 min foer 16:00-lukningen
# ⚠ RYKKET 15:51 -> 15:30 den 3/8-2026 (Soeren, bevidst valg) — og det BRYDER
# PARITETEN MED BACKTESTEN. cross_sectional_rs_backtest.EOD_FORCE er dtime(15, 51),
# altsaa praecis det live koerte foer. "exit=eod" er én af de FIRE frosne
# plateau-parametre (T=09:45, K=3, score=early_rs, exit=eod), og "eod" betyder
# 15:51 her. Vi exit'er nu 21 minutter tidligere end det der bestod den
# praeregistrerede test.
#
# Det er IKKE overfitting — der er ikke sweepet mod et bedre tal; det er en
# operationel beslutning om at alle US-strategier skal vaere ude en halv time foer
# lukningen. Men konsekvensen skal staa her: selection alpha maalt live efter
# 3/8-2026 kan ikke sammenlignes 1:1 med backtestens tal, og shadow-eval
# (der bruger EOD_FORCE=15:51) maaler nu en anden exit end den vi handler.
# Vil man have paritet tilbage, er det denne linje der skal til 15:51.
TOP_K             = 3
SCORE             = "early_rs"     # (pris_ved_T - open_0930) / open_0930

# ── Sizing ─────────────────────────────────────────────────────
STRATEGY_NOTIONAL_USD     = None     # udledes = NOTIONAL_PCT * NLV ved on_start
NOTIONAL_PCT              = 0.03     # 3% af NLV fordeles paa K=3 (~1% pr. navn) — fallback-default
STRATEGY_NOTIONAL_FB      = 500.0    # fallback hvis NLV ikke kan laeses (~3% af ~$16k)

# ── Univers ────────────────────────────────────────────────────
# BRAGT I OVERENSSTEMMELSE MED DEN VALIDEREDE POPULATION 3/8-2026.
#
# Indtil nu hentede live sit univers via fetch_tv_top_gainers ($3-500,
# NYSE/NASDAQ/AMEX). Det afveg fra backtesten paa TO maader, og begge betyder
# noget for en TVAERSNITLIG strategi:
#
#   1. SPREDNINGEN VAR FIRE GANGE SAA STOR. Maalt paa early_rs, median pr. dag:
#          live (11 dage, jul-2026):  spaend 33,80 %   std 5,85 %   IQR 4,33 %
#          backtest (43 dage, mar-maj): spaend  8,51 % std 1,92 %   IQR 2,28 %
#      Live blandede $2,41-navne (NCRA) med $195-navne (MANH). Relationen
#      "morgen-styrke forudsiger dags-styrke" er ikke skalauafhaengig: i midcap-
#      universet er +2 % kl. 09:45 et signal, i live-universet er dagens topnavn
#      typisk +14 % — og det er ofte et nyhedsspring der falder tilbage.
#
#   2. LIVE FORFILTREREDE PAA MOMENTUM. fetch_tv_top_gainers vaelger dagens
#      stoerste STIGNINGER. Backtestens univers blev valgt paa pris/likviditet og
#      rangeret paa en VOLATILITETS-proxy — aldrig paa dagsaendring. At forfiltrere
#      paa momentum skaerer i netop den rangordning early_rs skal maale.
#
# Nu bruges den delte volatilitets-screener (samme som K2/BuyTheDip), rangeret paa
# Volatility.M — ikke paa change. Market cap-baandet er small+mid (300 mio.-10 mia.),
# hvilket er den validerede midcap-population udvidet nedad med small cap (Soerens
# valg 3/8). Prisbaandet foelger rekonstruktionen ($5-50).
#
# ⚠ perf_w_min sendes BEVIDST IKKE. K2 og BuyTheDip bruger det som momentum-gate,
#   men her ville det vaere selvmord: early_rs ER rangeringen. Forfiltrering paa
#   samme akse som scoren afkorter tvaersnittet og oedelaegger selection alpha.
#   Samme grund til at REQUIRE_ALL_GREEN altid har vaeret False.
#
# Dette er IKKE tuning. Der er ikke sweepet noget, og ingen parameter er valgt ud
# fra et resultat — sporets praeregistrering forbyder udtrykkeligt nye sweeps.
# Aendringen FJERNER en utestet afvigelse, saa det vi koerer er det vi validerede.
# relstyrke_shadow_eval maaler fortsat selection alpha 1:1 med backtestens
# definition og er dermed dommeren.
UNIVERSE_TOP_N        = 25
UNIVERSE_PRICE_MIN    = 5.0
UNIVERSE_PRICE_MAX    = 50.0
UNIVERSE_MKT_CAP_MIN  = 300_000_000        # small cap-gulv
UNIVERSE_MKT_CAP_MAX  = 10_000_000_000     # mid cap-loft
UNIVERSE_MIN_VOLUME   = 500_000
UNIVERSE_EXCHANGES    = ["NASDAQ", "NYSE"]
UNIVERSE_TYPES        = ["stock"]          # ingen depotbeviser
UNIVERSE_ORDER_BY     = "Volatility.M"     # IKKE "change" — se punkt 2 ovenfor
REQUIRE_ALL_GREEN     = False
SCAN_TIMEOUT_SEC      = 15

# ── Score-bars (1-min RTH, look-ahead-ren: kun bars <= T) ──────
SCORE_BAR_DURATION = "3600 S"
SCORE_BAR_SIZE     = "1 min"

# ── Operationelt (spejler BuyTheDip) ────────────────────────────
LOOP_SLEEP_SECONDS       = 15
HEARTBEAT_INTERVAL_SEC   = 300
CLOSE_FILL_WAIT_SEC      = 8
FORCE_CLOSE_MAX_ATTEMPTS = 4
FORCE_CLOSE_RETRY_DELAY  = 4
RECONCILE_TIMEOUT_SEC    = 30
HOLD_BAR_DURATION        = "7200 S"   # ~2t 1m-bars pr. fetch (til hold-forensik MFE/MAE)
HOLD_BAR_SIZE            = "1 min"


class RelStyrkeLive(BaseStrategy):
    """Live-wrapper for Relativ Styrke (tvaersnitlig RS, long-only, EEN beslutning/dag, paper)."""

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn
        # Beslutningslogik bor i denne fil (ingen pakke-strategi).
        self._strategy = None

        # Positioner: {side, entry_price, shares, entry_time, trade_id, early_rs, rank, stop_traj}.
        self._positions: dict[str, dict] = {}
        # Tickers der har taget (eller forsoegt) dagens ene entry -> ingen genentry.
        self._done_today: set[str] = set()

        self._nlv: float = 0.0
        self._strategy_notional: float = 0.0
        self._decided: bool = False              # dagens ene beslutning taget?

        # Diagnostik (Lag C)
        self._diag_scored  = 0
        self._diag_entries = 0

    # -------------------------------------------------------------
    # BaseStrategy interface
    # -------------------------------------------------------------
    @property
    def name(self) -> str:
        return SOURCE

    @property
    def description(self) -> str:
        # Studio-meta: eksplicit om edgens natur (long-only -> beta i P&L; edge = selection alpha).
        return ("Tvaersnitlig relativ styrke, long-only: ranger dagens scanner-univers efter "
                "morgen-RS (early_rs) ved 09:45 ET, gaa LONG top-3, hold til EOD. Long-only, saa "
                "raa P&L indeholder markeds-/small-cap-beta; edgen er SELECTION ALPHA (top-K minus "
                "universe-snit), maalt i shadow-eval. Paper.")

    @property
    def asset_class(self) -> str:
        return "equity"

    # -------------------------------------------------------------
    # Pre-flight
    # -------------------------------------------------------------
    async def pre_flight(self) -> tuple[bool, str]:
        # Instans-note (IKKE hard gate): kanonisk hjem er algoserveren (delt paper-konto,
        # eneste sted univers-beviset akkumuleres konsistent). Men strategien er MANUEL-start
        # kun (aldrig i scheduleren), saa parallel-koersel kraever to bevidste menneske-klik —
        # og BuyTheDip (skabelonen) har heller ingen intern guard. Vi BLOKERER derfor ikke
        # (saa Soeren kan smoke-teste paa sin workstation mod egen paper-konto); vi ADVARER blot.
        try:
            from accounts import identity
            if identity.instance_role != "algoserver":
                await self._log(
                    f"⚠ Relativ Styrke startet paa '{identity.instance_role}' (ikke algoserveren). "
                    f"Kanonisk koersel + univers-bevis hoerer til algoserveren; start IKKE parallelt "
                    f"paa to maskiner mod samme konto.", level="warning")
        except Exception as e:
            logger.warning(f"[RelStyrke] instans-note kunne ikke verificeres: {e}")

        ok, err, checks = await self._preflight_connection_and_account()
        if not ok:
            return False, err
        # cache NLV til notional-sizing
        try:
            acct = self.conn.get_account_summary()
            self._nlv = float(acct.get("net_liquidation", 0) or 0)
        except Exception:
            self._nlv = 0.0

        self._status("started", "Pre-flight: Tester datafeed (AAPL)...")
        test_bars = await self.conn.get_historical_bars(
            "AAPL", duration="1 D", bar_size="1 min", what_to_show="TRADES")
        if not test_bars:
            test_bars = await self.conn.get_historical_bars(
                "AAPL", duration="1 D", bar_size="1 min", what_to_show="MIDPOINT")
        if not test_bars:
            return False, "Kan ikke hente markedsdata"
        checks.append(f"Datafeed virker — {len(test_bars)} bars")

        summary = " | ".join([f"OK {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        return True, summary

    # -------------------------------------------------------------
    # Start / Stop
    # -------------------------------------------------------------
    async def on_start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            logger.warning("[RelStyrke] _trading_loop koerer allerede — afbryder ny start")
            return
        self._status("started", "Algoritme starter — Relativ Styrke (tvaersnitlig RS, long-only)")
        try:
            await asyncio.wait_for(self._reconcile_orphans(), timeout=RECONCILE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error(f"[RelStyrke] reconcile-timeout ({RECONCILE_TIMEOUT_SEC}s) — springer over")
            self._status("started", "Reconciliation timeout — fortsaetter til handel")

        # position_size = notional PR. NAVN (samme betydning som K2's "kapital/handel"), saa en
        # Relativ Styrke-position er lige saa stor som en K2-position. Samlet = pr-navn x TOP_K.
        # Konfigurerbart via Konfiguratoren; fald tilbage til NOTIONAL_PCT*NLV/K hvis uoploest.
        per_name = self._resolve_risk("position_size")
        if per_name <= 0:
            per_name = ((NOTIONAL_PCT * self._nlv) if self._nlv > 0 else STRATEGY_NOTIONAL_FB) / TOP_K
        self._strategy_notional = per_name * TOP_K
        self._status("started",
                     f"Strategi-notional: ${self._strategy_notional:,.0f} "
                     f"({'3% af NLV' if self._nlv > 0 else 'fallback (NLV ukendt)'}) "
                     f"→ ~${self._strategy_notional / TOP_K:,.0f} pr. navn")

        # Nulstil dagens state + diagnostik.
        self._positions.clear(); self._done_today.clear()
        self.universe = []
        self._decided = False
        self._diag_scored = self._diag_entries = 0
        self.reset_diagnostics()
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def on_bar(self, ticker: str, bar: dict) -> None:
        pass  # vi poller selv i _trading_loop

    async def on_stop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self._positions:
            await self._close_all("strategi stoppet")

    # -------------------------------------------------------------
    # Scoped reconcile (spejler BuyTheDip — long-only, source=self.name)
    # -------------------------------------------------------------
    async def _reconcile_orphans(self) -> None:
        """Best-effort wrapper: ENHVER fejl i reconcile fanges og blokerer ALDRIG
        handelsstarten. Selve logikken bor i _reconcile_orphans_impl."""
        try:
            await self._reconcile_orphans_impl()
        except Exception as e:
            logger.exception(f"[RelStyrke] reconcile fejlede (best-effort, ignoreret): {e}")
            self._status("started", "Reconciliation sprang fejlet over — fortsaetter til handel")

    async def _reconcile_orphans_impl(self) -> None:
        """Ryd KUN Relativ Styrkes egne spoegelser (source=self.name). Aldrig blind-flatten —
        delt konto. Ved opstart er self._positions tom, saa enhver aaben Relativ Styrke-row er
        et levn. IBKR samme vej & |net|>=antal → luk vores andel (reconcile_flatten);
        IBKR flad → fantom (reconcile_phantom, ingen ordre); ellers observe-only."""
        if self.conn is None or not self.conn.connected:
            self._status("started", "Reconciliation sprunget over — IBKR ikke forbundet")
            return
        open_rows = []
        try:
            from trade_queries import list_trades
            db = getattr(self._journal, "_db", None) if self._journal else None
            if db is not None:
                open_rows = await list_trades(db, status="open", source=self.name)
        except Exception as e:
            logger.error(f"[RelStyrke] reconciliation: list_trades fejlede: {e}")
            return
        # Reliable live-read: absence-baseret luk (net==0 -> fantom) maa ALDRIG ske paa et
        # tomt/degraderet feed (kold cache lige efter connect -> aegte position ser "flad"
        # ud -> foraeldreloesgoeres). reliable=False -> spring hele reconcile over.
        ibkr_positions, feed_ok = await self.conn.get_positions_reliable()
        if not feed_ok:
            logger.warning("[RelStyrke] reconciliation: positions-feed upaalideligt "
                           "(tom/timeout ved opstart) — springer over for ikke at "
                           "foraeldreloesgoere en aegte position")
            self._status("started", "Reconciliation sprunget over — positions-feed upaalideligt")
            return
        ibkr_by_ticker = {
            (p.get("ticker") or "").upper(): (p.get("position") or 0)
            for p in ibkr_positions if p.get("position")
        }
        cleaned = 0
        own: set[str] = set()
        for row in open_rows:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            own.add(sym)
            shares = int(abs(row.get("shares") or 0))
            side   = (row.get("side") or "").lower()
            sign   = 1 if side == "long" else -1
            if shares <= 0:
                continue
            net = ibkr_by_ticker.get(sym, 0)

            # Idempotens-gate (lukker Del C): er der ALLEREDE afgivet en reconcile-close for
            # row'en (durabel markoer)? → bekraeftelses-sti, ALDRIG blind gen-placering.
            if (row.get("current_stage") or "") == RECONCILE_CLOSING:
                if await self._reconcile_confirm(sym, row, shares, sign, net):
                    cleaned += 1
                continue

            if net == 0:
                await self._reconcile_mark_closed(sym, row)
                cleaned += 1
                continue
            net_sign = 1 if net > 0 else -1
            if net_sign != sign or abs(net) < shares:
                await self._log(
                    f"🔎 {sym}: RelStyrke-journal siger {side} {shares}, IBKR holder "
                    f"{net:+.0f} — inkonsistent, observe-only (roerer den IKKE)", level="warning")
                continue
            await self._reconcile_close(sym, row, shares, sign)
            cleaned += 1
        for sym, net in ibkr_by_ticker.items():
            if net and sym not in own:
                await self._log(
                    f"🔎 {sym} ({net:+.0f}) holdes i IBKR uden RelStyrke-journal-spor — "
                    f"observe-only (anden strategi eller manuel handel)", level="warning")
        self._status("started",
                     f"Reconciliation: ryddede {cleaned} gammel/gamle RelStyrke-position(er) op"
                     if cleaned else "Reconciliation: ingen RelStyrke-spoegelser at rydde op")

    async def _reconcile_close(self, sym: str, row: dict, shares: int, sign: int) -> None:
        close_action = "SELL" if sign > 0 else "BUY"
        entry = row.get("entry_price") or 0.0
        snap  = await self.conn.get_snapshot(sym)
        exit_price = (snap.get("last") if snap else None) or entry or 0.0
        # Undgaa DUPLIKAT-lukordre: hviler der allerede en aktiv {close_action}-ordre paa
        # symbolet, saa lad DEN lukke positionen — laeg ikke endnu en. Row forbliver aaben;
        # naeste reconcile bekraefter naar den eksisterende er fyldt.
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
                logger.error(f"[RelStyrke] kunne ikke saette reconcile_closing-markoer ({sym}): {e}")

        result = await self.conn.place_paper_order(
            sym, close_action, shares, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC, order_ref=_ref,
        )
        if not result:
            await self._log(f"⚠ Kunne ikke lukke gammel position {sym} — luk manuelt i TWS",
                            level="warning")
            return
        filled = result.get("filled") or 0
        if filled < shares:
            await self._log(
                f"⚠ {sym}: reconcile-lukning IKKE bekraeftet fyldt "
                f"(status={result.get('status')}, filled={filled}/{shares}) "
                f"— journal-row forbliver aaben (markedet kan vaere lukket). Luk evt. manuelt i TWS.",
                level="warning")
            return
        fill = result.get("avg_fill")
        if fill and fill > 0:
            exit_price = fill
        pnl = (exit_price - entry) * shares * sign if (entry and exit_price) else 0.0
        trade_id = row.get("trade_id")
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id=trade_id, exit_price=exit_price, exit_time=datetime.now(ET),
                exit_reason="reconcile_flatten", pnl=pnl, payload={"reconcile": True})
        await self._log(f"♻ Gammel aaben RelStyrke-position {sym} (long {shares}) lukket "
                        f"@ ${exit_price:.2f} (reconcile) | P&L: ${pnl:+.2f}")

    async def _reconcile_mark_closed(self, sym: str, row: dict) -> None:
        trade_id = row.get("trade_id")
        entry = row.get("entry_price") or 0.0
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id=trade_id, exit_price=entry, exit_time=datetime.now(ET),
                exit_reason="reconcile_phantom", pnl=0.0,
                payload={"reconcile": True, "phantom": True})
        await self._log(f"🧹 {sym}: RelStyrke-journal stod aaben men IBKR er flad — bogfoert "
                        f"lukket (fantom, nul-P&L), ingen ordre sendt", level="warning")

    async def _reconcile_confirm(self, sym: str, row: dict, shares: int, sign: int,
                                 net) -> bool:
        """Bekraeftelses-sti for en row der ALLEREDE er i reconcile_closing (delt, ren
        beslutning via decide_confirmation). Placerer ALDRIG paa UKENDT udfald (Del C)."""
        ref = reconcile_close_ref(row.get("trade_id"))
        net_sign = 1 if net > 0 else (-1 if net < 0 else 0)
        position_open = (net != 0 and net_sign == sign and abs(net) >= shares)
        try:
            outcome = await self.conn.get_order_outcome(ref)
        except Exception:
            outcome = "not_findable"
        decision = decide_confirmation(outcome, position_open)
        if decision == WAIT:
            await self._log(f"⏸ {sym}: reconcile-close ({ref}) hviler stadig (udfald={outcome}) — afventer",
                            level="warning")
            return False
        if decision == FLATTEN:
            await self._reconcile_mark_filled(sym, row, outcome)
            return True
        if decision == RETRY:
            await self._log(f"↻ {sym}: reconcile-close bekraeftet doed-ufyldt (udfald={outcome}), "
                            f"position stadig aaben — genafgiver", level="warning")
            await self._reconcile_close(sym, row, shares, sign)
            return False
        await self._log(f"🔎 {sym}: reconcile-close ({ref}) ubekraeftet og ikke synlig; position ser "
                        f"stadig aaben ud (net {net:+.0f}) — observe-only, afventer", level="warning")
        return False

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

    # -------------------------------------------------------------
    # Univers — genbruger TrendJoins TV-gainer-helper (scannerens live-univers)
    # -------------------------------------------------------------
    async def _scan_universe(self) -> list[str]:
        """Dagens univers via den DELTE volatilitets-screener (samme som K2/BuyTheDip),
        rangeret paa Volatility.M — ikke paa dagsaendring.

        Skiftet 3/8-2026 fra fetch_tv_top_gainers. Se univers-blokken oeverst: den
        gamle kilde forfiltrerede paa momentum (dagens stoerste stigninger), hvilket
        skaerer i netop den rangordning early_rs skal maale, og gav et univers med
        fire gange backtestens spredning. Returnerer symboler (upper, dedup);
        tom liste ved timeout/fejl.
        """
        from strategies.shared.tv_scanner import build_volatility_universe_rows
        try:
            _pool, rows = await build_volatility_universe_rows(
                top_n       = UNIVERSE_TOP_N,
                price_min   = UNIVERSE_PRICE_MIN,
                price_max   = UNIVERSE_PRICE_MAX,
                mkt_cap_min = UNIVERSE_MKT_CAP_MIN,
                mkt_cap_max = UNIVERSE_MKT_CAP_MAX,
                min_avg_vol = UNIVERSE_MIN_VOLUME,
                # perf_w_min sendes BEVIDST IKKE — se univers-blokken.
                types       = UNIVERSE_TYPES,
                order_by    = UNIVERSE_ORDER_BY,
                exchanges   = UNIVERSE_EXCHANGES,
                timeout     = SCAN_TIMEOUT_SEC,
                log_tag     = "RelStyrke",
            )
        except asyncio.TimeoutError:
            logger.error("[RelStyrke] TV volatilitets-scan timeout")
            return []
        except Exception as e:
            logger.error(f"[RelStyrke] TV volatilitets-scan fejl: {e}")
            return []
        return list(dict.fromkeys(
            (r.get("symbol") or "").upper() for r in (rows or []) if r.get("symbol")))

    # -------------------------------------------------------------
    # Trading-loop — EEN beslutning ved T; ellers hold til EOD
    # -------------------------------------------------------------
    async def _trading_loop(self):
        self._status("trading", "Overvaager markedet — venter paa beslutningstid 09:45 ET...")
        consecutive_errors = 0
        _last_heartbeat = datetime.now(ET)
        _prefetched = False
        try:
            while self.loop_skal_koere():
                now_et = datetime.now(ET)
                t = now_et.time()

                if t < SESSION_START:
                    self._status("orb_ready",
                                 f"Venter paa aabning {SESSION_START.strftime('%H:%M')} ET",
                                 persist=False)
                    await asyncio.sleep(LOOP_SLEEP_SECONDS)
                    continue

                if t >= FORCE_CLOSE_ET:
                    if self._positions:
                        self._status("trading", "Markedet lukker — lukker alle positioner")
                        await self._close_all(f"force_close {FORCE_CLOSE_ET.strftime('%H:%M')}")
                    wins   = sum(1 for tr in self.trades if tr["pnl"] > 0)
                    losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                    self._status("done",
                                 f"✅ Handelsdagen afsluttet | P&L: ${self.total_pnl:+,.2f} | "
                                 f"{len(self.trades)} handler ({wins}W/{losses}L)")
                    await self._log_self_stop("Sessions-slut: force-close",
                                              StrategyStatus.STOPPED, "done")
                    break

                try:
                    # Forud-hent universet EEN gang FOER fyring (maa gerne — ingen handling foer T).
                    if not _prefetched and not self._decided and t < DECISION_FIRE_ET:
                        self.universe = await self._scan_universe()
                        _prefetched = True
                        if self.universe:
                            self._status("scanning",
                                         f"Forud-hentede univers ({len(self.universe)}) — "
                                         f"afventer beslutning T={DECISION_ET.strftime('%H:%M')} "
                                         f"(fyrer {DECISION_FIRE_ET.strftime('%H:%M')} naar T-baren er komplet)")

                    # Dagens ene beslutning: fyr naar 09:45-baren er KOMPLET (09:46), saa early_rs
                    # (bars <= 09:45) faar 09:45-close == backtestens price_T (bar-paritet).
                    if not self._decided and t >= DECISION_FIRE_ET:
                        await self._make_decision(now_et)
                        self._decided = True
                    elif self._decided and self._positions:
                        # Holder til EOD: opdater bar-historik til hold-forensik (MFE/MAE).
                        await self._update_holdings()

                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    logger.exception(f"[RelStyrke] fejl i handels-loop: {e}")
                    if consecutive_errors >= 3:
                        self._status("trading", f"⚠ {consecutive_errors} fejl — genforbinder...")
                        if await self._reconnect():
                            consecutive_errors = 0
                            self._status("trading", "✅ Genforbundet — fortsaetter")
                        else:
                            await self._log_self_stop("IBKR-forbindelse tabt",
                                                      StrategyStatus.ERROR, "error")
                            break

                self._status("trading",
                             f"{('Holder ' + str(self.stats.open_positions) + ' position(er)') if self._decided else 'Afventer beslutning'} — "
                             f"{now_et.strftime('%H:%M:%S')} ET | "
                             f"Pos: {self.stats.open_positions}/{self.config.max_open_positions}",
                             persist=False)

                if (now_et - _last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL_SEC:
                    _last_heartbeat = now_et
                    await self.log_heartbeat({
                        "scored":         self._diag_scored,
                        "entries":        self._diag_entries,
                        "open_positions": self.stats.open_positions,
                        "universe_size":  len(self.universe),
                        "decided":        self._decided,
                    })

                await asyncio.sleep(LOOP_SLEEP_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[RelStyrke] _trading_loop crashede")
            await self._log_self_stop(
                f"Fejl i strategi-loop: {type(e).__name__}: {str(e)[:80]}",
                StrategyStatus.ERROR, "error")

    # -------------------------------------------------------------
    # Beslutning ved T — EET snapshot: score, rang, vaelg top-K, entr
    # -------------------------------------------------------------
    async def _make_decision(self, now_et: datetime) -> None:
        """Dagens ene beslutning: hent scanner-universet, udregn early_rs pr. navn (kun bars
        <= T), ranger tvaersnitligt, vaelg top-K og gaa LONG. Emitterer HELE tvaersnittet som
        shadow-event (selection-alpha-beviset), FOER entries."""
        if not self.universe:
            self.universe = await self._scan_universe()
        today = now_et.date()

        # ── Score hvert navn (look-ahead-ren): early_rs = (c_T - open_0930)/open_0930 ──
        scored: list[tuple] = []   # (early_rs, sym, open_0930, price_T)
        for sym in list(self.universe):
            if self.status != StrategyStatus.RUNNING:
                break
            bars = await self._fetch_bars(sym, duration=SCORE_BAR_DURATION, bar_size=SCORE_BAR_SIZE)
            res = self._early_rs(bars, today)
            if res is None:
                await self.log_rejection_change(sym, "ingen brugbare RTH-bars <= T (early_rs udefineret)")
                continue
            rs, open_0930, price_T = res
            scored.append((rs, sym, open_0930, price_T))
            self._bar_history[sym] = bars   # til hold-/exit-forensik
            self._diag_scored += 1
            await self.log_bar_evaluation(
                ticker=sym, bar_time_et=now_et.strftime("%Y-%m-%d %H:%M:%S"),
                status="scanning", score=int(round(rs * 100)))

        # ── Log dagens univers (Lag A) ──
        await self.log_universe(self.universe, meta={
            "scored": len(scored), "score": SCORE, "decision_et": DECISION_ET.strftime("%H:%M"),
            "source": "tv_intraday_volatility", "top_k": TOP_K,
            "order_by": UNIVERSE_ORDER_BY,
            "price_min": UNIVERSE_PRICE_MIN, "price_max": UNIVERSE_PRICE_MAX,
            "mkt_cap_min": UNIVERSE_MKT_CAP_MIN, "mkt_cap_max": UNIVERSE_MKT_CAP_MAX})

        if len(scored) < 2:
            self._status("orb_ready",
                         f"Kun {len(scored)} navn(e) med brugbar early_rs — Relativ Styrke "
                         f"handler ikke i dag (kraever >= 2 for tvaersnit)")
            await self._log_shadow_snapshot([], [], now_et)
            return

        # ── Tvaersnitlig rangering (percentil i dagens univers) ──
        scored.sort(key=lambda x: -x[0])
        n = len(scored)
        ranked: list[dict] = []
        rank_of: dict[str, int] = {}
        for i, (rs, sym, open_0930, price_T) in enumerate(scored):
            rank = i + 1
            pct = 1.0 if n == 1 else 1.0 - (rank - 1) / (n - 1)
            rank_of[sym] = rank
            ranked.append({
                "symbol": sym, "early_rs": round(rs, 6), "rank": rank,
                "percentile": round(pct, 4), "open_0930": round(open_0930, 4),
                "price_at_T": round(price_T, 4)})

        selected = [sym for _rs, sym, _o, _p in scored[:TOP_K]]

        # ── Emit shadow-snapshot (hele tvaersnittet + valgte top-K) FOER entries ──
        await self._log_shadow_snapshot(ranked, selected, now_et)

        self._status("universe_ready",
                     f"📋 Beslutning {DECISION_ET.strftime('%H:%M')} ET — top-{TOP_K} af {n}: "
                     f"{', '.join(selected)}")

        # ── Entr LONG top-K equal weight paa naeste bar's open (markedsordre nu) ──
        for rs, sym, open_0930, price_T in scored[:TOP_K]:
            if self.stats.open_positions >= self.config.max_open_positions:
                break
            await self._open(sym, rs, rank_of.get(sym, 0), price_T)

    def _early_rs(self, bars: list[Bar], today) -> Optional[tuple]:
        """early_rs = (pris_ved_T - open_0930)/open_0930 fra 1-min RTH-bars. Look-ahead-ren:
        KUN bars <= DECISION_ET (assert som i backtesten). None hvis < 2 brugbare bars."""
        upto = [b for b in bars
                if b.timestamp.date() == today and b.timestamp.time() <= DECISION_ET]
        upto.sort(key=lambda b: b.timestamp)
        if len(upto) < 2:
            return None
        assert max(b.timestamp.time() for b in upto) <= DECISION_ET, "look-ahead: bar efter T"
        open_0930 = upto[0].open   # foerste RTH-bar (09:30) open
        if open_0930 <= 0:
            return None
        price_T = upto[-1].close   # sidste faerdige bar close <= T
        return (price_T - open_0930) / open_0930, open_0930, price_T

    # -------------------------------------------------------------
    # Open — notional-baseret equal-weight sizing (ingen stop/target)
    # -------------------------------------------------------------
    async def _open(self, ticker: str, early_rs: float, rank: int, ref_price: float) -> None:
        if ticker in self._positions or ticker in self._done_today:
            return
        entry = ref_price   # naeste bar's open ~ pris ved T (markedsordre placeres nu)
        if entry <= 0:
            self._done_today.add(ticker)
            return
        # Equal weight: strategi-notional / K. Intet separat notional-loft — position_size
        # styrer den faktiske position (pr-navn = position_size/TOP_K er selv-begraenset).
        per_name = self._strategy_notional / TOP_K
        shares = int(per_name / entry)
        if shares < 1:
            self._done_today.add(ticker)
            return

        order = OrderRequest(
            strategy_name=self.name, ticker=ticker, action="BUY", quantity=shares,
            order_type="MKT", asset_class="equity",
            reason=f"RelStyrke early_rs {early_rs*100:+.2f}% rank {rank}")
        if self._risk_manager:
            if not await self.request_order(order):
                return
        result = await self.conn.place_paper_order(
            ticker, "BUY", shares, source=self.name,
            await_fill_sec=ENTRY_FILL_WAIT_SEC)
        # Bogfoer KUN hvad IBKR bekraefter fyldt — ellers bygger vi en fantom-position.
        filled_qty = await self._entry_fill_qty(result, ticker, shares)
        if filled_qty <= 0:
            return
        shares = int(filled_qty)
        fill = result.get("avg_fill")
        if fill and fill > 0:
            entry = fill
        entry_time = datetime.now(ET)

        # OrdersTracker
        try:
            from orders_tracker import get_tracker
            oid = result.get("order_id")
            if oid:
                get_tracker().record_placed(order_id=oid, source=self.name, ticker=ticker,
                                            action="BUY", shares=shares, order_type="MKT")
        except Exception as e:
            logger.warning(f"[RelStyrke] tracker-registrering fejlede: {e}")

        trade_id = None
        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source=self.name, symbol=ticker, side="long", shares=shares,
                entry_price=entry, entry_time=entry_time,
                entry_reason=f"RelStyrke top-{TOP_K} (early_rs {early_rs*100:+.2f}%, rank {rank})",
                current_stop=None, current_target=None, current_stage="held",
                payload={"early_rs": round(early_rs, 6), "rank": rank,
                         "price_at_T": round(ref_price, 4)})

        # Stop/target-trajektorie: ingen stop/target i baseline -> eet punkt uden niveauer.
        stop_traj: list = []
        append_stop_point(stop_traj, entry_time, None, None)

        self._positions[ticker] = {
            "side": "long", "entry_price": entry, "shares": shares,
            "entry_time": entry_time, "trade_id": trade_id,
            "early_rs": early_rs, "rank": rank, "stop_traj": stop_traj}
        self.stats.open_positions = len(self._positions)
        self._done_today.add(ticker)
        self._mfe[ticker] = entry
        self._mae[ticker] = entry
        self._diag_entries += 1

        # Entry-forensik (samme builders som BuyTheDip/TrendJoin) + relstyrke-blok. FAIL-SAFE.
        try:
            snap = build_entry_snapshot(
                ticker=ticker, entry_price=entry, entry_time=entry_time, shares=shares,
                bars=self._bar_history.get(ticker, []), context={}, tape_buffer=None,
                variant_name=self.name)
            snap["relstyrke"] = {"early_rs": round(early_rs, 6), "rank": rank,
                                 "price_at_T": round(ref_price, 4)}
            if self._journal:
                await self._journal.log_event(source=self.name, event_type="trade_forensics",
                                              symbol=ticker, payload=snap)
        except Exception as e:
            logger.warning(f"[RelStyrke] entry-forensik fejlede for {ticker}: {e}")

        if self._broadcast_fn:
            await self._broadcast_async({
                "type": "algo_trade", "strategy": self.name, "action": "buy",
                "ticker": ticker, "price": entry, "shares": shares,
                "time": entry_time.strftime("%H:%M:%S")})
        await self._log(f"🚀 {ticker}: BUY {shares} @ ${entry:.2f} "
                        f"(early_rs {early_rs*100:+.2f}%, rank {rank}, notional ~${shares*entry:,.0f})")

    # -------------------------------------------------------------
    # Hold — opdater bar-historik (MFE/MAE) mens positionerne holdes til EOD
    # -------------------------------------------------------------
    async def _update_holdings(self) -> None:
        """Ingen exit-beslutning (baseline holder til EOD). Vi henter blot seneste bars for
        aabne navne saa hold-forensikken (MFE/MAE + chart_bars) er komplet ved luk."""
        for ticker in list(self._positions.keys()):
            if self.status != StrategyStatus.RUNNING:
                break
            bars = await self._fetch_bars(ticker, duration=HOLD_BAR_DURATION, bar_size=HOLD_BAR_SIZE)
            if not bars:
                continue
            self._bar_history[ticker] = bars
            new_bar = bars[-1]
            last = self._last_bar_processed.get(ticker)
            if last is not None and new_bar.timestamp <= last:
                continue
            self._last_bar_processed[ticker] = new_bar.timestamp
            if ticker in self._mfe:
                self._mfe[ticker] = max(self._mfe[ticker], new_bar.high)
            if ticker in self._mae:
                self._mae[ticker] = min(self._mae[ticker], new_bar.low)

    # -------------------------------------------------------------
    # Shadow-snapshot — emit hele tvaersnittet (selection-alpha-beviset)
    # -------------------------------------------------------------
    async def _log_shadow_snapshot(self, ranked: list[dict], selected: list[str],
                                   now_et: datetime) -> None:
        """Emit EET 'relstyrke_shadow_candidate'-event pr. dag med HELE scanner-universet
        (hvert navns early_rs + rank + percentil) og de valgte top-K. Laeses offline af
        relstyrke_shadow_eval.py, der efter luk maaler afkast entry->close pr. navn og
        realiseret selection alpha (top-K minus universe-snit). Append-only, best-effort."""
        await self._log(f"👻 shadow-snapshot: {len(ranked)} navne rangeret · "
                        f"top-{TOP_K}: {', '.join(selected) if selected else '(ingen)'}")
        if not self._journal:
            return
        try:
            await self._journal.log_event(
                source=self.name, event_type="relstyrke_shadow_candidate",
                payload={
                    "scan_ts_utc": now_et.timestamp(),
                    "scan_et": now_et.strftime("%H:%M:%S"),
                    "decision_et": DECISION_ET.strftime("%H:%M"),
                    "score": SCORE,
                    "top_k": TOP_K,
                    "n_universe": len(ranked),
                    "selected": selected,
                    "universe": ranked,
                })
        except Exception as e:
            logger.error(f"[RelStyrke] shadow-snapshot-skriv fejlede: {e}")

    # -------------------------------------------------------------
    # Close (long-only, fuld lukning) — spejler BuyTheDip._close
    # -------------------------------------------------------------
    async def _close(self, ticker: str, price: float, reason: str) -> None:
        pos = self._positions.get(ticker)
        if pos is None:
            return
        shares = pos["shares"]
        result = await self.conn.place_paper_order(
            ticker, "SELL", shares, source=self.name, await_fill_sec=CLOSE_FILL_WAIT_SEC)
        if not result:
            await self._log(f"⚠ {ticker}: lukkeordre ikke sendt — position forbliver aaben",
                            level="warning")
            return
        filled = result.get("filled") or 0
        if filled < shares:
            # Spoerg IBKR FOER naeste bar gen-afgiver — ellers saelges en position der
            # allerede er lukket, og residualet bliver en ejerloes short.
            # ⚠ SPOERG ORDREN, IKKE BEHOLDNINGEN. Foer stod her
            # _ibkr_still_holds(), som laeste kontoens NETTOposition. Paa en
            # ticker to strategier deler, kan den ikke skelne "mine aktier er der
            # endnu" fra "den andens" — og svarede derfor "genafgiv" naar den
            # burde have sagt "fyldt". Resultatet var en short paa praecis én
            # andel. Ordrens egen status er et direkte svar. Se strategy_base.
            still = await self._lukkeordre_ufyldt(result, ticker, "long", shares)
            if still is False:
                await self._log(f"ℹ {ticker}: SELL var ubekraeftet, men IBKR er flad — "
                                f"ordren fyldte alligevel. Bogfoerer (gen-afgiver IKKE).",
                                level="warning")
            else:
                _why = result.get("reject_reason")
                await self._log(f"⚠ {ticker}: SELL ikke bekraeftet fyldt "
                                f"(status={result.get('status')}, filled={filled}/{shares}"
                                + (f", IBKR: {_why}" if _why else "") + ")"
                                + (" — position bekraeftet aaben, genforsoeger" if still
                                   else " — positions-feed upaalideligt, gen-afgiver IKKE"),
                                level="warning")
                return
        fill = result.get("avg_fill")
        if fill and fill > 0:
            price = fill

        entry = pos["entry_price"]
        pnl = (price - entry) * shares      # long-only
        self._positions.pop(ticker, None)
        self.stats.open_positions = len(self._positions)
        self.total_pnl += pnl
        self.stats.trades_today += 1
        self.stats.pnl_today    += pnl
        self.stats.last_trade_time = datetime.now(ET).strftime("%H:%M:%S")
        if pnl > 0:
            self.stats.wins_today += 1
        else:
            self.stats.losses_today += 1
        exit_time = datetime.now(ET)
        pnl_pct = ((price - entry) / entry * 100.0) if entry else 0.0

        try:
            from orders_tracker import get_tracker
            oid = result.get("order_id")
            if oid:
                get_tracker().record_placed(order_id=oid, source=self.name, ticker=ticker,
                                            action="SELL", shares=shares, order_type="MKT")
        except Exception as e:
            logger.warning(f"[RelStyrke] tracker (luk) fejlede: {e}")

        trade = {"ticker": ticker, "side": "long", "entry_price": entry, "exit_price": price,
                 "shares": shares, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                 "reason": reason, "entry_time": pos["entry_time"].strftime("%H:%M:%S"),
                 "exit_time": exit_time.strftime("%H:%M:%S")}
        self.trades.append(trade)

        mfe = self._mfe.pop(ticker, None)
        mae = self._mae.pop(ticker, None)
        # OHLCV-oejebliksbillede + stop-trajektorie (ground truth til Handels-charten). FAIL-SAFE.
        try:
            chart_bars = bars_to_chart_payload(self._bar_history.get(ticker, []),
                                               entry_time=pos.get("entry_time"))
        except Exception as e:
            logger.warning(f"[RelStyrke] chart_bars-snapshot fejlede for {ticker}: {e}")
            chart_bars = []
        if pos.get("trade_id") and self._journal:
            await self._journal.log_trade_close(
                trade_id=pos["trade_id"], exit_price=price, exit_time=exit_time,
                exit_reason=reason, pnl=pnl,
                payload={"max_favorable_excursion": round(mfe, 4) if mfe is not None else None,
                         "max_adverse_excursion":   round(mae, 4) if mae is not None else None,
                         "early_rs": round(pos.get("early_rs", 0), 6),
                         "rank": pos.get("rank"),
                         "chart_bars": chart_bars,
                         "stop_trajectory": pos.get("stop_traj", [])})

        try:
            snap = build_exit_snapshot(
                ticker=ticker, entry_price=entry, exit_price=price,
                entry_time=pos["entry_time"], exit_time=exit_time, shares=shares,
                pnl=pnl, reason=reason, bars=self._bar_history.get(ticker, []),
                context={}, tape_buffer=None, variant_name=self.name)
            snap["relstyrke"] = {"reason": reason, "early_rs": round(pos.get("early_rs", 0), 6),
                                 "rank": pos.get("rank")}
            if self._journal:
                await self._journal.log_event(source=self.name, event_type="trade_forensics",
                                              symbol=ticker, payload=snap)
        except Exception as e:
            logger.warning(f"[RelStyrke] exit-forensik fejlede for {ticker}: {e}")

        if self._broadcast_fn:
            await self._broadcast_async({"type": "algo_trade", "strategy": self.name,
                                         "action": "sell", **trade})
        emoji = "✅" if pnl > 0 else "❌"
        await self._log(f"{emoji} {ticker}: lukket @ ${price:.2f} ({reason}) | "
                        f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")

    async def _close_all(self, reason: str):
        """Robust force-close: bekraeftet fyldning + genforsoeg (spejler BuyTheDip/TrendJoin).
        Det der STADIG ikke fyldes bevares aabent → fanges af opstarts-reconcile."""
        for attempt in range(1, FORCE_CLOSE_MAX_ATTEMPTS + 1):
            tickers = list(self._positions.keys())
            if not tickers:
                return
            for ticker in tickers:
                pos = self._positions.get(ticker)
                if pos is None:
                    continue
                snap = await self.conn.get_snapshot(ticker)
                price = (snap.get("last") if snap else None) or pos["entry_price"]
                await self._close(ticker, price, reason)
            if not self._positions:
                return
            if attempt < FORCE_CLOSE_MAX_ATTEMPTS:
                await self._log(f"⚠ {len(self._positions)} position(er) ikke bekraeftet lukket "
                                f"(forsoeg {attempt}/{FORCE_CLOSE_MAX_ATTEMPTS}) — genforsoeger om "
                                f"{FORCE_CLOSE_RETRY_DELAY}s", level="warning")
                await asyncio.sleep(FORCE_CLOSE_RETRY_DELAY)
        if self._positions:
            await self._log(f"⛔ {len(self._positions)} position(er) kunne IKKE bekraeftes lukket "
                            f"efter {FORCE_CLOSE_MAX_ATTEMPTS} forsoeg — bevaret aabne i journalen, "
                            f"ryddes ved naeste opstart (luk dem evt. manuelt i TWS nu)",
                            level="warning")

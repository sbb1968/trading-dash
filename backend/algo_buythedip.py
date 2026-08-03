"""
algo_buythedip.py
─────────────────
Live trading-wrapper for "BuyTheDip" — long-only intradag mean-reversion der køber
dip-bunden efter en impuls og rider bounce'en. K2's komplement: K2 køber
impuls-TOPPEN, BuyTheDip køber DYKKET-bouncen (0/3 tab på K2's tabsdage i validering).

Entry/exit-logikken er den validerede regel fra june_correlation.scan_trade, live-
tilpasset: detektér dip på FÆRDIGE 1-min bars, entr på bounce-barens LUK (lidt
mere konservativt end backtestens open-entry — paper er dommeren).

Strukturelt spejler den Confluence2Live (samme universmotor — EGET TradingView
Intraday-Volatility-scan): scoped reconcile (rører kun egne journal-rows), robust
force-close (bekræftet fyldning + genforsøg), throttlet status, eksplicitte forensik-
hooks. Bevidst forskel: entries evalueres på FÆRDIGE bars (ikke forming bars).
Strategierne er ellers HELT adskilte (eget scan, egen konto, egne parametre) — kun
det globale tabsmax deles.

PAPER-deployment (DUO509856, delt konto). Manuel start. Ikke kapital.

Navn/source = "BuyTheDip" overalt (backend, orderRef, journal, events, Studio-meta).

Placering: C:\\Projects\\trading_dash\\backend\\algo_buythedip.py
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
ET            = pytz.timezone("America/New_York")
SESSION_START = dtime(9, 30)     # US RTH-åbning
OPEN_UNTIL_ET = dtime(10, 30)    # ingen nye entries efter dette (kun åbningen)
FORCE_CLOSE_ET = dtime(15, 55)   # tvangsluk-backstop før 16:00-lukning

# ── Validerede strategi-parametre (june_correlation DEFAULTS) ──
LOOKBACK      = 20
MIN_RUNUP_PCT = 3.0    # impuls: (ref_high−ref_low)/ref_low ≥ dette
DIP_PCT   = 1.5    # dip: bar.low ≤ ref_high·(1−dette/100)
# Target er R-BASERET fra 3/8-2026 (revision 1). Var faste +2,0 % af entry.
#
# Problemet med en fast procent er at stoppet IKKE er fast: det er dip_low raat,
# uden ATR-gulv, saa afstanden svinger vildt. I juli 2026:
#     UMC   stop 0,10 % under entry  ->  risiko 0,10 % for 2,0 %  =  1:20
#     RIVN  stop 3,10 % under entry  ->  risiko 3,10 % for 2,0 %  =  1:0,6
# Med 17 % win rate er RIVN-forholdet matematisk haabloest. Og et mere volatilt
# univers (se ovenfor) goer dykkene dybere — altsaa forholdet endnu skaevere.
#
# target = entry + TARGET_R × R,  hvor R = entry − dip_low.
# Saa foelger gevinstmaalet med stoppet, og forholdet er ENS paa tvaers af
# handler uanset hvor dybt dykket var.
TARGET_R      = 2.0    # target = entry + dette × R

# ── Sizing (DEPLOY-valg — backtesten var %-baseret; TUN på paper) ──
RISK_BUDGET_USD  = 100.0    # risiko (entry−stop) pr. handel
NOTIONAL_CAP_USD = 1000.0   # notional-loft (beskytter mod hale: ~−$116 værst)

# ── Operationelt ───────────────────────────────────────────────
LOOP_SLEEP_SECONDS       = 15
HEARTBEAT_INTERVAL_SEC   = 300
CLOSE_FILL_WAIT_SEC      = 8    # vent på bekræftet fyldning af lukke-ordre
FORCE_CLOSE_MAX_ATTEMPTS = 4
FORCE_CLOSE_RETRY_DELAY  = 4
RECONCILE_TIMEOUT_SEC    = 30   # maks sekunder opstarts-reconcile må tage før den springes over
# ── Univers: BuyTheDip scanner sit EGET univers via TradingViews Intraday
#    Volatility-screener — samme DELTE screener-helper som K2, men HELT adskilt:
#    egne parametre, eget scan, INTET forbrug af K2's publicerede univers. Total
#    strategi-adskillelse (på nær globalt tabsmax). Tuner du her, rører det ikke K2.
#    Værdierne spejler p.t. K2's, men er BuyTheDips egne at justere uafhængigt. ──
# OMLAGT 3/8-2026 (revision 1), samme retning som K2 fik 1/8. Eksekveringen er
# uaendret paa naer target'et (se TARGET_R); det var universet der sultede den.
#
#   market cap   $5B-$1T  ->  $300M-$10B    large cap -> small/mid cap
#   ATR%(1W) >5  fjernet                    erstattet af de to nedenfor
#   Volatility 1M         NY   5-50 %       maanedens udsving
#   Perf 1W               NY   > 6 %        dyk i en OPTREND, ikke i et frit fald
#
# BEGRUNDELSEN er staerkere for BuyTheDip end for K2. Strategien kraever en
# 3 %-bevaegelse inden for 20 minutter — det sker sjaeldent i et $5 mia.-navn, og
# juli viste konsekvensen: 29 handler paa en hel maaned mod backtestens 299.
# Og Perf 1W > 6 % er selve definitionen paa "koeb dykket": et dyk i en optrend.
# Uden det koeber man lige saa gerne et dyk i et frit fald.
#
# Backtesten (washout_reclaim_output, maj 2026, samme regel) peger samme vej:
#   bredt univers     n=1587  WR 57%  snit +0,278%  PF 1,40
#   midcap, volatilt  n= 299  WR 52%  snit +0,378%  PF 1,75
# Faerre men bedre handler. Win rate falder, gennemsnittet pr. handel stiger.
#
# BEMAERK at boerser og type IKKE er aendret her (K2 fik 2 boerser og kun 'stock').
# Soeren bad om de to filter-aendringer, ikke om de oevrige — de staar aabne.
UNIVERSE_TOP_N        = 25
UNIVERSE_PRICE_MIN    = 5.0
UNIVERSE_PRICE_MAX    = 50.0
UNIVERSE_MKT_CAP_MIN  = 300_000_000          # 300 M — small cap-gulvet
UNIVERSE_MKT_CAP_MAX  = 10_000_000_000       # 10 B — mid cap-loftet
UNIVERSE_MIN_VOLUME   = 500_000              # 30-dages gennemsnitsvolumen
UNIVERSE_VOL_M_MIN    = 5.0                  # Volatility 1M, nedre (%)
UNIVERSE_VOL_M_MAX    = 50.0                 # Volatility 1M, oevre (%)
UNIVERSE_PERF_W_MIN   = 6.0                  # Perf 1W > 6 %
UNIVERSE_EXCHANGES    = ["NASDAQ", "NYSE", "AMEX", "CBOE"]  # AMEX = TV's "NYSE Arca"
MIN_UNIVERSE_SIZE     = 3                    # færre end dette → fallback/advarsel
SCAN_TIMEOUT_SEC      = 15                   # TV-screener timeout pr. forsøg
FALLBACK_UNIVERSE: list[str] = []            # tom = ingen handel hvis scan fejler


class BuyTheDipLive(BaseStrategy):
    """Live-wrapper for BuyTheDip (buy-the-dip, long-only, paper)."""

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn
        # Beslutningslogik bor i denne fil (ingen pakke-strategi som K2/EUREVERSION).
        self._strategy = None

        # Per-ticker dip-state: None | {"dip_low", "ref_high"}. Sat når en
        # dip detekteres; ryddes ved bounce-entry (eller ny dag).
        self._dip_state: dict[str, dict] = {}
        # Tickers der allerede har taget (eller forsøgt) dagens ene setup → ingen genentry.
        self._done_today: set[str] = set()
        # Positions-dict pr. ticker: {side, entry_price, shares, stop, target,
        #   entry_time, trade_id, dip_depth, ref_high, dip_low}.
        self._positions: dict[str, dict] = {}

        # Diagnostik (Lag C)
        self._diag_eval_count = 0
        self._diag_setups     = 0
        self._diag_entries    = 0
        self._universe_date: Optional[str] = None

    # -------------------------------------------------------------
    # BaseStrategy interface
    # -------------------------------------------------------------
    @property
    def name(self) -> str:
        return "BuyTheDip"

    @property
    def description(self) -> str:
        return ("Long-only intradag buy-the-dip (køber dykket-bouncen efter en "
                "impuls). K2-komplement. Eget TV-volatility-scan. Paper.")

    @property
    def asset_class(self) -> str:
        return "equity"

    # -------------------------------------------------------------
    # Pre-flight
    # -------------------------------------------------------------
    async def pre_flight(self) -> tuple[bool, str]:
        ok, err, checks = await self._preflight_connection_and_account()
        if not ok:
            return False, err

        self._status("started", "Pre-flight: Tester datafeed (AAPL)...")
        test_bars = await self.conn.get_historical_bars(
            "AAPL", duration="1 D", bar_size="1 min", what_to_show="TRADES")
        if not test_bars:
            test_bars = await self.conn.get_historical_bars(
                "AAPL", duration="1 D", bar_size="1 min", what_to_show="MIDPOINT")
        if not test_bars:
            return False, "Kan ikke hente markedsdata"
        checks.append(f"Datafeed virker — {len(test_bars)} bars")

        summary = " | ".join([f"✅ {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        return True, summary

    # -------------------------------------------------------------
    # Start / Stop
    # -------------------------------------------------------------
    async def on_start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            logger.warning("[BuyTheDip] _trading_loop kører allerede — afbryder ny start")
            return
        self._status("started", "Algoritme starter — BuyTheDip (buy-the-dip)")
        # Best-effort OG tidsbegrænset: hverken fejl eller hang må blokere starten.
        try:
            await asyncio.wait_for(self._reconcile_orphans(), timeout=RECONCILE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error(f"[BuyTheDip] reconcile-timeout ({RECONCILE_TIMEOUT_SEC}s) "
                         f"— springer over, fortsætter til handel")
            self._status("started", "Reconciliation timeout — fortsætter til handel")
        await self._prepare_universe()
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def on_bar(self, ticker: str, bar: dict) -> None:
        pass  # vi poller selv i _trading_loop

    async def on_stop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self._positions:
            await self._close_all("strategi stoppet")

    # -------------------------------------------------------------
    # Scoped reconcile (spejler K2 b78c474 — long-only)
    # -------------------------------------------------------------
    async def _reconcile_orphans(self) -> None:
        """Best-effort wrapper: ENHVER fejl i reconcile fanges og blokerer ALDRIG
        handelsstarten. Selve logikken bor i _reconcile_orphans_impl."""
        try:
            await self._reconcile_orphans_impl()
        except Exception as e:
            logger.exception(f"[BuyTheDip] reconcile fejlede (best-effort, ignoreret): {e}")
            self._status("started", "Reconciliation sprang fejlet over — fortsætter til handel")

    async def _reconcile_orphans_impl(self) -> None:
        """Ryd KUN BuyTheDips egne spøgelser (source=self.name). Aldrig blind-flatten —
        delt konto. Ved opstart er self._positions tom, så enhver åben BuyTheDip-row er
        et levn. IBKR samme vej & |net|≥antal → luk vores andel (reconcile_flatten);
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
            logger.error(f"[BuyTheDip] reconciliation: list_trades fejlede: {e}")
            return
        # Reliable live-read: absence-baseret luk (net==0 -> fantom) må ALDRIG ske på et
        # tomt/degraderet feed (kold cache lige efter connect -> ægte position ser "flad"
        # ud -> forældreløsgøres). reliable=False -> spring hele reconcile over.
        ibkr_positions, feed_ok = await self.conn.get_positions_reliable()
        if not feed_ok:
            logger.warning("[BuyTheDip] reconciliation: positions-feed upålideligt "
                           "(tom/timeout ved opstart) — springer over for ikke at "
                           "forældreløsgøre en ægte position")
            self._status("started", "Reconciliation sprunget over — positions-feed upålideligt")
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
                    f"🔎 {sym}: BuyTheDip-journal siger {side} {shares}, IBKR holder "
                    f"{net:+.0f} — inkonsistent, observe-only (rører den IKKE)", level="warning")
                continue
            await self._reconcile_close(sym, row, shares, sign)
            cleaned += 1
        for sym, net in ibkr_by_ticker.items():
            if net and sym not in own:
                await self._log(
                    f"🔎 {sym} ({net:+.0f}) holdes i IBKR uden BuyTheDip-journal-spor — "
                    f"observe-only (anden strategi eller manuel handel)", level="warning")
        self._status("started",
                     f"Reconciliation: ryddede {cleaned} gammel/gamle BuyTheDip-position(er) op"
                     if cleaned else "Reconciliation: ingen BuyTheDip-spøgelser at rydde op")

    async def _reconcile_close(self, sym: str, row: dict, shares: int, sign: int) -> None:
        close_action = "SELL" if sign > 0 else "BUY"
        entry = row.get("entry_price") or 0.0
        snap  = await self.conn.get_snapshot(sym)
        exit_price = (snap.get("last") if snap else None) or entry or 0.0
        # Undgaa DUPLIKAT-lukordre: hviler der allerede en aktiv {close_action}-ordre paa
        # symbolet (fx en GTC force-close fra en tidligere session der ikke fyldte), saa lad
        # DEN lukke positionen — laeg ikke endnu en (ellers dobbelt-salg ved aabningen). Row
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
                logger.error(f"[BuyTheDip] kunne ikke saette reconcile_closing-markoer ({sym}): {e}")

        result = await self.conn.place_paper_order(
            sym, close_action, shares, source=self.name,
            await_fill_sec=CLOSE_FILL_WAIT_SEC, order_ref=_ref,
        )
        if not result:
            await self._log(f"⚠ Kunne ikke lukke gammel position {sym} — luk manuelt i TWS",
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
                f"— journal-row forbliver åben (markedet kan være lukket: en MKT-ordre "
                f"fylder først ved åbning). Luk evt. manuelt i TWS.", level="warning")
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
        await self._log(f"♻ Gammel åben BuyTheDip-position {sym} (long {shares}) lukket "
                        f"@ ${exit_price:.2f} (reconcile) | P&L: ${pnl:+.2f}")

    async def _reconcile_mark_closed(self, sym: str, row: dict) -> None:
        trade_id = row.get("trade_id")
        entry = row.get("entry_price") or 0.0
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id=trade_id, exit_price=entry, exit_time=datetime.now(ET),
                exit_reason="reconcile_phantom", pnl=0.0,
                payload={"reconcile": True, "phantom": True})
        await self._log(f"🧹 {sym}: BuyTheDip-journal stod åben men IBKR er flad — bogført "
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
    # Universe — EGET scan (spejler K2, men helt adskilt)
    # -------------------------------------------------------------
    async def _prepare_universe(self) -> None:
        """Scan BuyTheDips EGET univers via TradingViews Intraday Volatility-screener.

        HELT adskilt fra K2: samme delte screener-helper, men egne parametre og eget
        scan — BuyTheDip forbruger IKKE K2's publicerede univers (så strategierne kan
        køre uafhængigt, på hver sin maskine/konto). Spejler K2's _prepare_universe.
        """
        self._status("scanning", "Scanner markedet efter dagens dip-kandidater...")

        # Nulstil dagens diagnostik ved dagsstart (egne tællere + base-state).
        self._diag_eval_count = 0
        self._diag_setups     = 0
        self._diag_entries    = 0
        self.reset_diagnostics()

        raw_tickers: list[str] = []
        for attempt in range(1, 3):
            raw_tickers = await self._scan_volatility_universe(UNIVERSE_TOP_N)
            if len(raw_tickers) >= MIN_UNIVERSE_SIZE:
                break
            if attempt < 2:
                self._status("scanning",
                             f"Scanner returnerede {len(raw_tickers)} — prøver igen...")
                await asyncio.sleep(3)

        if len(raw_tickers) < MIN_UNIVERSE_SIZE and FALLBACK_UNIVERSE:
            self._status("scanning",
                         f"Scanner gav for få — bruger fallback ({len(FALLBACK_UNIVERSE)})")
            raw_tickers = FALLBACK_UNIVERSE[:]

        # Upper-case + dedup, bevar screenerens rækkefølge (sorteret efter dagsændring).
        self.universe = list(dict.fromkeys(str(x).upper() for x in raw_tickers))

        if not self.universe:
            self._status("orb_ready",
                         "Scanner returnerede 0 tickers — BuyTheDip handler ikke i dag")
        else:
            self._status("universe_ready",
                         f"📋 Dagens univers — {len(self.universe)} aktier der potentielt "
                         f"kan handles: {', '.join(self.universe)}")

        # Eget universe_selected-event (source=self.name) via base-loggeren.
        await self.log_universe(
            self.universe,
            meta={
                "raw_count":  len(raw_tickers),
                "price_min":  UNIVERSE_PRICE_MIN,
                "price_max":  UNIVERSE_PRICE_MAX,
                "source":     "tv_intraday_volatility",
                # Del 1-instrumentering (rent tilføjende — påvirker ikke udvælgelsen):
                "pool_size":  getattr(self, "_last_scan_pool_size", None),
                "rows":       getattr(self, "_last_scan_rows", []),
                "filters":    {
                    "mkt_cap_min": UNIVERSE_MKT_CAP_MIN,
                    "mkt_cap_max": UNIVERSE_MKT_CAP_MAX,
                    "min_avg_vol": UNIVERSE_MIN_VOLUME,
                    "exchanges":   UNIVERSE_EXCHANGES,
                    "vol_m_min":   UNIVERSE_VOL_M_MIN,
                    "vol_m_max":   UNIVERSE_VOL_M_MAX,
                    "perf_w_min":  UNIVERSE_PERF_W_MIN,
                },
            },
        )

    async def _scan_volatility_universe(self, top_n: int) -> list[str]:
        """BuyTheDips EGET univers via TradingViews Intraday Volatility-screener.

        Kalder den delte screener-helper (samme som K2), men med BuyTheDips EGNE
        parametre. Returnerer symboler sorteret efter seneste dagsændring (faldende);
        tom liste hvis API'et fejler/timeout. Spejler K2's _scan_volatility_universe 1:1.
        """
        from strategies.shared.tv_scanner import build_volatility_universe_rows
        pool_size, rows = await build_volatility_universe_rows(
            top_n       = top_n,
            price_min   = UNIVERSE_PRICE_MIN,
            price_max   = UNIVERSE_PRICE_MAX,
            mkt_cap_min = UNIVERSE_MKT_CAP_MIN,
            mkt_cap_max = UNIVERSE_MKT_CAP_MAX,
            min_avg_vol = UNIVERSE_MIN_VOLUME,
            # atr_pct_min sendes IKKE laengere — ATR%(1W)-filteret er erstattet.
            vol_m_min   = UNIVERSE_VOL_M_MIN,
            vol_m_max   = UNIVERSE_VOL_M_MAX,
            perf_w_min  = UNIVERSE_PERF_W_MIN,
            exchanges   = UNIVERSE_EXCHANGES,
            timeout     = SCAN_TIMEOUT_SEC,
            log_tag     = "BuyTheDip",
        )
        # Del 1-instrumentering: gem seneste scans fulde rækker + puljestørrelse
        # (spejler K2 — egne konstanter, samme instrumentering).
        self._last_scan_pool_size = pool_size
        self._last_scan_rows = rows
        return [r["symbol"] for r in rows]

    # -------------------------------------------------------------
    # Trading-loop (to-fase: exits, så entries efter dip-dybde-prioritet)
    # -------------------------------------------------------------
    async def _trading_loop(self):
        self._status("trading", "Overvåger markedet — venter på 1-min bars...")
        consecutive_errors = 0
        _last_heartbeat = datetime.now(ET)
        try:
            while self.status == StrategyStatus.RUNNING:
                now_et = datetime.now(ET)
                t = now_et.time()

                if t < SESSION_START:
                    self._status("orb_ready",
                                 f"Venter på handelsvindue — starter kl. "
                                 f"{SESSION_START.strftime('%H:%M')} ET", persist=False)
                    await asyncio.sleep(LOOP_SLEEP_SECONDS)
                    continue

                if t >= FORCE_CLOSE_ET:
                    if self._positions:
                        self._status("trading", "Markedet lukker — lukker alle positioner")
                        await self._close_all(f"market_close {FORCE_CLOSE_ET.strftime('%H:%M')}")
                    wins   = sum(1 for tr in self.trades if tr["pnl"] > 0)
                    losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                    self._status("done",
                                 f"✅ Handelsdagen afsluttet | P&L: ${self.total_pnl:+,.2f} | "
                                 f"{len(self.trades)} handler ({wins}W/{losses}L)")
                    await self._log_self_stop("Sessions-slut: force-close",
                                              StrategyStatus.STOPPED, "done")
                    break

                if not self.universe:
                    self._status("orb_ready", "Intet univers — ingen handel i dag", persist=False)
                    await asyncio.sleep(60)
                    continue

                self._status("trading",
                             f"Overvåger {len(self.universe)} aktier — "
                             f"{now_et.strftime('%H:%M:%S')} ET | "
                             f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}",
                             persist=False)

                if (now_et - _last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL_SEC:
                    _last_heartbeat = now_et
                    await self.log_heartbeat({
                        "evaluations":    self._diag_eval_count,
                        "setups":         self._diag_setups,
                        "entries":        self._diag_entries,
                        "open_positions": self.stats.open_positions,
                        "universe_size":  len(self.universe),
                    })

                try:
                    allow_entries = (t <= OPEN_UNTIL_ET)
                    candidates = []   # (ticker, setup, bar)
                    for ticker in self.universe:
                        if self.status != StrategyStatus.RUNNING:
                            break
                        res = await self._check_ticker(ticker, allow_entries)
                        if res is not None:
                            candidates.append(res)
                    # Entries efter dybeste dip først, op til frie pladser.
                    await self._open_candidates(candidates)
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    logger.exception(f"[BuyTheDip] fejl i handels-loop: {e}")
                    if consecutive_errors >= 3:
                        self._status("trading", f"⚠ {consecutive_errors} fejl — genforbinder...")
                        if await self._reconnect():
                            consecutive_errors = 0
                            self._status("trading", "✅ Genforbundet — fortsætter")
                        else:
                            await self._log_self_stop("IBKR-forbindelse tabt",
                                                      StrategyStatus.ERROR, "error")
                            break

                await asyncio.sleep(LOOP_SLEEP_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[BuyTheDip] _trading_loop crashede")
            await self._log_self_stop(
                f"Fejl i strategi-loop: {type(e).__name__}: {str(e)[:80]}",
                StrategyStatus.ERROR, "error")

    async def _check_ticker(self, ticker: str, allow_entries: bool):
        """Hent seneste færdige bar; håndtér exit på åben position; ellers detektér
        setup (returnér (ticker, setup, bar) hvis bounce-entry er klar)."""
        new_bar = await self._fetch_latest_bar(ticker)
        if new_bar is None:
            return None
        last = self._last_bar_processed.get(ticker)
        if last is not None and new_bar.timestamp <= last:
            return None   # ikke en ny færdig bar
        self._bar_history.setdefault(ticker, []).append(new_bar)
        self._last_bar_processed[ticker] = new_bar.timestamp
        self._diag_eval_count += 1

        # Forensik/watchdog: log hver bar-evaluering.
        wo = self._dip_state.get(ticker)
        status = ("holding" if ticker in self._positions
                  else "dip_pending" if wo else "scanning")
        await self.log_bar_evaluation(
            ticker=ticker, bar_time_et=new_bar.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            status=status, score=int(wo["dip_depth"]) if wo and "dip_depth" in wo else None)

        if ticker in self._positions:
            await self._check_exit(ticker, new_bar)
            return None

        if not allow_entries or ticker in self._done_today:
            return None
        return self._detect(ticker, new_bar)

    async def _fetch_latest_bar(self, ticker: str) -> Optional[Bar]:
        """Seneste FÆRDIGE 1-min bar (sidste bar i batchen). None hvis ingen."""
        bars = await self._fetch_bars(ticker, duration="3600 S", bar_size="1 min")
        return bars[-1] if bars else None

    # -------------------------------------------------------------
    # Detektion — dip → bounce (validerede scan_trade-regel, streaming)
    # -------------------------------------------------------------
    def _detect(self, ticker: str, bar: Bar):
        """Returnér (ticker, setup, bar) hvis DENNE bar er bounce'en efter en dip,
        ellers None. Sætter dip-state når en dip først detekteres."""
        hist = self._bar_history.get(ticker, [])
        if len(hist) < LOOKBACK:
            return None
        wo = self._dip_state.get(ticker)
        if wo is None:
            window = hist[-LOOKBACK:]
            ref_high = max(b.high for b in window)
            ref_low  = min(b.low for b in window)
            if ref_low <= 0:
                return None
            if (ref_high - ref_low) / ref_low * 100.0 < MIN_RUNUP_PCT:
                return None
            # Er DENNE bar en dip (lav nok under impuls-toppen)?
            if bar.low > ref_high * (1 - DIP_PCT / 100.0):
                return None
            dip_low = min(b.low for b in window)
            self._dip_state[ticker] = {
                "dip_low": dip_low, "ref_high": ref_high,
                "dip_depth": (ref_high - dip_low) / ref_high * 100.0,
            }
            self._diag_setups += 1
            return None   # bounce er en SENERE bar
        # Dip afventer bounce: første grønne bar (close > forrige close).
        if len(hist) < 2 or bar.close <= hist[-2].close:
            return None
        setup = dict(wo)            # dip_low, ref_high, dip_depth
        return (ticker, setup, bar)

    async def _open_candidates(self, candidates):
        """Åbn entry-kandidater efter DYBESTE dip_depth først, op til
        max_open_positions (to-fase-concurrency). candidates: liste af
        (ticker, setup, bar)."""
        for ticker, setup, bar in sorted(candidates, key=lambda c: -c[1]["dip_depth"]):
            if self.stats.open_positions >= self.config.max_open_positions:
                break
            await self._open(ticker, setup, bar)

    # -------------------------------------------------------------
    # Open — risiko-baseret sizing + notional-loft + forensik
    # -------------------------------------------------------------
    async def _open(self, ticker: str, setup: dict, bar: Bar):
        if ticker in self._positions or ticker in self._done_today:
            return
        entry = bar.close                       # live: entr på bounce-barens LUK
        stop  = setup["dip_low"]
        risk_per_share = entry - stop
        if risk_per_share <= 0 or entry <= 0:
            self._done_today.add(ticker)
            self._dip_state.pop(ticker, None)
            return
        # Konfigurerbar risiko/handel + notional-loft (risk_config, pr. konto).
        risk_budget  = self._resolve_risk("position_size") or RISK_BUDGET_USD
        notional_cap = self._resolve_risk("notional_cap") or NOTIONAL_CAP_USD
        shares = int(min(risk_budget / risk_per_share, notional_cap / entry))
        if shares < 1:
            self._done_today.add(ticker)
            self._dip_state.pop(ticker, None)
            return

        order = OrderRequest(
            strategy_name=self.name, ticker=ticker, action="BUY", quantity=shares,
            order_type="MKT", asset_class="equity",
            reason=f"BuyTheDip bounce dip_depth={setup['dip_depth']:.1f}%")
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
        # R genberegnes med den FAKTISKE fyldpris. risk_per_share ovenfor blev
        # udregnet paa bar.close (foer ordren) og bruges kun til sizing; target'et
        # skal afspejle den risiko vi rent faktisk loeb.
        R = entry - stop
        if R <= 0:
            # Fyldte vi under dip_low, er der ingen risiko at gange op. Falder
            # tilbage paa det gamle faste target frem for at saette et meningsloest.
            target = entry * 1.02
            await self._log(
                f"⚠ {ticker}: fyld ${entry:.2f} paa/under stop ${stop:.2f} — "
                f"R er ikke positiv, bruger fast +2 % target", level="warning")
        else:
            target = entry + TARGET_R * R
        entry_time = datetime.now(ET)

        # OrdersTracker
        try:
            from orders_tracker import get_tracker
            oid = result.get("order_id")
            if oid:
                get_tracker().record_placed(order_id=oid, source=self.name, ticker=ticker,
                                            action="BUY", shares=shares, order_type="MKT")
        except Exception as e:
            logger.warning(f"[BuyTheDip] tracker-registrering fejlede: {e}")

        trade_id = None
        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source=self.name, symbol=ticker, side="long", shares=shares,
                entry_price=entry, entry_time=entry_time,
                entry_reason=f"BuyTheDip bounce (dip_depth={setup['dip_depth']:.1f}%)",
                current_stop=stop, current_target=target, current_stage="initial",
                payload={"dip_depth": round(setup["dip_depth"], 4),
                         "ref_high": round(setup["ref_high"], 4),
                         "dip_low": round(stop, 4),
                         "risk_per_share": round(risk_per_share, 4)})

        # Stop/target-trajektorie (BuyTheDip: fast stop/target -> ét punkt = flad step-linje).
        stop_traj: list = []
        append_stop_point(stop_traj, entry_time, stop, target)

        self._positions[ticker] = {
            "side": "long", "entry_price": entry, "shares": shares,
            "stop": stop, "target": target, "entry_time": entry_time,
            "trade_id": trade_id, "dip_depth": setup["dip_depth"],
            "ref_high": setup["ref_high"], "dip_low": stop,
            "stop_traj": stop_traj,
        }
        self.stats.open_positions = len(self._positions)
        self._done_today.add(ticker)
        self._dip_state.pop(ticker, None)
        self._mfe[ticker] = entry
        self._mae[ticker] = entry
        self._diag_entries += 1

        # Entry-forensik (samme builders som K2/EUREVERSION) + buythedip-blok. FAIL-SAFE.
        try:
            snap = build_entry_snapshot(
                ticker=ticker, entry_price=entry, entry_time=entry_time, shares=shares,
                bars=self._bar_history.get(ticker, []), context={}, tape_buffer=None,
                variant_name=self.name)
            snap["buythedip"] = {
                "dip_depth": round(setup["dip_depth"], 4), "ref_high": round(setup["ref_high"], 4),
                "dip_low": round(stop, 4), "target": round(target, 4),
                "risk_per_share": round(risk_per_share, 4)}
            if self._journal:
                await self._journal.log_event(source=self.name, event_type="trade_forensics",
                                              symbol=ticker, payload=snap)
        except Exception as e:
            logger.warning(f"[BuyTheDip] entry-forensik fejlede for {ticker}: {e}")

        if self._broadcast_fn:
            await self._broadcast_async({
                "type": "algo_trade", "strategy": self.name, "action": "buy",
                "ticker": ticker, "price": entry, "shares": shares,
                "time": entry_time.strftime("%H:%M:%S")})
        await self._log(f"📉➡️📈 {ticker}: BUY {shares} @ ${entry:.2f} "
                        f"(stop ${stop:.2f}, target ${target:.2f}, wo {setup['dip_depth']:.1f}%)")

    # -------------------------------------------------------------
    # Exit
    # -------------------------------------------------------------
    async def _check_exit(self, ticker: str, bar: Bar):
        pos = self._positions.get(ticker)
        if pos is None:
            return
        if ticker in self._mfe:
            self._mfe[ticker] = max(self._mfe[ticker], bar.high)
        if ticker in self._mae:
            self._mae[ticker] = min(self._mae[ticker], bar.low)
        if bar.low <= pos["stop"]:
            await self._close(ticker, pos["stop"], "stop")
        elif bar.high >= pos["target"]:
            await self._close(ticker, pos["target"], "target")

    async def _close(self, ticker: str, price: float, reason: str):
        pos = self._positions.get(ticker)
        if pos is None:
            return
        shares = pos["shares"]
        result = await self.conn.place_paper_order(
            ticker, "SELL", shares, source=self.name, await_fill_sec=CLOSE_FILL_WAIT_SEC)
        if not result:
            await self._log(f"⚠ {ticker}: lukkeordre ikke sendt — position forbliver åben",
                            level="warning")
            return
        filled = result.get("filled") or 0
        if filled < shares:
            # Spørg IBKR FØR næste bar gen-afgiver — ellers sælges en position der
            # allerede er lukket, og residualet bliver en ejerløs short.
            still = await self._ibkr_still_holds(ticker, "long", shares)
            if still is False:
                await self._log(f"ℹ {ticker}: SELL var ubekræftet, men IBKR er flad — "
                                f"ordren fyldte alligevel. Bogfører (gen-afgiver IKKE).",
                                level="warning")
            else:
                _why = result.get("reject_reason")
                await self._log(f"⚠ {ticker}: SELL ikke bekræftet fyldt "
                                f"(status={result.get('status')}, filled={filled}/{shares}"
                                + (f", IBKR: {_why}" if _why else "") + ")"
                                + (" — position bekræftet åben, genforsøger" if still
                                   else " — positions-feed upålideligt, gen-afgiver IKKE"),
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
            logger.warning(f"[BuyTheDip] tracker (luk) fejlede: {e}")

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
            logger.warning(f"[BuyTheDip] chart_bars-snapshot fejlede for {ticker}: {e}")
            chart_bars = []
        if pos.get("trade_id") and self._journal:
            await self._journal.log_trade_close(
                trade_id=pos["trade_id"], exit_price=price, exit_time=exit_time,
                exit_reason=reason, pnl=pnl,
                payload={"max_favorable_excursion": round(mfe, 4) if mfe is not None else None,
                         "max_adverse_excursion":   round(mae, 4) if mae is not None else None,
                         "dip_depth": round(pos.get("dip_depth", 0), 4),
                         "chart_bars": chart_bars,
                         "stop_trajectory": pos.get("stop_traj", [])})

        try:
            snap = build_exit_snapshot(
                ticker=ticker, entry_price=entry, exit_price=price,
                entry_time=pos["entry_time"], exit_time=exit_time, shares=shares,
                pnl=pnl, reason=reason, bars=self._bar_history.get(ticker, []),
                context={}, tape_buffer=None, variant_name=self.name)
            snap["buythedip"] = {"reason": reason, "dip_depth": round(pos.get("dip_depth", 0), 4)}
            if self._journal:
                await self._journal.log_event(source=self.name, event_type="trade_forensics",
                                              symbol=ticker, payload=snap)
        except Exception as e:
            logger.warning(f"[BuyTheDip] exit-forensik fejlede for {ticker}: {e}")

        if self._broadcast_fn:
            await self._broadcast_async({"type": "algo_trade", "strategy": self.name,
                                         "action": "sell", **trade})
        emoji = "✅" if pnl > 0 else "❌"
        await self._log(f"{emoji} {ticker}: lukket @ ${price:.2f} ({reason}) | "
                        f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")

    async def _close_all(self, reason: str):
        """Robust force-close: bekræftet fyldning + genforsøg (spejler K2 331f898).
        Det der STADIG ikke fyldes bevares åbent → fanges af opstarts-reconcile."""
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
                await self._log(f"⚠ {len(self._positions)} position(er) ikke bekræftet lukket "
                                f"(forsøg {attempt}/{FORCE_CLOSE_MAX_ATTEMPTS}) — genforsøger om "
                                f"{FORCE_CLOSE_RETRY_DELAY}s", level="warning")
                await asyncio.sleep(FORCE_CLOSE_RETRY_DELAY)
        if self._positions:
            await self._log(f"⛔ {len(self._positions)} position(er) kunne IKKE bekræftes lukket "
                            f"efter {FORCE_CLOSE_MAX_ATTEMPTS} forsøg — bevaret åbne i journalen, "
                            f"ryddes ved næste opstart (luk dem evt. manuelt i TWS nu)",
                            level="warning")

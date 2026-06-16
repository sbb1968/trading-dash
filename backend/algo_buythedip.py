"""
algo_buythedip.py
─────────────────
Live trading-wrapper for "BuyTheDip" — long-only intradag mean-reversion der køber
dip-bunden efter en impuls og rider bounce'en. K2's komplement: K2 køber
impuls-TOPPEN, BuyTheDip køber DYKKET-bouncen (0/3 tab på K2's tabsdage i validering).

Entry/exit-logikken er den validerede regel fra june_correlation.scan_trade, live-
tilpasset: detektér dip på FÆRDIGE 1-min bars, entr på bounce-barens LUK (lidt
mere konservativt end backtestens open-entry — paper er dommeren).

Strukturelt spejler den Confluence2Live (samme delte konto, samme universmotor):
scoped reconcile (rører kun egne journal-rows), robust force-close (bekræftet fyldning
+ genforsøg), throttlet status, eksplicitte forensik-hooks. To bevidste forskelle:
  - Universet FORBRUGES fra K2's publicerede universe_selected (ikke eget scan).
  - Entries evalueres på FÆRDIGE bars (ikke forming bars).

PAPER-deployment (DUO509856, delt konto). Manuel start. Ikke kapital.

Navn/source = "BuyTheDip" overalt (backend, orderRef, journal, events, Studio-meta).

Placering: C:\\Projects\\trading_dash\\backend\\algo_buythedip.py
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, time as dtime
from typing import Optional

import pytz

from strategy_base import BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus
from ibkr_connect import IBKRConnection
from strategies.base import Bar
from trade_forensics import build_entry_snapshot, build_exit_snapshot

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
TARGET_PCT    = 2.0    # target = entry × (1 + dette/100)

# ── Sizing (DEPLOY-valg — backtesten var %-baseret; TUN på paper) ──
RISK_BUDGET_USD  = 100.0    # risiko (entry−stop) pr. handel
NOTIONAL_CAP_USD = 1000.0   # notional-loft (beskytter mod hale: ~−$116 værst)

# ── Operationelt ───────────────────────────────────────────────
LOOP_SLEEP_SECONDS       = 15
HEARTBEAT_INTERVAL_SEC   = 300
CLOSE_FILL_WAIT_SEC      = 8    # vent på bekræftet fyldning af lukke-ordre
FORCE_CLOSE_MAX_ATTEMPTS = 4
FORCE_CLOSE_RETRY_DELAY  = 4
UNIVERSE_WAIT_MIN        = 10   # giv K2 op til så mange min til at publicere univers


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
                "impuls). K2-komplement. Forbruger K2's univers. Paper.")

    @property
    def asset_class(self) -> str:
        return "equity"

    # -------------------------------------------------------------
    # Pre-flight
    # -------------------------------------------------------------
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
        await self._reconcile_orphans()
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
        try:
            ibkr_positions = self.conn.get_positions()
        except Exception as e:
            logger.error(f"[BuyTheDip] reconciliation: kunne ikke hente IBKR-positioner: {e}")
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
            if net == 0:
                await self._reconcile_mark_closed(sym, row)
                cleaned += 1
                continue
            net_sign = 1 if net > 0 else -1
            if net_sign != sign or abs(net) < shares:
                await self._log(
                    f"🔎 {sym}: BuyTheDip-journal siger {side} {shares}, IBKR holder "
                    f"{net:+d} — inkonsistent, observe-only (rører den IKKE)", level="warning")
                continue
            await self._reconcile_close(sym, row, shares, sign)
            cleaned += 1
        for sym, net in ibkr_by_ticker.items():
            if net and sym not in own:
                await self._log(
                    f"🔎 {sym} ({net:+d}) holdes i IBKR uden BuyTheDip-journal-spor — "
                    f"observe-only (anden strategi eller manuel handel)", level="warning")
        self._status("started",
                     f"Reconciliation: ryddede {cleaned} gammel/gamle BuyTheDip-position(er) op"
                     if cleaned else "Reconciliation: ingen BuyTheDip-spøgelser at rydde op")

    async def _reconcile_close(self, sym: str, row: dict, shares: int, sign: int) -> None:
        close_action = "SELL" if sign > 0 else "BUY"
        entry = row.get("entry_price") or 0.0
        snap  = await self.conn.get_snapshot(sym)
        exit_price = (snap.get("last") if snap else None) or entry or 0.0
        result = await self.conn.place_paper_order(sym, close_action, shares, source=self.name)
        if not result:
            await self._log(f"⚠ Kunne ikke lukke gammel position {sym} — luk manuelt i TWS",
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

    # -------------------------------------------------------------
    # Universe — FORBRUG af K2's publicerede univers (LÅST)
    # -------------------------------------------------------------
    async def _prepare_universe(self) -> None:
        """Hent K2's universe_selected for i dag (source 'Konfluens 2'). Logger
        BuyTheDips EGET universe_selected til forensik (de navne vi faktisk loadede)."""
        tickers = self._load_k2_universe()
        waited = 0
        while not tickers and waited < UNIVERSE_WAIT_MIN:
            self._status("started",
                         f"Venter på K2's univers ({waited}/{UNIVERSE_WAIT_MIN} min)...")
            await asyncio.sleep(60)
            waited += 1
            tickers = self._load_k2_universe()
        self.universe = tickers
        if not tickers:
            self._status("orb_ready",
                         "Intet K2-univers fundet i dag — BuyTheDip handler ikke")
        else:
            self._status("started", f"Univers: {len(tickers)} K2-navne loadet")
        # Eget universe_selected-event (forensik), source=self.name
        if self._journal:
            try:
                await self._journal.log_event(
                    source=self.name, event_type="universe_selected",
                    payload={"tickers": tickers, "consumed_from": "Konfluens 2"})
            except Exception as e:
                logger.error(f"[BuyTheDip] kunne ikke logge universe_selected: {e}")

    def _load_k2_universe(self) -> list[str]:
        """Read-only opslag i journalen efter K2's seneste universe_selected i dag."""
        path = getattr(self._journal, "db_path", None) if self._journal else None
        if not path:
            return []
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            rows = con.execute(
                "SELECT payload_json FROM events WHERE event_type='universe_selected' "
                "AND source='Konfluens 2' AND date(ts_local)=date('now','localtime') "
                "ORDER BY ts_local ASC").fetchall()
            con.close()
        except Exception as e:
            logger.warning(f"[BuyTheDip] universe-opslag fejlede: {e}")
            return []
        tickers: list[str] = []
        for (pj,) in rows:
            try:
                t = json.loads(pj).get("tickers", [])
                if t:
                    tickers = [str(x).upper() for x in t]   # seneste ikke-tomme vinder
            except Exception:
                continue
        return tickers

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
                    self.status = StrategyStatus.STOPPED
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
                    candidates.sort(key=lambda c: -c[1]["dip_depth"])
                    for ticker, setup, bar in candidates:
                        if self.stats.open_positions >= self.config.max_open_positions:
                            break
                        await self._open(ticker, setup, bar)
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
                            self._status("error", "❌ Kunne ikke genforbinde — stopper")
                            self.status = StrategyStatus.ERROR
                            break

                await asyncio.sleep(LOOP_SLEEP_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[BuyTheDip] _trading_loop crashede")
            raise

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
        """Seneste FÆRDIGE 1-min bar (spejler K2)."""
        try:
            bars = await self.conn.get_historical_bars(
                ticker, duration="3600 S", bar_size="1 min", what_to_show="TRADES")
        except Exception as e:
            logger.warning(f"  [BuyTheDip] {ticker}: kunne ikke hente bar: {e}")
            return None
        if not bars:
            return None
        raw = bars[-1]
        ts = raw.get("datetime") if isinstance(raw, dict) else raw.date
        if not isinstance(ts, datetime):
            return None
        ts = ET.localize(ts) if ts.tzinfo is None else ts.astimezone(ET)
        g = (lambda k, a: raw.get(k) if isinstance(raw, dict) else getattr(raw, a))
        try:
            return Bar(timestamp=ts,
                       open=float(g("open", "open")), high=float(g("high", "high")),
                       low=float(g("low", "low")), close=float(g("close", "close")),
                       volume=float(g("volume", "volume") or 0))
        except Exception:
            return None

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
        shares = int(min(RISK_BUDGET_USD / risk_per_share, NOTIONAL_CAP_USD / entry))
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
        result = await self.conn.place_paper_order(ticker, "BUY", shares, source=self.name)
        if not result:
            return
        fill = result.get("avg_fill")
        if fill and fill > 0:
            entry = fill
        target = entry * (1 + TARGET_PCT / 100.0)
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

        self._positions[ticker] = {
            "side": "long", "entry_price": entry, "shares": shares,
            "stop": stop, "target": target, "entry_time": entry_time,
            "trade_id": trade_id, "dip_depth": setup["dip_depth"],
            "ref_high": setup["ref_high"], "dip_low": stop,
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
            await self._log(f"⚠ {ticker}: SELL ikke bekræftet fyldt "
                            f"(status={result.get('status')}, filled={filled}/{shares}) "
                            f"— beholder position åben, genforsøger", level="warning")
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
        if pos.get("trade_id") and self._journal:
            await self._journal.log_trade_close(
                trade_id=pos["trade_id"], exit_price=price, exit_time=exit_time,
                exit_reason=reason, pnl=pnl,
                payload={"max_favorable_excursion": round(mfe, 4) if mfe is not None else None,
                         "max_adverse_excursion":   round(mae, 4) if mae is not None else None,
                         "dip_depth": round(pos.get("dip_depth", 0), 4)})

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

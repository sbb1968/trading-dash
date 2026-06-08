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
    backtest bor i meanrev_backtest.py — vi spejler dens z-matematik 1:1).
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
from statistics import pstdev
from typing import Optional

import pytz

from strategy_base import BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus
from ibkr_connect import IBKRConnection

logger = logging.getLogger(__name__)

# ── Konstanter (alle navngivne så de er nemme at ændre/sweep'e) ────────────
ET = pytz.timezone("America/New_York")

SESSION_START_ET = dtime(2, 0)    # europæisk session åbner 02:00 ET
SESSION_END_ET   = dtime(8, 0)    # lukker 08:00 ET (= 14:00 dansk)
FORCE_CLOSE_ET   = dtime(7, 55)   # tvangsluk-klokkeslæt (sidste sikre bar før 08:00)

LOOKBACK         = 30             # bars til MA/std (Søren vil kunne prøve 40 — ét tal-skift)
ENTRY_Z          = 2.0
EXIT_Z           = 0.5
STOP_Z           = 3.5
BAR_SIZE         = "15 mins"
BAR_MINUTES      = 15             # afledt af BAR_SIZE — bruges til "er baren færdig?"-tjek
INSTRUMENTS      = ["MES", "M2K"] # IKKE MNQ (mean-reverter ikke pålideligt — se SPEC)
RISK_PCT         = 0.01           # 1% af konto-equity pr. handel

# Kontrakt-multiplikatorer ($ pr. prispoint). VERIFICÉR mod IBKR ved første
# live-kvalificering. MES og M2K er begge $5/point.
MULTIPLIER = {
    "MES": 5.0,
    "M2K": 5.0,
}

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

# Den sidste 15-min slot der stadig regnes som "i sessionen" (08:00 − 15 min).
# Bruges som bar-baseret tvangsluk-backstop ved siden af det tidsbaserede.
LAST_SESSION_BAR_ET = dtime(7, 45)


@dataclass
class Bar:
    timestamp: datetime   # tz-aware ET
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float


def _compute_z(closes: list[float]):
    """
    z-score over en sekvens af closes — SPEJLER meanrev_backtest.zscore 1:1.

    Returnerer (z, std) hvor std er population-std (pstdev) i prisenheder, så
    sizing kan bruge den samme std som z'et beregnes med. None hvis std ≤ 0
    eller seneste close ≤ 0 (ingen brugbart signal).
    """
    if len(closes) < 2:
        return None
    ma = sum(closes) / len(closes)
    sd = pstdev(closes)
    if sd <= 0 or closes[-1] <= 0:
        return None
    return (closes[-1] - ma) / sd, sd


class EuropaReversionLive(BaseStrategy):
    """
    Live-wrapper for Europa-reversion. Følger BaseStrategy-interfacet så
    StrategyManager kan administrere den parallelt med ORB/K1/K2.
    """

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn

        # Fast univers (vises i status-dict via base.get_status_dict)
        self.universe: list[str] = list(INSTRUMENTS)

        # Bar-historie pr. instrument (warmup + live), og sidst-behandlede
        # bar-tid pr. instrument (dedup — vi evaluerer kun FÆRDIGE bars én gang).
        self._bar_history:        dict[str, list[Bar]]    = {}
        self._last_bar_processed: dict[str, datetime]     = {}

        # Åbne positioner pr. instrument. Én pr. instrument ad gangen.
        # Hver: {side, entry_price, contracts, multiplier, entry_time,
        #        stop_price, std, reserved, trade_id}
        self._positions: dict[str, dict] = {}

        # Legacy-felter UI/journal forventer
        self.trades:    list[dict] = []
        self.total_pnl: float      = 0.0

        self._loop_task: Optional[asyncio.Task] = None

    # -------------------------------------------------------------
    # BaseStrategy interface — properties
    # -------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Europa-reversion"

    @property
    def description(self) -> str:
        return ("Mean-reversion på index-micro futures (MES/M2K) i den "
                "europæiske session (02–08 ET)")

    @property
    def asset_class(self) -> str:
        return "futures"

    # -------------------------------------------------------------
    # Pre-flight (§1)
    # -------------------------------------------------------------

    async def pre_flight(self) -> tuple[bool, str]:
        checks = []

        self._status("started", "Pre-flight: Tjekker IBKR-forbindelse...")
        if not self.conn.connected:
            return False, "IBKR ikke forbundet"
        checks.append("IBKR forbundet")

        self._status("started", "Pre-flight: Henter konto-data...")
        account = self.conn.get_account_summary()
        balance = account.get("net_liquidation", 0)
        if balance <= 0:
            return False, "Ingen konto-data"
        checks.append(f"Konto aktiv — NLV: ${balance:,.2f}")

        if self._risk_manager:
            await self._risk_manager.update_nlv(balance)

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
        await self._reconcile_orphans()

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
        """
        Delt-konto-sikker reconcile ved opstart.

        To guards i stedet for K1/K2's nul:
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

        try:
            ibkr_positions = self.conn.get_positions()
        except Exception as e:
            logger.error(f"[Europa-reversion] reconciliation: kunne ikke hente positioner: {e}")
            return

        # Guard 1: instrument-klasse — KUN vores futures-symboler.
        ours = [p for p in ibkr_positions
                if p.get("ticker") in INSTRUMENTS and p.get("position")]

        if not ours:
            self._status("started",
                         "Reconciliation: ingen gamle futures-positioner i vores instrumenter")
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
                # Ægte vores (lukke-ordre fyldte aldrig) → luk + bogfør.
                await self._reconcile_close(sym, qty, open_rows[0])
            else:
                # Observe-only: aldrig blind-flatte.
                await self._log(
                    f"🔎 Gammel åben position {sym} ({qty:+.0f}) i IBKR uden journal-spor "
                    f"fra os — observe-only, rører den IKKE", level="warning")

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

        result = await self.conn.place_paper_order(sym, close_action, contracts, source=self.name)
        if not result:
            await self._log(
                f"⚠ Kunne ikke lukke gammel position {sym} — luk den manuelt i TWS",
                level="warning")
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

    # -------------------------------------------------------------
    # Status broadcast (samme format som ORB/K1/K2)
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
        logger.info(f"[Europa-reversion][{status}] {message}")

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

        self._status("orb_ready",
                     f"✅ Klar — {len(ready)}/{len(INSTRUMENTS)} instrumenter med "
                     f"warmup-historie ({', '.join(INSTRUMENTS)})")

    async def _fetch_warmup_bars(self, sym: str) -> list[Bar]:
        try:
            raw_bars = await self.conn.get_historical_bars(
                sym, duration=WARMUP_DURATION, bar_size=BAR_SIZE, what_to_show="TRADES",
            )
        except Exception as e:
            logger.error(f"[Europa-reversion] {sym}: warmup-fetch fejlede: {e}")
            return []
        parsed = [self._parse_bar(b) for b in raw_bars]
        return [b for b in parsed if b is not None]

    def _parse_bar(self, raw) -> Optional[Bar]:
        """Konverter en rå IBKR-bar (dict fra get_historical_bars) til Bar.

        Tidszone-håndtering spejler K2: naive timestamps lokaliseres til ET,
        tz-aware konverteres til ET. (IBKR intraday-bars kommer i TWS-tidszonen
        med formatDate=1 — samme antagelse som de eksisterende aktie-strategier
        kører på i produktion.)
        """
        ts = raw.get("datetime") if isinstance(raw, dict) else getattr(raw, "date", None)
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

        try:
            return Bar(
                timestamp=ts,
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=float(v) if v else 0.0,
            )
        except (TypeError, ValueError):
            return None

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
                                 f"{SESSION_START_ET.strftime('%H:%M')} ET")
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
                    self.status = StrategyStatus.STOPPED
                    break

                self._status("trading",
                             f"Overvåger {len(INSTRUMENTS)} instrumenter — "
                             f"{now_et.strftime('%H:%M:%S')} ET | "
                             f"Positioner: {self.stats.open_positions}/{self.config.max_open_positions}")

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
                            self._status("error", "❌ Kunne ikke genforbinde — stopper")
                            self.status = StrategyStatus.ERROR
                            break

                await asyncio.sleep(LOOP_SLEEP_SECONDS)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[Europa-reversion] _trading_loop crashede: {e}")
            raise

    async def _reconnect(self) -> bool:
        for attempt in range(1, MAX_CONNECT_RETRIES + 1):
            try:
                self.conn.disconnect()
                await asyncio.sleep(2)
                if await self.conn.connect():
                    return True
            except Exception as e:
                logger.error(f"[Europa-reversion] genforbindelsesforsøg {attempt} fejlede: {e}")
            if attempt < MAX_CONNECT_RETRIES:
                await asyncio.sleep(CONNECT_RETRY_DELAY)
        return False

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
                res = _compute_z([b.close for b in hist[-LOOKBACK:]])
                if res is not None:
                    z, sd = res
                    await self._evaluate_bar(sym, bar, z, sd)

        # Hold hukommelsen i ave (vi behøver kun de seneste LOOKBACK closes).
        if len(hist) > LOOKBACK * 8:
            self._bar_history[sym] = hist[-LOOKBACK * 8:]

    async def _fetch_recent_bars(self, sym: str) -> list[Bar]:
        try:
            raw_bars = await self.conn.get_historical_bars(
                sym, duration=LATEST_FETCH_DURATION, bar_size=BAR_SIZE, what_to_show="TRADES",
            )
        except Exception as e:
            logger.warning(f"[Europa-reversion] {sym}: kunne ikke hente bars: {e}")
            return []
        parsed = [self._parse_bar(b) for b in raw_bars]
        return [b for b in parsed if b is not None]

    async def _evaluate_bar(self, sym: str, bar: Bar, z: float, sd: float):
        """Kør z-reglen på én netop-færdiggjort bar (spejler run_backtest)."""
        bar_t = bar.timestamp.time()
        in_session = SESSION_START_ET <= bar_t < SESSION_END_ET

        # ── Åben position: tjek exit ──
        if sym in self._positions:
            pos = self._positions[sym]
            side = pos["side"]

            # Bar-baseret tvangsluk-backstop: sessionens sidste bar lukker altid.
            if bar_t >= LAST_SESSION_BAR_ET:
                await self._close(sym, bar.close, "session_end")
                return

            exit_now, reason = False, ""
            if side == "long":
                if z >= -EXIT_Z:
                    exit_now, reason = True, "revert"
                elif z <= -STOP_Z:
                    exit_now, reason = True, "stop"
            else:  # short
                if z <= EXIT_Z:
                    exit_now, reason = True, "revert"
                elif z >= STOP_Z:
                    exit_now, reason = True, "stop"

            if exit_now:
                await self._close(sym, bar.close, reason)
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
        if abs(z) < ENTRY_Z:
            return

        side = "short" if z >= ENTRY_Z else "long"
        await self._open(sym, side, bar, sd)

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

        stop_dist = (STOP_Z - ENTRY_Z) * sd          # = 1.5 × std, i prispoint
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

    async def _open(self, sym: str, side: str, bar: Bar, sd: float):
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

        # Trades-tabel
        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source        = self.name,
                symbol        = sym,
                side          = side,
                shares        = contracts,
                entry_price   = entry_price,
                entry_time    = entry_time,
                entry_reason  = f"Europa-reversion {side} (z-entry)",
                current_stop  = stop_price,
                current_stage = "initial",
                payload       = {
                    "std":               round(sd, 4),
                    "multiplier":        mult,
                    "stop_distance_pts": round(stop_dist, 4),
                    "contracts":         contracts,
                },
            )
            if trade_id:
                self._positions[sym]["trade_id"] = trade_id

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

    async def _close(self, sym: str, price: float, reason: str):
        pos = self._positions.get(sym)
        if pos is None:
            return

        side      = pos["side"]
        contracts = pos["contracts"]
        mult      = pos["multiplier"]
        entry     = pos["entry_price"]

        close_action = "SELL" if side == "long" else "BUY"

        # Send luknings-ordren FØR vi bogfører noget.
        result = await self.conn.place_paper_order(sym, close_action, contracts, source=self.name)
        if not result:
            logger.error(f"[Europa-reversion] _close({sym}): lukke-ordre kunne IKKE sendes "
                         f"— beholder position åben")
            self._status("trading", f"⚠ {sym}: lukkeordre ikke sendt — position forbliver åben")
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

        # Trades-tabel
        trade_id = pos.get("trade_id")
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id    = trade_id,
                exit_price  = price,
                exit_time   = datetime.now(ET),
                exit_reason = reason,
                pnl         = pnl,
                payload     = {"multiplier": mult, "contracts": contracts},
            )

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
        """Luk alle åbne positioner (sessions-slut eller stop)."""
        for sym in list(self._positions.keys()):
            pos = self._positions[sym]
            snap = await self.conn.get_snapshot(sym)
            price = (snap.get("last") if snap else None) or pos["entry_price"]
            await self._close(sym, price, reason)

    # -------------------------------------------------------------
    # Hjælper
    # -------------------------------------------------------------

    async def _broadcast_async(self, msg: dict):
        """Send broadcast — håndtér både sync og async broadcast_fn (spejler K2)."""
        if not self._broadcast_fn:
            return
        import inspect
        if inspect.iscoroutinefunction(self._broadcast_fn):
            await self._broadcast_fn(msg)
        else:
            self._broadcast_fn(msg)

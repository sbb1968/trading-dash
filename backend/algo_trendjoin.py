"""
algo_trendjoin.py
─────────────────
Live trading-wrapper for "Trend Join Long" — HumbledTraders gap-and-go-pipeline:
TOP-GAPPERS re-scannet hvert 30. min → kun aktier hvis gap er udløst af en POSITIV
NYHEDSKATALYSATOR → join momentum når den fortsætter (ny HOD over premarket-high).

KERNEN er nyhedsfilteret: en aktie kommer KUN i puljen hvis Finnhub har en frisk,
positiv nyhed i dag (gap uden katalysator fader typisk — derfor filtreres det fra).
Det er den del der ikke kan backtestes billigt historisk → vi tester den her LIVE på
paper (vej B). Regel-parametrene kommer fra rules.json (Trend Join Long).

Strukturelt spejler den BuyTheDipLive/Confluence2Live (samme robuste maskineri): scoped
reconcile (rører kun egne journal-rows), bekræftet force-close m. genforsøg, throttlet
status, eksplicitte forensik-hooks, Lag A/B/C-diagnostik. Bevidste forskelle: (1) 30-min
gapper-RESCAN i loopet (ikke ét scan ved start), (2) nyhedskatalysator-gate, (3) flertrins-
exit (partial 1/3 @0.75R + breakeven @1.0R + 5m swing-low-trail).

PAPER. MANUEL start (IKKE i scheduleren — auto-starter aldrig). Long-only.
Navn/source = "Trend Join Long" overalt (backend, orderRef, journal, events).

Placering: C:\\Projects\\trading_dash\\backend\\algo_trendjoin.py
"""

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta
from typing import Optional

import aiohttp
import pytz

from strategy_base import BaseStrategy, StrategyConfig, OrderRequest, StrategyStatus
from ibkr_connect import IBKRConnection
from strategies.base import Bar
from trade_forensics import build_entry_snapshot, build_exit_snapshot
from finnhub_news import FINNHUB_API_KEY, FINNHUB_BASE, _guess_sentiment

logger = logging.getLogger(__name__)

# ── Tidszone + session (rules.json time_filter) ────────────────
ET             = pytz.timezone("America/New_York")
SESSION_START  = dtime(9, 30)     # US RTH-åbning (premarket-high låses her)
ENTRY_EARLIEST = dtime(10, 5)     # earliest_entry_et
ENTRY_LATEST   = dtime(15, 30)    # latest_entry_et — ingen nye entries efter
FORCE_CLOSE_ET = dtime(15, 51)    # force_close_et — holder ALDRIG over natten

# ── Regel-parametre (rules.json) ────────────────────────────────
MIN_GAP_PCT     = 3.0    # D3: pris ≥ dette % over forrige luk (intradag-mover)
MIN_PRICE_USD   = 3.0    # universe_filters.min_price_usd
SMA_LEN         = 200    # D2: forrige luk > SMA200 (daglig)
RVOL_MIN        = 2.0    # I3
RVOL_LOOKBACK   = 14     # I3_rvol_lookback_days
STOP_PCT        = 0.01   # initial_stop_rule: lod_minus_1pct
PARTIAL_R       = 0.75   # partial_profit_trigger_R
PARTIAL_FRAC    = 0.3333 # partial_profit_fraction
BE_R            = 1.0    # breakeven_trigger_R
SWING_LEFT      = 2      # post_breakeven_trail: swing_low_5m_2_2
SWING_RIGHT     = 2

# ── Risiko (rules.json risk; %-baseret på NLV, m. konstant fallback) ──
RISK_PCT          = 0.01    # max_risk_per_trade_pct (1 %)
NOTIONAL_PCT      = 0.10    # max_position_size_pct_of_portfolio (10 %)
RISK_BUDGET_FB    = 160.0   # fallback hvis NLV ikke kan læses (~1% af ~$16k)
NOTIONAL_CAP_FB   = 1600.0  # fallback (~10% af ~$16k)

# ── Univers / scan (top-gappers, HumbledTrader: re-scan hvert 30. min) ──
RESCAN_INTERVAL_SEC = 1800   # 30 min — nyt top-gapper-pull (pipeline-loopet)
UNIVERSE_TOP_N      = 25
UNIVERSE_PRICE_MIN  = 3.0
UNIVERSE_PRICE_MAX  = 500.0  # large caps gapper sjældent — lad prisloftet være vidt
UNIVERSE_MIN_VOLUME = 500_000
SCAN_TIMEOUT_SEC    = 15

# ── Operationelt (spejler BuyTheDip) ────────────────────────────
LOOP_SLEEP_SECONDS       = 15
HEARTBEAT_INTERVAL_SEC   = 300
CLOSE_FILL_WAIT_SEC      = 8
FORCE_CLOSE_MAX_ATTEMPTS = 4
FORCE_CLOSE_RETRY_DELAY  = 4
RECONCILE_TIMEOUT_SEC    = 30
NEWS_MAX_AGE_HOURS       = 20    # katalysator skal være frisk (overnight + premarket)
BAR_DURATION             = "7200 S"   # ~2t 5m-bars pr. fetch (nok til swing/HOD/LOD)
BAR_SIZE                 = "5 mins"


# ═══════════════════════════════════════════════════════════════
# Nyhedskatalysator — Finnhub company-news + keyword-sentiment
# ═══════════════════════════════════════════════════════════════
async def check_positive_catalyst(
    session: aiohttp.ClientSession, ticker: str
) -> tuple[bool, str]:
    """Har `ticker` en FRISK, POSITIV nyhedskatalysator i dag?

    Henter Finnhub company-news (i dag + i går), kører keyword-sentiment på hver
    overskrift (samme heuristik som News Room). Positiv katalysator = mindst én
    'bullish' overskrift nyere end NEWS_MAX_AGE_HOURS OG netto bullish (flere
    bullish end bearish blandt friske). Returnerer (har_katalysator, overskrift).

    Best-effort: enhver fejl/timeout → (False, "<årsag>"). Det er KERNEN i
    strategien — ingen katalysator, ingen handel.
    """
    to_date = datetime.now().date()
    from_date = to_date - timedelta(days=1)
    url = f"{FINNHUB_BASE}/company-news"
    params = {"symbol": ticker, "from": from_date.isoformat(),
              "to": to_date.isoformat(), "token": FINNHUB_API_KEY}
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return False, f"finnhub HTTP {resp.status}"
            items = await resp.json()
    except asyncio.TimeoutError:
        return False, "finnhub timeout"
    except Exception as e:
        return False, f"finnhub fejl: {type(e).__name__}"
    if not isinstance(items, list) or not items:
        return False, "ingen nyheder"

    cutoff = datetime.now().timestamp() - NEWS_MAX_AGE_HOURS * 3600
    bull, bear, best = 0, 0, ""
    best_ts = 0.0
    for it in items:
        ts = it.get("datetime", 0) or 0
        if ts < cutoff:
            continue
        headline = (it.get("headline", "") or "").strip()
        if not headline:
            continue
        sent = _guess_sentiment(headline)
        if sent == "bullish":
            bull += 1
            if ts > best_ts:
                best_ts, best = ts, headline
        elif sent == "bearish":
            bear += 1
    if bull > 0 and bull > bear:
        return True, best
    if bull > 0 and bull <= bear:
        return False, f"blandet/negativ (bull={bull} bear={bear})"
    return False, "ingen frisk positiv nyhed"


def _swing_low_2_2(bars: list[Bar]) -> Optional[float]:
    """Seneste BEKRÆFTEDE 5m swing-low (2 bars hver side). None hvis ingen."""
    n = len(bars)
    best = None
    for j in range(SWING_LEFT, n - SWING_RIGHT):
        lo = bars[j].low
        if all(lo < bars[j - k].low for k in range(1, SWING_LEFT + 1)) and \
           all(lo < bars[j + k].low for k in range(1, SWING_RIGHT + 1)):
            best = lo   # seneste bekræftede vinder
    return best


class TrendJoinLive(BaseStrategy):
    """Live-wrapper for Trend Join Long (gap-and-go m. nyhedskatalysator, long, paper)."""

    def __init__(self, conn: IBKRConnection, config: Optional[StrategyConfig] = None):
        super().__init__(config)
        self.conn = conn
        self._strategy = None

        # Per-ticker kontekst når den er optaget i puljen (vettet): prior_close,
        # prior_high, sma200, premarket_high, gap_pct, catalyst, hod, lod, avg_day_vol.
        self._ctx: dict[str, dict] = {}
        # Tickers vi allerede har nyheds-vettet i dag (ja/nej) → re-tjek ikke hver cyklus.
        self._vetted: dict[str, bool] = {}
        # Tickers der har taget (eller forsøgt) dagens ene setup → ingen genentry.
        self._done_today: set[str] = set()
        # Positioner: {entry, shares_total, shares_rem, stop, R, partial_done, be_done,
        #   entry_time, trade_id, gap_pct}.
        self._positions: dict[str, dict] = {}

        self._nlv: float = 0.0
        self._last_scan: Optional[datetime] = None

        # Diagnostik (Lag C)
        self._diag_eval_count = 0
        self._diag_vetted     = 0
        self._diag_entries    = 0

    # -------------------------------------------------------------
    # BaseStrategy interface
    # -------------------------------------------------------------
    @property
    def name(self) -> str:
        return "Trend Join Long"

    @property
    def description(self) -> str:
        return ("Gap-and-go long: top-gappers (re-scan/30min) MED positiv nyheds"
                "katalysator, join momentum (ny HOD>premarket-high). Flertrins-exit. Paper.")

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
        # cache NLV til risiko-sizing
        try:
            acct = self.conn.get_account_summary()
            self._nlv = float(acct.get("net_liquidation", 0) or 0)
        except Exception:
            self._nlv = 0.0

        self._status("started", "Pre-flight: Tester datafeed (AAPL)...")
        test_bars = await self.conn.get_historical_bars(
            "AAPL", duration="1 D", bar_size="5 mins", what_to_show="TRADES")
        if not test_bars:
            test_bars = await self.conn.get_historical_bars(
                "AAPL", duration="1 D", bar_size="5 mins", what_to_show="MIDPOINT")
        if not test_bars:
            return False, "Kan ikke hente markedsdata"
        checks.append(f"Datafeed virker — {len(test_bars)} bars")

        # Test nyheds-API (kernen). Best-effort — advarer men blokerer ikke start.
        try:
            async with aiohttp.ClientSession() as s:
                has, detail = await check_positive_catalyst(s, "NVDA")
            checks.append(f"Finnhub-news svarer ({'katalysator' if has else 'ingen'} på NVDA)")
        except Exception as e:
            checks.append(f"⚠ Finnhub-news ikke verificeret: {type(e).__name__}")

        summary = " | ".join([f"✅ {c}" for c in checks])
        self._status("orb_ready", f"Pre-flight OK: {summary}")
        return True, summary

    # -------------------------------------------------------------
    # Start / Stop
    # -------------------------------------------------------------
    async def on_start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            logger.warning("[TrendJoin] _trading_loop kører allerede — afbryder ny start")
            return
        self._status("started", "Algoritme starter — Trend Join Long (gap-and-go m. nyheder)")
        try:
            await asyncio.wait_for(self._reconcile_orphans(), timeout=RECONCILE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error(f"[TrendJoin] reconcile-timeout ({RECONCILE_TIMEOUT_SEC}s) — springer over")
            self._status("started", "Reconciliation timeout — fortsætter til handel")
        # Nulstil dagens state + diagnostik
        self._ctx.clear(); self._vetted.clear(); self._done_today.clear()
        self.universe = []
        self._diag_eval_count = self._diag_vetted = self._diag_entries = 0
        self.reset_diagnostics()
        self._loop_task = asyncio.create_task(self._trading_loop())

    async def on_bar(self, ticker: str, bar: dict) -> None:
        pass  # vi poller selv

    async def on_stop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        if self._positions:
            await self._close_all("strategi stoppet")

    # -------------------------------------------------------------
    # Scoped reconcile (spejler BuyTheDip — long-only, source=self.name)
    # -------------------------------------------------------------
    async def _reconcile_orphans(self) -> None:
        try:
            await self._reconcile_orphans_impl()
        except Exception as e:
            logger.exception(f"[TrendJoin] reconcile fejlede (best-effort, ignoreret): {e}")
            self._status("started", "Reconciliation sprang fejlet over — fortsætter til handel")

    async def _reconcile_orphans_impl(self) -> None:
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
            logger.error(f"[TrendJoin] reconciliation: list_trades fejlede: {e}")
            return
        try:
            ibkr_positions = self.conn.get_positions()
        except Exception as e:
            logger.error(f"[TrendJoin] reconciliation: kunne ikke hente IBKR-positioner: {e}")
            return
        ibkr_by_ticker = {(p.get("ticker") or "").upper(): (p.get("position") or 0)
                          for p in ibkr_positions if p.get("position")}
        cleaned = 0
        own: set[str] = set()
        for row in open_rows:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            own.add(sym)
            shares = int(abs(row.get("shares") or 0))
            side = (row.get("side") or "").lower()
            sign = 1 if side == "long" else -1
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
                    f"🔎 {sym}: TrendJoin-journal siger {side} {shares}, IBKR holder "
                    f"{net:+.0f} — inkonsistent, observe-only (rører den IKKE)", level="warning")
                continue
            await self._reconcile_close(sym, row, shares, sign)
            cleaned += 1
        for sym, net in ibkr_by_ticker.items():
            if net and sym not in own:
                await self._log(
                    f"🔎 {sym} ({net:+.0f}) holdes i IBKR uden TrendJoin-journal-spor — "
                    f"observe-only (anden strategi eller manuel handel)", level="warning")
        self._status("started",
                     f"Reconciliation: ryddede {cleaned} gammel/gamle TrendJoin-position(er) op"
                     if cleaned else "Reconciliation: ingen TrendJoin-spøgelser at rydde op")

    async def _reconcile_close(self, sym: str, row: dict, shares: int, sign: int) -> None:
        close_action = "SELL" if sign > 0 else "BUY"
        entry = row.get("entry_price") or 0.0
        snap = await self.conn.get_snapshot(sym)
        exit_price = (snap.get("last") if snap else None) or entry or 0.0
        _dups = [o for o in await self.conn.get_open_orders()
                 if o["symbol"] == sym.upper() and o["action"] == close_action]
        if _dups:
            await self._log(
                f"⏸ {sym}: en {close_action}-ordre hviler allerede "
                f"({_dups[0]['status']}, rest {_dups[0]['remaining']:.0f}) — lægger IKKE duplikat.",
                level="warning")
            return
        result = await self.conn.place_paper_order(
            sym, close_action, shares, source=self.name, await_fill_sec=CLOSE_FILL_WAIT_SEC)
        if not result:
            await self._log(f"⚠ Kunne ikke lukke gammel position {sym} — luk manuelt i TWS",
                            level="warning")
            return
        filled = result.get("filled") or 0
        if filled < shares:
            await self._log(
                f"⚠ {sym}: reconcile-lukning IKKE bekræftet fyldt "
                f"(status={result.get('status')}, filled={filled}/{shares}) — row forbliver åben.",
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
        await self._log(f"♻ Gammel åben TrendJoin-position {sym} (long {shares}) lukket "
                        f"@ ${exit_price:.2f} (reconcile) | P&L: ${pnl:+.2f}")

    async def _reconcile_mark_closed(self, sym: str, row: dict) -> None:
        trade_id = row.get("trade_id")
        entry = row.get("entry_price") or 0.0
        if trade_id and self._journal:
            await self._journal.log_trade_close(
                trade_id=trade_id, exit_price=entry, exit_time=datetime.now(ET),
                exit_reason="reconcile_phantom", pnl=0.0,
                payload={"reconcile": True, "phantom": True})
        await self._log(f"🧹 {sym}: TrendJoin-journal stod åben men IBKR er flad — bogført "
                        f"lukket (fantom, nul-P&L), ingen ordre sendt", level="warning")

    # -------------------------------------------------------------
    # 30-min top-gapper RESCAN + nyhedskatalysator-gate + daglige filtre
    # -------------------------------------------------------------
    async def _rescan_watchlist(self) -> None:
        """HumbledTrader-pipeline: pull top-gappers, og for HVER ny kandidat tjek
        nyhedskatalysator + daglige filtre (D2 SMA200). Bestås → optag i puljen.
        Emitterer ALTID et 'trendjoin_scan'-forensik-event (alle gappers + verdikt +
        max-change), så trendjoin_forensik.py kan vise hvad hver 30-min-runde fandt."""
        self._last_scan = datetime.now(ET)
        self._status("scanning", "Pull af top-gappers + nyhedstjek...")

        gappers = await self._scan_top_gappers()
        records: list[dict] = []   # forensik: én pr. gapper denne runde (m. verdikt)
        added: list[str] = []
        max_change = max((c for _, _, c, _ in gappers), default=None)
        top_sym = gappers[0][0].upper() if gappers else None

        async with aiohttp.ClientSession() as session:
            for sym, price, change, vol in gappers:
                sym = sym.upper()
                rec = {"sym": sym, "change": round(change, 2), "price": round(price, 2),
                       "vol": int(vol), "verdict": "", "detail": ""}

                if sym in self._positions or sym in self.universe:
                    rec["verdict"] = "i_pulje"; records.append(rec); continue
                if sym in self._done_today:
                    rec["verdict"] = "handlet_i_dag"; records.append(rec); continue
                if change < MIN_GAP_PCT:
                    rec["verdict"] = "under_gap"; records.append(rec); continue
                if price < MIN_PRICE_USD:
                    rec["verdict"] = "under_pris"; records.append(rec); continue
                if self._vetted.get(sym) is False:
                    rec["verdict"] = "tidl_afvist"; records.append(rec); continue

                # ── KERNE: positiv nyhedskatalysator? ──
                has_cat, detail = await check_positive_catalyst(session, sym)
                await asyncio.sleep(1.1)   # Finnhub rate-limit
                rec["detail"] = detail[:120]
                if not has_cat:
                    self._vetted[sym] = False
                    rec["verdict"] = "ingen_katalysator"
                    await self.log_rejection_change(sym, f"ingen positiv katalysator ({detail})")
                    records.append(rec); continue

                # ── Daglige filtre: D2 (forrige luk > SMA200) + prior_high + prior_close ──
                ctx = await self._build_daily_context(sym)
                if ctx is None:
                    self._vetted[sym] = False
                    rec["verdict"] = "mangler_daglig_data"
                    await self.log_rejection_change(sym, "manglende daglige data (SMA200/prior)")
                    records.append(rec); continue
                if not (ctx["prior_close"] > ctx["sma200"]):
                    self._vetted[sym] = False
                    rec["verdict"] = "d2_fejl_under_sma200"
                    rec["detail"] = f"luk {ctx['prior_close']:.2f} < SMA200 {ctx['sma200']:.2f}"
                    await self.log_rejection_change(
                        sym, f"D2 fejl: forrige luk {ctx['prior_close']:.2f} < SMA200 {ctx['sma200']:.2f}")
                    records.append(rec); continue

                # ── Premarket-high (I1-reference) ──
                ctx["premarket_high"] = await self._premarket_high(sym)
                ctx["gap_pct"] = change
                ctx["catalyst"] = detail
                ctx["hod"] = 0.0
                ctx["lod"] = float("inf")
                self._ctx[sym] = ctx
                self._vetted[sym] = True
                self.universe.append(sym)
                added.append(sym)
                self._diag_vetted += 1
                rec["verdict"] = "OPTAGET"
                records.append(rec)
                await self._log(
                    f"📰➕ {sym} optaget: gap {change:+.1f}%, katalysator «{detail[:70]}» "
                    f"(prior_high ${ctx['prior_high']:.2f}, pm_high ${ctx['premarket_high']:.2f})")

        # ── Forensik: skriv hele scannet (top-gappers + verdikt + max-change) ──
        await self._log_scan(records, max_change, top_sym, added)

        if added:
            self._status("universe_ready",
                         f"📋 Puljen udvidet med {len(added)}: {', '.join(added)} "
                         f"(i alt {len(self.universe)})")
            await self.log_universe(self.universe, meta={
                "added": added, "min_gap_pct": MIN_GAP_PCT, "source": "tv_top_gainers+finnhub"})

    async def _log_scan(self, records: list[dict], max_change: Optional[float],
                        top_sym: Optional[str], added: list[str]) -> None:
        """Skriv ét 'trendjoin_scan'-event pr. 30-min-runde (forensik): alle top-gappers
        med verdikt + max-change + top-gapper + hvem der blev optaget. Læses af
        trendjoin_forensik.py. Best-effort — må aldrig nedbryde scannet."""
        scan_et = self._last_scan or datetime.now(ET)
        mc = (f"{max_change:+.1f}% ({top_sym})" if max_change is not None else "—")
        await self._log(f"🔁 30-min scan: {len(records)} gappers · max-change {mc} · "
                        f"{len(added)} ny optaget · pulje {len(self.universe)}")
        if not self._journal:
            return
        try:
            await self._journal.log_event(
                source=self.name, event_type="trendjoin_scan",
                payload={
                    "scan_et": scan_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "num_gappers": len(records),
                    "max_change": round(max_change, 2) if max_change is not None else None,
                    "top_gapper": top_sym,
                    "added": added,
                    "universe_size": len(self.universe),
                    "gappers": records,
                })
        except Exception as e:
            logger.error(f"[TrendJoin] scan-event-skriv fejlede: {e}")

    async def _scan_top_gappers(self) -> list[tuple]:
        """Top-gainers (= gappers, sorteret efter dagsændring) via TV-screeneren,
        kørt i executor m. timeout. Returnerer [(sym, price, change%, vol)]."""
        from strategies.shared.tv_scanner import fetch_tv_top_gainers
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda: fetch_tv_top_gainers(
                    top_n=UNIVERSE_TOP_N, price_min=UNIVERSE_PRICE_MIN,
                    price_max=UNIVERSE_PRICE_MAX, min_volume=UNIVERSE_MIN_VOLUME,
                    require_all_green=False)),    # rå gappere; vi gater selv på gap/SMA/katalysator
                timeout=SCAN_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            logger.error("[TrendJoin] TV top-gainer scan timeout")
            return []
        except Exception as e:
            logger.error(f"[TrendJoin] TV top-gainer scan fejl: {e}")
            return []

    async def _build_daily_context(self, ticker: str) -> Optional[dict]:
        """Daglige bars → SMA200, forrige dags high, forrige luk, 14-dages snit-volumen."""
        raw = await self.conn.get_historical_bars(
            ticker, duration="1 Y", bar_size="1 day", what_to_show="TRADES")
        bars = [self._parse_bar(b) for b in (raw or [])]
        bars = [b for b in bars if b is not None]
        if len(bars) < SMA_LEN + 1:
            return None
        closes = [b.close for b in bars]
        # Seneste FÆRDIGE dag = sidste daglige bar (i dag er endnu ikke "lukket" i 1-day-historik
        # før luk; IBKR's sidste daglige bar er typisk i går efter premarket — vi bruger den som
        # 'forrige dag'). SMA200 på lukker FØR den.
        prior_close = closes[-1]
        prior_high = bars[-1].high
        sma200 = sum(closes[-SMA_LEN:]) / SMA_LEN
        vols = [b.volume for b in bars[-RVOL_LOOKBACK:] if b.volume]
        avg_day_vol = (sum(vols) / len(vols)) if vols else 0.0
        return {"prior_close": prior_close, "prior_high": prior_high,
                "sma200": sma200, "avg_day_vol": avg_day_vol}

    async def _premarket_high(self, ticker: str) -> float:
        """Dagens premarket-high (bars FØR 09:30 ET, use_rth=False). -1 hvis ingen."""
        try:
            raw = await self.conn.get_historical_bars(
                ticker, duration="1 D", bar_size="5 mins",
                what_to_show="TRADES", use_rth=False)
        except Exception:
            return -1.0
        today = datetime.now(ET).date()
        highs = []
        for b in (raw or []):
            pb = self._parse_bar(b)
            if pb is None:
                continue
            if pb.timestamp.date() == today and pb.timestamp.time() < SESSION_START:
                highs.append(pb.high)
        return max(highs) if highs else -1.0

    # -------------------------------------------------------------
    # Trading-loop (rescan/30min · exits · entries)
    # -------------------------------------------------------------
    async def _trading_loop(self):
        self._status("trading", "Overvåger markedet — venter på handelsvindue...")
        consecutive_errors = 0
        _last_heartbeat = datetime.now(ET)
        try:
            while self.status == StrategyStatus.RUNNING:
                now_et = datetime.now(ET)
                t = now_et.time()

                if t < SESSION_START:
                    self._status("orb_ready",
                                 f"Venter på åbning {SESSION_START.strftime('%H:%M')} ET",
                                 persist=False)
                    await asyncio.sleep(LOOP_SLEEP_SECONDS)
                    continue

                if t >= FORCE_CLOSE_ET:
                    if self._positions:
                        self._status("trading", "Markedet lukker — lukker alle positioner")
                        await self._close_all(f"force_close {FORCE_CLOSE_ET.strftime('%H:%M')}")
                    wins = sum(1 for tr in self.trades if tr["pnl"] > 0)
                    losses = sum(1 for tr in self.trades if tr["pnl"] <= 0)
                    self._status("done",
                                 f"✅ Handelsdagen afsluttet | P&L: ${self.total_pnl:+,.2f} | "
                                 f"{len(self.trades)} handler ({wins}W/{losses}L)")
                    await self._log_self_stop("Sessions-slut: force-close",
                                              StrategyStatus.STOPPED, "done")
                    break

                # ── 30-min top-gapper RESCAN (HumbledTrader-loop) ──
                if (self._last_scan is None or
                        (now_et - self._last_scan).total_seconds() >= RESCAN_INTERVAL_SEC):
                    try:
                        await self._rescan_watchlist()
                    except Exception as e:
                        logger.exception(f"[TrendJoin] rescan-fejl: {e}")

                self._status("trading",
                             f"Overvåger {len(self.universe)} gappers — "
                             f"{now_et.strftime('%H:%M:%S')} ET | "
                             f"Pos: {self.stats.open_positions}/{self.config.max_open_positions}",
                             persist=False)

                if (now_et - _last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL_SEC:
                    _last_heartbeat = now_et
                    await self.log_heartbeat({
                        "evaluations": self._diag_eval_count, "vetted": self._diag_vetted,
                        "entries": self._diag_entries, "open_positions": self.stats.open_positions,
                        "universe_size": len(self.universe)})

                try:
                    allow_entries = (ENTRY_EARLIEST <= t <= ENTRY_LATEST)
                    candidates = []
                    for ticker in list(self.universe):
                        if self.status != StrategyStatus.RUNNING:
                            break
                        res = await self._check_ticker(ticker, allow_entries)
                        if res is not None:
                            candidates.append(res)
                    await self._open_candidates(candidates)
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    logger.exception(f"[TrendJoin] fejl i handels-loop: {e}")
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
            logger.exception("[TrendJoin] _trading_loop crashede")
            await self._log_self_stop(
                f"Fejl i strategi-loop: {type(e).__name__}: {str(e)[:80]}",
                StrategyStatus.ERROR, "error")

    async def _check_ticker(self, ticker: str, allow_entries: bool):
        """Hent seneste færdige 5m bar; opdater HOD/LOD; håndtér exit; ellers evaluér entry."""
        new_bar = await self._fetch_latest_bar(ticker)
        if new_bar is None:
            return None
        last = self._last_bar_processed.get(ticker)
        if last is not None and new_bar.timestamp <= last:
            return None
        self._bar_history.setdefault(ticker, []).append(new_bar)
        self._last_bar_processed[ticker] = new_bar.timestamp
        self._diag_eval_count += 1

        ctx = self._ctx.get(ticker)
        if ctx is not None and new_bar.timestamp.time() >= SESSION_START:
            ctx["hod"] = max(ctx.get("hod", 0.0), new_bar.high)
            ctx["lod"] = min(ctx.get("lod", float("inf")), new_bar.low)

        status = ("holding" if ticker in self._positions
                  else "monitor" if ctx else "scanning")
        await self.log_bar_evaluation(
            ticker=ticker, bar_time_et=new_bar.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            status=status, score=int(ctx["gap_pct"]) if ctx and "gap_pct" in ctx else None)

        if ticker in self._positions:
            await self._check_exit(ticker, new_bar)
            return None
        if not allow_entries or ticker in self._done_today or ctx is None:
            return None
        return self._evaluate_entry(ticker, new_bar, ctx)

    async def _fetch_latest_bar(self, ticker: str) -> Optional[Bar]:
        bars = await self._fetch_bars(ticker, duration=BAR_DURATION, bar_size=BAR_SIZE)
        return bars[-1] if bars else None

    # -------------------------------------------------------------
    # Entry-evaluering (rules.json intraday-filtre, på FÆRDIG 5m bar)
    # -------------------------------------------------------------
    def _evaluate_entry(self, ticker: str, bar: Bar, ctx: dict):
        """Returnér (ticker, bar, ctx) hvis ALLE intradag-filtre rammer; ellers None.
        D1 >prior_high · D3 ≥3% over prior_close · I1 >premarket_high · I2 ny HOD ·
        I3 RVOL≥2. (D2 + katalysator allerede vettet i _rescan_watchlist.)"""
        price = bar.close
        prior_close = ctx["prior_close"]
        # HOD FØR denne bar (I2 = denne bar laver ny intradag-top)
        hist = self._bar_history.get(ticker, [])
        rth = [b for b in hist[:-1] if b.timestamp.time() >= SESSION_START]
        hod_before = max((b.high for b in rth), default=0.0)

        # D3: intradag-mover ≥3% over forrige luk
        if prior_close <= 0 or (price - prior_close) / prior_close * 100.0 < MIN_GAP_PCT:
            return None
        # D1: over forrige dags high
        if price <= ctx["prior_high"]:
            return None
        # I1: over premarket-high (−1 = ingen premarket-data → consider passed)
        if ctx["premarket_high"] > 0 and price <= ctx["premarket_high"]:
            return None
        # I2: ny HOD (bar laver ny intradag-top)
        if bar.high <= hod_before:
            return None
        # I3: RVOL ≥ 2 (kumulativ RTH-vol i dag vs forventet ved samme tidspunkt)
        rvol = self._estimate_rvol(ticker, ctx, bar)
        if rvol is not None and rvol < RVOL_MIN:
            return None

        return (ticker, bar, ctx)

    def _estimate_rvol(self, ticker: str, ctx: dict, bar: Bar) -> Optional[float]:
        """Live RVOL-proxy: dagens kumulative RTH-volumen / (14-dages snit-dagsvolumen ×
        andel af handelsdagen forløbet). None hvis daglig snit-vol ukendt (→ filter springes)."""
        avg = ctx.get("avg_day_vol") or 0.0
        if avg <= 0:
            return None
        hist = self._bar_history.get(ticker, [])
        cum = sum(b.volume for b in hist if b.timestamp.time() >= SESSION_START)
        mins = (bar.timestamp.hour * 60 + bar.timestamp.minute) - (9 * 60 + 30)
        frac = max(min(mins / 390.0, 1.0), 1e-3)   # andel af 390-min RTH-dag
        expected = avg * frac
        return (cum / expected) if expected > 0 else None

    async def _open_candidates(self, candidates):
        """Åbn entries efter STØRSTE gap først, op til max_open_positions."""
        for ticker, bar, ctx in sorted(candidates, key=lambda c: -c[2].get("gap_pct", 0)):
            if self.stats.open_positions >= self.config.max_open_positions:
                break
            await self._open(ticker, bar, ctx)

    # -------------------------------------------------------------
    # Open — risiko-baseret sizing (rules.json: 1% risiko, 10% notional-loft)
    # -------------------------------------------------------------
    async def _open(self, ticker: str, bar: Bar, ctx: dict):
        if ticker in self._positions or ticker in self._done_today:
            return
        entry = bar.close
        lod = ctx.get("lod", float("inf"))
        if lod == float("inf") or lod <= 0:
            lod = bar.low
        stop = lod * (1 - STOP_PCT)
        risk_per_share = entry - stop
        if risk_per_share <= 0 or entry <= 0:
            self._done_today.add(ticker)
            return

        risk_budget = (self._nlv * RISK_PCT) if self._nlv > 0 else RISK_BUDGET_FB
        notional_cap = (self._nlv * NOTIONAL_PCT) if self._nlv > 0 else NOTIONAL_CAP_FB
        shares = int(min(risk_budget / risk_per_share, notional_cap / entry))
        if shares < 1:
            self._done_today.add(ticker)
            return

        order = OrderRequest(
            strategy_name=self.name, ticker=ticker, action="BUY", quantity=shares,
            order_type="MKT", asset_class="equity",
            reason=f"TrendJoin gap {ctx.get('gap_pct', 0):+.1f}% katalysator")
        if self._risk_manager:
            if not await self.request_order(order):
                return
        result = await self.conn.place_paper_order(ticker, "BUY", shares, source=self.name)
        if not result:
            return
        fill = result.get("avg_fill")
        if fill and fill > 0:
            entry = fill
            stop = lod * (1 - STOP_PCT)
            risk_per_share = entry - stop
            if risk_per_share <= 0:
                risk_per_share = entry * STOP_PCT  # degenerate-vagt
        entry_time = datetime.now(ET)
        R = risk_per_share

        try:
            from orders_tracker import get_tracker
            oid = result.get("order_id")
            if oid:
                get_tracker().record_placed(order_id=oid, source=self.name, ticker=ticker,
                                            action="BUY", shares=shares, order_type="MKT")
        except Exception as e:
            logger.warning(f"[TrendJoin] tracker-registrering fejlede: {e}")

        trade_id = None
        if self._journal:
            trade_id = await self._journal.log_trade_open(
                source=self.name, symbol=ticker, side="long", shares=shares,
                entry_price=entry, entry_time=entry_time,
                entry_reason=f"TrendJoin join (gap {ctx.get('gap_pct',0):+.1f}%, "
                             f"katalysator «{str(ctx.get('catalyst',''))[:50]}»)",
                current_stop=stop, current_target=round(entry + PARTIAL_R * R, 4),
                current_stage="initial",
                payload={"gap_pct": round(ctx.get("gap_pct", 0), 4),
                         "prior_high": round(ctx.get("prior_high", 0), 4),
                         "premarket_high": round(ctx.get("premarket_high", 0), 4),
                         "sma200": round(ctx.get("sma200", 0), 4),
                         "R": round(R, 4), "catalyst": str(ctx.get("catalyst", ""))[:200]})

        self._positions[ticker] = {
            "side": "long", "entry": entry, "shares_total": shares, "shares_rem": shares,
            "stop": stop, "R": R, "partial_done": False, "be_done": False,
            "entry_time": entry_time, "trade_id": trade_id, "gap_pct": ctx.get("gap_pct", 0)}
        self.stats.open_positions = len(self._positions)
        self._done_today.add(ticker)
        self._mfe[ticker] = entry
        self._mae[ticker] = entry
        self._diag_entries += 1

        try:
            snap = build_entry_snapshot(
                ticker=ticker, entry_price=entry, entry_time=entry_time, shares=shares,
                bars=self._bar_history.get(ticker, []), context={}, tape_buffer=None,
                variant_name=self.name)
            snap["trendjoin"] = {"gap_pct": round(ctx.get("gap_pct", 0), 4),
                                 "stop": round(stop, 4), "R": round(R, 4),
                                 "catalyst": str(ctx.get("catalyst", ""))[:200]}
            if self._journal:
                await self._journal.log_event(source=self.name, event_type="trade_forensics",
                                              symbol=ticker, payload=snap)
        except Exception as e:
            logger.warning(f"[TrendJoin] entry-forensik fejlede for {ticker}: {e}")

        if self._broadcast_fn:
            await self._broadcast_async({
                "type": "algo_trade", "strategy": self.name, "action": "buy",
                "ticker": ticker, "price": entry, "shares": shares,
                "time": entry_time.strftime("%H:%M:%S")})
        await self._log(f"🚀 {ticker}: BUY {shares} @ ${entry:.2f} (stop ${stop:.2f}, R ${R:.2f}, "
                        f"gap {ctx.get('gap_pct',0):+.1f}%, target1 ${entry + PARTIAL_R*R:.2f})")

    # -------------------------------------------------------------
    # Exit — stop-first · partial 1/3 @0.75R · BE @1.0R · 5m swing-low-trail
    # -------------------------------------------------------------
    async def _check_exit(self, ticker: str, bar: Bar):
        pos = self._positions.get(ticker)
        if pos is None:
            return
        if ticker in self._mfe:
            self._mfe[ticker] = max(self._mfe[ticker], bar.high)
        if ticker in self._mae:
            self._mae[ticker] = min(self._mae[ticker], bar.low)
        entry, R, stop = pos["entry"], pos["R"], pos["stop"]

        # STOP-FIRST (pessimistisk)
        if bar.low <= stop:
            await self._close(ticker, stop, "stop", pos["shares_rem"])
            return
        # Partial 1/3 @ +0.75R
        if not pos["partial_done"] and bar.high >= entry + PARTIAL_R * R:
            qty = int(round(pos["shares_total"] * PARTIAL_FRAC))
            qty = max(1, min(qty, pos["shares_rem"] - 1)) if pos["shares_rem"] > 1 else 0
            if qty >= 1:
                await self._close(ticker, entry + PARTIAL_R * R, "partial", qty, partial=True)
                pos = self._positions.get(ticker)
                if pos is None:
                    return
            pos["partial_done"] = True
        # Breakeven @ +1.0R (flyt stop til entry)
        if not pos["be_done"] and bar.high >= entry + BE_R * R:
            pos["stop"] = max(pos["stop"], entry)
            pos["be_done"] = True
            await self._log(f"🟰 {ticker}: breakeven nået (+{BE_R}R) — stop flyttet til ${pos['stop']:.2f}")
        # Trail til 5m swing-low(2,2) EFTER breakeven
        if pos["be_done"]:
            sl = _swing_low_2_2(self._bar_history.get(ticker, []))
            if sl is not None and sl > pos["stop"]:
                pos["stop"] = sl
                await self._log(f"⤴ {ticker}: trail-stop hævet til swing-low ${sl:.2f}")

    async def _close(self, ticker: str, price: float, reason: str,
                     qty: Optional[int] = None, partial: bool = False):
        pos = self._positions.get(ticker)
        if pos is None:
            return
        shares = qty if qty is not None else pos["shares_rem"]
        shares = min(shares, pos["shares_rem"])
        if shares < 1:
            return
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
                            f"— beholder position, genforsøger", level="warning")
            return
        fill = result.get("avg_fill")
        if fill and fill > 0:
            price = fill

        entry = pos["entry"]
        pnl = (price - entry) * shares
        pos["shares_rem"] -= shares
        self.total_pnl += pnl
        self.stats.pnl_today += pnl

        try:
            from orders_tracker import get_tracker
            oid = result.get("order_id")
            if oid:
                get_tracker().record_placed(order_id=oid, source=self.name, ticker=ticker,
                                            action="SELL", shares=shares, order_type="MKT")
        except Exception as e:
            logger.warning(f"[TrendJoin] tracker (luk) fejlede: {e}")

        exit_time = datetime.now(ET)
        pnl_pct = ((price - entry) / entry * 100.0) if entry else 0.0
        full_close = pos["shares_rem"] <= 0

        if self._broadcast_fn:
            await self._broadcast_async({
                "type": "algo_trade", "strategy": self.name, "action": "sell",
                "ticker": ticker, "entry_price": entry, "exit_price": price,
                "shares": shares, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                "reason": reason, "entry_time": pos["entry_time"].strftime("%H:%M:%S"),
                "exit_time": exit_time.strftime("%H:%M:%S")})
        emoji = "🟢" if pnl > 0 else "🔴"
        leg = "PARTIAL " if partial else ""
        await self._log(f"{emoji} {ticker}: {leg}SELL {shares} @ ${price:.2f} ({reason}) | "
                        f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
                        + (f" | rest {pos['shares_rem']}" if not full_close else ""))

        if not full_close:
            return   # partial-ben: positionen kører videre

        # ── Fuld lukning: bogfør handel + journal + forensik ──
        self.stats.trades_today += 1
        self.stats.last_trade_time = exit_time.strftime("%H:%M:%S")
        if pnl > 0:
            self.stats.wins_today += 1
        else:
            self.stats.losses_today += 1

        trade = {"ticker": ticker, "side": "long", "entry_price": entry, "exit_price": price,
                 "shares": pos["shares_total"], "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
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
                         "max_adverse_excursion": round(mae, 4) if mae is not None else None,
                         "gap_pct": round(pos.get("gap_pct", 0), 4),
                         "partial_taken": pos.get("partial_done", False)})
        try:
            snap = build_exit_snapshot(
                ticker=ticker, entry_price=entry, exit_price=price,
                entry_time=pos["entry_time"], exit_time=exit_time, shares=pos["shares_total"],
                pnl=pnl, reason=reason, bars=self._bar_history.get(ticker, []),
                context={}, tape_buffer=None, variant_name=self.name)
            snap["trendjoin"] = {"reason": reason, "gap_pct": round(pos.get("gap_pct", 0), 4)}
            if self._journal:
                await self._journal.log_event(source=self.name, event_type="trade_forensics",
                                              symbol=ticker, payload=snap)
        except Exception as e:
            logger.warning(f"[TrendJoin] exit-forensik fejlede for {ticker}: {e}")

        self._positions.pop(ticker, None)
        self.stats.open_positions = len(self._positions)

    async def _close_all(self, reason: str):
        """Robust force-close: bekræftet fyldning + genforsøg (spejler BuyTheDip)."""
        for attempt in range(1, FORCE_CLOSE_MAX_ATTEMPTS + 1):
            tickers = list(self._positions.keys())
            if not tickers:
                return
            for ticker in tickers:
                pos = self._positions.get(ticker)
                if pos is None:
                    continue
                snap = await self.conn.get_snapshot(ticker)
                price = (snap.get("last") if snap else None) or pos["entry"]
                await self._close(ticker, price, reason, pos["shares_rem"])
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

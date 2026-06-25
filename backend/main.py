import asyncio
import json
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware

from alert_engine import AlertEngine
from strategy_manager import StrategyManager
from strategy_base    import StrategyStatus
from risk_manager import RiskConfig

import notifier
from tws_watchdog import TWSWatchdog
from scheduler    import AlgoScheduler

from journal          import Journal
from orders_tracker   import get_tracker


from accounts import identity

from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path

from contextlib import asynccontextmanager
import aiosqlite
import aiohttp

import secrets
from fastapi import HTTPException, Header

from fastapi import Depends, Query
from pydantic import BaseModel

import dagens_log   # modulet har __name__-guard, saa import koerer ikke main()

# ── Tidsstemplet logging: alle linjer (print, logger, uvicorn) ──
import logging, builtins
from datetime import datetime as _dt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
# Rut uvicorns egne loggere gennem root, så DERES linjer også får tid:
for _n in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    _lg = logging.getLogger(_n)
    _lg.handlers.clear()
    _lg.propagate = True
logging.getLogger("ib_async").setLevel(logging.WARNING)

# Dæmp den hyppige dash-snapshot-access-linje (drukner ellers alt andet):
class _DropSnapshotAccess(logging.Filter):
    def filter(self, record):
        return "/account/dash-snapshot" not in record.getMessage()
logging.getLogger("uvicorn.access").addFilter(_DropSnapshotAccess())

# De bare print()-kald i alle moduler stemples i ét greb (print er en builtin,
# så dette rammer [Server]/[Watchdog]/[Algo]/[StrategyManager]/... overalt):
_orig_print = builtins.print
def _ts_print(*args, **kwargs):
    _orig_print(_dt.now().strftime("%H:%M:%S"), *args, **kwargs)
builtins.print = _ts_print

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ──────────────────────────────────────────────
alert_engine      = AlertEngine(threshold=0.5)
# ── Studio auth ───────────────────────────────────────────────
# Simpel in-memory token store. Tokens forsvinder ved backend-genstart,
# så brugeren skal logge ind igen efter restart. Det er fint for vores
# brug.
_studio_tokens: set[str] = set()


def _create_studio_token() -> str:
    """Generer en ny session-token og gem den."""
    token = secrets.token_urlsafe(32)
    _studio_tokens.add(token)
    return token


def require_studio_auth(authorization: str = Header(None),
                        x_internal_key: str = Header(None, alias="X-Internal-Key")) -> None:
    """
    FastAPI dependency: kræver gyldig adgang til beskyttede endpoints.

    Tre veje ind:
      1. Peer-maskine med korrekt intern nøgle (fuld tillid inden for Tailscale)
      2. Workstation i dev-mode (ingen auth — udviklingsbekvemmelighed)
      3. Almindeligt bruger-token fra /auth/login (mennesket ved Studio)
    """
    # 1. Peer-maskine med fælles intern nøgle
    if x_internal_key and identity.internal_key and \
            secrets.compare_digest(x_internal_key, identity.internal_key):
        return
    # 2. Workstation dev-mode
    if identity.instance_role == "workstation":
        return
    # 3. Almindeligt bruger-token
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    token = authorization[7:]
    if token not in _studio_tokens:
        raise HTTPException(status_code=401, detail="Ugyldig session")
    
connected_clients: list[WebSocket] = []
current_prices:   dict[str, float] = {}

algo_clients: list[WebSocket] = []

strategy_manager = StrategyManager(risk_config=RiskConfig(daily_loss_limit=300.0))
strategy_clients: list[WebSocket] = []

ibkr_conn      = None
live_feed      = None
live_feed_task = None
ibkr_connected = False
journal = Journal("trading_dash.db")
# ── Autonom drift: watchdog + scheduler ───────────────────────
tws_watchdog: TWSWatchdog | None     = None
algo_scheduler: AlgoScheduler | None = None


# ── Broadcast ─────────────────────────────────────────────────
async def broadcast(message: dict):
    if message.get("type") == "ticks":
        for tick in message.get("data", []):
            current_prices[tick["ticker"]] = tick["price"]
    disconnected = []
    for client in connected_clients:
        try:
            await client.send_text(json.dumps(message))
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        connected_clients.remove(client)


async def broadcast_algo(message: dict):
    disconnected = []
    for client in algo_clients:
        try:
            await client.send_text(json.dumps(message))
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        algo_clients.remove(client)


async def broadcast_strategy(message: dict):
    disconnected = []
    for client in strategy_clients:
        try:
            await client.send_text(json.dumps(message))
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        strategy_clients.remove(client)


# ── IBKR live feed ────────────────────────────────────────────
async def start_ibkr_feed():
    global ibkr_conn, live_feed, live_feed_task

    try:
        from ibkr_live_feed import IBKRLiveFeed

        # Brug den delte forbindelse fra StrategyManager
        ibkr_conn = strategy_manager.get_ibkr()

        if ibkr_conn is None:
            print("[LiveFeed] Ingen IBKR-forbindelse — bruger mock data")
            asyncio.create_task(mock_data_loop())
            return

        print("[LiveFeed] Bruger delt IBKR-forbindelse — starter live feed")

        live_feed      = IBKRLiveFeed(ibkr_conn, broadcast, alert_engine)
        live_feed_task = asyncio.create_task(live_feed.start())

    except Exception as e:
        print(f"[LiveFeed] Fejl: {e} — bruger mock data")
        asyncio.create_task(mock_data_loop())


# ── Mock fallback ─────────────────────────────────────────────
async def mock_data_loop():
    from mock_data import simulate_tick, generate_news_item
    news_id_counter = 0
    tick_count      = 0
    print("[MockFeed] Starter (IBKR ikke tilgængelig)")

    while True:
        await asyncio.sleep(0.8)
        if not connected_clients:
            continue

        ticks  = simulate_tick()
        alerts = alert_engine.process_ticks(ticks)

        await broadcast({"type": "ticks", "data": ticks, "timestamp": datetime.now().isoformat()})
        if alerts:
            await broadcast({"type": "alerts", "data": alerts})

        tick_count += 1
        if tick_count % 8 == 0:
            news_id_counter += 1
            news_item = generate_news_item(news_id_counter)
            await broadcast({"type": "news", "data": news_item})


# ── Algo ──────────────────────────────────────────────────────
async def start_algo(strategy_name: str = ""):
    """Start en strategi via StrategyManager (med fælles risk limits).

    Sikrer en levende IBKR-forbindelse FØR strategien startes. Det er
    afgørende for autonom drift: IBKR lukker forbindelsen hver nat, og
    backenden kører videre med en død forbindelse. Når Iben logger ind på
    TWS igen om morgenen, har vi en frisk port at forbinde til — men
    backendens forbindelsesobjekt er stadig det døde fra i nat.

    connect_ibkr() er nu selv-helende: hvis den eksisterende forbindelse
    er død (ib.isConnected() == False), rydder den op og genforbinder mod
    den friske TWS. Hvis forbindelsen allerede lever, er kaldet en hurtig
    no-op. Derfor er det sikkert at kalde her ved hver start.
    """
    global ibkr_connected

    # ── Sørg for en levende IBKR-forbindelse før vi starter ──────
    ok = await strategy_manager.connect_ibkr(paper_trading=True)
    ibkr_connected = ok
    if not ok:
        msg = "Kan ikke starte — IBKR ikke forbundet (er TWS logget ind?)"
        print(f"[Algo] {msg}")
        await broadcast_algo({
            "type": "algo_status", "strategy": strategy_name,
            "status": "error", "message": msg,
            "total_pnl": 0, "positions": 0, "trades": 0,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
        return

    success, msg = await strategy_manager.start_strategy(strategy_name)

    if not success:
        await broadcast_algo({
            "type": "algo_status", "strategy": strategy_name,
            "status": "error", "message": msg,
            "total_pnl": 0, "positions": 0, "trades": 0,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
    else:
        print(f"[Algo] {msg} — fælles risk limits gælder nu")

def broadcast_algo_sync(message: dict):
    """
    Dual-mode broadcast: virker både som sync fire-and-forget og som awaitable.
    Returnerer asyncio.Future så kalderen kan vælge: ignorere eller await'e.
    """
    try:
        return asyncio.ensure_future(broadcast_algo(message))
    except Exception as e:
        print(f"[Algo] Broadcast fejl: {e}")
        return None


async def stop_algo(strategy_name: str = ""):
    """Stop en strategi via StrategyManager."""
    await strategy_manager.stop_strategy(strategy_name, reason="Manuelt stoppet fra UI")
    print(f"[Algo] {strategy_name} stoppet via StrategyManager")

# ── Startup ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global ibkr_connected

    await journal.init()

    # ── Replikering: start snapshot-push (no-op hvis enabled=false) ──
    import replication
    replication.replicator.init(
        db_path      = journal.db_path,
        source_id    = identity.source_id,
        target_url   = identity.replication_target_url,
        internal_key = identity.internal_key,
        enabled      = identity.replication_enabled,
    )
    await replication.replicator.start()

    await journal.log_event(
        source="system",
        event_type="system_startup",
        payload={"message": "Trading Dash backend startet"},
    )

    strategy_manager.set_journal(journal)
    strategy_manager.set_broadcast_fn(broadcast_strategy)

    # Opret én delt IBKR-forbindelse ejet af StrategyManager
    ok = await strategy_manager.connect_ibkr(paper_trading=True)
    ibkr_connected = ok
    await journal.log_event(
        source     = "system",
        event_type = "ibkr_connect_attempt",
        payload    = {"connected": ok, "paper_trading": True},
    )

    # Registrér strategier hos StrategyManager
    if True:   # ALTID registrér — strategier skal findes selv hvis TWS er nede ved opstart
        from strategy_base import StrategyConfig

        # ── Registrér Konfluens 2 (1-min impuls) parallelt ──────
        from algo_confluence2 import Confluence2Live

        confluence2_config = StrategyConfig(
            max_loss_per_trade  = 150.0,
            max_daily_loss      = 300.0,     # var 250.0
            max_open_positions  = 3,
            max_position_size   = 1000.0,
        )
        confluence2 = Confluence2Live(strategy_manager.get_ibkr(), config=confluence2_config)
        strategy_manager.register(confluence2)
        confluence2._broadcast_fn = broadcast_algo_sync
        print(f"[Server] Konfluens 2 registreret — capital per handel: ${confluence2_config.max_position_size:.0f}")

        # ── Registrér Europa-reversion (futures mean-reversion, EU-session) ──
        from algo_europa_reversion import EuropaReversionLive

        europa_rev_config = StrategyConfig(
            max_loss_per_trade  = 170.0,   # ~1% af ~$17k konto (justér til faktisk equity)
            max_daily_loss      = 300.0,   # var 400.0
            max_open_positions  = 2,       # MES + M2K
            max_position_size   = 2000.0,  # defensivt; futures sizer på kontrakter (§2)
        )
        europa_rev = EuropaReversionLive(strategy_manager.get_ibkr(), config=europa_rev_config)
        strategy_manager.register(europa_rev)
        europa_rev._broadcast_fn = broadcast_algo_sync
        print(f"[Server] Europa-reversion registreret — futures MES/M2K, EU-session")

        # ── Registrér BuyTheDip (buy-the-dip, K2-komplement, paper) ──
        from algo_buythedip import BuyTheDipLive

        buythedip_config = StrategyConfig(
            max_loss_per_trade  = 150.0,
            max_daily_loss      = 300.0,
            max_open_positions  = 3,       # MAX_CONCURRENT
            max_position_size   = 1000.0,  # = NOTIONAL_CAP_USD (sizer risiko-baseret)
        )
        buythedip = BuyTheDipLive(strategy_manager.get_ibkr(), config=buythedip_config)
        strategy_manager.register(buythedip)
        buythedip._broadcast_fn = broadcast_algo_sync
        print(f"[Server] BuyTheDip registreret — buy-the-dip, forbruger K2-univers")

    asyncio.create_task(start_ibkr_feed())
    print(f"[Server] Trading Dash backend startet")
    print(f"[Server] Identitet: {identity.account_display_name} ({identity.account_id})")
    print(f"[Server] Instans:   {identity.instance_display_name} ({identity.instance_role})")
    print(f"[Server] IBKR:      {identity.ibkr_account} ({'paper' if identity.paper_trading else 'LIVE'})")
    # ── Start TWS watchdog ────────────────────────────────────
    global tws_watchdog, algo_scheduler

    tws_watchdog = TWSWatchdog()
    await tws_watchdog.start()
    print("[Server] TWS watchdog startet — tjekker port 7497 hvert 30. sek")

    # ── Start autonom scheduler ───────────────────────────────
    # Scheduler hooker ind i StrategyManager via callbacks.
    # Den kender ikke til strategy_manager direkte — kun til funktioner.

    def get_daily_summary() -> dict:
        """Saml dagens samlede stats på tværs af ALLE registrerede strategier."""
        strats = strategy_manager._strategies.values()
        return {
            "trades":    sum(s.stats.trades_today for s in strats),
            "wins":      sum(s.stats.wins_today for s in strats),
            "total_pnl": sum(s.stats.pnl_today for s in strats),
        }

    def tws_is_online() -> bool:
        return tws_watchdog.is_online if tws_watchdog else False

    async def reset_daily_counters():
        """Nulstil alle daglige tællere ved midnat ET."""
        await strategy_manager.reset_for_new_day()

    algo_scheduler = AlgoScheduler(
        start_algo_fn    = start_algo,
        stop_algo_fn     = stop_algo,
        get_summary_fn   = get_daily_summary,
        tws_is_online_fn = tws_is_online,
        reset_daily_fn   = reset_daily_counters,
        instance_role    = identity.instance_role,
    )
    await algo_scheduler.start()
    print("[Server] Algo-scheduler startet — autonom dagsplan aktiv")

    # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
    # await notifier.send(
    #     message  = f"Backend startet på {identity.instance_display_name}. Autonom drift aktiv.",
    #     title    = "🟢 Trading Dash backend startet",
    #     priority = 2,
    #     tags     = "robot,green_circle",
    # )

# ── Shutdown ──────────────────────────────────────────────────
@app.on_event("shutdown")
async def shutdown():
    """Ryd op pænt — stop scheduler, watchdog, ALLE strategier, baggrunds-
    loops og luk IBKR. Uden at annullere baggrunds-loopsene (portfolio,
    feed, finnhub) hænger uvicorn ved nedlukning, fordi de kører while True
    og aldrig afslutter af sig selv."""
    print("[Server] Shutting down...")

    # 1. Stop scheduler først så ingen nye jobs starter
    if algo_scheduler:
        try:
            await algo_scheduler.stop()
            print("[Server] Scheduler stoppet")
        except Exception as e:
            print(f"[Server] Fejl ved scheduler-stop: {e}")

    # 2. Stop watchdog så vi ikke får falske offline-alerts
    if tws_watchdog:
        try:
            await tws_watchdog.stop()
            print("[Server] Watchdog stoppet")
        except Exception as e:
            print(f"[Server] Fejl ved watchdog-stop: {e}")

    # 3. Stop ALLE kørende strategier pænt (ikke kun Momentum ORB).
    #    stop_strategy udløser on_stop → daily_diagnostics via try/finally.
    for _name, _strat in list(strategy_manager._strategies.items()):
        try:
            if _strat.status == StrategyStatus.RUNNING:
                await strategy_manager.stop_strategy(_name, reason="Backend shutdown")
                print(f"[Server] {_name} stoppet pænt")
        except Exception as e:
            print(f"[Server] Fejl ved stop af {_name}: {e}")

    # 4. Stop live_feed pænt (annullerer market-data subscriptions)
    if live_feed is not None:
        try:
            await live_feed.stop()
            print("[Server] Live feed stoppet")
        except Exception as e:
            print(f"[Server] Fejl ved live_feed-stop: {e}")

    # 5. Annullér de gemte baggrunds-tasks (live feed)
    for _task_name, _task in (("live_feed_task", live_feed_task),):
        if _task is not None and not _task.done():
            _task.cancel()
            try:
                await _task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[Server] {_task_name} afsluttede med fejl: {e}")
            print(f"[Server] {_task_name} annulleret")

    # 6. Annullér KUN vores egne baggrunds-loops ved navn (mock_data_loop).
    #    Vi maa IKKE feje alle tasks, for det rammer ogsaa
    #    uvicorns egen lifespan-task og giver CancelledError-traceback.
    #    Identificér ved coroutine-navn, spring shutdown selv over.
    _OURS = {"mock_data_loop"}
    _current = asyncio.current_task()
    _to_cancel = []
    for _t in asyncio.all_tasks():
        if _t is _current or _t.done():
            continue
        _coro = _t.get_coro()
        _name = getattr(_coro, "__name__", None) or getattr(
            getattr(_coro, "cr_code", None), "co_name", None)
        if _name in _OURS:
            _to_cancel.append(_t)
    for _t in _to_cancel:
        _t.cancel()
    if _to_cancel:
        await asyncio.gather(*_to_cancel, return_exceptions=True)
        print(f"[Server] {len(_to_cancel)} egne baggrunds-loops annulleret")

    # 7. Luk IBKR-forbindelse
    try:
        ibkr = strategy_manager.get_ibkr()
        if ibkr and ibkr.connected:
            ibkr.disconnect()
            print("[Server] IBKR forbindelse lukket")
    except Exception as e:
        print(f"[Server] Fejl ved IBKR-disconnect: {e}")

    # 8. Journal shutdown event (best effort)
    try:
        await journal.log_event(
            source     = "system",
            event_type = "system_shutdown",
            payload    = {"message": "Trading Dash backend stoppet"},
        )
    except Exception:
        pass

    # ── Replikering: ét sidste flush + stop loop ──
    try:
        import replication
        await replication.replicator.flush()
        await replication.replicator.stop()
        print("[Server] Replication flush + stop")
    except Exception as e:
        print(f"[Server] Replication shutdown-fejl: {e}")

    print("[Server] Shutdown færdig")

# ── /ws ───────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    print(f"[Server] Klient forbundet — {len(connected_clients)} aktive")

    await websocket.send_text(json.dumps({"type": "ibkr_status", "connected": ibkr_connected}))

    try:
        while True:
            raw     = await websocket.receive_text()
            message = json.loads(raw)

            if message["type"] == "set_threshold":
                alert_engine.set_threshold(float(message["value"]))

            elif message["type"] == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif message["type"] in ("ibkr_buy", "ibkr_sell"):
                # Manuel ordre fra watchlist-rækken — går DIREKTE til IBKR
                # paper trading kontoen (ikke den lokale mock-portfolio).
                action = "BUY" if message["type"] == "ibkr_buy" else "SELL"
                ticker = str(message.get("ticker", "")).upper().strip()
                try:
                    shares = int(message.get("shares", 0))
                except (TypeError, ValueError):
                    shares = 0

                if not ticker or shares <= 0:
                    await websocket.send_text(json.dumps({
                        "type":    "ibkr_order_result",
                        "success": False,
                        "ticker":  ticker,
                        "action":  action,
                        "shares":  shares,
                        "error":   "Ugyldig ticker eller mængde",
                    }))
                    continue

                ibkr = strategy_manager.get_ibkr()
                if ibkr is None or not ibkr.connected:
                    await websocket.send_text(json.dumps({
                        "type":    "ibkr_order_result",
                        "success": False,
                        "ticker":  ticker,
                        "action":  action,
                        "shares":  shares,
                        "error":   "IBKR ikke forbundet — start TWS",
                    }))
                    continue

                try:
                    result = await ibkr.place_paper_order(
                        ticker=ticker,
                        action=action,
                        quantity=shares,
                        order_type="MKT",
                    )
                except Exception as e:
                    await websocket.send_text(json.dumps({
                        "type":    "ibkr_order_result",
                        "success": False,
                        "ticker":  ticker,
                        "action":  action,
                        "shares":  shares,
                        "error":   f"Ordre-fejl: {e}",
                    }))
                    # Journaliser fejlen så vi kan se den senere
                    await journal.log_event(
                        source     = "manual_watchlist",
                        event_type = "ibkr_order_error",
                        symbol     = ticker,
                        payload    = {"action": action, "shares": shares, "error": str(e)},
                    )
                    continue

                if result is None:
                    await websocket.send_text(json.dumps({
                        "type":    "ibkr_order_result",
                        "success": False,
                        "ticker":  ticker,
                        "action":  action,
                        "shares":  shares,
                        "error":   "Ordre returnerede tom — tjek TWS",
                    }))
                    continue

                # Succes — send resultat tilbage til frontend
                await websocket.send_text(json.dumps({
                    "type":    "ibkr_order_result",
                    "success": True,
                    "ticker":  ticker,
                    "action":  action,
                    "shares":  shares,
                    "status":  result.get("status"),
                    "filled":  result.get("filled"),
                    "avg_fill": result.get("avg_fill"),
                }))

                # Registrer i orders tracker så ordrer-vinduet kan vise den
                order_id = result.get("order_id")
                if order_id:
                    get_tracker().record_placed(
                        order_id=order_id,
                        source="manual_watchlist",
                        ticker=ticker,
                        action=action,
                        shares=shares,
                        order_type="MKT",
                    )

                # Journaliser manuel ordre
                await journal.log_event(
                    source     = "manual_watchlist",
                    event_type = "ibkr_order_placed",
                    symbol     = ticker,
                    payload    = {
                        "action":   action,
                        "shares":   shares,
                        "order_id": order_id,
                        "status":   result.get("status"),
                        "filled":   result.get("filled"),
                        "avg_fill": result.get("avg_fill"),
                    },
                )

    except WebSocketDisconnect:
        pass  # Normal frakobling
    except Exception as e:
        print(f"[Server] Fejl: {e}")
    finally:
        # Sikker cleanup uanset hvordan vi forlader try-blokken
        if websocket in connected_clients:
            connected_clients.remove(websocket)
            print(f"[Server] Klient afbrudt — {len(connected_clients)} aktive")


# ── /ws/algo ──────────────────────────────────────────────────
@app.websocket("/ws/algo")
async def websocket_algo(websocket: WebSocket):
    await websocket.accept()
    algo_clients.append(websocket)
    print(f"[Algo] Klient forbundet — {len(algo_clients)} aktive")

    # Send initial-status for hver registreret algo-strategi.
    # LiveAlgo.tsx vil modtage flere algo_status-beskeder; hver med 'strategy'-felt
    # så frontenden kan adskille dem. Bagudkompatibilitet: hvis ingen ekstra
    # strategier er registreret, sender vi som før (uden strategy-felt).
    for strat_name in ("Konfluens 2", "Europa-reversion"):
        strat = strategy_manager._strategies.get(strat_name)
        if strat and strat.status == StrategyStatus.RUNNING:
            status  = "trading"
            message = f"{strat_name} kører"
            pnl     = strat.stats.pnl_today
            pos     = strat.stats.open_positions
            trades  = strat.stats.trades_today
        elif strat:
            status  = "idle"
            message = f"{strat_name} er ikke startet"
            pnl     = 0
            pos     = 0
            trades  = 0
        else:
            # Strategi ikke registreret — spring over
            continue

        await websocket.send_text(json.dumps({
            "type":      "algo_status",
            "strategy":  strat_name,
            "status":    status,
            "message":   message,
            "total_pnl": pnl,
            "positions": pos,
            "trades":    trades,
            "time":      datetime.now().strftime("%H:%M:%S"),
        }))

    # Open-positions snapshot, så UI'et kan genopbygge "Åbne positioner"-panelet efter
    # et reload/reconnect. algo_status sender kun ANTAL — panelet bygges ellers kun af
    # live algo_trade-events og mistes ved reload. Iterér ALLE kørende strategier.
    open_positions = []
    for strat in strategy_manager._strategies.values():
        if strat.status == StrategyStatus.RUNNING:
            try:
                for p in strat.open_positions_snapshot():
                    open_positions.append({**p, "strategy": strat.name})
            except Exception as e:
                print(f"[Algo] open_positions_snapshot fejl for {strat.name}: {e}")
    await websocket.send_text(json.dumps({
        "type":      "positions_snapshot",
        "positions": open_positions,
        "time":      datetime.now().strftime("%H:%M:%S"),
    }))

    try:
        while True:
            raw     = await websocket.receive_text()
            message = json.loads(raw)

            if message.get("command") == "start":
                # Strategi-agnostisk start. Default = "Momentum ORB" for bagudkompat
                # (eksisterende LiveAlgo.tsx sender ingen strategy-felt).
                strat_name = message.get("strategy", "")
                print(f"[Algo] Start-kommando modtaget for: {strat_name}")
                asyncio.create_task(start_algo(strat_name))

            elif message.get("command") == "stop":
                strat_name = message.get("strategy", "")
                print(f"[Algo] Stop-kommando modtaget for: {strat_name}")
                await stop_algo(strat_name)
                await broadcast_algo({
                    "type":      "algo_status",
                    "strategy":  strat_name,
                    "status":    "stopped",
                    "message":   f"{strat_name} stoppet manuelt",
                    "total_pnl": 0, "positions": 0, "trades": 0,
                    "time":      datetime.now().strftime("%H:%M:%S"),
                })

            elif message.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Algo] Fejl: {e}")
    finally:
        if websocket in algo_clients:
            algo_clients.remove(websocket)
            print(f"[Algo] Klient afbrudt — {len(algo_clients)} aktive")

# ── Orders endpoints ──────────────────────────────────────────
# Bruges af Ordrer-vinduet i Trading Dash til at vise og annullere ordrer

class CancelOrderRequest(BaseModel):
    order_id: int


# Ordrer-vinduet viser KUN ordrer oprettet i Trading Dash (manuelle). Algo-ordrer
# registreres stadig i trackeren (orders_log.json) til dagens_log/journal, men hoerer
# ikke til her — de ses i dagens_log/Studio.
MANUAL_ORDER_SOURCES = {"manual_watchlist", "manual"}


@app.get("/orders/list")
async def get_orders_list(period_hours: int = 24):
    """Returnér Trading Dash's MANUELLE ordrer i de seneste N timer med holdbar status."""
    ibkr = strategy_manager.get_ibkr()
    orders = await get_tracker().get_all_orders(
        ibkr, period_hours=period_hours, sources=MANUAL_ORDER_SOURCES)
    return {"orders": orders, "ibkr_connected": ibkr is not None and ibkr.connected}


@app.post("/orders/cancel")
async def cancel_order(req: CancelOrderRequest):
    """Annullér en åben ordre via IBKR."""
    ibkr = strategy_manager.get_ibkr()
    result = await get_tracker().cancel(ibkr, req.order_id)
    return result


# ── Swing-rapport endpoint ────────────────────────────────────
# Koerer det manuelle swing-egnethedsvaerktoej (swing_report.run_full) for EEN
# ticker og returnerer rapporten som TEKST. Frontend (SwingReport.tsx) viser den
# i en monospace-blok. De tre valgfrie skydere (sr/pattern/candle = Ibens
# manuelle chart-read) flettes ind via manual_overlay.

class SwingAnalyzeRequest(BaseModel):
    ticker:  str
    sr:      float | None = None   # support/modstand-overlay  (-100..+100)
    pattern: float | None = None   # chart-moenster-overlay     (-100..+100)
    candle:  float | None = None   # candlestick-overlay        (-100..+100)


def _run_swing_report(ticker: str, sr, pattern, candle) -> str:
    """BLOKERENDE: koerer hele swing-rapporten (IBKR-bars + FMP/Finnhub + TV).
    KALDES KUN i en threadpool (asyncio.to_thread), ALDRIG direkte i
    event-loopet - data_source._run ville ellers deadlocke paa det koerende loop."""
    import os
    import swing_report
    manual = swing_report.manual_overlay(sr=sr, chart_pattern=pattern, candlestick=candle)
    api_key = os.environ.get("FMP_API_KEY", "")
    return swing_report.run_full(ticker, api_key, manual=manual)


@app.post("/swing/analyze")
async def swing_analyze(req: SwingAnalyzeRequest):
    """Swing-egnethed for EEN ticker. Returnerer {ticker, report} hvor report er
    den fulde tekst-rapport. run_full koeres i threadpool (se _run_swing_report)."""
    ticker = (req.ticker or "").strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Mangler ticker")
    try:
        report = await asyncio.to_thread(
            _run_swing_report, ticker, req.sr, req.pattern, req.candle
        )
    except ValueError as e:
        # fx ingen prisdata for tickeren (delisted/ukendt/TWS nede)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Swing trading-analyse fejlede: {e}")
    return {"ticker": ticker, "report": report}


def _run_swing_json(ticker: str, sr, pattern, candle) -> dict:
    """BLOKERENDE: som _run_swing_report, men returnerer struktureret JSON.
    Samme threadpool-regel - kald ALDRIG direkte i event-loopet."""
    import os
    import swing_report
    manual = swing_report.manual_overlay(sr=sr, chart_pattern=pattern, candlestick=candle)
    api_key = os.environ.get("FMP_API_KEY", "")
    return swing_report.analyze_json(ticker, api_key, manual=manual)


@app.post("/swing/analyze_json")
async def swing_analyze_json(req: SwingAnalyzeRequest):
    """Swing-egnethed for EEN ticker som struktureret JSON (til UI-rendering).
    Samme scoring som /swing/analyze, men felter i stedet for tekst."""
    ticker = (req.ticker or "").strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Mangler ticker")
    try:
        data = await asyncio.to_thread(
            _run_swing_json, ticker, req.sr, req.pattern, req.candle
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Swing trading-analyse fejlede: {e}")
    return data


class IntradagAnalyzeRequest(BaseModel):
    symbol:    str
    timeframe: str = "5 mins"            # "1 min" / "3 mins" / "5 mins"
    sr:        float | None = None       # manuelle chart-overlays (default FRA)
    pattern:   float | None = None
    candle:    float | None = None


@app.post("/intradag/analyze_json")
async def intradag_analyze_json(req: IntradagAnalyzeRequest):
    """Intradag-konfluens for EET symbol -> struktureret JSON (UI). Genbruger appens
    DELTE IBKR-forbindelse. compute_intradag_report er native-async paa den forbindelses
    event-loop -> await DIREKTE, ALDRIG asyncio.to_thread (modsat /swing/analyze, hvor
    swing_report er SYNC og derfor MAA to_thread'es paa det koerende loop)."""
    import intradag_report
    symbol = (req.symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Mangler symbol")

    await strategy_manager.connect_ibkr(paper_trading=True)   # selv-helende, no-op hvis levende
    conn = strategy_manager.get_ibkr()
    if conn is None or not conn.connected:
        return {"error": "IBKR ikke forbundet (er TWS/Gateway logget ind paa 7497?)",
                "ibkr_connected": False}
    try:
        report = await intradag_report.compute_intradag_report(
            conn.ib, symbol, timeframe=req.timeframe,
            sr=req.sr, chart_pattern=req.pattern, candlestick=req.candle)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Day trading-analyse fejlede: {e}")
    return intradag_report.report_to_json(report)


# Vanilla-JS der tegner den annoterede swing-chart i PDF-siden. Samme layout som
# SwingChart.tsx (skalaer, anker-mapping, kollisionsfri etiketter), men print-
# farver (lyst tema) og fast 900x420 viewBox skaleret til sidebredden. CHART
# injiceres som JSON foran scriptet i _swing_report_html.
SWING_CHART_JS = r"""(function(){
  if(!CHART || !CHART.bars || !CHART.bars.length) return;
  var W=900, H=420, TOP=28, BOTM=52, XL=14, GUTTER=62, PILL_H=22, GAP=8;
  var BULL="#15803d", BEAR="#b91c1c", NEU="#b45309", MUT="#6b7280", GRID="#e5e7eb", PILLBG="#fff", TAGBG="#374151";
  var MONTHS=["jan","feb","mar","apr","maj","jun","jul","aug","sep","okt","nov","dec"];
  function pillW(t){return t.length*7+18;}
  function scol(s){return s.indexOf("HH/HL")===0?BULL:s.indexOf("LH/LL")===0?BEAR:NEU;}
  function esc(t){return String(t).replace(/[&<>"]/g,function(ch){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[ch];});}

  var bars=CHART.bars, n=bars.length, BOT=H-BOTM, xr=W-GUTTER;
  var lo=Infinity, hi=-Infinity, i, b;
  for(i=0;i<n;i++){b=bars[i]; if(b.l<lo)lo=b.l; if(b.h>hi)hi=b.h;}
  (CHART.levels||[]).forEach(function(lv){if(lv.price<lo)lo=lv.price; if(lv.price>hi)hi=lv.price;});
  if(CHART.swing_high && CHART.swing_high.price>hi) hi=CHART.swing_high.price;
  if(CHART.swing_low && CHART.swing_low.price<lo) lo=CHART.swing_low.price;
  var pad=(hi-lo)*0.06||1, pmin=lo-pad, pmax=hi+pad;
  var slot=(xr-XL)/Math.max(n,1);
  function x(idx){return XL+(idx+0.5)*slot;}
  function y(p){return BOT-(p-pmin)/(pmax-pmin)*(BOT-TOP);}
  var idxByDate={}; bars.forEach(function(bb,ii){idxByDate[bb.t]=ii;});
  var optrend=CHART.structure.indexOf("HH/HL")===0, downtrend=CHART.structure.indexOf("LH/LL")===0;
  var trendCol=scol(CHART.structure);

  function resolve(labels, dir){
    var out=[], sorted=labels.slice().sort(function(a,b){return a.cx-b.cx;});
    sorted.forEach(function(lab){
      var w=lab.w, x0=Math.max(XL, Math.min(lab.cx-w/2, xr-w)), x1=x0+w;
      var y0 = dir==="above" ? lab.anchorY-GAP-PILL_H : lab.anchorY+GAP;
      function ov(yy){return out.some(function(p){return x0<p.x1 && x1>p.x0 && yy<p.y1 && (yy+PILL_H)>p.y0;});}
      var guard=0;
      while(ov(y0) && guard++<50){
        var bl=out.filter(function(p){return x0<p.x1 && x1>p.x0;});
        if(dir==="above"){var m=Infinity; bl.forEach(function(p){if(p.y0<m)m=p.y0;}); y0=m-PILL_H-4;}
        else {var mx=-Infinity; bl.forEach(function(p){if(p.y1>mx)mx=p.y1;}); y0=mx+4;}
      }
      y0=Math.max(4, Math.min(y0, BOT-2-PILL_H));
      out.push({kind:lab.kind,text:lab.text,x0:x0,x1:x1,y0:y0,y1:y0+PILL_H,ax:lab.ax,ay:lab.ay,color:lab.color});
    });
    return out;
  }

  var above=[], below=[];
  if(CHART.swing_high && (CHART.swing_high.t in idxByDate)){
    var ih=idxByDate[CHART.swing_high.t];
    var th=optrend?"Higher High":downtrend?"Lower High":"Sving-top";
    above.push({kind:"hh",text:th,cx:x(ih),anchorY:y(CHART.swing_high.price),w:pillW(th),ax:x(ih),ay:y(CHART.swing_high.price),color:trendCol});
  }
  (CHART.candles||[]).forEach(function(c){
    var ic=n-1;
    above.push({kind:"candle",text:c.name,cx:x(ic),anchorY:y(bars[ic].h),w:pillW(c.name),ax:x(ic),ay:y(bars[ic].h),color:NEU});
  });
  if(CHART.swing_low && (CHART.swing_low.t in idxByDate)){
    var il=idxByDate[CHART.swing_low.t];
    var tl=optrend?"Higher Low":downtrend?"Lower Low":"Sving-bund";
    below.push({kind:"hl",text:tl,cx:x(il),anchorY:y(CHART.swing_low.price),w:pillW(tl),ax:x(il),ay:y(CHART.swing_low.price),color:trendCol});
  }
  var pills=resolve(above,"above").concat(resolve(below,"below"));

  var ticks=[], span=pmax-pmin;
  var step=span>400?100:span>200?50:span>80?25:span>30?10:5;
  for(var p=Math.ceil(pmin/step)*step;p<pmax;p+=step)ticks.push(p);
  var months=[], prevM="";
  bars.forEach(function(bb,ii){var m=bb.t.slice(5,7); if(m!==prevM){months.push({x:x(ii),label:MONTHS[parseInt(m,10)-1]||m}); prevM=m;}});

  var bw=Math.min(8, slot*0.6);
  var s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" xmlns="http://www.w3.org/2000/svg" style="display:block">';
  s+='<defs><marker id="swcp-ah" markerWidth="9" markerHeight="9" refX="6.5" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="'+MUT+'"/></marker></defs>';
  ticks.forEach(function(pp){
    s+='<line x1="'+XL+'" y1="'+y(pp)+'" x2="'+xr+'" y2="'+y(pp)+'" stroke="'+GRID+'" stroke-width="0.5"/>';
    s+='<text x="'+(xr+6)+'" y="'+(y(pp)+5)+'" font-size="14" fill="'+MUT+'">'+pp.toFixed(0)+'</text>';
  });
  months.forEach(function(m){
    s+='<line x1="'+m.x+'" y1="'+BOT+'" x2="'+m.x+'" y2="'+(BOT+4)+'" stroke="'+MUT+'" stroke-width="0.5"/>';
    s+='<text x="'+m.x+'" y="'+(BOT+22)+'" text-anchor="middle" font-size="14" fill="'+MUT+'">'+m.label+'</text>';
  });
  (CHART.levels||[]).forEach(function(lv){
    s+='<line x1="'+XL+'" y1="'+y(lv.price)+'" x2="'+xr+'" y2="'+y(lv.price)+'" stroke="'+(lv.kind==="resistance"?BEAR:BULL)+'" stroke-width="1.3" stroke-dasharray="5 4" opacity="0.8"/>';
  });
  bars.forEach(function(bb,ii){
    var up=bb.c>=bb.o, col=up?BULL:BEAR, cx=x(ii);
    var yb=y(Math.max(bb.o,bb.c)), hb=Math.max(Math.abs(y(bb.o)-y(bb.c)),1.2);
    s+='<line x1="'+cx+'" y1="'+y(bb.h)+'" x2="'+cx+'" y2="'+y(bb.l)+'" stroke="'+col+'" stroke-width="1"/>';
    s+='<rect x="'+(cx-bw/2)+'" y="'+yb+'" width="'+bw+'" height="'+hb+'" fill="'+col+'" rx="1"/>';
  });
  var yc=y(CHART.current_price);
  s+='<line x1="'+XL+'" y1="'+yc+'" x2="'+xr+'" y2="'+yc+'" stroke="'+MUT+'" stroke-width="0.5" stroke-dasharray="2 3"/>';
  s+='<rect x="'+(xr+2)+'" y="'+(yc-11)+'" width="'+(GUTTER-6)+'" height="22" rx="4" fill="'+TAGBG+'"/>';
  s+='<text x="'+(xr+2+(GUTTER-6)/2)+'" y="'+(yc+5)+'" text-anchor="middle" font-size="15" font-weight="700" fill="#fff">'+CHART.current_price.toFixed(2)+'</text>';
  pills.forEach(function(pl){
    var ab = pl.y1<=pl.ay;
    var lx=Math.max(pl.x0+6, Math.min(pl.ax, pl.x1-6));
    var ly=ab?pl.y1:pl.y0;
    if(pl.kind!=="candle") s+='<circle cx="'+pl.ax+'" cy="'+pl.ay+'" r="3.5" fill="'+pl.color+'"/>';
    s+='<line x1="'+lx+'" y1="'+ly+'" x2="'+pl.ax+'" y2="'+pl.ay+'" stroke="'+MUT+'" stroke-width="1.2" marker-end="url(#swcp-ah)"/>';
    s+='<rect x="'+pl.x0+'" y="'+pl.y0+'" width="'+(pl.x1-pl.x0)+'" height="'+PILL_H+'" rx="6" fill="'+PILLBG+'" stroke="'+pl.color+'" stroke-width="1.3"/>';
    s+='<text x="'+((pl.x0+pl.x1)/2)+'" y="'+(pl.y0+15)+'" text-anchor="middle" font-size="13" font-weight="600" fill="'+pl.color+'">'+esc(pl.text)+'</text>';
  });
  var sw=pillW(CHART.structure);
  s+='<rect x="12" y="6" width="'+sw+'" height="22" rx="6" fill="'+PILLBG+'" stroke="'+trendCol+'" stroke-width="1.3"/>';
  s+='<text x="'+(12+sw/2)+'" y="21" text-anchor="middle" font-size="13" font-weight="700" fill="'+trendCol+'">'+esc(CHART.structure)+'</text>';
  s+='</svg>';
  document.getElementById("swing-chart").innerHTML=s;
})();"""


def _swing_report_html(d: dict, detail: bool = False) -> str:
    """Render analyze_json som en paen, print-venlig HTML-side (lyst tema). Spejler
    UI'ets kort/score/drivers/info + chart-kontekst som tekst (den interaktive SVG-
    chart vises i appen).

    detail=True tilfoejer en fuld faktor-nedbrydning pr. lag (Parameter/Vaerdi/Bidrag/
    Vaegt/Vaegtet) EFTER opsummeringen — opt-in (til Soeren). detail=False (default) =
    normal rapport, pixel-identisk (til Iben)."""
    import html as _html
    esc = _html.escape
    BULL, BEAR, NEU, MUT = "#15803d", "#b91c1c", "#b45309", "#6b7280"
    BAND = {
        "STAERK SWING-KANDIDAT": "Staerk swing trading-kandidat", "EGNET MED FORBEHOLD": "Egnet med forbehold",
        "NEUTRAL-AFVENT": "Neutral - afvent", "SVAG": "Svag", "FRARAADES": "Fraraades",
        "Staerk": "Staerk", "Medvind": "Medvind", "Neutral": "Neutral", "Fraraades": "Fraraades",
    }

    def sc(v): return BULL if v >= 15 else BEAR if v <= -15 else NEU
    def fb(b):
        if b.startswith("STAERK"): return BULL
        if b.startswith("EGNET"): return NEU
        if b.startswith("SVAG") or b.startswith("FRARAADES"): return BEAR
        return MUT
    def bl(b): return BAND.get(b, b)
    def sg(v, dd=0): return ("+" if v > 0 else "") + f"{v:.{dd}f}"
    def pf(v, dd=1): return "—" if v is None else f"{v:.{dd}f}%"
    def usd(v):
        if v is None: return "—"
        if v >= 1e9: return f"${v/1e9:.1f}B"
        if v >= 1e6: return f"${v/1e6:.1f}M"
        return f"${round(v):,}"
    def sh(v):
        if v is None: return "—"
        if v >= 1e9: return f"{v/1e9:.1f}B"
        if v >= 1e6: return f"{v/1e6:.1f}M"
        return f"{v:,}"

    def layer_html(title, ly):
        rows = ""
        for g in ly["groups"]:
            col, pct = sc(g["score"]), min(abs(g["score"]), 100)
            side = "left:50%" if g["score"] >= 0 else f"left:{50 - pct/2}%"
            rows += (
                '<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px;color:#374151">'
                f'<span>{esc(g["name"])}</span><span style="color:{col};font-weight:700">{sg(g["score"])}</span></div>'
                '<div style="position:relative;height:6px;background:#e5e7eb;border-radius:3px;margin-top:3px">'
                '<div style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:#9ca3af"></div>'
                f'<div style="position:absolute;top:0;bottom:0;{side};width:{pct/2}%;background:{col};border-radius:3px"></div>'
                '</div></div>'
            )
        col = sc(ly["score"])
        return (
            '<div style="flex:1;min-width:200px;border:1px solid #e5e7eb;border-radius:8px;padding:12px">'
            f'<div style="display:flex;justify-content:space-between"><b style="font-size:13px">{esc(title)}</b>'
            f'<span style="font-size:11px;color:{MUT}">{round(ly["weight"]*100)}%</span></div>'
            f'<div style="margin:4px 0"><span style="font-size:22px;font-weight:800;color:{col}">{sg(ly["score"])}</span> '
            f'<span style="font-size:11px;color:{col}">{esc(bl(ly["band"]))}</span></div>{rows}</div>'
        )

    def detail_section_html(layers) -> str:
        """Fuld faktor-nedbrydning pr. lag — 1:1 med referenceguidens kolonner
        (Parameter/Vaerdi/Bidrag/Vaegt/Vaegtet). Rent rendering af d["layers"];
        ingen omberegning. print-color-adjust:exact saettes paa containeren (arves)."""
        INTRO = {
            "technical": ("Det tungeste lag (55 % af Kombineret). 22 faktorer i seks grupper, der "
                          "maaler om aktien er i en sund, handelbar optrend lige nu. Hver faktor "
                          "scorer -100..+100; Bidrag = raa signal, Vaegt = hvor meget den taeller, "
                          "Vaegtet = de to ganget."),
            "fundamental": "20 % af Kombineret. Maaler virksomhedens kvalitet, vaekst og vaerdiansaettelse.",
            "catalyst": ("25 % af Kombineret. Maaler analytiker-revisioner, earnings-naerhed/-risiko "
                         "og eksterne katalysatorer."),
        }
        TITLES = {"technical": "Teknisk", "fundamental": "Fundamental", "catalyst": "Katalysator"}

        def fcol(v):   # faktor-fortegnsfarve (taerskel +-5, jf. SPEC)
            return BULL if v > 5 else BEAR if v < -5 else MUT

        out = ['<div style="margin-top:10px;print-color-adjust:exact;-webkit-print-color-adjust:exact">'
               '<div style="font-size:16px;font-weight:800;border-bottom:2px solid #111;padding-bottom:4px">'
               'Detaljeret faktor-nedbrydning</div>']
        for key in ("technical", "fundamental", "catalyst"):
            ly = layers.get(key) or {}
            out.append(
                '<div style="margin-top:14px;background:#0f766e;color:#fff;border-radius:6px 6px 0 0;'
                'padding:8px 12px;display:flex;justify-content:space-between;align-items:center">'
                f'<b style="font-size:14px">{esc(TITLES[key])} &middot; {round(ly.get("weight",0)*100)}% af Kombineret</b>'
                f'<span style="font-weight:800;font-size:14px">{sg(ly.get("score",0))} '
                f'<span style="font-weight:600;font-size:12px">{esc(bl(ly.get("band","")))}</span></span></div>'
            )
            out.append(f'<div style="font-size:12px;color:#374151;padding:8px 12px;background:#f9fafb">'
                       f'{esc(INTRO[key])}</div>')
            for g in ly.get("groups", []):
                facs = g.get("factors", [])
                gw = sum((f.get("weight") or 0) for f in facs) * 100
                gcol = sc(g.get("score", 0))
                out.append(
                    '<table style="width:100%;border-collapse:collapse;font-size:12px"><thead>'
                    '<tr style="background:#ccfbf1">'
                    f'<th colspan="2" style="text-align:left;padding:5px 8px;color:#0f766e">{esc(g.get("name",""))} '
                    f'<span style="color:{MUT};font-weight:600">({gw:.1f}%)</span></th>'
                    f'<th colspan="3" style="text-align:right;padding:5px 8px;color:{gcol}">Gruppe {sg(g.get("score",0))}</th></tr>'
                    f'<tr style="border-bottom:1px solid #e5e7eb;color:{MUT}">'
                    '<th style="text-align:left;padding:3px 8px;font-weight:600">Parameter</th>'
                    '<th style="text-align:left;padding:3px 8px;font-weight:600">Vaerdi</th>'
                    '<th style="text-align:right;padding:3px 8px;font-weight:600">Bidrag</th>'
                    '<th style="text-align:right;padding:3px 8px;font-weight:600">Vaegt</th>'
                    '<th style="text-align:right;padding:3px 8px;font-weight:600">Vaegtet</th></tr></thead><tbody>'
                )
                for i, f in enumerate(facs):
                    bg = "#ffffff" if i % 2 == 0 else "#f9fafb"
                    sig = f.get("signal") or 0
                    wt  = (f.get("weight") or 0) * 100
                    wtd = f.get("weighted") or 0
                    raw = f.get("raw")
                    raw_s = esc(str(raw)) if raw is not None else "—"
                    out.append(
                        f'<tr style="background:{bg}">'
                        f'<td style="text-align:left;padding:3px 8px">{esc(f.get("name",""))}</td>'
                        f'<td style="text-align:left;padding:3px 8px;color:#374151">{raw_s}</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{fcol(sig)};font-weight:600">{sg(sig)}</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{MUT}">{wt:.1f}%</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{fcol(wtd)};font-weight:700">{sg(wtd,1)}</td></tr>'
                    )
                out.append('</tbody></table>')
            exc = ly.get("excluded") or []
            if exc:
                ex_txt = "; ".join(f'{esc(e.get("name",""))} ({esc(str(e.get("why","")))})' for e in exc)
                out.append(f'<div style="font-size:11px;color:{MUT};padding:6px 12px;background:#f9fafb">'
                           f'Ekskluderet: {ex_txt}</div>')
        out.append('</div>')
        return "".join(out)

    def drivers_html(title, col, items):
        rows = "".join(
            '<div style="display:flex;justify-content:space-between;font-size:12px;padding:2px 0">'
            f'<span style="color:#374151">{esc(it["name"])}</span>'
            f'<span style="color:{col};font-weight:700">{sg(it["contribution"],1)}</span></div>'
            for it in items
        ) or f'<div style="font-size:12px;color:{MUT}">Ingen.</div>'
        return f'<div style="flex:1"><div style="font-weight:700;color:{col};font-size:12px;margin-bottom:4px">{esc(title)}</div>{rows}</div>'

    def chip(lbl, val):
        return (f'<div style="border:1px solid #e5e7eb;border-radius:6px;padding:5px 9px">'
                f'<div style="font-size:9px;color:{MUT};text-transform:uppercase">{esc(lbl)}</div>'
                f'<div style="font-size:13px;font-weight:700">{esc(val)}</div></div>')

    info = d.get("info", {})
    chips = "".join([
        chip("EPS-vaekst", pf(info.get("eps_growth"))),
        chip("Dollar-vol", usd(info.get("dollar_vol"))),
        chip("Float", sh(info.get("float_shares"))),
        chip("Gap", pf(info.get("gap_pct"))),
        chip("Spread", pf(info.get("spread_pct"), 2)),
        chip("Dage til earnings", "—" if info.get("days_to_earnings") is None else f'{info["days_to_earnings"]}d'),
    ])

    ch = d.get("chart") or {}
    res = [l for l in ch.get("levels", []) if l["kind"] == "resistance"]
    sup = [l for l in ch.get("levels", []) if l["kind"] == "support"]
    sr_txt = "ingen i baandet - pris naer periodens top/bund" if not ch.get("levels") else "  ·  ".join(filter(None, [
        ("Modstand " + ", ".join(f'{l["price"]:.2f} ({l["touches"]}x)' for l in res)) if res else "",
        ("Stoette " + ", ".join(f'{l["price"]:.2f} ({l["touches"]}x)' for l in sup)) if sup else "",
    ]))
    candles = ", ".join(f'{esc(c["name"])} ({esc(c["when"])})' for c in ch.get("candles", [])) or "ingen tydelige moenstre"

    css = ("@page{margin:14mm}*{box-sizing:border-box}"
           "body{margin:0;background:#fff;color:#111;font-family:-apple-system,Segoe UI,Roboto,sans-serif}"
           ".bar{padding:8px 16px;background:#eee;font-size:13px;display:flex;gap:10px;align-items:center}"
           ".bar button{font-size:13px;padding:4px 12px;cursor:pointer}"
           ".wrap{padding:16px;max-width:920px;margin:0 auto;display:flex;flex-direction:column;gap:12px}"
           "@media print{.bar{display:none}}")
    t = esc(d["ticker"])
    co = esc(d.get("company") or "")
    co_html = f' <span style="font-size:14px;font-weight:600;color:{MUT}">{co}</span>' if co else ""
    price = f'${d["price"]:.2f}' if d.get("price") is not None else "—"
    import json as _json
    fbc = fb(d["final_band"])
    chart_block = (
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:12px;'
        'print-color-adjust:exact;-webkit-print-color-adjust:exact">'
        '<div id="swing-chart"></div>'
        f'<div style="font-size:12px;color:#374151;margin-top:8px"><b>Stoette/modstand:</b> {esc(sr_txt)}</div>'
        '<div style="margin-top:8px;padding:8px;background:#fef9c3;border-radius:6px;font-size:12px">'
        '&#128065; <b>Chart-moenster kraever menneskelig vurdering.</b> Flag, trekant, '
        'hoved-skulder, cup &amp; handle, range &mdash; kig paa charten. Resten er beregnet '
        'og tegnet automatisk.</div></div>'
    )
    chart_script = '<script>const CHART=' + _json.dumps(d.get("chart")) + ';' + SWING_CHART_JS + '</script>'
    detail_section = detail_section_html(d["layers"]) if detail else ""
    return (
        f'<!DOCTYPE html><html lang="da"><head><meta charset="utf-8"><title>Swing trading - {t}</title>'
        f'<style>{css}</style></head><body>'
        '<div class="bar">Gem som PDF: <button onclick="window.print()">Gem / Print</button>'
        '<span>(eller Ctrl+P -&gt; "Gem som PDF")</span></div><div class="wrap">'
        '<div style="display:flex;justify-content:space-between;align-items:center;border:1px solid #e5e7eb;border-radius:8px;padding:12px 16px">'
        f'<div><div style="font-size:24px;font-weight:800">{t}{co_html}</div><div style="color:{MUT};font-size:13px">{price}</div></div>'
        f'<div style="text-align:right"><div style="font-size:34px;font-weight:800;color:{fbc}">{sg(d["final"])}</div>'
        f'<div style="font-size:12px;font-weight:700;color:{fbc}">{esc(bl(d["final_band"]))}</div></div></div>'
        f'<div style="font-size:12px;color:{MUT}">Kombineret <b>{sg(d["combined"])}</b> &times; Tradability-gate <b>{d["gate"]:.2f}</b> &rarr; Samlet <b style="color:{fbc}">{sg(d["final"])}</b></div>'
        '<div style="display:flex;gap:10px;flex-wrap:wrap">'
        f'{layer_html("Teknisk", d["layers"]["technical"])}{layer_html("Fundamental", d["layers"]["fundamental"])}{layer_html("Katalysator", d["layers"]["catalyst"])}</div>'
        '<div style="display:flex;gap:16px;border:1px solid #e5e7eb;border-radius:8px;padding:12px">'
        f'{drivers_html("Medvind", BULL, d["drivers"]["positive"])}<div style="width:1px;background:#e5e7eb"></div>{drivers_html("Modvind", BEAR, d["drivers"]["negative"])}</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">{chips}</div>'
        f'{chart_block}{detail_section}'
        f'</div>{chart_script}</body></html>'
    )


@app.get("/swing/report.html", response_class=HTMLResponse)
async def swing_report_html(ticker: str, sr: float | None = None,
                            pattern: float | None = None, candle: float | None = None,
                            detail: int = 0):
    """Den paene rapport som printbar HTML-side til EKSTERN browser (PDF-knappen).
    Print sker i browserens eget vindue -> ingen print-overlay paa app-vinduet, saa
    man ikke ved et uheld lukker Trading Dash via app'ens X."""
    t = (ticker or "").strip().upper()
    if not t:
        raise HTTPException(status_code=400, detail="Mangler ticker")
    try:
        d = await asyncio.to_thread(_run_swing_json, t, sr, pattern, candle)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Swing trading-rapport fejlede: {e}")
    return HTMLResponse(_swing_report_html(d, detail=bool(detail)))


# Vanilla-JS draw-script til intradag-PDF'en — PORT af IntradagChart.tsx (samme
# skalaer/candlestick/polylinje/niveau/volumen-logik) men med PRINT-farver (lyst
# tema) og fast 900x460 viewBox. Browseren tegner SVG'en naar report.html aabnes ->
# med i print. CHART injiceres som JSON foran scriptet i _intradag_report_html.
INTRADAG_CHART_JS = r"""(function(){
  if(!CHART || !CHART.bars || !CHART.bars.length) return;
  var W=900, H=460, TOP=22, BOTM=46, XL=14, GUTTER=58, VOL_H=70, PILL_H=20;
  var BULL="#15803d", BEAR="#b91c1c", GRID="#e5e7eb", MUT="#6b7280", PILLBG="#fff", TAGBG="#374151";
  var OVS={vwap:{c:"#7c3aed",w:1.6,o:0.95,d:"0"}, ema9:{c:"#d97706",w:1.4,o:0.9,d:"0"}, ema20:{c:"#2563eb",w:1.4,o:0.9,d:"0"}, vw_upper:{c:"#7c3aed",w:0.8,o:0.25,d:"4 4"}, vw_lower:{c:"#7c3aed",w:0.8,o:0.25,d:"4 4"}};
  function pillW(t){return t.length*6.5+14;}
  function esc(t){return String(t).replace(/[&<>"]/g,function(ch){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[ch];});}
  function lvCol(k){return (k==="orb_high"||k==="orb_low")?"#b45309":(k==="hod")?"#6b7280":"#475569";}
  function lvDash(k){return (k==="orb_high"||k==="orb_low")?"5 4":(k==="hod")?"2 3":"0";}

  var bars=CHART.bars, n=bars.length, xr=W-GUTTER;
  var priceBot=H-BOTM-VOL_H, volTop=priceBot+8, volBot=H-BOTM;
  var ov=CHART.overlays||{}, lvs=CHART.levels||[];
  var lo=Infinity, hi=-Infinity, i, b, v, arr;
  for(i=0;i<n;i++){b=bars[i]; if(b.l<lo)lo=b.l; if(b.h>hi)hi=b.h;}
  lvs.forEach(function(L){if(L.price<lo)lo=L.price; if(L.price>hi)hi=L.price;});
  ["vwap","ema9","ema20"].forEach(function(key){arr=ov[key]||[]; for(i=0;i<arr.length;i++){v=arr[i]; if(v!=null){if(v<lo)lo=v; if(v>hi)hi=v;}}});
  if(!isFinite(lo)||!isFinite(hi)){lo=0;hi=1;}
  var pad=(hi-lo)*0.06||1, pmin=lo-pad, pmax=hi+pad;
  var slot=(xr-XL)/Math.max(n,1);
  function x(idx){return XL+(idx+0.5)*slot;}
  function y(p){return priceBot-(p-pmin)/(pmax-pmin)*(priceBot-TOP);}
  var vmax=0; for(i=0;i<n;i++){if((bars[i].v||0)>vmax)vmax=bars[i].v||0;}
  function vy(vv){return volBot-(vmax>0?vv/vmax:0)*(volBot-volTop);}
  function segs(a){var out=[],cur=[],j; a=a||[]; for(j=0;j<a.length;j++){var vv=a[j]; if(vv==null){if(cur.length>1)out.push(cur.join(" ")); cur=[];} else cur.push(x(j).toFixed(1)+","+y(vv).toFixed(1));} if(cur.length>1)out.push(cur.join(" ")); return out;}

  var ticks=[], span=pmax-pmin;
  var step=span>400?100:span>200?50:span>80?25:span>30?10:span>12?5:span>5?2:span>2?1:0.5;
  for(var pp0=Math.ceil(pmin/step)*step;pp0<pmax;pp0+=step)ticks.push(pp0);
  var tt=[], everyN=Math.max(1,Math.round(n/8));
  bars.forEach(function(bb,ii){if(ii%everyN===0)tt.push({x:x(ii),label:bb.t});});

  var lvls=lvs.map(function(L){var txt=L.label+" "+L.price.toFixed(2); return {color:lvCol(L.kind),dash:lvDash(L.kind),lineY:y(L.price),txt:txt,w:pillW(txt)};}).sort(function(a,b){return a.lineY-b.lineY;});
  var lp=[], lastB=-Infinity;
  lvls.forEach(function(it){var y0=it.lineY-PILL_H/2; if(y0<lastB+2)y0=lastB+2; y0=Math.max(2,Math.min(y0,priceBot-PILL_H)); lastB=y0+PILL_H; lp.push({color:it.color,dash:it.dash,lineY:it.lineY,txt:it.txt,x0:xr-it.w,x1:xr,y0:y0});});

  var bw=Math.min(7, slot*0.6);
  var s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" xmlns="http://www.w3.org/2000/svg" style="display:block">';
  ticks.forEach(function(p){
    s+='<line x1="'+XL+'" y1="'+y(p)+'" x2="'+xr+'" y2="'+y(p)+'" stroke="'+GRID+'" stroke-width="0.5"/>';
    s+='<text x="'+(xr+5)+'" y="'+(y(p)+4)+'" font-size="12" fill="'+MUT+'">'+p.toFixed(p<10?1:0)+'</text>';
  });
  lp.forEach(function(it){ s+='<line x1="'+XL+'" y1="'+it.lineY+'" x2="'+xr+'" y2="'+it.lineY+'" stroke="'+it.color+'" stroke-width="1.2" stroke-dasharray="'+it.dash+'" opacity="0.85"/>'; });
  bars.forEach(function(bb,ii){
    var up=bb.c>=bb.o, col=up?BULL:BEAR, cx=x(ii);
    var yb=y(Math.max(bb.o,bb.c)), hb=Math.max(Math.abs(y(bb.o)-y(bb.c)),1.2);
    s+='<line x1="'+cx+'" y1="'+y(bb.h)+'" x2="'+cx+'" y2="'+y(bb.l)+'" stroke="'+col+'" stroke-width="1"/>';
    s+='<rect x="'+(cx-bw/2)+'" y="'+yb+'" width="'+bw+'" height="'+hb+'" fill="'+col+'" rx="1"/>';
  });
  ["vw_upper","vw_lower","vwap","ema20","ema9"].forEach(function(key){
    var st=OVS[key]; segs(ov[key]).forEach(function(pts){ s+='<polyline points="'+pts+'" fill="none" stroke="'+st.c+'" stroke-width="'+st.w+'" opacity="'+st.o+'" stroke-dasharray="'+st.d+'" stroke-linejoin="round" stroke-linecap="round"/>'; });
  });
  var yc=y(CHART.current_price);
  s+='<line x1="'+XL+'" y1="'+yc+'" x2="'+xr+'" y2="'+yc+'" stroke="'+MUT+'" stroke-width="0.5" stroke-dasharray="2 3"/>';
  s+='<rect x="'+(xr+2)+'" y="'+(yc-10)+'" width="'+(GUTTER-5)+'" height="20" rx="4" fill="'+TAGBG+'"/>';
  s+='<text x="'+(xr+2+(GUTTER-5)/2)+'" y="'+(yc+4)+'" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">'+CHART.current_price.toFixed(2)+'</text>';
  lp.forEach(function(it){
    s+='<rect x="'+it.x0+'" y="'+it.y0+'" width="'+(it.x1-it.x0)+'" height="'+PILL_H+'" rx="5" fill="'+PILLBG+'" stroke="'+it.color+'" stroke-width="1.1"/>';
    s+='<text x="'+((it.x0+it.x1)/2)+'" y="'+(it.y0+14)+'" text-anchor="middle" font-size="11" font-weight="600" fill="'+it.color+'">'+esc(it.txt)+'</text>';
  });
  s+='<line x1="'+XL+'" y1="'+(volTop-3)+'" x2="'+xr+'" y2="'+(volTop-3)+'" stroke="'+GRID+'" stroke-width="0.5"/>';
  bars.forEach(function(bb,ii){ var up=bb.c>=bb.o, vt=vy(bb.v); s+='<rect x="'+(x(ii)-bw/2)+'" y="'+vt+'" width="'+bw+'" height="'+Math.max(volBot-vt,0.5)+'" fill="'+(up?BULL:BEAR)+'" opacity="0.45"/>'; });
  tt.forEach(function(tk){
    s+='<line x1="'+tk.x+'" y1="'+volBot+'" x2="'+tk.x+'" y2="'+(volBot+4)+'" stroke="'+MUT+'" stroke-width="0.5"/>';
    s+='<text x="'+tk.x+'" y="'+(volBot+18)+'" text-anchor="middle" font-size="12" fill="'+MUT+'">'+esc(tk.label)+'</text>';
  });
  s+='<text x="'+XL+'" y="13" font-size="11" font-weight="700" fill="#7c3aed">VWAP</text>';
  s+='<text x="'+(XL+44)+'" y="13" font-size="11" font-weight="700" fill="#d97706">EMA9</text>';
  s+='<text x="'+(XL+88)+'" y="13" font-size="11" font-weight="700" fill="#2563eb">EMA20</text>';
  s+='</svg>';
  document.getElementById("intradag-chart").innerHTML=s;
})();"""


def _intradag_report_html(d: dict, detail: bool = False) -> str:
    """Render intradag report_to_json som en paen, print-venlig HTML-side (lyst tema),
    aabnet i EKSTERN browser fra PDF-knappen. Spejler _swing_report_html.

    detail=True tilfoejer fuld faktor-nedbrydning pr. lag (Parameter/Vaerdi/Bidrag/Vaegt/
    Vaegtet) EFTER opsummeringen — opt-in, spejler swing-detaljen. detail=False (default)
    = normal rapport uaendret. Haandterer pending katalysator-lag (score=None)."""
    import html as _html
    esc = _html.escape
    BULL, BEAR, NEU, MUT = "#15803d", "#b91c1c", "#b45309", "#6b7280"
    # Intradag-baand (technical_intraday._band, brugt for baade slut+lag) -> pyntet dansk.
    BAND = {
        "Staerk intradag-opstilling": "Stærk", "Medvind": "Medvind",
        "Neutral / blandet": "Neutral", "Svag": "Svag",
        "Fraraades (intradag)": "Frarådes", "ingen data": "Afventer",
    }

    def sc(v): return BULL if v >= 15 else BEAR if v <= -15 else NEU
    def bandcolor(b):
        if b is None: return MUT
        if b.startswith("Staerk"): return BULL          # "Staerk intradag-opstilling"
        if b == "Medvind": return BULL
        if b.startswith("Neutral"): return NEU          # "Neutral / blandet"
        if b.startswith("Svag") or b.startswith("Fraraades"): return BEAR
        return MUT                                       # "ingen data" (pending katalysator)
    def bl(b): return BAND.get(b, b) if b is not None else "—"
    def sg(v, dd=0): return ("+" if v > 0 else "") + f"{v:.{dd}f}"
    def usd(v):
        if v is None: return "—"
        if v >= 1e9: return f"${v/1e9:.1f}B"
        if v >= 1e6: return f"${v/1e6:.1f}M"
        return f"${round(v):,}"
    def sh(v):
        if v is None: return "—"
        if v >= 1e9: return f"{v/1e9:.1f}B"
        if v >= 1e6: return f"{v/1e6:.1f}M"
        return f"{v:,}"
    def spread(v): return "—" if v is None else f"{v*100:.2f}%"   # decimal -> %

    def layer_html(title, ly):
        pending = ly.get("score") is None
        rows = ""
        for g in ly["groups"]:
            col, pct = sc(g["score"]), min(abs(g["score"]), 100)
            side = "left:50%" if g["score"] >= 0 else f"left:{50 - pct/2}%"
            rows += (
                '<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px;color:#374151">'
                f'<span>{esc(g["name"])}</span><span style="color:{col};font-weight:700">{sg(g["score"])}</span></div>'
                '<div style="position:relative;height:6px;background:#e5e7eb;border-radius:3px;margin-top:3px">'
                '<div style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:#9ca3af"></div>'
                f'<div style="position:absolute;top:0;bottom:0;{side};width:{pct/2}%;background:{col};border-radius:3px"></div>'
                '</div></div>'
            )
        wpct = "—" if ly.get("weight") is None else f'{round(ly["weight"]*100)}%'
        if pending:
            head = f'<span style="font-size:14px;font-weight:700;color:{MUT}">Afventer (trin 7)</span>'
            body = rows or f'<div style="font-size:12px;color:{MUT};margin-top:6px">Realtids-news/halts ikke aktiveret endnu.</div>'
        else:
            col = sc(ly["score"])
            head = (f'<span style="font-size:22px;font-weight:800;color:{col}">{sg(ly["score"])}</span> '
                    f'<span style="font-size:11px;color:{col}">{esc(bl(ly["band"]))}</span>')
            body = rows
        return (
            '<div style="flex:1;min-width:200px;border:1px solid #e5e7eb;border-radius:8px;padding:12px">'
            f'<div style="display:flex;justify-content:space-between"><b style="font-size:13px">{esc(title)}</b>'
            f'<span style="font-size:11px;color:{MUT}">{wpct}</span></div>'
            f'<div style="margin:4px 0">{head}</div>{body}</div>'
        )

    def detail_section_html(layers) -> str:
        """Fuld faktor-nedbrydning pr. lag — spejler swing-detaljen, men intradag-lag
        (teknisk/forsyning/katalysator) og haandterer pending katalysator (score=None)."""
        INTRO = {
            "technical": ("Det tungeste lag. Faktorer i grupper der maaler intradag-momentum og "
                          "trend-sundhed lige nu. Hver faktor scorer -100..+100; Bidrag = raa "
                          "signal, Vaegt = hvor meget den taeller, Vaegtet = de to ganget."),
            "supply": "Float-centreret forsynings-lag — lav float = mere eksplosiv intradag-bevaegelse.",
            "catalyst": "Realtids-katalysatorer: friske nyheder og handels-halts.",
        }
        TITLES = {"technical": "Teknisk", "supply": "Forsyning", "catalyst": "Katalysator"}

        def fcol(v):   # faktor-fortegnsfarve (taerskel +-5, jf. swing)
            return BULL if v > 5 else BEAR if v < -5 else MUT

        out = ['<div style="margin-top:10px;print-color-adjust:exact;-webkit-print-color-adjust:exact">'
               '<div style="font-size:16px;font-weight:800;border-bottom:2px solid #111;padding-bottom:4px">'
               'Detaljeret faktor-nedbrydning</div>']
        for key in ("technical", "supply", "catalyst"):
            ly = layers.get(key) or {}
            pending = ly.get("score") is None
            wpct = "—" if ly.get("weight") is None else f'{round(ly["weight"]*100)}%'
            score_html = ('<span style="font-weight:700;font-size:12px">Afventer</span>' if pending
                          else f'{sg(ly.get("score",0))} <span style="font-weight:600;font-size:12px">'
                               f'{esc(bl(ly.get("band","")))}</span>')
            out.append(
                '<div style="margin-top:14px;background:#0f766e;color:#fff;border-radius:6px 6px 0 0;'
                'padding:8px 12px;display:flex;justify-content:space-between;align-items:center">'
                f'<b style="font-size:14px">{esc(TITLES[key])} &middot; {wpct} af Kombineret</b>'
                f'<span style="font-weight:800;font-size:14px">{score_html}</span></div>'
            )
            out.append(f'<div style="font-size:12px;color:#374151;padding:8px 12px;background:#f9fafb">'
                       f'{esc(INTRO[key])}</div>')
            groups = ly.get("groups", [])
            if pending and not groups:
                out.append(f'<div style="font-size:11px;color:{MUT};padding:6px 12px;background:#f9fafb">'
                           f'Realtids-news/halts ikke aktiveret endnu.</div>')
            for g in groups:
                facs = g.get("factors", [])
                gw = sum((f.get("weight") or 0) for f in facs) * 100
                gscore = g.get("score") or 0
                gcol = sc(gscore)
                out.append(
                    '<table style="width:100%;border-collapse:collapse;font-size:12px"><thead>'
                    '<tr style="background:#ccfbf1">'
                    f'<th colspan="2" style="text-align:left;padding:5px 8px;color:#0f766e">{esc(g.get("name",""))} '
                    f'<span style="color:{MUT};font-weight:600">({gw:.1f}%)</span></th>'
                    f'<th colspan="3" style="text-align:right;padding:5px 8px;color:{gcol}">Gruppe {sg(gscore)}</th></tr>'
                    f'<tr style="border-bottom:1px solid #e5e7eb;color:{MUT}">'
                    '<th style="text-align:left;padding:3px 8px;font-weight:600">Parameter</th>'
                    '<th style="text-align:left;padding:3px 8px;font-weight:600">Vaerdi</th>'
                    '<th style="text-align:right;padding:3px 8px;font-weight:600">Bidrag</th>'
                    '<th style="text-align:right;padding:3px 8px;font-weight:600">Vaegt</th>'
                    '<th style="text-align:right;padding:3px 8px;font-weight:600">Vaegtet</th></tr></thead><tbody>'
                )
                for i, f in enumerate(facs):
                    bg = "#ffffff" if i % 2 == 0 else "#f9fafb"
                    sig = f.get("signal") or 0
                    wt  = (f.get("weight") or 0) * 100
                    wtd = f.get("weighted") or 0
                    raw = f.get("raw")
                    raw_s = esc(str(raw)) if raw is not None else "—"
                    out.append(
                        f'<tr style="background:{bg}">'
                        f'<td style="text-align:left;padding:3px 8px">{esc(f.get("name",""))}</td>'
                        f'<td style="text-align:left;padding:3px 8px;color:#374151">{raw_s}</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{fcol(sig)};font-weight:600">{sg(sig)}</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{MUT}">{wt:.1f}%</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{fcol(wtd)};font-weight:700">{sg(wtd,1)}</td></tr>'
                    )
                out.append('</tbody></table>')
            exc = ly.get("excluded") or []
            if exc:
                ex_txt = "; ".join(f'{esc(e.get("name",""))} ({esc(str(e.get("why","")))})' for e in exc)
                out.append(f'<div style="font-size:11px;color:{MUT};padding:6px 12px;background:#f9fafb">'
                           f'Ekskluderet: {ex_txt}</div>')
        out.append('</div>')
        return "".join(out)

    def drivers_html(title, col, items):
        rows = "".join(
            '<div style="display:flex;justify-content:space-between;font-size:12px;padding:2px 0">'
            f'<span style="color:#374151">{esc(it["name"])}</span>'
            f'<span style="color:{col};font-weight:700">{sg(it["contribution"],1)}</span></div>'
            for it in items
        ) or f'<div style="font-size:12px;color:{MUT}">Ingen.</div>'
        return f'<div style="flex:1"><div style="font-weight:700;color:{col};font-size:12px;margin-bottom:4px">{esc(title)}</div>{rows}</div>'

    def chip(lbl, val):
        return (f'<div style="border:1px solid #e5e7eb;border-radius:6px;padding:5px 9px">'
                f'<div style="font-size:9px;color:{MUT};text-transform:uppercase">{esc(lbl)}</div>'
                f'<div style="font-size:13px;font-weight:700">{esc(val)}</div></div>')

    info = d.get("info", {})
    gi = info.get("gate_inputs", {}) or {}
    fl = info.get("float_shares")
    float_val = (sh(fl) + (f' ({info["float_pct"]:.0f}%)' if info.get("float_pct") is not None else "")) if fl is not None else "—"
    chips = "".join([
        chip("Float", float_val),
        chip("Spaend", spread(info.get("spread_pct"))),
        chip("Dollar-vol", usd(gi.get("dollar_vol"))),
        chip("ADV (20D)", sh(gi.get("adv20"))),
    ])

    css = ("@page{margin:14mm}*{box-sizing:border-box;print-color-adjust:exact;-webkit-print-color-adjust:exact}"
           "body{margin:0;background:#fff;color:#111;font-family:-apple-system,Segoe UI,Roboto,sans-serif}"
           ".bar{padding:8px 16px;background:#eee;font-size:13px;display:flex;gap:10px;align-items:center}"
           ".bar button{font-size:13px;padding:4px 12px;cursor:pointer}"
           ".wrap{padding:16px;max-width:920px;margin:0 auto;display:flex;flex-direction:column;gap:12px}"
           "@media print{.bar{display:none}}")
    t = esc(d["symbol"])
    tf = esc(d.get("timeframe") or "")
    tf_html = f' <span style="font-size:14px;font-weight:600;color:{MUT}">{tf}</span>' if tf else ""
    price = f'${d["price"]:.2f}' if d.get("price") is not None else "—"
    fbc = bandcolor(d.get("final_band"))
    finaltxt = "—" if d.get("final") is None else sg(d["final"])
    combtxt = "—" if d.get("combined") is None else sg(d["combined"])
    chart = d.get("chart")
    has_chart = bool(chart and chart.get("bars"))
    chart_block = '<div id="intradag-chart" style="margin:6px 0"></div>' if has_chart else ''
    chart_script = ('<script>const CHART=' + _json.dumps(chart) + ';' + INTRADAG_CHART_JS + '</script>') if has_chart else ''
    detail_section = detail_section_html(d["layers"]) if detail else ""
    return (
        f'<!DOCTYPE html><html lang="da"><head><meta charset="utf-8"><title>Day trading - {t}</title>'
        f'<style>{css}</style></head><body>'
        '<div class="bar">Gem som PDF: <button onclick="window.print()">Gem / Print</button>'
        '<span>(eller Ctrl+P -&gt; "Gem som PDF")</span></div><div class="wrap">'
        '<div style="display:flex;justify-content:space-between;align-items:center;border:1px solid #e5e7eb;border-radius:8px;padding:12px 16px">'
        f'<div><div style="font-size:24px;font-weight:800">{t}{tf_html}</div><div style="color:{MUT};font-size:13px">{price}</div></div>'
        f'<div style="text-align:right"><div style="font-size:34px;font-weight:800;color:{fbc}">{finaltxt}</div>'
        f'<div style="font-size:12px;font-weight:700;color:{fbc}">{esc(bl(d.get("final_band")))}</div></div></div>'
        f'<div style="font-size:12px;color:{MUT}">Kombineret <b>{combtxt}</b> &times; Handelbarheds-gate <b>{d["gate"]:.3f}</b> &rarr; Samlet <b style="color:{fbc}">{finaltxt}</b></div>'
        '<div style="display:flex;gap:10px;flex-wrap:wrap">'
        f'{layer_html("Teknisk", d["layers"]["technical"])}{layer_html("Forsyning", d["layers"]["supply"])}{layer_html("Katalysator", d["layers"]["catalyst"])}</div>'
        '<div style="display:flex;gap:16px;border:1px solid #e5e7eb;border-radius:8px;padding:12px">'
        f'{drivers_html("Medvind", BULL, d["drivers"]["positive"])}<div style="width:1px;background:#e5e7eb"></div>{drivers_html("Modvind", BEAR, d["drivers"]["negative"])}</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">{chips}</div>'
        f'{chart_block}{detail_section}'
        f'</div>{chart_script}</body></html>'
    )


@app.get("/intradag/report.html", response_class=HTMLResponse)
async def intradag_report_html(symbol: str, timeframe: str = "5 mins",
                               sr: float | None = None,        # LAERDOM Fix A: default None, ALDRIG 0
                               pattern: float | None = None,
                               candle: float | None = None,
                               detail: int = 0):
    """Pen, print-venlig HTML for EET symbol (lyst tema), aabnet i EKSTERN browser fra
    Print-knappen (print sker i browserens eget vindue -> ingen overlay paa app'en, saa
    man ikke ved et uheld lukker Trading Dash via app'ens X). Samme scoring som skaermen;
    overlay-FRA (None) = identisk med skaerm. await DIREKTE paa delt ib (som 5a)."""
    import intradag_report
    s = (symbol or "").strip().upper()
    if not s:
        raise HTTPException(status_code=400, detail="Mangler symbol")
    await strategy_manager.connect_ibkr(paper_trading=True)
    conn = strategy_manager.get_ibkr()
    if conn is None or not conn.connected:
        return HTMLResponse("<h2>IBKR ikke forbundet (er TWS/Gateway logget ind paa 7497?)</h2>",
                            status_code=503)
    try:
        report = await intradag_report.compute_intradag_report(
            conn.ib, s, timeframe=timeframe, sr=sr, chart_pattern=pattern, candlestick=candle)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Day trading-rapport fejlede: {e}")
    return HTMLResponse(_intradag_report_html(intradag_report.report_to_json(report), detail=bool(detail)))


# Vanilla-JS uge-trend-chart til buyhold-PDF'en — candlesticks + 10/30/40-uge MA + ATH
# (spejler BuyHoldChart.tsx + Pine 'Trend-kontekst'-tvillingen). Ingen pills/S/R. CHART
# injiceres som JSON foran scriptet i _buyhold_report_html.
BUYHOLD_CHART_JS = r"""(function(){
  if(!CHART || !CHART.bars || !CHART.bars.length) return;
  var W=900, H=380, TOP=24, BOTM=44, XL=14, GUTTER=62;
  var BULL="#15803d", BEAR="#b91c1c", MUT="#6b7280", GRID="#e5e7eb", TAGBG="#374151";
  var MA10="#34c759", MA30="#ff9500", MA40="#0a84ff", ATHC="#8e8e93";
  var bars=CHART.bars, n=bars.length, BOT=H-BOTM, xr=W-GUTTER;
  var lo=Infinity, hi=-Infinity, i, b;
  for(i=0;i<n;i++){b=bars[i]; if(b.l<lo)lo=b.l; if(b.h>hi)hi=b.h;}
  if(CHART.ath>hi)hi=CHART.ath;
  var pad=(hi-lo)*0.06||1, pmin=lo-pad, pmax=hi+pad;
  var slot=(xr-XL)/Math.max(n,1);
  function x(idx){return XL+(idx+0.5)*slot;}
  function y(p){return BOT-(p-pmin)/(pmax-pmin)*(BOT-TOP);}
  var s='<svg viewBox="0 0 '+W+' '+H+'" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">';
  var span=pmax-pmin, step=span>400?100:span>200?50:span>80?25:span>30?10:5, p;
  for(p=Math.ceil(pmin/step)*step;p<pmax;p+=step){
    s+='<line x1="'+XL+'" y1="'+y(p)+'" x2="'+xr+'" y2="'+y(p)+'" stroke="'+GRID+'" stroke-width="0.5"/>';
    s+='<text x="'+(xr+4)+'" y="'+(y(p)+4)+'" font-size="13" fill="'+MUT+'">'+p+'</text>';
  }
  var prevY="";
  bars.forEach(function(bb,ii){
    var yr=bb.t.slice(0,4);
    if(yr!==prevY){
      s+='<line x1="'+x(ii)+'" y1="'+TOP+'" x2="'+x(ii)+'" y2="'+(BOT+4)+'" stroke="'+MUT+'" stroke-width="0.5"/>';
      s+='<text x="'+x(ii)+'" y="'+(BOT+20)+'" text-anchor="middle" font-size="14" fill="'+MUT+'">'+yr+'</text>';
      prevY=yr;
    }
  });
  s+='<line x1="'+XL+'" y1="'+y(CHART.ath)+'" x2="'+xr+'" y2="'+y(CHART.ath)+'" stroke="'+ATHC+'" stroke-width="1.2" stroke-dasharray="5 4"/>';
  s+='<text x="'+(XL+4)+'" y="'+(y(CHART.ath)-4)+'" font-size="12" fill="'+ATHC+'">ATH '+CHART.ath.toFixed(2)+'</text>';
  var bw=Math.min(6, slot*0.6);
  bars.forEach(function(bb,ii){
    var up=bb.c>=bb.o, col=up?BULL:BEAR, cx=x(ii);
    var yb=y(Math.max(bb.o,bb.c)), hb=Math.max(Math.abs(y(bb.o)-y(bb.c)),1);
    s+='<line x1="'+cx+'" y1="'+y(bb.h)+'" x2="'+cx+'" y2="'+y(bb.l)+'" stroke="'+col+'" stroke-width="0.8"/>';
    s+='<rect x="'+(cx-bw/2)+'" y="'+yb+'" width="'+bw+'" height="'+hb+'" fill="'+col+'"/>';
  });
  function poly(arr,color){
    if(!arr)return; var d="", started=false, ii;
    for(ii=0;ii<arr.length;ii++){ if(arr[ii]==null)continue; d+=(started?"L":"M")+x(ii)+" "+y(arr[ii])+" "; started=true; }
    if(d) s+='<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="1.5"/>';
  }
  poly(CHART.ma40, MA40); poly(CHART.ma30, MA30); poly(CHART.ma10, MA10);
  var yc=y(CHART.current_price);
  s+='<line x1="'+XL+'" y1="'+yc+'" x2="'+xr+'" y2="'+yc+'" stroke="'+MUT+'" stroke-width="0.5" stroke-dasharray="2 3"/>';
  s+='<rect x="'+(xr+2)+'" y="'+(yc-11)+'" width="'+(GUTTER-6)+'" height="22" rx="4" fill="'+TAGBG+'"/>';
  s+='<text x="'+(xr+2+(GUTTER-6)/2)+'" y="'+(yc+5)+'" text-anchor="middle" font-size="14" font-weight="700" fill="#fff">'+CHART.current_price.toFixed(2)+'</text>';
  var lx=XL+4, ly=TOP+12;
  function leg(col,txt){ s+='<line x1="'+lx+'" y1="'+ly+'" x2="'+(lx+16)+'" y2="'+ly+'" stroke="'+col+'" stroke-width="2"/>'; s+='<text x="'+(lx+20)+'" y="'+(ly+4)+'" font-size="12" fill="'+MUT+'">'+txt+'</text>'; lx+=70; }
  leg(MA10,"10u"); leg(MA30,"30u"); leg(MA40,"40u");
  s+='</svg>';
  document.getElementById("buyhold-chart").innerHTML=s;
})();"""


# ── Buy-and-Hold rapport (kopi af swing-renderen; 4 lag + gate-nedbrydning + OE) ──
def _buyhold_report_html(d: dict, detail: bool = False) -> str:
    """Render buyhold_report.report_to_json som print-venlig HTML (lyst tema). Spejler
    _swing_report_html: 4 lag-kort, gate-blok med 'hvorfor', Owner-Earnings-panel,
    langsigtede tiles, header-flag. detail=True -> fuld faktor-nedbrydning (4 lag)."""
    import html as _html
    esc = _html.escape
    BULL, BEAR, NEU, MUT = "#15803d", "#b91c1c", "#b45309", "#6b7280"
    TITLES = {"quality": "Kvalitet", "growth": "Vaekst & holdbarhed",
              "valuation": "Vaerdiansaettelse", "trend": "Langsigtet trend"}
    ORDER = ("quality", "growth", "valuation", "trend")

    def sc(v): return BULL if v >= 15 else BEAR if v <= -15 else NEU

    def fb(b):
        b = b or ""
        if b.startswith("STAERK"): return BULL
        if b.startswith("EGNET"): return NEU
        if b.startswith("SVAG") or b.startswith("FRARAADES"): return BEAR
        return MUT

    def sg(v, dd=0): return ("+" if v > 0 else "") + f"{v:.{dd}f}"

    def usd(v):
        if v is None: return "—"
        if abs(v) >= 1e9: return f"${v/1e9:.1f}B"
        if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
        return f"${v:,.0f}"

    def pct(v, dd=1, scale=1.0): return "—" if v is None else f"{v*scale:.{dd}f}%"

    def layer_card(key):
        ly = d["layers"][key]
        rows = ""
        for g in ly["groups"]:
            col, p = sc(g["score"]), min(abs(g["score"]), 100)
            side = "left:50%" if g["score"] >= 0 else f"left:{50 - p/2}%"
            rows += (
                '<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:12px;color:#374151">'
                f'<span>{esc(g["name"])}</span><span style="color:{col};font-weight:700">{sg(g["score"])}</span></div>'
                '<div style="position:relative;height:6px;background:#e5e7eb;border-radius:3px;margin-top:3px">'
                '<div style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:#9ca3af"></div>'
                f'<div style="position:absolute;top:0;bottom:0;{side};width:{p/2}%;background:{col};border-radius:3px"></div></div></div>'
            )
        empty = not ly.get("groups")            # ingen faktor-grupper = intet scoret
        head_val = "—" if empty else sg(ly["score"])
        head_col = MUT if empty else sc(ly["score"])
        head_band = "ingen data" if empty else esc(ly["band"])
        return (
            '<div style="flex:1;min-width:210px;border:1px solid #e5e7eb;border-radius:8px;padding:12px">'
            f'<div style="display:flex;justify-content:space-between"><b style="font-size:13px">{esc(TITLES[key])}</b>'
            f'<span style="font-size:11px;color:{MUT}">{round(ly["weight"]*100)}%</span></div>'
            f'<div style="margin:4px 0"><span style="font-size:22px;font-weight:800;color:{head_col}">{head_val}</span> '
            f'<span style="font-size:11px;color:{head_col}">{head_band}</span></div>{rows}</div>'
        )

    def fcol(v): return BULL if v > 5 else BEAR if v < -5 else MUT

    def detail_section():
        INTRO = {
            "quality": "35 % af helheden. Forretningens kvalitet: afkast paa kapital, balance-soliditet, cash flow-kvalitet.",
            "growth": "25 %. Vaekst (oms/EPS/FCF) og dens holdbarhed (konsistens, margin-trend, reinvesteringsafkast).",
            "valuation": "25 %. Hvad koeber jeg afkast for — multipler + yields (Owner-Earnings yield er hovedfaktoren).",
            "trend": "15 %. Langsigtet teknisk: sekulaer trend (40-uge/200-dag MA), relativ styrke, stabilitet.",
        }
        out = ['<div style="margin-top:10px;print-color-adjust:exact;-webkit-print-color-adjust:exact">'
               '<div style="font-size:16px;font-weight:800;border-bottom:2px solid #111;padding-bottom:4px">'
               'Detaljeret faktor-nedbrydning</div>']
        for key in ORDER:
            ly = d["layers"][key]
            out.append(
                '<div style="margin-top:14px;background:#0f766e;color:#fff;border-radius:6px 6px 0 0;'
                'padding:8px 12px;display:flex;justify-content:space-between;align-items:center">'
                f'<b style="font-size:14px">{esc(TITLES[key])} &middot; {round(ly["weight"]*100)}% af helheden</b>'
                f'<span style="font-weight:800;font-size:14px">{sg(ly["score"])} '
                f'<span style="font-weight:600;font-size:12px">{esc(ly["band"])}</span></span></div>'
            )
            out.append(f'<div style="font-size:12px;color:#374151;padding:8px 12px;background:#f9fafb">{esc(INTRO[key])}</div>')
            for g in ly["groups"]:
                facs = g["factors"]
                gw = sum((fa.get("weight") or 0) for fa in facs) * 100
                out.append(
                    '<table style="width:100%;border-collapse:collapse;font-size:12px"><thead>'
                    '<tr style="background:#ccfbf1">'
                    f'<th colspan="2" style="text-align:left;padding:5px 8px;color:#0f766e">{esc(g["name"])} '
                    f'<span style="color:{MUT};font-weight:600">({gw:.1f}%)</span></th>'
                    f'<th colspan="3" style="text-align:right;padding:5px 8px;color:{sc(g["score"])}">Gruppe {sg(g["score"])}</th></tr>'
                    f'<tr style="border-bottom:1px solid #e5e7eb;color:{MUT}">'
                    '<th style="text-align:left;padding:3px 8px">Parameter</th><th style="text-align:left;padding:3px 8px">Vaerdi</th>'
                    '<th style="text-align:right;padding:3px 8px">Bidrag</th><th style="text-align:right;padding:3px 8px">Vaegt</th>'
                    '<th style="text-align:right;padding:3px 8px">Vaegtet</th></tr></thead><tbody>'
                )
                for i, fa in enumerate(facs):
                    bg = "#ffffff" if i % 2 == 0 else "#f9fafb"
                    sig, wt, wtd = fa.get("signal") or 0, (fa.get("weight") or 0) * 100, fa.get("weighted") or 0
                    out.append(
                        f'<tr style="background:{bg}">'
                        f'<td style="text-align:left;padding:3px 8px">{esc(fa["name"])}</td>'
                        f'<td style="text-align:left;padding:3px 8px;color:#374151">{esc(str(fa.get("raw","")))}</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{fcol(sig)};font-weight:600">{sg(sig)}</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{MUT}">{wt:.1f}%</td>'
                        f'<td style="text-align:right;padding:3px 8px;color:{fcol(wtd)};font-weight:700">{sg(wtd,1)}</td></tr>'
                    )
                out.append('</tbody></table>')
            if ly["excluded"]:
                ex = "; ".join(f'{esc(e["name"])} ({esc(str(e["why"]))})' for e in ly["excluded"])
                out.append(f'<div style="font-size:11px;color:{MUT};padding:6px 12px;background:#f9fafb">Ekskluderet: {ex}</div>')
        out.append('</div>')
        return "".join(out)

    def drivers_html(title, col, items):
        rows = "".join(
            '<div style="display:flex;justify-content:space-between;font-size:12px;padding:2px 0">'
            f'<span style="color:#374151">{esc(it["name"])}</span>'
            f'<span style="color:{col};font-weight:700">{sg(it["contribution"],1)}</span></div>' for it in items
        ) or f'<div style="font-size:12px;color:{MUT}">Ingen.</div>'
        return f'<div style="flex:1"><div style="font-weight:700;color:{col};font-size:12px;margin-bottom:4px">{esc(title)}</div>{rows}</div>'

    # ── Gate-blok (med 'hvorfor' naar gate < 1) ──
    gate = d["gate"]
    gcol = BULL if gate >= 0.7 else (NEU if gate >= 0.3 else BEAR)
    gate_rows = ""
    if gate < 1.0:
        gate_rows = ('<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:8px">'
                     f'<tr style="color:{MUT};border-bottom:1px solid #e5e7eb">'
                     '<th style="text-align:left;padding:3px 6px">Risiko-signal</th>'
                     '<th style="text-align:right;padding:3px 6px">Faktor</th>'
                     '<th style="text-align:left;padding:3px 6px">Raa</th></tr>')
        for gb in d["gate_breakdown"]:
            fc = BULL if gb["factor"] >= 0.7 else (NEU if gb["factor"] >= 0.3 else BEAR)
            gate_rows += (f'<tr><td style="padding:3px 6px">{esc(gb["signal"])}</td>'
                          f'<td style="text-align:right;padding:3px 6px;color:{fc};font-weight:700">{gb["factor"]:.2f}</td>'
                          f'<td style="padding:3px 6px;color:#374151">{esc(gb["raw"])}</td></tr>')
        gate_rows += '</table>'
    gate_block = (
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:12px;'
        'print-color-adjust:exact;-webkit-print-color-adjust:exact">'
        f'<div style="display:flex;justify-content:space-between;align-items:center">'
        f'<b style="font-size:13px">Risiko-gate</b><span style="font-size:26px;font-weight:800;color:{gcol}">{gate:.2f}</span></div>'
        '<div style="font-size:11px;color:#6b7280">Strukturelle fatale fejl (konkurs/udvanding/FCF-distress) traekker gaten mod 0.</div>'
        f'{gate_rows}</div>'
    )

    # ── Owner-Earnings-panel ──
    oe = d.get("owner_earnings")
    tl = d.get("tiles", {})            # bruges baade af OE-yield-ankeret og tiles nedenfor
    oe_block = ""
    if oe:
        oey = pct(tl.get("oe_yield"))
        mcap = usd(tl.get("market_cap"))
        oe_block = (
            '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;background:#f9fafb;'
            'display:flex;gap:16px;flex-wrap:wrap;print-color-adjust:exact;-webkit-print-color-adjust:exact">'
            '<div style="flex:1;min-width:240px">'
            f'<b style="font-size:13px">Owner Earnings</b> <span style="font-size:11px;color:{MUT}">'
            f'({oe.get("norm_years")}-aars norm., est., {esc(str(oe.get("method") or ""))})</span>'
            '<div style="display:inline-block;font-size:9px;font-weight:700;color:#92400e;background:#fef3c7;'
            'border-radius:4px;padding:2px 6px;margin-left:6px;text-transform:uppercase;letter-spacing:.3px">'
            'Absolut &middot; stoerrelse &middot; ej sammenligneligt</div>'
            f'<div style="font-size:24px;font-weight:800;margin-top:3px">{usd(oe.get("value"))}</div>'
            f'<div style="font-size:11px;color:#374151">vedligeholds-capex {usd(oe.get("maint_capex"))} &middot; '
            f'seneste aar: OCF {usd(oe.get("ocf_latest"))} / FCF {usd(oe.get("fcf_latest"))}</div>'
            f'<div style="font-size:11px;color:{MUT};margin-top:4px">Hele virksomhedens aarlige ejer-indtjening. '
            'Stoerrelses-tal &mdash; siger intet om aktien er dyr eller billig.</div></div>'
            '<div style="width:1px;background:#e5e7eb"></div>'
            '<div style="min-width:210px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;'
            'padding:10px 12px;display:flex;flex-direction:column;justify-content:center;'
            'print-color-adjust:exact;-webkit-print-color-adjust:exact">'
            '<div style="font-size:9px;font-weight:700;color:#0369a1;text-transform:uppercase;letter-spacing:.3px">'
            '&#8597; Sammenlign aktier her</div>'
            '<div style="font-size:13px;color:#374151;margin-top:2px">Owner-Earnings yield</div>'
            f'<div style="font-size:26px;font-weight:800;color:#0369a1">{oey}</div>'
            f'<div style="font-size:11px;color:{MUT}">= {usd(oe.get("value"))} / markedsvaerdi {mcap}</div></div></div>'
        )

    # ── Tiles ──

    def tile(lbl, val, accent=False):
        bc = "#bae6fd" if accent else "#e5e7eb"
        return (f'<div style="border:1px solid {bc};border-radius:6px;padding:5px 9px">'
                f'<div style="font-size:9px;color:{MUT};text-transform:uppercase">{esc(lbl)}</div>'
                f'<div style="font-size:13px;font-weight:700">{esc(val)}</div></div>')

    div_txt = pct(tl.get("dividend_yield"), 1, 100.0)
    if tl.get("payout") is not None:
        div_txt += f" (payout {tl['payout']*100:.0f}%)"
    cmp_tiles = "".join([           # sammenlignelige paa tvaers (ratios/yields) — accent
        tile("P/E", "—" if tl.get("pe") is None else f'{tl["pe"]:.1f}', True),
        tile("Owner-Earnings yield", pct(tl.get("oe_yield")), True),
        tile("FCF-yield", pct(tl.get("fcf_yield"), 1, 100.0), True),
        tile("ROIC", pct(tl.get("roic")), True),
        tile("Udbytte", div_txt, True),
        tile("Altman Z", "—" if tl.get("altman_z") is None else f'{tl["altman_z"]:.2f}', True),
    ])
    ctx_tiles = "".join([           # kontekst — denne akties stoerrelse (ej sammenligneligt)
        tile("Markedsvaerdi", usd(tl.get("market_cap"))),
        tile("Sektor", tl.get("sector") or "—"),
    ])
    tiles_html = (
        '<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:10px 12px;'
        'print-color-adjust:exact;-webkit-print-color-adjust:exact">'
        '<div style="font-size:10px;font-weight:700;color:#0369a1;text-transform:uppercase;letter-spacing:.4px;'
        'margin-bottom:7px">&#8597; Sammenlignelige noegletal &middot; paa tvaers af aktier</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">{cmp_tiles}</div></div>'
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px">'
        '<div style="font-size:10px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:.4px;'
        'margin-bottom:7px">Kontekst &middot; denne akties stoerrelse (ej sammenligneligt)</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">{ctx_tiles}</div></div>'
    )

    # ── Flag-striber ──
    flags = ""
    if d.get("fundamental_na"):
        flags += ('<div style="background:#fef9c3;border-radius:6px;padding:8px 12px;font-size:12px">'
                  '&#9888; Fundamentaldata utilgaengelig for denne ticker — kun trend-laget vurderet; '
                  'scoren er IKKE en fuld vurdering.</div>')
    if d.get("is_financial"):
        flags += ('<div style="background:#fef9c3;border-radius:6px;padding:8px 12px;font-size:12px">'
                  '&#9888; Begraenset model — finansiel sektor (capex/FCF/Owner-Earnings/gaeld-faktorer udeladt).</div>')

    css = ("@page{margin:14mm}*{box-sizing:border-box}"
           "body{margin:0;background:#fff;color:#111;font-family:-apple-system,Segoe UI,Roboto,sans-serif}"
           ".bar{padding:8px 16px;background:#eee;font-size:13px;display:flex;gap:10px;align-items:center}"
           ".bar button{font-size:13px;padding:4px 12px;cursor:pointer}"
           ".wrap{padding:16px;max-width:920px;margin:0 auto;display:flex;flex-direction:column;gap:12px}"
           "@media print{.bar{display:none}}")
    t = esc(d["ticker"])
    co = esc(d.get("company") or "")
    co_html = f' <span style="font-size:14px;font-weight:600;color:{MUT}">{co}</span>' if co else ""
    price = f'${d["price"]:.2f}' if d.get("price") is not None else "—"
    fbc = fb(d["final_band"])
    detail_html = detail_section() if detail else ""
    import json as _json
    chart = d.get("chart")
    has_chart = bool(chart and chart.get("bars"))
    chart_block = (
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px">'
        '<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">'
        'Langsigtet trend &middot; uge-chart (10/30/40-uge MA + ATH) &mdash; kontekst, ikke dommen</div>'
        '<div id="buyhold-chart"></div></div>'
    ) if has_chart else ""
    chart_script = ('<script>const CHART=' + _json.dumps(chart) + ';' + BUYHOLD_CHART_JS + '</script>') if has_chart else ""
    return (
        f'<!DOCTYPE html><html lang="da"><head><meta charset="utf-8"><title>Buy-and-Hold - {t}</title>'
        f'<style>{css}</style></head><body>'
        '<div class="bar">Gem som PDF: <button onclick="window.print()">Gem / Print</button>'
        '<span>(eller Ctrl+P -&gt; "Gem som PDF")</span></div><div class="wrap">'
        '<div style="display:flex;justify-content:space-between;align-items:center;border:1px solid #e5e7eb;border-radius:8px;padding:12px 16px">'
        f'<div><div style="font-size:24px;font-weight:800">{t}{co_html}</div>'
        f'<div style="color:{MUT};font-size:13px">{price} &middot; LANGSIGTET vurdering (min. 1 aar)</div></div>'
        f'<div style="text-align:right"><div style="font-size:34px;font-weight:800;color:{fbc}">{sg(d["final"])}</div>'
        f'<div style="font-size:12px;font-weight:700;color:{fbc}">{esc(d["final_band"])}</div>'
        '<div style="font-size:10px;color:#0369a1;font-weight:700;margin-top:3px">&#8597; SAMMENLIGN KANDIDATER PAA DETTE TAL</div></div></div>'
        f'{flags}'
        f'<div style="font-size:12px;color:{MUT}">Kombineret <b>{sg(d["combined"])}</b> &times; Risiko-gate <b>{d["gate"]:.2f}</b>'
        f' &minus; (1&minus;gate)&times;40 &rarr; Samlet <b style="color:{fbc}">{sg(d["final"])}</b></div>'
        '<div style="font-size:12px;color:#374151;background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;'
        'padding:8px 12px;print-color-adjust:exact;-webkit-print-color-adjust:exact">'
        '<b style="color:#0369a1">Sammenlign paa tvaers af aktier:</b> SAMLET-scoren, de fire lag-scorer og de '
        '<b>sammenlignelige noegletal</b> nederst. De absolutte beloeb (Owner Earnings, markedsvaerdi) viser '
        'selskabets <b>stoerrelse</b> &mdash; ikke vaerdien pr. investeret krone.</div>'
        '<div style="display:flex;gap:10px;flex-wrap:wrap">'
        f'{layer_card("quality")}{layer_card("growth")}{layer_card("valuation")}{layer_card("trend")}</div>'
        f'{chart_block}'
        f'{gate_block}{oe_block}'
        '<div style="display:flex;gap:16px;border:1px solid #e5e7eb;border-radius:8px;padding:12px">'
        f'{drivers_html("Medvind", BULL, d["drivers"]["positive"])}<div style="width:1px;background:#e5e7eb"></div>'
        f'{drivers_html("Modvind", BEAR, d["drivers"]["negative"])}</div>'
        f'{tiles_html}'
        f'{detail_html}</div>{chart_script}</body></html>'
    )


class BuyHoldAnalyzeRequest(BaseModel):
    ticker: str


@app.post("/buyhold/analyze_json")
async def buyhold_analyze_json(req: BuyHoldAnalyzeRequest):
    """Buy-and-Hold struktureret JSON. compute_buyhold er ASYNC + kraever ib til Lag 4 ->
    await DIREKTE paa delt ib (som /intradag, IKKE to_thread). FMP-kaldet er to_thread'et
    inde i compute_buyhold. Er ib nede, ekskluderes Lag 4 paent."""
    import buyhold
    import buyhold_report
    t = (req.ticker or "").strip().upper()
    if not t:
        raise HTTPException(status_code=400, detail="Mangler ticker")
    if not os.environ.get("FMP_API_KEY", ""):
        raise HTTPException(status_code=503, detail="FMP_API_KEY ikke sat i miljoeet")
    await strategy_manager.connect_ibkr(paper_trading=True)
    conn = strategy_manager.get_ibkr()
    ib = conn.ib if (conn and conn.connected) else None
    try:
        core = await buyhold.compute_buyhold(ib, t, os.environ.get("FMP_API_KEY", ""))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Buy-and-Hold-rapport fejlede: {e}")
    return buyhold_report.report_to_json(core)


@app.get("/buyhold/report.html", response_class=HTMLResponse)
async def buyhold_report_html(ticker: str, detail: int = 0):
    """Pen, printbar HTML til EKSTERN browser (PDF-knappen). detail=1 -> nedbrydning."""
    import buyhold
    import buyhold_report
    t = (ticker or "").strip().upper()
    if not t:
        raise HTTPException(status_code=400, detail="Mangler ticker")
    if not os.environ.get("FMP_API_KEY", ""):
        return HTMLResponse("<h2>FMP_API_KEY ikke sat i miljoeet</h2>", status_code=503)
    await strategy_manager.connect_ibkr(paper_trading=True)
    conn = strategy_manager.get_ibkr()
    ib = conn.ib if (conn and conn.connected) else None
    try:
        core = await buyhold.compute_buyhold(ib, t, os.environ.get("FMP_API_KEY", ""))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Buy-and-Hold-rapport fejlede: {e}")
    return HTMLResponse(_buyhold_report_html(buyhold_report.report_to_json(core), detail=bool(detail)))


# ── Hjaelpe-assistent endpoint ────────────────────────────────
# Iben spoerger om hvordan Trading Dash bruges; help_assistant kalder Anthropic
# (Haiku 4.5) med dokumentationen som kontekst. Noegle: ANTHROPIC_API_KEY i miljoeet.
# BaseModel + HTTPException er allerede importeret oeverst i main.py (swing bruger dem).

class HelpRequest(BaseModel):
    question: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]


@app.post("/help/ask")
async def help_ask(req: HelpRequest):
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Mangler spoergsmaal")
    try:
        import help_assistant   # LAZY: en manglende anthropic-pakke maa ALDRIG vaelte backenden
        answer = await help_assistant.answer_question(q, req.history)
        return {"answer": answer}
    except Exception as e:
        # Venlig fejl i stedet for 500 - UI'et viser den som en besked til Iben.
        # Saertilfaelde: tom Anthropic-kreditkonto -> specifik besked til Iben (Soeren
        # bad om denne) saa hun ved at bede Soeren toppe kontoen op.
        emsg = str(e).lower()
        if any(k in emsg for k in ("credit balance", "insufficient", "billing", "quota", "payment")):
            return {
                "answer": "Du har brugt al din Claude-hjaelpekredit. Bed Soeren om at "
                          "saette flere penge ind paa Claude API-kontoen, saa hjaelpe-"
                          "assistenten virker igen.",
                "error": f"{type(e).__name__}: {e}",
            }
        return {
            "answer": "Hjælpe-assistenten kunne ikke svare lige nu. Sig til Søren hvis det bliver ved.",
            "error": f"{type(e).__name__}: {e}",
        }


# ── Swing top-10 (vindue) ─────────────────────────────────────
# Vinduet viser seneste top-10 + tidsstempel og kan starte en frisk koersel
# (IBKR-kilde, ~et par timer) som baggrundsproces. swing_top10.py skriver
# swing_top10_latest.json (resultatet) og en laasefil mens den koerer.
# NB: os/sys er IKKE modul-importeret oeverst i main.py (kun lokalt) -> importer her.
import os
import sys
import json as _json
import subprocess as _subprocess
import datetime as _datetime

_SWING_DIR = os.path.dirname(os.path.abspath(__file__))   # backend/
_SWING_LATEST_PATH = os.path.join(_SWING_DIR, "swing_top10_latest.json")
_SWING_LOCK_PATH = os.path.join(_SWING_DIR, "swing_top10_running.lock")
_SWING_RUNLOG_PATH = os.path.join(_SWING_DIR, "swing_top10_run.log")
_SWING_STALE_SEC = 4 * 3600   # laas aeldre end dette = staale (proces doede) -> ikke koerende


def _swing_running():
    """(running: bool, started_utc: str|None). Laasefilen er kilden, med staale-backstop."""
    if not os.path.exists(_SWING_LOCK_PATH):
        return False, None
    try:
        with open(_SWING_LOCK_PATH, encoding="utf-8") as f:
            lock = _json.load(f)
        started = lock.get("started_utc")
        age = (_datetime.datetime.now(_datetime.timezone.utc)
               - _datetime.datetime.fromisoformat(started)).total_seconds()
        return (age <= _SWING_STALE_SEC), started
    except Exception:
        return False, None


@app.get("/swing/top10")
async def swing_top10_get():
    """Seneste top-10 (fra swing_top10_latest.json) + om en koersel er i gang."""
    data = {"generated_local": None, "generated_utc": None, "source": None, "count": 0, "rows": []}
    if os.path.exists(_SWING_LATEST_PATH):
        try:
            with open(_SWING_LATEST_PATH, encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            pass
    running, started = _swing_running()
    data["running"] = running
    data["started_utc"] = started
    return data


@app.post("/swing/top10/run")
async def swing_top10_run():
    """Start en frisk top-10-koersel (IBKR) som baggrundsproces. Afvis hvis en allerede koerer."""
    running, started = _swing_running()
    if running:
        return {"started": False, "already_running": True, "started_utc": started}
    # Ryd evt. staale laas foer ny start
    if os.path.exists(_SWING_LOCK_PATH):
        try:
            os.remove(_SWING_LOCK_PATH)
        except OSError:
            pass
    try:
        logf = open(_SWING_RUNLOG_PATH, "a", encoding="utf-8")
        _subprocess.Popen(
            [sys.executable, "swing_top10.py", "--source", "ibkr"],
            cwd=_SWING_DIR, stdout=logf, stderr=_subprocess.STDOUT,
        )
    except Exception as e:
        return {"started": False, "error": f"{type(e).__name__}: {e}"}
    return {"started": True}


# ── Intradag Top-10 Konfluens (trin 6) ──────────────────────────────────────
# Spejler swing-top10-endpoints, MEN scanen koerer IN-PROCESS som en asyncio.Task
# paa den DELTE ib (ikke en subprocess med egen klient, der konkurrerer om feed).
import intradag_scanner

_INTRADAG_TOP10_JSON = os.path.join(_SWING_DIR, "intradag_top10_latest.json")
_intradag_top10 = {"task": None, "started_utc": None}
_INTRADAG_STALE_SEC = 15 * 60   # task aeldre end dette uden at vaere done = staale


def _intradag_running():
    """(running: bool, started_utc: str|None). Task-tilstand med staale-backstop."""
    t = _intradag_top10["task"]
    if t is None or t.done():
        return False, None
    started = _intradag_top10["started_utc"]
    if started:
        age = (_datetime.datetime.now(_datetime.timezone.utc)
               - _datetime.datetime.fromisoformat(started)).total_seconds()
        if age > _INTRADAG_STALE_SEC:
            return False, started
    return True, started


@app.get("/intradag/top10")
async def intradag_top10_get():
    """Seneste intradag top-10 (fra intradag_top10_latest.json) + fremdrift/running."""
    data = {"generated_local": None, "generated_utc": None, "source": None,
            "count": 0, "rows": [], "progress": None}
    if os.path.exists(_INTRADAG_TOP10_JSON):
        try:
            with open(_INTRADAG_TOP10_JSON, encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            pass
    running, started = _intradag_running()
    data["running"] = running
    data["started_utc"] = started
    return data


@app.post("/intradag/top10/run")
async def intradag_top10_run():
    """Start en frisk intradag-scan (in-process async paa delt ib). Afvis hvis en koerer."""
    running, started = _intradag_running()
    if running:
        return {"started": False, "already_running": True, "started_utc": started}
    await strategy_manager.connect_ibkr(paper_trading=True)
    conn = strategy_manager.get_ibkr()
    if conn is None or not conn.connected:
        return {"started": False, "error": "IBKR ikke forbundet (er TWS/Gateway logget ind paa 7497?)"}
    ib = conn.ib
    _intradag_top10["started_utc"] = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    _intradag_top10["task"] = asyncio.create_task(intradag_scanner.run_scan(ib))
    return {"started": True, "already_running": False, "started_utc": _intradag_top10["started_utc"]}


# ── Live halt-scanner (movers-univers, halted-overvaagning paa delt ib) ──────
# In-process baggrunds-task (halt_scanner) der laeser IBKR's halted-felt pr. cyklus.
# /halt/list ensure'er at tasken koerer (start-on-demand) + returnerer tilstanden.
import halt_scanner


@app.get("/halt/list")
async def halt_list():
    """Aktuelt haltede + netop genaabnede navne fra movers-universet."""
    conn = strategy_manager.get_ibkr()
    if conn is not None and conn.connected:
        halt_scanner.ensure_running(conn.ib)   # delt ib — ALDRIG en egen klient
    return {
        "halted":        halt_scanner.snapshot(),
        "asof":          halt_scanner.asof(),
        "running":       halt_scanner.is_running(),
        "universe_size": halt_scanner.universe_size(),
    }


# ── Firma-hjemmeside (watchlist: klik paa ticker -> aaben firmaets website) ──
# yfinance .info["website"]. Caches i hukommelsen (websites aendrer sig ikke), saa
# kun foerste opslag pr. ticker koster et (langsomt) yfinance-kald.
_WEBSITE_CACHE: dict = {}


def _lookup_website(ticker: str):
    t = ticker.upper()
    if t in _WEBSITE_CACHE:
        return _WEBSITE_CACHE[t]
    try:
        import yfinance as yf
        url = yf.Ticker(t).info.get("website") or None
    except Exception:
        url = None
    _WEBSITE_CACHE[t] = url
    return url


@app.get("/company/website")
async def company_website(ticker: str):
    """Firmaets hjemmeside for en ticker (til watchlist-klik). website=null hvis
    ukendt — frontend falder saa tilbage paa en soegning."""
    t = (ticker or "").strip().upper()
    if not t:
        raise HTTPException(status_code=400, detail="Mangler ticker")
    url = await asyncio.to_thread(_lookup_website, t)
    return {"ticker": t, "website": url}


# ── Dokumentation (PDF'er i backend/docs/, aabnes eksternt af frontend) ──────
# Auto-listende: /docs/list laeser mappen ved hvert kald, saa nye PDF'er dukker
# op uden kodeaendring. /docs/file/{name} serverer EEN PDF (sti-traversal-sikret,
# kun .pdf), uden Content-Disposition saa browseren VISER den frem for at downloade.
DOCS_DIR = Path(__file__).parent / "docs"


# Kategori-praefiks i filnavnet styrer sektionen i docs-vinduet. Filnavn-moenster:
#   <kategori>_<NN>_<titel>.pdf   fx  swing_02_konfluens_v1_referenceguide.pdf
# Kategorien stripper ud af titlen (sektions-overskriften baerer den i stedet).
_DOC_CATEGORIES = [("swing", "Swing Trading"), ("day", "Day Trading"), ("buyhold", "Buy and Hold")]
_DOC_CAT_LABEL = dict(_DOC_CATEGORIES)
_DOC_CAT_ORDER = {key: i for i, (key, _) in enumerate(_DOC_CATEGORIES)}


@app.get("/docs/list")
async def docs_list():
    """Lister PDF'er i backend/docs/, grupperet. Returnerer {docs: [{name, title,
    category}]} sorteret paa (kategori-raekkefoelge, NN-praefiks, filnavn)."""
    if not DOCS_DIR.is_dir():
        return {"docs": []}
    items = []
    for p in DOCS_DIR.glob("*.pdf"):
        stem = p.stem
        # 1) Kategori-praefiks "swing_"/"day_" (valgfri) -> sektion, strippes af titlen.
        cat_key = ""
        if "_" in stem and stem.split("_", 1)[0].lower() in _DOC_CAT_LABEL:
            cat_key = stem.split("_", 1)[0].lower()
            stem = stem.split("_", 1)[1]
        # 2) Sorterings-praefiks "NN_" (valgfri) -> raekkefoelge i sektionen, vises ikke.
        order = 999
        if "_" in stem and stem.split("_", 1)[0].isdigit():
            order = int(stem.split("_", 1)[0])
            stem = stem.split("_", 1)[1]
        title = stem.replace("_", " ").replace("-", " ").strip()
        title = (title[:1].upper() + title[1:]) if title else p.name
        items.append({
            "name": p.name, "title": title,
            "category": _DOC_CAT_LABEL.get(cat_key, ""),
            "_catorder": _DOC_CAT_ORDER.get(cat_key, 99), "_order": order,
        })
    items.sort(key=lambda d: (d["_catorder"], d["_order"], d["name"].lower()))
    for d in items:
        d.pop("_catorder"); d.pop("_order")
    return {"docs": items}


@app.get("/docs/file/{name}")
async def docs_file(name: str):
    """Serverer EEN PDF fra backend/docs/. Kun filer i mappen, kun .pdf."""
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Kun PDF-filer")
    target = (DOCS_DIR / name).resolve()
    try:
        target.relative_to(DOCS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Ugyldig sti")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Dokumentet findes ikke")
    return FileResponse(str(target), media_type="application/pdf")


# ── Dagens log (markdown-rapport over et dato-interval, read-only) ───────────
# Beskyttet: paa algoserveren naas dette som fan-out-maal via X-Internal-Key
# (vej 1 i require_studio_auth); paa en workstation slipper dev-mode (vej 2) igennem.
@app.get("/dagenslog/report", dependencies=[Depends(require_studio_auth)])
async def dagenslog_report(from_: str = Query(None, alias="from"),
                           to: str = Query(None, alias="to")):
    """Dagens log som markdown over et dato-interval (inkl. forensik, alle strategier)."""
    today = datetime.now().date().isoformat()
    d_from = (from_ or today)[:10]
    d_to = (to or today)[:10]
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    def _run():
        con = dagens_log.ro_connect(dagens_log.DB_PATH)
        try:
            trades = dagens_log.load_trades(con, d_from, d_to, None)
            md = dagens_log.build_report_md(con, d_from, d_to, None, True)
            return md, len(trades)
        finally:
            con.close()

    md, n = await asyncio.to_thread(_run)   # sqlite er blokerende -> traad
    return {"markdown": md, "from": d_from, "to": d_to, "n_trades": n}


# ── Dagens log paa tvaers af flaaden (vaelg maskiner, fan-out + flet) ─────────
PEERS_PATH = Path(__file__).parent / "peers.json"


def load_peers() -> list[dict]:
    """peers.json er det eksisterende maskin-register (ingen nye hardkodede hosts)."""
    try:
        return json.loads(PEERS_PATH.read_text(encoding="utf-8")).get("peers", [])
    except Exception:
        return []


@app.get("/fleet/peers", dependencies=[Depends(require_studio_auth)])
async def fleet_peers():
    """Maskin-liste til vinduets afkrydsning. self markeres; tomme slots = ikke valgbare."""
    peers = load_peers()
    for p in peers:
        p["is_self"]    = (p.get("id") == identity.source_id)
        p["selectable"] = bool(p.get("url")) and bool(p.get("enabled"))
    return {"peers": peers, "self_id": identity.source_id}


@app.get("/dagenslog/report_fleet", dependencies=[Depends(require_studio_auth)])
async def dagenslog_report_fleet(from_: str = Query(None, alias="from"),
                                 to: str = Query(None, alias="to"),
                                 peers: str = Query("")):
    """Henter dagens log fra de valgte maskiner (self lokalt, peers over Tailscale
    med X-Internal-Key), robust mod en maskine der er nede, og fletter til een markdown."""
    today = datetime.now().date().isoformat()
    d_from = (from_ or today)[:10]
    d_to   = (to or today)[:10]
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    want = [pid for pid in peers.split(",") if pid.strip()]
    reg  = {p["id"]: p for p in load_peers()}
    selected = [reg[pid] for pid in want if pid in reg and reg[pid].get("url")]

    async def fetch_one(p: dict) -> dict:
        # SELF: koer lokalt (intet HTTP-hop, virker selv hvis eget hostname driller)
        if p["id"] == identity.source_id:
            def _run():
                con = dagens_log.ro_connect(dagens_log.DB_PATH)
                try:
                    n = len(dagens_log.load_trades(con, d_from, d_to, None))
                    return dagens_log.build_report_md(con, d_from, d_to, None, True), n
                finally:
                    con.close()
            try:
                md, n = await asyncio.to_thread(_run)
                return {"peer": p, "md": md, "n": n, "ok": True}
            except Exception as e:
                return {"peer": p, "md": None, "n": 0, "ok": False, "err": str(e)[:140]}
        # PEER: hent over Tailscale med X-Internal-Key
        try:
            url = f'{p["url"]}/dagenslog/report?from={d_from}&to={d_to}'
            headers = {"X-Internal-Key": identity.internal_key}
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(url, headers=headers) as r:
                    r.raise_for_status()
                    data = await r.json()
            return {"peer": p, "md": data.get("markdown", ""),
                    "n": data.get("n_trades", 0), "ok": True}
        except Exception as e:
            return {"peer": p, "md": None, "n": 0, "ok": False, "err": str(e)[:140]}

    results = await asyncio.gather(*[fetch_one(p) for p in selected]) if selected else []

    # --- flet til een markdown ---
    period = d_from if d_from == d_to else f"{d_from} -> {d_to}"
    status = " · ".join(
        f'{r["peer"]["name"]} ' + (f"OK ({r['n']} handler)" if r["ok"] else "kunne ikke naas")
        for r in results) or "ingen maskiner valgt"
    parts = [f"# Dagens log (flaade) — {period}", "", f"**Maskiner:** {status}", ""]
    for r in results:
        nm = r["peer"]["name"]; hs = r["peer"].get("host") or r["peer"]["id"]
        parts.append(f"\n\n## ==================  {nm}  ({hs})  ==================\n")
        if r["ok"] and r["md"]:
            parts.append(r["md"])
        elif r["ok"]:
            parts.append(f"_Ingen handler eller events for {nm} i perioden._")
        else:
            parts.append(f"_{nm}: kunne ikke naas ({r.get('err','offline/timeout')}). "
                         f"Tjek at maskinen koerer og er paa Tailscale._")
    machines = [{"id": r["peer"]["id"], "name": r["peer"]["name"], "ok": r["ok"], "n_trades": r["n"]}
                for r in results]
    return {"markdown": "\n".join(parts), "from": d_from, "to": d_to, "machines": machines}


# ── Journal / Trades endpoints ────────────────────────────────
# Læser fra trades-tabellen. Skriver sker via algo-strategierne
# (automatisk) eller via manuel-handel-endpoints (kommer senere).

import trade_queries


class UpdateNotesRequest(BaseModel):
    notes: str


# ── Del 3: arkiv-læsning ───────────────────────────────────────
# Vælg hvilken DB en read-query læser fra:
#   archive tom/None  → live journal.db (denne maskines egen data)
#   archive sat       → arkivet i archives/<archive>/trading_dash.db (read-only)
# Den arkiverede DB åbnes read-only pr. request og lukkes bagefter.
# Læser fra et statisk snapshot — ingen WAL-skrivning i gang, så mode=ro er sikkert.
@asynccontextmanager
async def _resolve_db(archive):
    if not archive:
        yield journal.db
        return
    import replication_store
    path = replication_store.archive_db_path(archive)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Intet arkiv for kilde: {archive!r}")
    conn = await aiosqlite.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        await conn.close()


@app.get("/journal/trades")
async def journal_trades(
    date_from:   str = None,
    date_to:     str = None,
    source:      str = None,
    symbol:      str = None,
    status:      str = None,
    account_id:  str = None,
    instance_id: str = None,
    archive:     str = None,
    limit:       int = 200,
    offset:      int = 0,
):
    """
    List trades med filtre.

    Alle query-parametre er valgfri:
      date_from, date_to: ISO-dato ("2026-05-19"), filtrerer på ET-handelsdag
      source: "Momentum ORB", "Konfluens", "manual"
      symbol: ticker
      status: "open", "closed", eller udelades for alle
      account_id, instance_id: filtrerer på maskine (på lokal: typisk udelades)
      limit, offset: paginering (default: 200 trades)
    """
    async with _resolve_db(archive) as db:
        trades = await trade_queries.list_trades(
            db,
            date_from=date_from, date_to=date_to,
            source=source, symbol=symbol, status=status,
            account_id=account_id, instance_id=instance_id,
            limit=limit, offset=offset,
        )
        total = await trade_queries.count_trades(
            db,
            date_from=date_from, date_to=date_to,
            source=source, symbol=symbol, status=status,
            account_id=account_id, instance_id=instance_id,
        )
        # Aggregat over HELE det filtrerede sæt (ikke kun denne side).
        # trades_summary tæller kun lukkede (pnl findes kun for lukkede);
        # status-filteret styrer kun tabellens rækker, ikke KPI-boksen.
        summary = await trade_queries.trades_summary(
            db,
            date_from=date_from, date_to=date_to,
            source=source, symbol=symbol,
            account_id=account_id, instance_id=instance_id,
        )
    return {"trades": trades, "count": len(trades), "total": total, "summary": summary}


@app.get("/journal/trades/{trade_id}")
async def journal_trade_detail(trade_id: str, archive: str = None):
    """Hent én specifik trade med fuld payload (forensics, indikatorer, etc.)."""
    async with _resolve_db(archive) as db:
        trade = await trade_queries.get_trade_by_id(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} findes ikke")
    return trade


@app.get("/journal/today")
async def journal_today(archive: str = None):
    """Dagens trades (ET-handelsdag) + summary statistik."""
    async with _resolve_db(archive) as db:
        return await trade_queries.today_trades_and_summary(db)


@app.get("/journal/open-positions")
async def journal_open_positions(archive: str = None):
    """Alle nuværende åbne positioner."""
    async with _resolve_db(archive) as db:
        positions = await trade_queries.open_positions(db)
    return {
        "positions": positions,
        "count":     len(positions),
    }


@app.get("/journal/events")
async def journal_events(
    date:       str = None,        # "2026-05-20" (ET-handelsdag) — påkrævet i praksis
    from_time:  str = "00:00",     # "HH:MM" ET
    to_time:    str = "23:59",     # "HH:MM" ET
    source:     str = None,        # "Momentum ORB" / "Konfluens" — None = alle
    event_type: str = None,        # None = alle
    limit:      int = 1000,
    archive:    str = None,
):
    """
    Hent gemte journal-events for et ET-tidsvindue. Bruges af Studio's
    Log-fane (Historik-tilstand) til at se hvad algoritmerne lavede selv
    hvis man ikke fulgte med live.

    date + from_time/to_time fortolkes i ET (America/New_York). Backenden
    oversætter til UTC-grænser så filteret rammer korrekt uanset
    sommer/vintertid. Ingen auth — kun læsning af log-data (samme som
    /account/dash-snapshot).
    """
    import pytz
    ET_TZ = pytz.timezone("America/New_York")

    # Hvis ingen dato angivet: brug dagens ET-dato
    if not date:
        date = datetime.now(ET_TZ).strftime("%Y-%m-%d")

    def et_window_to_utc(d: str, hhmm: str) -> str:
        """Lav 'YYYY-MM-DD' + 'HH:MM' i ET om til en ISO UTC-streng."""
        try:
            naive = datetime.strptime(f"{d} {hhmm}", "%Y-%m-%d %H:%M")
        except ValueError:
            naive = datetime.strptime(f"{d} 00:00", "%Y-%m-%d %H:%M")
        et_dt = ET_TZ.localize(naive)
        return et_dt.astimezone(pytz.utc).isoformat()

    from_utc = et_window_to_utc(date, from_time)
    to_utc   = et_window_to_utc(date, to_time)

    async with _resolve_db(archive) as db:
        events = await journal.get_events(
            from_utc=from_utc, to_utc=to_utc,
            source=source, event_type=event_type, limit=limit,
            db=db,
        )
    return {
        "date":      date,
        "from_time": from_time,
        "to_time":   to_time,
        "from_utc":  from_utc,
        "to_utc":    to_utc,
        "source":    source,
        "count":     len(events),
        "events":    events,
    }


@app.patch("/journal/trades/{trade_id}")
async def journal_update_notes(trade_id: str, req: UpdateNotesRequest):
    """
    Opdater notes-feltet på en trade. Brugbart fra Studio når man
    vil tilføje en kommentar efter en handel er lukket.
    """
    ok = await trade_queries.update_notes_via_journal(journal, trade_id, req.notes)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} findes ikke eller fejlede")
    return {"ok": True, "trade_id": trade_id}


# ── Manuelle handel-endpoints ─────────────────────────────────
# Frontend kalder disse når Iben/Søren manuelt åbner eller lukker
# en position via Trading Dash UI'et. Backend sender ordren til IBKR
# og logger trade-row i samme database som algo-handler.
#
# Designvalg:
#   - Kun MKT-ordrer i første omgang (LMT kommer senere som feature)
#   - Backend er autoritativ: ordre sendes server-side, trade_row
#     oprettes først når fill er bekræftet med faktisk fill-pris
#   - Ingen partial close: en manuel handel åbnes og lukkes komplet

import pytz
ET_TZ = pytz.timezone("America/New_York")


class ManualTradeOpenRequest(BaseModel):
    symbol:      str
    side:        str             # "long" eller "short"
    shares:      int
    order_type:  str   = "MKT"   # foreløbig kun "MKT"
    limit_price: float | None = None
    notes:       str   | None = None


class ManualTradeCloseRequest(BaseModel):
    order_type:  str   = "MKT"
    limit_price: float | None = None
    notes:       str   | None = None   # tilføjes til eksisterende notes


@app.post("/journal/manual-trade")
async def open_manual_trade(req: ManualTradeOpenRequest):
    """
    Åbn en manuel position via IBKR paper trading.

    Returnerer trade_id som frontend kan bruge til at lukke positionen
    senere med /journal/manual-trade/{trade_id}/close.
    """
    # ── Validering ────────────────────────────────────────────
    symbol = req.symbol.strip().upper()
    if not symbol or len(symbol) > 10:
        raise HTTPException(status_code=400, detail="Ugyldigt symbol")

    if req.side not in ("long", "short"):
        raise HTTPException(status_code=400, detail="side skal være 'long' eller 'short'")

    if req.shares <= 0:
        raise HTTPException(status_code=400, detail="shares skal være > 0")

    if req.order_type != "MKT":
        raise HTTPException(
            status_code=400,
            detail="Foreløbig kun MKT-ordrer understøttes for manuelle handler",
        )

    # ── IBKR-tjek ─────────────────────────────────────────────
    ibkr = strategy_manager.get_ibkr()
    if ibkr is None or not ibkr.connected:
        raise HTTPException(
            status_code=503,
            detail="IBKR ikke forbundet — start TWS og prøv igen",
        )

    # ── Konvertér side → action ───────────────────────────────
    # long  = BUY (køb først, sælg senere)
    # short = SELL (sælg først uden at eje = short på IBKR)
    action = "BUY" if req.side == "long" else "SELL"

    # ── Send ordre til IBKR ───────────────────────────────────
    result = await ibkr.place_paper_order(
        ticker=symbol,
        action=action,
        quantity=req.shares,
        order_type="MKT",
    )

    if result is None:
        raise HTTPException(
            status_code=500,
            detail=f"Ordre fejlede — IBKR returnerede ingen response for {symbol}",
        )

    # Tjek at ordren faktisk blev fyldt
    status   = result.get("status", "")
    filled   = result.get("filled", 0) or 0
    avg_fill = result.get("avg_fill", 0) or 0

    if filled < req.shares or avg_fill <= 0:
        raise HTTPException(
            status_code=500,
            detail=f"Ordre ikke fyldt: status={status}, filled={filled}/{req.shares}, "
                   f"avg_fill={avg_fill}. Tjek IBKR.",
        )

    # ── Registrer ordre hos OrdersTracker så Ordrer-vinduet viser den ──
    try:
        order_id = result.get("order_id")
        if order_id:
            get_tracker().record_placed(
                order_id=order_id,
                source="manual",
                ticker=symbol,
                action=action,
                shares=req.shares,
                order_type="MKT",
            )
    except Exception as e:
        # Ikke fatalt — handel er allerede fyldt, vi mangler bare ordre-tracking
        print(f"[ManualTrade] OrdersTracker fejl ved entry: {e}")

    # ── Log trade-row ─────────────────────────────────────────
    entry_time = datetime.now(ET_TZ)
    trade_id = await journal.log_trade_open(
        source       = "manual",
        symbol       = symbol,
        side         = req.side,
        shares       = req.shares,
        entry_price  = avg_fill,
        entry_time   = entry_time,
        variant      = None,
        entry_reason = "Manuel handel",
        notes        = req.notes,
        payload      = {
            "ibkr_order_id": result.get("order_id"),
            "ibkr_status":   status,
        },
    )

    if trade_id is None:
        # Ordren er gået igennem hos IBKR, men vi kunne ikke logge den.
        # Det her er en SÆRDELES ubehagelig situation — vi giver frontend
        # alligevel besked om at handlen er udført, men flagger fejlen.
        raise HTTPException(
            status_code=500,
            detail=f"⚠ Ordren blev udført på IBKR (fill @ ${avg_fill}), men "
                   f"trade kunne ikke gemmes i journal. Tjek backend-logs.",
        )

    return {
        "ok":          True,
        "trade_id":    trade_id,
        "symbol":      symbol,
        "side":        req.side,
        "shares":      req.shares,
        "entry_price": avg_fill,
        "entry_time":  entry_time.isoformat(),
        "ibkr": {
            "order_id": result.get("order_id"),
            "status":   status,
        },
    }


@app.post("/journal/manual-trade/{trade_id}/close")
async def close_manual_trade(trade_id: str, req: ManualTradeCloseRequest):
    """
    Luk en åben manuel position.

    Trade skal være:
      - Eksisterende (404 hvis ikke fundet)
      - Manuel (400 hvis source != "manual")
      - Åben (400 hvis allerede lukket)
      - Tilhøre denne maskine (403 hvis account_id mismatch)
    """
    # ── Slå trade op ──────────────────────────────────────────
    trade = await trade_queries.get_trade_by_id(journal.db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} findes ikke")

    # Manuel?
    if trade["source"] != "manual":
        raise HTTPException(
            status_code=400,
            detail=f"Trade {trade_id} er ikke en manuel handel "
                   f"(source='{trade['source']}'). Algo-handler lukkes af algoen selv.",
        )

    # Åben?
    if trade["exit_time_utc"] is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Trade {trade_id} er allerede lukket "
                   f"(exit_price=${trade['exit_price']}, reason='{trade['exit_reason']}')",
        )

    # Tilhører denne maskine?
    if trade["account_id"] != identity.account_id:
        raise HTTPException(
            status_code=403,
            detail=f"Trade {trade_id} tilhører konto '{trade['account_id']}', "
                   f"men denne backend er '{identity.account_id}'. "
                   f"Luk handlen fra den korrekte maskine.",
        )

    # ── Validér request ──────────────────────────────────────
    if req.order_type != "MKT":
        raise HTTPException(
            status_code=400,
            detail="Foreløbig kun MKT-ordrer understøttes for manuelle handler",
        )

    # ── IBKR-tjek ────────────────────────────────────────────
    ibkr = strategy_manager.get_ibkr()
    if ibkr is None or not ibkr.connected:
        raise HTTPException(
            status_code=503,
            detail="IBKR ikke forbundet — start TWS og prøv igen",
        )

    # ── Konvertér side → close-action ────────────────────────
    # long-close = SELL (vi solgte for at lukke vores køb)
    # short-close = BUY (buy-to-cover for at lukke vores short)
    close_action = "SELL" if trade["side"] == "long" else "BUY"
    symbol = trade["symbol"]
    shares = trade["shares"]

    # ── Send close-ordre ─────────────────────────────────────
    result = await ibkr.place_paper_order(
        ticker=symbol,
        action=close_action,
        quantity=shares,
        order_type="MKT",
    )

    if result is None:
        raise HTTPException(
            status_code=500,
            detail=f"Close-ordre fejlede — IBKR returnerede ingen response for {symbol}",
        )

    status   = result.get("status", "")
    filled   = result.get("filled", 0) or 0
    avg_fill = result.get("avg_fill", 0) or 0

    if filled < shares or avg_fill <= 0:
        # Position er muligvis halvt-lukket — vi logger ikke som lukket fordi
        # vi ikke ved hvor mange shares der faktisk blev fyldt. Brugeren må
        # tjekke i TWS og evt. lukke resten manuelt.
        raise HTTPException(
            status_code=500,
            detail=f"Close-ordre ikke fyldt komplet: status={status}, "
                   f"filled={filled}/{shares}. Tjek IBKR — positionen kan være "
                   f"halvt-lukket. trades-rækken er IKKE opdateret.",
        )

    # ── Registrer hos OrdersTracker ──────────────────────────
    try:
        order_id = result.get("order_id")
        if order_id:
            get_tracker().record_placed(
                order_id=order_id,
                source="manual",
                ticker=symbol,
                action=close_action,
                shares=shares,
                order_type="MKT",
            )
    except Exception as e:
        print(f"[ManualTrade] OrdersTracker fejl ved close: {e}")

    # ── Beregn P&L ────────────────────────────────────────────
    entry_price = trade["entry_price"]
    if trade["side"] == "long":
        pnl = (avg_fill - entry_price) * shares
    else:
        # Short: profit hvis vi købte tilbage billigere end vi solgte
        pnl = (entry_price - avg_fill) * shares

    # ── Append notes hvis sendt ──────────────────────────────
    # Vi merger med eksisterende notes for at bevare entry-noten
    if req.notes:
        existing = trade.get("notes") or ""
        if existing:
            merged_notes = f"{existing}\n[CLOSE] {req.notes}"
        else:
            merged_notes = f"[CLOSE] {req.notes}"
        await journal.update_trade_notes(trade_id, merged_notes)

    # ── Log trade-close ──────────────────────────────────────
    exit_time = datetime.now(ET_TZ)
    ok = await journal.log_trade_close(
        trade_id    = trade_id,
        exit_price  = avg_fill,
        exit_time   = exit_time,
        exit_reason = "manual",
        pnl         = pnl,
        payload     = {
            "ibkr_close_order_id": result.get("order_id"),
            "ibkr_close_status":   status,
        },
    )

    if not ok:
        # Ordren er fyldt, men vi kunne ikke opdatere DB'en.
        # Det er en ubehagelig tilstand — alarmér frontend tydeligt.
        raise HTTPException(
            status_code=500,
            detail=f"⚠ Close-ordren blev udført på IBKR (fill @ ${avg_fill}, "
                   f"P&L=${pnl:.2f}), men trade-rækken kunne ikke opdateres. "
                   f"Tjek backend-logs.",
        )

    # ── Beregn pnl_pct til response ──────────────────────────
    if trade["side"] == "long":
        pnl_pct = (avg_fill - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - avg_fill) / entry_price * 100

    return {
        "ok":          True,
        "trade_id":    trade_id,
        "symbol":      symbol,
        "side":        trade["side"],
        "shares":      shares,
        "entry_price": entry_price,
        "exit_price":  avg_fill,
        "exit_time":   exit_time.isoformat(),
        "pnl":         round(pnl, 2),
        "pnl_pct":     round(pnl_pct, 2),
        "ibkr": {
            "order_id": result.get("order_id"),
            "status":   status,
        },
    }


# ── Strategi-dokumentation ────────────────────────────────────
# Bruges af Live Algo og Studio til at vise strategi-info via UI-knap

@app.get("/strategies/{strategy_name}/docs/{version}")
async def get_strategy_docs(strategy_name: str, version: str):
    """
    Returnér markdown-dokumentation for en strategi.

    Argumenter:
        strategy_name: fx "confluence2"
        version: "iben" (almindelig) eller "teknisk"
    """
    # Whitelist gyldige versions for at undgå path traversal
    if version not in ("iben", "teknisk"):
        raise HTTPException(status_code=400, detail="Ugyldig version — brug 'iben' eller 'teknisk'")

    # Whitelist strategi-navne ud fra eksisterende mapper
    strategies_dir = Path(__file__).parent / "strategies"
    if not (strategies_dir / strategy_name).is_dir():
        raise HTTPException(status_code=404, detail=f"Strategi '{strategy_name}' findes ikke")

    # Konstruér filnavn — formatet er STRATEGI_<NAVN>_<version>.md
    upper_name = strategy_name.upper()
    doc_file = strategies_dir / strategy_name / f"STRATEGI_{upper_name}_{version}.md"

    if not doc_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dokumentation findes ikke: {doc_file.name}"
        )

    try:
        content = doc_file.read_text(encoding="utf-8")
        return {
            "strategy": strategy_name,
            "version":  version,
            "filename": doc_file.name,
            "content":  content,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kunne ikke læse fil: {e}")


@app.get("/strategies/{strategy_name}/docs")
async def list_strategy_docs(strategy_name: str):
    """Returnér hvilke dokumentations-versioner der findes for en strategi."""
    strategies_dir = Path(__file__).parent / "strategies"
    if not (strategies_dir / strategy_name).is_dir():
        raise HTTPException(status_code=404, detail=f"Strategi '{strategy_name}' findes ikke")

    upper_name = strategy_name.upper()
    versions_available = []
    for version in ("iben", "teknisk"):
        doc_file = strategies_dir / strategy_name / f"STRATEGI_{upper_name}_{version}.md"
        if doc_file.exists():
            versions_available.append({
                "version":  version,
                "filename": doc_file.name,
                "size":     doc_file.stat().st_size,
            })

    return {
        "strategy": strategy_name,
        "versions": versions_available,
    }

# ── /ws/strategy ──────────────────────────────────────────────
@app.websocket("/ws/strategy")
async def websocket_strategy(websocket: WebSocket):
    await websocket.accept()
    strategy_clients.append(websocket)
    await websocket.send_text(json.dumps(strategy_manager.get_full_status()))

    try:
        while True:
            raw      = await websocket.receive_text()
            command  = json.loads(raw)
            response = await strategy_manager.handle_command(command)
            await websocket.send_text(json.dumps(response))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Strategy] Fejl: {e}")
    finally:
        if websocket in strategy_clients:
            strategy_clients.remove(websocket)

# ── /ws/timesales/{ticker} ────────────────────────────────────
# Live tick-stream fra IBKR for et givent symbol. Bruges af
# Time & Sales-vinduet i Trading Dash.

@app.websocket("/ws/timesales/{ticker}")
async def websocket_timesales(websocket: WebSocket, ticker: str):
    await websocket.accept()
    ticker = ticker.upper()

    conn = strategy_manager.get_ibkr()
    if conn is None or not conn.connected:
        await websocket.send_text(json.dumps({
            "type":  "error",
            "error": "ibkr_not_connected",
            "msg":   "IBKR ikke forbundet — start TWS og prøv igen",
        }))
        await websocket.close()
        return

    # Importer ib_async typer her så vi ikke crasher hvis libben mangler ved opstart
    from ib_async import Stock, Ticker

    contract = Stock(ticker, "SMART", "USD")
    ib = conn.ib

    try:
        await ib.qualifyContractsAsync(contract)
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type":  "error",
            "error": "qualify_failed",
            "msg":   f"Kan ikke kvalificere {ticker}: {e}",
        }))
        await websocket.close()
        return

    # Start tick-by-tick streamen — AllLast = alle handler (ikke bare bid/ask)
    tick_data = ib.reqTickByTickData(contract, "AllLast", numberOfTicks=0, ignoreSize=False)

    # Holder forrige tick-pris til tick test fallback
    last_tick_price = None

    def on_tick_update(ticker_obj: Ticker):
        """Kaldes hver gang IBKR pusher en ny tick."""
        nonlocal last_tick_price

        # ticker_obj.tickByTicks indeholder nye ticks siden sidste update
        for t in ticker_obj.tickByTicks:
            # Direction-logik:
            # 1. Foretrukket: sammenlign mod bid/ask (præcis)
            # 2. Fallback: tick test (sammenlign mod forrige pris)
            #    Vigtigt: i pre-market og lavt-volumen er bid/ask ofte tomme
            bid = ticker_obj.bid
            ask = ticker_obj.ask
            direction = "neutral"

            if ask and t.price >= ask:
                direction = "up"
            elif bid and t.price <= bid:
                direction = "down"
            elif last_tick_price is not None:
                # Fallback når bid/ask mangler
                if t.price > last_tick_price:
                    direction = "up"
                elif t.price < last_tick_price:
                    direction = "down"

            last_tick_price = t.price

            payload = {
                "type":      "tick",
                "ticker":    ticker,
                "time":      t.time.isoformat() if t.time else None,
                "price":     float(t.price),
                "size":      int(t.size),
                "direction": direction,
            }
            asyncio.create_task(_safe_send(websocket, payload))

    tick_data.updateEvent += on_tick_update

    # Send "klar"-besked
    await websocket.send_text(json.dumps({
        "type":   "ready",
        "ticker": ticker,
    }))

    try:
        # Hold socket åben — venter på client disconnect
        while True:
            await websocket.receive_text()  # Bruges ikke, men holder forbindelsen
    except WebSocketDisconnect:
        pass
    finally:
        # Ryd op: fjern event-handler og cancel stream
        try:
            tick_data.updateEvent -= on_tick_update
            ib.cancelTickByTickData(contract, "AllLast")
        except Exception:
            pass


async def _safe_send(websocket: WebSocket, payload: dict):
    """Send JSON via websocket; ignorer hvis socket er lukket."""
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:
        pass

# ── /ws/level2/{ticker} ───────────────────────────────────────
# Live market depth (Level 2) fra IBKR. Bruges af Level 2-vinduet
# i Trading Dash. Kræver subscription — IBKR fortæller os om det
# ikke virker.

@app.websocket("/ws/level2/{ticker}")
async def websocket_level2(websocket: WebSocket, ticker: str):
    await websocket.accept()
    ticker = ticker.upper()

    conn = strategy_manager.get_ibkr()
    if conn is None or not conn.connected:
        await websocket.send_text(json.dumps({
            "type":  "error",
            "error": "ibkr_not_connected",
            "msg":   "IBKR ikke forbundet — start TWS og prøv igen",
        }))
        await websocket.close()
        return

    from ib_async import Stock

    contract = Stock(ticker, "SMART", "USD")
    ib = conn.ib

    try:
        await ib.qualifyContractsAsync(contract)
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type":  "error",
            "error": "qualify_failed",
            "msg":   f"Kan ikke kvalificere {ticker}: {e}",
        }))
        await websocket.close()
        return

    # Vi tracker subscription-fejl så vi kan formidle dem til frontend
    subscription_error = {"failed": False, "msg": ""}

    def on_error(reqId, errorCode, errorString, contract):
        # Error 309: Market depth requires subscription
        # Error 354: Requested market data is not subscribed
        # Error 10089/10090: Market depth subscription level not granted
        if errorCode in (309, 354, 10089, 10090):
            subscription_error["failed"] = True
            subscription_error["msg"]    = f"IBKR fejl {errorCode}: {errorString}"
            asyncio.create_task(_safe_send(websocket, {
                "type":  "error",
                "error": "subscription_required",
                "msg":   subscription_error["msg"],
            }))

    ib.errorEvent += on_error

    # Start market depth — numRows=10 giver 10 niveauer på hver side
    # isSmartDepth=True bruger SMART routing (aggregeret data)
    try:
        depth_ticker = ib.reqMktDepth(contract, numRows=10, isSmartDepth=True)
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type":  "error",
            "error": "depth_request_failed",
            "msg":   f"Kunne ikke starte market depth: {e}",
        }))
        ib.errorEvent -= on_error
        await websocket.close()
        return

    def on_depth_update(t):
        """Kaldes når orderbogen opdateres."""
        if subscription_error["failed"]:
            return

        # Bid/ask sider hver indeholder DOMLevel-objekter
        bids = [
            {
                "level":      i,
                "price":      float(d.price) if d.price else 0,
                "size":       int(d.size) if d.size else 0,
                "marketMaker": d.marketMaker or "",
            }
            for i, d in enumerate(t.domBids)
        ]
        asks = [
            {
                "level":       i,
                "price":       float(d.price) if d.price else 0,
                "size":        int(d.size) if d.size else 0,
                "marketMaker": d.marketMaker or "",
            }
            for i, d in enumerate(t.domAsks)
        ]

        asyncio.create_task(_safe_send(websocket, {
            "type":   "depth",
            "ticker": ticker,
            "bids":   bids,
            "asks":   asks,
        }))

    depth_ticker.updateEvent += on_depth_update

    await websocket.send_text(json.dumps({
        "type":   "ready",
        "ticker": ticker,
    }))

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            depth_ticker.updateEvent -= on_depth_update
            ib.errorEvent -= on_error
            ib.cancelMktDepth(contract, isSmartDepth=True)
        except Exception:
            pass

# ── /market-conditions ────────────────────────────────────────
@app.get("/market-conditions", dependencies=[Depends(require_studio_auth)])
async def market_conditions_endpoint():
    try:
        from market_conditions import MarketConditionChecker

        conn = strategy_manager.get_ibkr()
        if conn is None:
            return {"error": "IBKR ikke forbundet"}

        checker    = MarketConditionChecker(conn, journal=journal)
        conditions = await checker.check()
        return checker.format_detailed(conditions)
    except Exception as e:
        return {"error": str(e)}

# ── Health ────────────────────────────────────────────────────
@app.get("/health")
async def health():
    # Scheduler-data — bruges af Analyse-fanen til at vise foudsætningerne
    # for at ORB kører (scheduleret strategi, næste kørsel, handelsdag).
    # Kun algoserveren auto-starter strategier; workstation kører manuelt.
    sched = algo_scheduler.status_dict if algo_scheduler else None
    scheduled = None
    if sched and identity.instance_role == "algoserver":
        start_job = next((j for j in sched["jobs"] if j["name"] == "start_konfluens2"), None)
        # Udfyldes kun hvis et auto-start-job findes. Konfluens 2 auto-starter
        # 09:20 ET (15:20 DK) på algoserveren (tilføjet 2026-06-17).
        if start_job:
            next_dk = sched.get("next_start_dk")
            dk_time = next_dk.split(" ")[-1] if next_dk else None
            scheduled = {
                "strategy": "Konfluens 2",
                "et_time":  start_job["et_time"],
                "dk_time":  dk_time,            # fx "15:20" — så UI kan vise "09:20 ET / 15:20 DK"
            }

    # ── Flåde-board: ALLE auto-start-strategier (ikke kun K2) + per-strategi kør-status ──
    # auto_starts = hvad maskinen auto-starter (kun algoserveren); strategies = hvad der
    # faktisk KØRER netop nu (samme form som /algo/list) — saa Status-fanens flaade-board
    # kan vise "kører nu" pr. maskine uden en ekstra auth'et round-trip.
    JOBMAP = {"start_konfluens2": "Konfluens 2", "start_europa_reversion": "Europa-reversion"}
    auto_starts = []
    if sched and identity.instance_role == "algoserver":
        for j in sched["jobs"]:
            nm = JOBMAP.get(j["name"])
            if nm:
                auto_starts.append({"strategy": nm, "et_time": j["et_time"]})
    strategies_health = [
        {
            "name":    name,
            "running": (s.status == StrategyStatus.RUNNING),
            "status":  s.status.value if hasattr(s.status, "value") else str(s.status),
            "stats":   ({"trades_today":   s.stats.trades_today,
                         "pnl_today":      s.stats.pnl_today,
                         "open_positions": s.stats.open_positions}
                        if s.status == StrategyStatus.RUNNING else {}),
        }
        for name, s in strategy_manager._strategies.items()
    ]
    return {
        "status":           "ok",
        "clients":          len(connected_clients),
        "algo_clients":     len(algo_clients),
        "strategy_clients": len(strategy_clients),
        "algo_running":     any(s.status == StrategyStatus.RUNNING
                                for s in strategy_manager._strategies.values()),
        "ibkr_connected":   ibkr_connected,
        "threshold":        alert_engine.threshold,
        "journal_events":   await journal.count_events(),
        "time":             datetime.now().isoformat(),
        # ── Analyse-fanen: drifts-forudsætninger ──
        "role":             identity.instance_role,
        "paper_trading":    identity.paper_trading,
        "scheduled":        scheduled,                         # None = manuel (workstation) [bagudkompat]
        "auto_starts":      auto_starts,                        # ALLE auto-start-strategier (K2 + Europa)
        "strategies":       strategies_health,                 # per-strategi kør-status NETOP NU
        "next_start":       sched["next_start"]     if sched else None,
        "next_start_dk":    sched.get("next_start_dk") if sched else None,
        "is_trading_day":   sched["is_trading_day"] if sched else None,
        "scheduler_running": sched["running"]        if sched else False,
    }

# ── /status — Komplet system-snapshot for autonom drift ───────
@app.get("/status")
async def status():
    """
    Returnerer komplet system-status for monitorering og fejlfinding.

    Bruges af:
      - Studio's dashboard til at se om alt kører
      - Manuel debugging (curl http://localhost:8000/status)
      - Eventuel ekstern uptime-monitor

    Ingen auth-krav — viser kun read-only health-data, ingen handlinger.
    """
    # Algoritme-status
    # Strategi-agnostisk: rapportér den første KØRENDE strategi (algoserver = Konfluens 2,
    # workstation = EUREVERSION/BuyTheDip). ORB-hardkodningen var død på begge maskiner.
    running_strat = next((s for s in strategy_manager._strategies.values()
                          if s.status == StrategyStatus.RUNNING), None)
    algo_running = running_strat is not None

    algo_stats = None
    if running_strat:
        algo_stats = {
            "strategy":       running_strat.name,
            "status":         running_strat.status,
            "trades_today":   running_strat.stats.trades_today,
            "wins_today":     running_strat.stats.wins_today,
            "losses_today":   running_strat.stats.losses_today,
            "pnl_today":      round(running_strat.stats.pnl_today, 2),
            "open_positions": running_strat.stats.open_positions,
            "last_trade":     running_strat.stats.last_trade_time,
        }

    return {
        "ok":   True,
        "time": datetime.now().isoformat(),

        "identity": {
            "account":  identity.account_display_name,
            "instance": identity.instance_display_name,
            "role":     identity.instance_role,
            "ibkr":     identity.ibkr_account,
            "paper":    identity.paper_trading,
        },

        "backend": {
            "clients":          len(connected_clients),
            "algo_clients":     len(algo_clients),
            "strategy_clients": len(strategy_clients),
            "journal_events":   await journal.count_events(),
        },

        "ibkr": {
            "connected": ibkr_connected,
        },

        "tws_watchdog": tws_watchdog.status_dict if tws_watchdog else {"running": False},

        "scheduler": algo_scheduler.status_dict if algo_scheduler else {"running": False},

        "algo": {
            "running": algo_running,
            "stats":   algo_stats,
        },

        "risk": strategy_manager.risk_manager.get_status_dict(),
    }

# ── /auth/login ───────────────────────────────────────────────
# Login til Studio. Returnerer en session-token der bruges i
# Authorization-headeren på efterfølgende requests.

class LoginRequest(BaseModel):
    password: str


@app.post("/auth/login")
async def auth_login(req: LoginRequest):
    if req.password != identity.studio_password:
        # Lille delay for at gøre brute-force mere besværligt
        await asyncio.sleep(0.5)
        raise HTTPException(status_code=401, detail="Forkert password")

    token = _create_studio_token()
    return {"token": token, "expires": "indtil backend genstartes"}


@app.post("/auth/logout")
async def auth_logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        _studio_tokens.discard(token)
    return {"ok": True}


@app.get("/auth/check")
async def auth_check(_=Depends(require_studio_auth)):
    """Tjek om token stadig er gyldig. Bruges af frontend til at vide
    om brugeren skal redirectes til login."""
    return {"ok": True}

# ── /algo — Studio kontrol af algoritmen ──────────────────────
# Disse endpoints styrer algoritmen via REST i stedet for WebSocket.
# Bruges af Studio's "Strategier"-side for nem fjernstart/stop.
# Bruger strategy_manager (samme tilgang som /health og /ws/algo).

@app.get("/algo/list", dependencies=[Depends(require_studio_auth)])
async def algo_list():
    """Returnér status for ALLE registrerede strategier."""
    ibkr = strategy_manager.get_ibkr()
    ibkr_connected = ibkr is not None and ibkr.connected

    strategies = []
    for name, strat in strategy_manager._strategies.items():
        running = strat.status == StrategyStatus.RUNNING
        stats = {}
        if running:
            stats = {
                "pnl_today":      strat.stats.pnl_today,
                "trades_today":   strat.stats.trades_today,
                "open_positions": strat.stats.open_positions,
            }
        strategies.append({
            "name":    name,
            "running": running,
            "status":  strat.status.value if hasattr(strat.status, "value") else str(strat.status),
            "stats":   stats,
        })

    return {
        "strategies":     strategies,
        "ibkr_connected": ibkr_connected,
        "instance":       identity.instance_display_name,
    }


@app.get("/algo/status", dependencies=[Depends(require_studio_auth)])
async def algo_status():
    """Bevaret for bagudkompatibilitet — status for den FØRSTE kørende strategi."""
    running_strat = next((s for s in strategy_manager._strategies.values()
                          if s.status == StrategyStatus.RUNNING), None)
    running = running_strat is not None

    ibkr = strategy_manager.get_ibkr()
    ibkr_connected = ibkr is not None and ibkr.connected

    stats = {}
    if running_strat:
        stats = {
            "pnl_today":      running_strat.stats.pnl_today,
            "trades_today":   running_strat.stats.trades_today,
            "open_positions": running_strat.stats.open_positions,
        }

    return {
        "running":        running,
        "ibkr_connected": ibkr_connected,
        "instance":       identity.instance_display_name,
        "stats":          stats,
    }


class AlgoActionRequest(BaseModel):
    strategy: str = ""


@app.post("/algo/start", dependencies=[Depends(require_studio_auth)])
async def algo_start_endpoint(req: AlgoActionRequest = AlgoActionRequest()):
    """Start en navngiven strategi. Idempotent."""
    strat = strategy_manager._strategies.get(req.strategy)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Ukendt strategi: {req.strategy}")
    if strat.status == StrategyStatus.RUNNING:
        return {"ok": True, "already_running": True, "message": f"{req.strategy} kører allerede"}

    asyncio.create_task(start_algo(req.strategy))
    return {"ok": True, "already_running": False, "message": f"{req.strategy} startes"}


@app.post("/algo/stop", dependencies=[Depends(require_studio_auth)])
async def algo_stop_endpoint(req: AlgoActionRequest = AlgoActionRequest()):
    """Stop en navngiven strategi. Idempotent."""
    strat = strategy_manager._strategies.get(req.strategy)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Ukendt strategi: {req.strategy}")
    if strat.status != StrategyStatus.RUNNING:
        return {"ok": True, "was_running": False, "message": f"{req.strategy} kørte ikke"}

    await stop_algo(req.strategy)
    await broadcast_algo({
        "type": "algo_status", "strategy": req.strategy, "status": "stopped",
        "message": f"{req.strategy} stoppet via Studio",
        "total_pnl": 0, "positions": 0, "trades": 0,
        "time": datetime.now().strftime("%H:%M:%S"),
    })
    return {"ok": True, "was_running": True, "message": f"{req.strategy} stoppet"}

@app.get("/account", dependencies=[Depends(require_studio_auth)])
async def account_info():
    """Returnerer identiteten for denne backend-instans. Bruges af frontend."""
    return {
        "account_id":             identity.account_id,
        "account_display_name":   identity.account_display_name,
        "instance_role":          identity.instance_role,
        "instance_display_name":  identity.instance_display_name,
        "ibkr_account":           identity.ibkr_account,
        "paper_trading":          identity.paper_trading,
        "autostart_strategies":   identity.autostart_strategies,
    }

@app.get("/peers")
async def get_peers():
    """Returnér listen af kendte maskiner i Trading Dash-netværket.
    
    Frontend bruger denne til at vide hvilke backends den skal kalde.
    Ingen auth-krav — det er kun maskinnavn/IP, intet hemmeligt.
    """
    import json
    peers_path = Path(__file__).parent / "peers.json"
    if not peers_path.exists():
        return {"peers": []}
    try:
        with open(peers_path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        return {"peers": [], "error": str(e)}

@app.get("/machines")
async def get_machines():
    """Samlet liste over maskiner Studio kan læse data for.

    is_self=True   → denne maskines egen data (læses direkte fra journal.db)
    har_arkiv=True → en anden maskine hvis data ligger replikeret i archives/

    Ingen auth — kun maskinnavne, intet hemmeligt (som /peers).
    """
    import replication_store
    sources = replication_store.list_sources()
    out = [{
        "id":        identity.source_id,
        "name":      identity.instance_display_name,
        "source_id": identity.source_id,
        "har_arkiv": False,
        "is_self":   True,
    }]
    for s in sources:
        if s == identity.source_id:
            continue
        out.append({
            "id":        s,
            "name":      s.replace("_", " ").title(),
            "source_id": s,
            "har_arkiv": True,
            "is_self":   False,
        })
    return {"machines": out}

@app.get("/fleet/network")
async def fleet_network():
    """Tailscale-status pr. maskine — 'online' = på tailnet = maskinen er tændt.

    Dette er UAFHÆNGIGT af om trading-backenden kører: Tailscale ved om en
    maskine er forbundet til netværket, også når uvicorn ikke kører. Bruges af
    Analyse-fanen til en pålidelig 'tændt'-status (fetch til /health kan ikke
    skelne 'backend nede' fra 'maskine slukket' over Tailscale).

    Ingen auth — kun maskinnavne/online-flag, intet hemmeligt (som /peers).
    """
    import shutil
    ts = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        proc = await asyncio.create_subprocess_exec(
            ts, "status", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return {"ok": False, "error": stderr.decode("utf-8", "replace").strip()
                    or f"tailscale exit {proc.returncode}", "machines": {}}
        data = json.loads(stdout.decode("utf-8", "replace"))
    except FileNotFoundError:
        return {"ok": False, "error": "tailscale CLI ikke fundet", "machines": {}}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "machines": {}}

    out: dict = {}

    def add(node: dict, force_online: bool = False):
        if not node:
            return
        rec = {
            "online":    True if force_online else bool(node.get("Online")),
            "last_seen": node.get("LastSeen"),
            "ip":        (node.get("TailscaleIPs") or [None])[0],
            "os":        node.get("OS"),
        }
        # Match på både HostName og første DNS-label, små bogstaver — så
        # peers.json's host (fx "win11sbb") rammer uanset Tailscale-navngivning.
        names = set()
        hn = (node.get("HostName") or "").lower()
        if hn:
            names.add(hn)
        dns = (node.get("DNSName") or "").lower().rstrip(".")
        if dns:
            names.add(dns.split(".")[0])
        for n in names:
            out[n] = rec

    add(data.get("Self"), force_online=True)   # Self (denne maskine) er pr. def. online
    for peer in (data.get("Peer") or {}).values():
        add(peer)

    return {"ok": True, "machines": out}

@app.get("/internal-key", dependencies=[Depends(require_studio_auth)])
async def get_internal_key():
    """Giver den autentificerede Studio-browser den fælles interne nøgle,
    så den kan kalde peer-maskiner direkte (Vej A).

    Beskyttet af require_studio_auth — kun en allerede-indlogget bruger
    (eller dev-mode workstation) kan hente den.
    """
    return {"internal_key": identity.internal_key}

# Tracker hvornår sidste account_snapshot blev skrevet til journal —
# så vi ikke logger ved hver auto-refresh, kun én gang i timen.
_last_snapshot_journaled_at: datetime | None = None


def _best_snapshot_price(snap: dict):
    """Bedste tilgængelige 'aktuelle' pris fra et get_snapshot-dict.

    IBKR's `last` er ofte NaN/0 uden for RTH eller ved forsinket data — så vi
    falder tilbage: last → bid/ask-midtpris → seneste close → open. Returnerer
    None hvis intet brugbart felt findes.

    Det er denne fallback der gør, at Studio ALTID kan vise en aktuel pris pr.
    position (og dermed urealiseret P&L pr. handel) — ikke kun det aggregerede
    konto-tal i toppen. Før faldt vi tilbage til None hvis `last` manglede, så
    rækkerne stod tomme selvom konto-summen havde et tal.
    """
    if not snap:
        return None
    import math

    def f(v):
        if v is None:
            return None
        try:
            x = float(v)
        except (ValueError, TypeError):
            return None
        if math.isnan(x) or math.isinf(x) or x <= 0:
            return None
        return x

    last = f(snap.get("last"))
    if last is not None:
        return last
    bid, ask = f(snap.get("bid")), f(snap.get("ask"))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 4)
    for key in ("close", "open"):
        v = f(snap.get(key))
        if v is not None:
            return v
    return None


@app.get("/account/snapshot", dependencies=[Depends(require_studio_auth)])
async def account_snapshot(force_journal: bool = False):
    """
    Returner et live snapshot af IBKR-kontoen.

    Inkluderer NLV, cash, P&L og åbne positioner. Bruges af Studio's
    konto-side til auto-refresh og manuel refresh.

    Logger til journal højst én gang i timen — eller når force_journal=true
    sættes (manuel refresh fra UI).
    """
    global _last_snapshot_journaled_at

    conn = strategy_manager.get_ibkr()
    if conn is None:
        return {
            "ok":    False,
            "error": "IBKR ikke forbundet",
        }

    try:
        summary   = conn.get_account_summary()
        positions = conn.get_positions()

        # Saniter NaN/Inf fra summary (IBKR returnerer dem en gang imellem)
        import math
        for k, v in summary.items():
            try:
                if math.isnan(float(v)) or math.isinf(float(v)):
                    summary[k] = 0.0
            except (ValueError, TypeError):
                summary[k] = 0.0

        # Berig positioner med live pris og estimeret P&L
        import math

        def safe_float(v):
            """Konverter til float — eller None hvis NaN/Inf/falsy."""
            if v is None:
                return None
            try:
                f = float(v)
                if math.isnan(f) or math.isinf(f):
                    return None
                return f
            except (ValueError, TypeError):
                return None

        # Hent alle priser parallelt med 2-sek timeout per ticker
        async def fetch_price(ticker):
            try:
                snap = await asyncio.wait_for(conn.get_snapshot(ticker), timeout=2.0)
                # Robust pris: last → bid/ask-midt → close → open. IBKR's `last`
                # er ofte NaN uden for RTH, hvilket før gav tomme pris/P&L-felter.
                return _best_snapshot_price(snap)
            except (asyncio.TimeoutError, Exception):
                return None

        prices = await asyncio.gather(*[fetch_price(p["ticker"]) for p in positions])

        enriched = []
        for p, price in zip(positions, prices):
            cost = safe_float(p["avg_cost"])
            qty  = p["position"]

            if price is not None and cost is not None and cost != 0:
                pnl     = round((price - cost) * qty, 2)
                pnl_pct = round((price - cost) / cost * 100, 2)
            else:
                pnl     = None
                pnl_pct = None

            enriched.append({
                "ticker":     p["ticker"],
                "position":   qty,
                "avg_cost":   cost,
                "last_price": price,
                "pnl":        pnl,
                "pnl_pct":    pnl_pct,
            })

        result = {
            "ok":              True,
            "ibkr_account":    identity.ibkr_account,
            "paper_trading":   identity.paper_trading,
            "net_liquidation": summary["net_liquidation"],
            "cash_balance":    summary["cash_balance"],
            "unrealized_pnl":  summary["unrealized_pnl"],
            "realized_pnl":    summary["realized_pnl"],
            "positions":       enriched,
            "checked_at":      datetime.now().isoformat(),
        }

        # Journal: én gang per time som baseline + når force_journal er sat
        now = datetime.now()
        should_journal = force_journal or (
            _last_snapshot_journaled_at is None or
            (now - _last_snapshot_journaled_at).total_seconds() >= 3600
        )
        if should_journal:
            await journal.log_event(
                source     = "system",
                event_type = "account_snapshot",
                payload    = {
                    "net_liquidation": result["net_liquidation"],
                    "cash_balance":    result["cash_balance"],
                    "unrealized_pnl":  result["unrealized_pnl"],
                    "realized_pnl":    result["realized_pnl"],
                    "open_positions":  len(enriched),
                    "force":           force_journal,
                },
            )
            _last_snapshot_journaled_at = now

        return result

    except Exception as e:
        return {
            "ok":    False,
            "error": f"Fejl ved hentning: {str(e)}",
        }
    
# ── /account/dash-snapshot — Trading Dash konto-data ──────────
# Samme data som /account/snapshot men uden auth-krav.
# Trading Dash kører kun lokalt på 127.0.0.1 og har ikke login.
@app.get("/account/dash-snapshot")
async def account_dash_snapshot():
    """
    Open snapshot endpoint til Trading Dash (lokal frontend).
    Returnerer NLV, cash, P&L og åbne positioner beriget med live priser.
    """
    conn = strategy_manager.get_ibkr()
    if conn is None or not conn.connected:
        return {
            "ok":    False,
            "error": "IBKR ikke forbundet",
        }

    try:
        import math

        summary   = conn.get_account_summary()
        positions = conn.get_positions()

        # Saniter NaN/Inf fra summary
        for k, v in summary.items():
            try:
                if math.isnan(float(v)) or math.isinf(float(v)):
                    summary[k] = 0.0
            except (ValueError, TypeError):
                summary[k] = 0.0

        def safe_float(v):
            if v is None:
                return None
            try:
                f = float(v)
                if math.isnan(f) or math.isinf(f):
                    return None
                return f
            except (ValueError, TypeError):
                return None

        # Hent alle priser parallelt med 2-sek timeout per ticker
        async def fetch_price(ticker):
            try:
                snap = await asyncio.wait_for(conn.get_snapshot(ticker), timeout=2.0)
                # Robust pris: last → bid/ask-midt → close → open. IBKR's `last`
                # er ofte NaN uden for RTH, hvilket før gav tomme pris/P&L-felter.
                return _best_snapshot_price(snap)
            except (asyncio.TimeoutError, Exception):
                return None

        # Wrap hele gather i timeout — hvis markedet er lukket og IBKR
        # ikke svarer, returnerer vi uden live priser i stedet for at hænge.
        try:
            prices = await asyncio.wait_for(
                asyncio.gather(*[fetch_price(p["ticker"]) for p in positions]),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            print("[DashSnapshot] Timeout — markedet er lukket, returnerer uden live priser")
            prices = [None] * len(positions)

        enriched = []
        for p, price in zip(positions, prices):
            cost = safe_float(p["avg_cost"])
            qty  = p["position"]

            if price is not None and cost is not None and cost != 0:
                pnl     = round((price - cost) * qty, 2)
                pnl_pct = round((price - cost) / cost * 100, 2)
            else:
                pnl     = None
                pnl_pct = None

            enriched.append({
                "ticker":        p["ticker"],
                "position":      qty,
                "avg_cost":      cost,
                "last_price":    price,
                "current_price": safe_float(price),
                "market_value":  safe_float(price * qty) if price is not None else None,
                "pnl":           pnl,
                "pnl_pct":       pnl_pct,
            })

        # Risiko-graenseforbrug pr. strategi (unik vaerdi — TWS kender ikke vores
        # graenser). Per-strategi-graensen hentes fra strategiens egen config.
        risk_status = strategy_manager.risk_manager.get_status_dict()
        per_strategy = []
        for s, pnl_today in risk_status.get("pnl_by_strategy", {}).items():
            strat = strategy_manager._strategies.get(s)
            limit = getattr(strat.config, "max_daily_loss", None) if strat else None
            per_strategy.append({"strategy": s, "pnl_today": pnl_today, "limit": limit})

        return {
            "ok":                   True,
            "ibkr_account":         identity.ibkr_account,
            "paper_trading":        identity.paper_trading,
            "net_liquidation":      summary["net_liquidation"],
            "cash_balance":         summary["cash_balance"],
            "unrealized_pnl":       summary["unrealized_pnl"],
            "realized_pnl":         summary["realized_pnl"],
            "buying_power":         summary.get("buying_power", 0),
            "available_funds":      summary.get("available_funds", 0),
            "excess_liquidity":     summary.get("excess_liquidity", 0),
            "maint_margin":         summary.get("maint_margin", 0),
            "gross_position_value": summary.get("gross_position_value", 0),
            "positions":            enriched,
            "risk": {
                "total_pnl_today":  risk_status.get("total_pnl_today", 0),
                "daily_loss_limit": risk_status.get("daily_loss_limit", 0),
                "per_strategy":     per_strategy,
            },
            "checked_at":           datetime.now().isoformat(),
        }

    except Exception as e:
        return {
            "ok":    False,
            "error": f"Fejl ved hentning: {str(e)}",
        }

# ── /ticker/info — slå firmanavn op for én ticker ─────────────
@app.get("/ticker/info")
async def get_ticker_info(ticker: str):
    """
    Returnér det smart-forkortede firmanavn for en ticker.
    Tom string hvis ikke fundet — frontend viser så "(ukendt)".
    """
    from ticker_info import get_ticker_name
    name = await get_ticker_name(ticker)
    return {"ticker": ticker.upper(), "name": name}

@app.get("/studio")
async def studio_index():
    """Servér Studio's index.html. Studio er en separat browser-baseret app
    til konfiguration, analyse og administration. Kører i samme backend."""
    studio_path = Path(__file__).parent / "studio" / "index.html"
    if not studio_path.exists():
        return {"error": "Studio findes ikke — placeholder mangler i backend/studio/"}
    return FileResponse(studio_path)

@app.get("/studio/{filename}")
async def studio_static(filename: str):
    """Servér statiske filer fra studio/-mappen (fx dev_config.js).
    
    Begrænset til kendte filtyper for sikkerhed — vi vil ikke risikere
    at servere fx account.yaml hvis nogen gætter stien.
    """
    # Whitelist af tilladte filer
    allowed = {"dev_config.js"}
    if filename not in allowed:
        return {"error": "File not found"}, 404
    
    file_path = Path(__file__).parent / "studio" / filename
    if not file_path.exists():
        return {"error": "File not found"}, 404
    
    return FileResponse(file_path)

# ── Replikering: modtager-endpoint (algoserveren) ──────────────
@app.post("/replication/upload")
async def replication_upload(
    request:  Request,
    source:   str = "",
    artifact: str = "db",
    x_internal_key: str = Header(None),
):
    if not identity.internal_key or x_internal_key != identity.internal_key:
        raise HTTPException(status_code=401, detail="Ugyldig intern nøgle")
    body = await request.body()
    import replication_store
    try:
        result = await asyncio.to_thread(
            replication_store.store_artifact, source, artifact, body
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lagring fejlede: {e}")
    return result


@app.get("/replication/sources", dependencies=[Depends(require_studio_auth)])
async def replication_sources():
    import replication_store
    return {"sources": replication_store.list_sources()}

# ── Analyse-side endpoint ──────────────────────────
@app.get("/analysis/summary", dependencies=[Depends(require_studio_auth)])
async def analysis_summary(period: str = "all"):
    from analysis import build_summary
    if period not in ("today", "7d", "30d", "all"):
        return {"error": f"Ugyldig periode: {period}"}
    return build_summary(period)


# ── Fleet-rapport + AI dagsrapport ─────────────────────────────
@app.get("/analysis/fleet-summary", dependencies=[Depends(require_studio_auth)])
async def analysis_fleet_summary(period: str = "today"):
    """Tal på tværs af alle maskiner (denne + arkiver). Ingen AI."""
    if period not in ("today", "7d", "30d", "all"):
        return {"error": f"Ugyldig periode: {period}"}
    import fleet_report
    return await fleet_report.build_fleet_report(journal.db, period=period)


@app.post("/ai/daily-report", dependencies=[Depends(require_studio_auth)])
async def ai_daily_report(period: str = "today", model: str = "qwen3:8b"):
    """Fleet-tal + AI-narrativ (dansk). Tallene returneres altid; narrative er
    None hvis Ollama er nede."""
    if period not in ("today", "7d", "30d", "all"):
        return {"error": f"Ugyldig periode: {period}"}
    import daily_report
    return await daily_report.generate_daily_report(journal.db, period=period, model=model)
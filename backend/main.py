import asyncio
import json
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware

from alert_engine import AlertEngine
from paper_trading import PaperTrading
from strategy_manager import StrategyManager
from strategy_base    import StrategyStatus
from risk_manager import RiskConfig

import notifier
from tws_watchdog import TWSWatchdog
from scheduler    import AlgoScheduler

from journal          import Journal
from orders_tracker   import get_tracker

from finnhub_news import FinnhubNewsFeed

from accounts import identity

from fastapi.responses import FileResponse
from pathlib import Path

from contextlib import asynccontextmanager
import aiosqlite

import secrets
from fastapi import HTTPException, Header

from fastapi import Depends
from pydantic import BaseModel

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
    
paper_trading     = PaperTrading()
connected_clients: list[WebSocket] = []
current_prices:   dict[str, float] = {}

algo_clients: list[WebSocket] = []

strategy_manager = StrategyManager(risk_config=RiskConfig(daily_loss_limit=300.0))
strategy_clients: list[WebSocket] = []

ibkr_conn      = None
live_feed      = None
live_feed_task = None
ibkr_connected = False
finnhub_feed:      FinnhubNewsFeed | None = None
finnhub_feed_task                          = None
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


# ── Portfolio loop ────────────────────────────────────────────
async def portfolio_loop():
    while True:
        await asyncio.sleep(5)
        if connected_clients:
            summary = paper_trading.get_summary(current_prices)
            await broadcast({"type": "portfolio", "data": summary})


# ── Algo ──────────────────────────────────────────────────────
async def start_algo(strategy_name: str = "Momentum ORB"):
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


async def stop_algo(strategy_name: str = "Momentum ORB"):
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
        from algo_momentum import MomentumORB
        from strategy_base import StrategyConfig

        momentum_config = StrategyConfig(
            max_loss_per_trade  = 100.0,
            max_daily_loss      = 300.0,     # var 150.0
            max_open_positions  = 3,
            max_position_size   = 1000.0,    # Capital per handel
        )
        momentum = MomentumORB(strategy_manager.get_ibkr(), config=momentum_config)
        strategy_manager.register(momentum)
        # Override broadcast: algoen sender via algo_clients (ikke strategy_clients)
        momentum._broadcast_fn = broadcast_algo_sync
        print(f"[Server] MomentumORB registreret — capital per handel: ${momentum_config.max_position_size:.0f}")

        # ── Registrér Konfluens-strategien parallelt med ORB ────
        from algo_confluence import ConfluenceLive

        confluence_config = StrategyConfig(
            max_loss_per_trade  = 150.0,     # Lidt løsere end ORB
            max_daily_loss      = 300.0,     # var 250.0
            max_open_positions  = 3,
            max_position_size   = 1000.0,    # Samme kapital pr. trade som ORB
        )
        confluence = ConfluenceLive(strategy_manager.get_ibkr(), config=confluence_config)
        strategy_manager.register(confluence)
        confluence._broadcast_fn = broadcast_algo_sync
        print(f"[Server] Konfluens registreret — capital per handel: ${confluence_config.max_position_size:.0f}")

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

    asyncio.create_task(portfolio_loop())
    asyncio.create_task(start_ibkr_feed())
    asyncio.create_task(start_finnhub_feed())
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
        """Saml dagens stats fra MomentumORB til daily summary push."""
        momentum = strategy_manager._strategies.get("Momentum ORB")
        if not momentum:
            return {"trades": 0, "wins": 0, "total_pnl": 0.0}
        return {
            "trades":    momentum.stats.trades_today,
            "wins":      momentum.stats.wins_today,
            "total_pnl": momentum.stats.pnl_today,
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

async def start_finnhub_feed():
    """Start Finnhub news polling i baggrunden."""
    global finnhub_feed, finnhub_feed_task

    try:
        finnhub_feed      = FinnhubNewsFeed(broadcast)
        finnhub_feed_task = asyncio.create_task(finnhub_feed.start())
        print("[FinnhubNews] Feed startet — poller hver 5. minut")
    except Exception as e:
        print(f"[FinnhubNews] Kunne ikke starte: {e}")

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

    # 5. Annullér de gemte baggrunds-tasks (feed + finnhub)
    for _task_name, _task in (("live_feed_task", live_feed_task),
                              ("finnhub_feed_task", finnhub_feed_task)):
        if _task is not None and not _task.done():
            _task.cancel()
            try:
                await _task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[Server] {_task_name} afsluttede med fejl: {e}")
            print(f"[Server] {_task_name} annulleret")

    # 6. Annullér KUN vores egne baggrunds-loops ved navn (portfolio_loop,
    #    mock_data_loop). Vi maa IKKE feje alle tasks, for det rammer ogsaa
    #    uvicorns egen lifespan-task og giver CancelledError-traceback.
    #    Identificér ved coroutine-navn, spring shutdown selv over.
    _OURS = {"portfolio_loop", "mock_data_loop"}
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

    summary = paper_trading.get_summary(current_prices)
    await websocket.send_text(json.dumps({"type": "portfolio", "data": summary}))
    await websocket.send_text(json.dumps({"type": "ibkr_status", "connected": ibkr_connected}))

    try:
        while True:
            raw     = await websocket.receive_text()
            message = json.loads(raw)

            if message["type"] == "set_threshold":
                alert_engine.set_threshold(float(message["value"]))

            elif message["type"] == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif message["type"] == "buy":
                result = paper_trading.buy(
                    ticker=message["ticker"],
                    shares=float(message["shares"]),
                    price=float(message["price"]),
                )
                await websocket.send_text(json.dumps({"type": "trade_result", "data": result}))
                if result["success"]:
                    summary = paper_trading.get_summary(current_prices)
                    await broadcast({"type": "portfolio", "data": summary})

            elif message["type"] == "sell":
                result = paper_trading.sell(
                    ticker=message["ticker"],
                    shares=float(message["shares"]),
                    price=float(message["price"]),
                )
                await websocket.send_text(json.dumps({"type": "trade_result", "data": result}))
                if result["success"]:
                    summary = paper_trading.get_summary(current_prices)
                    await broadcast({"type": "portfolio", "data": summary})

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

            elif message["type"] == "reset_portfolio":
                paper_trading.reset()
                summary = paper_trading.get_summary(current_prices)
                await broadcast({"type": "portfolio", "data": summary})

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
    for strat_name in ("Momentum ORB", "Konfluens", "Konfluens 2", "Europa-reversion"):
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

    try:
        while True:
            raw     = await websocket.receive_text()
            message = json.loads(raw)

            if message.get("command") == "start":
                # Strategi-agnostisk start. Default = "Momentum ORB" for bagudkompat
                # (eksisterende LiveAlgo.tsx sender ingen strategy-felt).
                strat_name = message.get("strategy", "Momentum ORB")
                print(f"[Algo] Start-kommando modtaget for: {strat_name}")
                asyncio.create_task(start_algo(strat_name))

            elif message.get("command") == "stop":
                strat_name = message.get("strategy", "Momentum ORB")
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


@app.get("/orders/list")
async def get_orders_list(period_hours: int = 24):
    """Returnér alle Trading Dash-ordrer i de seneste N timer med live status."""
    ibkr = strategy_manager.get_ibkr()
    orders = await get_tracker().get_all_orders(ibkr, period_hours=period_hours)
    return {"orders": orders, "ibkr_connected": ibkr is not None and ibkr.connected}


@app.post("/orders/cancel")
async def cancel_order(req: CancelOrderRequest):
    """Annullér en åben ordre via IBKR."""
    ibkr = strategy_manager.get_ibkr()
    result = await get_tracker().cancel(ibkr, req.order_id)
    return result

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
        strategy_name: fx "momentum_orb"
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
        start_job = next((j for j in sched["jobs"] if j["name"] == "start_algo"), None)
        # Kun udfyld hvis et auto-start-job faktisk findes. ORB blev fjernet
        # 2026-06-10, så start_job er None og scheduled forbliver None (Analyse
        # viser "ingen planlagt strategi"). Når et nyt auto-start-job tilføjes,
        # opdateres "strategy"-labelen her til den nye strategi.
        if start_job:
            next_dk = sched.get("next_start_dk")
            dk_time = next_dk.split(" ")[-1] if next_dk else None
            scheduled = {
                "strategy": "Momentum ORB",
                "et_time":  start_job["et_time"],
                "dk_time":  dk_time,            # fx "15:44" — så UI kan vise "09:44 ET / 15:44 DK"
            }
    return {
        "status":           "ok",
        "clients":          len(connected_clients),
        "algo_clients":     len(algo_clients),
        "strategy_clients": len(strategy_clients),
        "algo_running":     ("Momentum ORB" in strategy_manager._strategies and
                             strategy_manager._strategies["Momentum ORB"].status == StrategyStatus.RUNNING),
        "ibkr_connected":   ibkr_connected,
        "threshold":        alert_engine.threshold,
        "journal_events":   await journal.count_events(),
        "time":             datetime.now().isoformat(),
        # ── Analyse-fanen: drifts-forudsætninger ──
        "role":             identity.instance_role,
        "paper_trading":    identity.paper_trading,
        "scheduled":        scheduled,                         # None = manuel (workstation)
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
    momentum = strategy_manager._strategies.get("Momentum ORB")
    algo_running = (momentum is not None and
                    momentum.status == StrategyStatus.RUNNING)

    algo_stats = None
    if momentum:
        algo_stats = {
            "status":         momentum.status,
            "trades_today":   momentum.stats.trades_today,
            "wins_today":     momentum.stats.wins_today,
            "losses_today":   momentum.stats.losses_today,
            "pnl_today":      round(momentum.stats.pnl_today, 2),
            "open_positions": momentum.stats.open_positions,
            "last_trade":     momentum.stats.last_trade_time,
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
    """Bevaret for bagudkompatibilitet — status for Momentum ORB alene."""
    momentum = strategy_manager._strategies.get("Momentum ORB")
    running  = momentum is not None and momentum.status == StrategyStatus.RUNNING

    ibkr = strategy_manager.get_ibkr()
    ibkr_connected = ibkr is not None and ibkr.connected

    stats = {}
    if running:
        stats = {
            "pnl_today":      momentum.stats.pnl_today,
            "trades_today":   momentum.stats.trades_today,
            "open_positions": momentum.stats.open_positions,
        }

    return {
        "running":        running,
        "ibkr_connected": ibkr_connected,
        "instance":       identity.instance_display_name,
        "stats":          stats,
    }


class AlgoActionRequest(BaseModel):
    strategy: str = "Momentum ORB"


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
                "ticker":     p["ticker"],
                "position":   qty,
                "avg_cost":   cost,
                "last_price": price,
                "pnl":        pnl,
                "pnl_pct":    pnl_pct,
            })

        return {
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
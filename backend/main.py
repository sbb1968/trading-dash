import asyncio
import json
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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

from accounts import identity

from fastapi.responses import FileResponse
from pathlib import Path

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


def require_studio_auth(authorization: str = Header(None)) -> None:
    """
    FastAPI dependency: kræver gyldig Studio-token i Authorization header.
    Brug som: dependencies=[Depends(require_studio_auth)] på protected endpoints.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    token = authorization[7:]  # Strip "Bearer "
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
async def start_algo():
    """Start MomentumORB via StrategyManager (med fælles risk limits)."""
    success, msg = await strategy_manager.start_strategy("Momentum ORB")

    if not success:
        await broadcast_algo({
            "type": "algo_status", "status": "error",
            "message": msg,
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


async def stop_algo():
    """Stop MomentumORB via StrategyManager."""
    await strategy_manager.stop_strategy("Momentum ORB", reason="Manuelt stoppet fra UI")
    print("[Algo] MomentumORB stoppet via StrategyManager")

# ── Startup ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global ibkr_connected

    await journal.init()
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
    if ok:
        from algo_momentum import MomentumORB
        from strategy_base import StrategyConfig

        momentum_config = StrategyConfig(
            max_loss_per_trade  = 100.0,
            max_daily_loss      = 150.0,
            max_open_positions  = 3,
            max_position_size   = 2500.0,    # Capital per handel
        )
        momentum = MomentumORB(strategy_manager.get_ibkr(), config=momentum_config)
        strategy_manager.register(momentum)
        # Override broadcast: algoen sender via algo_clients (ikke strategy_clients)
        momentum._broadcast_fn = broadcast_algo_sync
        print(f"[Server] MomentumORB registreret — capital per handel: ${momentum_config.max_position_size:.0f}")

    asyncio.create_task(portfolio_loop())
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
    )
    await algo_scheduler.start()
    print("[Server] Algo-scheduler startet — autonom dagsplan aktiv")

    # Notificer Iben at backend er oppe og kører
    await notifier.send(
        message  = f"Backend startet på {identity.instance_display_name}. Autonom drift aktiv.",
        title    = "🟢 Trading Dash backend startet",
        priority = 2,
        tags     = "robot,green_circle",
    )

# ── Shutdown ──────────────────────────────────────────────────
@app.on_event("shutdown")
async def shutdown():
    """Ryd op pænt — stop scheduler, watchdog, algo og luk IBKR."""
    print("[Server] Shutting down...")

    # 1. Stop scheduler først så ingen nye jobs starter
    if algo_scheduler:
        await algo_scheduler.stop()
        print("[Server] Scheduler stoppet")

    # 2. Stop watchdog så vi ikke får falske offline-alerts
    if tws_watchdog:
        await tws_watchdog.stop()
        print("[Server] Watchdog stoppet")

    # 3. Stop kørende strategi pænt (lukker åbne positioner via on_stop)
    momentum = strategy_manager._strategies.get("Momentum ORB")
    if momentum and momentum.status == StrategyStatus.RUNNING:
        await strategy_manager.stop_strategy("Momentum ORB", reason="Backend shutdown")
        print("[Server] MomentumORB stoppet pænt")

    # 4. Luk IBKR-forbindelse
    try:
        ibkr = strategy_manager.get_ibkr()
        if ibkr and ibkr.connected:
            ibkr.disconnect()
            print("[Server] IBKR forbindelse lukket")
    except Exception as e:
        print(f"[Server] Fejl ved IBKR-disconnect: {e}")

    # 5. Journal shutdown event (best effort)
    try:
        await journal.log_event(
            source     = "system",
            event_type = "system_shutdown",
            payload    = {"message": "Trading Dash backend stoppet"},
        )
    except Exception:
        pass

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

    momentum = strategy_manager._strategies.get("Momentum ORB")
    if momentum and momentum.status == StrategyStatus.RUNNING:
        status  = "trading"
        message = "Algoritme kører"
        pnl     = momentum.stats.pnl_today
        pos     = momentum.stats.open_positions
        trades  = momentum.stats.trades_today
    else:
        status  = "idle"
        message = "Algoritmen er ikke startet"
        pnl     = 0
        pos     = 0
        trades  = 0

    await websocket.send_text(json.dumps({
        "type": "algo_status", "status": status, "message": message,
        "total_pnl": pnl, "positions": pos, "trades": trades,
        "time": datetime.now().strftime("%H:%M:%S"),
    }))

    try:
        while True:
            raw     = await websocket.receive_text()
            message = json.loads(raw)

            if message.get("command") == "start":
                print("[Algo] Start-kommando modtaget")
                asyncio.create_task(start_algo())

            elif message.get("command") == "stop":
                print("[Algo] Stop-kommando modtaget")
                await stop_algo()
                await broadcast_algo({
                    "type": "algo_status", "status": "stopped",
                    "message": "Algoritme stoppet manuelt",
                    "total_pnl": 0, "positions": 0, "trades": 0,
                    "time": datetime.now().strftime("%H:%M:%S"),
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

    def on_tick_update(ticker_obj: Ticker):
        """Kaldes hver gang IBKR pusher en ny tick."""
        # ticker_obj.tickByTicks indeholder nye ticks siden sidste update
        for t in ticker_obj.tickByTicks:
            # Bestem retning: pris ved eller over ask = køber initieret (op),
            # pris ved eller under bid = sælger initieret (ned)
            bid = ticker_obj.bid
            ask = ticker_obj.ask
            direction = "neutral"
            if ask and t.price >= ask:
                direction = "up"
            elif bid and t.price <= bid:
                direction = "down"

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

@app.get("/algo/status", dependencies=[Depends(require_studio_auth)])
async def algo_status():
    """Returnér status om algoritmen kører."""
    momentum = strategy_manager._strategies.get("Momentum ORB")
    running  = momentum is not None and momentum.status == StrategyStatus.RUNNING

    ibkr_connected = False
    ibkr = strategy_manager.get_ibkr()
    if ibkr is not None:
        ibkr_connected = ibkr.connected

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


@app.post("/algo/start", dependencies=[Depends(require_studio_auth)])
async def algo_start_endpoint():
    """Start algoritmen. Idempotent — gør intet hvis allerede kører."""
    momentum = strategy_manager._strategies.get("Momentum ORB")
    if momentum is not None and momentum.status == StrategyStatus.RUNNING:
        return {"ok": True, "already_running": True, "message": "Algoritmen kører allerede"}

    asyncio.create_task(start_algo())
    return {"ok": True, "already_running": False, "message": "Algoritmen startes"}


@app.post("/algo/stop", dependencies=[Depends(require_studio_auth)])
async def algo_stop_endpoint():
    """Stop algoritmen. Idempotent — gør intet hvis ikke kører."""
    momentum = strategy_manager._strategies.get("Momentum ORB")
    if momentum is None or momentum.status != StrategyStatus.RUNNING:
        return {"ok": True, "was_running": False, "message": "Algoritmen kørte ikke"}

    await stop_algo()
    await broadcast_algo({
        "type": "algo_status", "status": "stopped",
        "message": "Algoritme stoppet via Studio",
        "total_pnl": 0, "positions": 0, "trades": 0,
        "time": datetime.now().strftime("%H:%M:%S"),
    })
    return {"ok": True, "was_running": True, "message": "Algoritmen stoppet"}

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

# Tracker hvornår sidste account_snapshot blev skrevet til journal —
# så vi ikke logger ved hver auto-refresh, kun én gang i timen.
_last_snapshot_journaled_at: datetime | None = None


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
                return safe_float(snap.get("last")) if snap else None
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

@app.get("/studio")
async def studio_index():
    """Servér Studio's index.html. Studio er en separat browser-baseret app
    til konfiguration, analyse og administration. Kører i samme backend."""
    studio_path = Path(__file__).parent / "studio" / "index.html"
    if not studio_path.exists():
        return {"error": "Studio findes ikke — placeholder mangler i backend/studio/"}
    return FileResponse(studio_path)

# ── Analyse-side endpoint ──────────────────────────
@app.get("/analysis/summary", dependencies=[Depends(require_studio_auth)])
async def analysis_summary(period: str = "all"):
    from analysis import build_summary
    if period not in ("today", "7d", "30d", "all"):
        return {"error": f"Ugyldig periode: {period}"}
    return build_summary(period)
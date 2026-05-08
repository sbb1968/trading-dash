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

from journal          import Journal

from accounts import identity

from fastapi.responses import FileResponse
from pathlib import Path

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


# ── /market-conditions ────────────────────────────────────────
@app.get("/market-conditions")
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

@app.get("/account")
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

@app.get("/studio")
async def studio_index():
    """Servér Studio's index.html. Studio er en separat browser-baseret app
    til konfiguration, analyse og administration. Kører i samme backend."""
    studio_path = Path(__file__).parent / "studio" / "index.html"
    if not studio_path.exists():
        return {"error": "Studio findes ikke — placeholder mangler i backend/studio/"}
    return FileResponse(studio_path)
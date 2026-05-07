"""
strategy_manager.py — Orkestrator der styrer alle strategier

Placering: C:\\Projects\trading-dash\backend\strategy_manager.py
"""

import logging
import asyncio
from datetime import datetime
from typing import Optional, Callable

from strategy_base import BaseStrategy, StrategyStatus, StrategyConfig
from risk_manager  import RiskManager, RiskConfig
from ibkr_connect  import IBKRConnection

logger = logging.getLogger(__name__)


class StrategyManager:
    """
    Central orkestrator for alle trading strategier.
    Forbinder strategier med RiskManager og broadcaster til frontend.
    """

    def __init__(self, risk_config: Optional[RiskConfig] = None):
        self.risk_manager = RiskManager(risk_config or RiskConfig(daily_loss_limit=300.0))
        self._strategies: dict[str, BaseStrategy] = {}
        self._broadcast_fn: Optional[Callable] = None

        # Delt IBKR-forbindelse — ejet af StrategyManager, brugt af alle strategier
        self.ibkr_conn:  Optional[IBKRConnection] = None
        self.ibkr_ready: bool = False

        self.risk_manager._emergency_stop_fn = self._on_risk_emergency
        self.risk_manager._broadcast_fn      = self._broadcast

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    def set_broadcast_fn(self, fn: Callable) -> None:
        self._broadcast_fn                   = fn
        self.risk_manager._broadcast_fn      = fn

    def set_journal(self, journal) -> None:
        """Giv journal videre til alle dele af systemet."""
        self.risk_manager._journal = journal
        for strategy in self._strategies.values():
            strategy._journal = journal

    async def connect_ibkr(self, paper_trading: bool = True) -> bool:
        """
        Opret og hold den ene delte IBKR-forbindelse.
        Sikker at kalde flere gange — genbruger eksisterende forbindelse.
        """
        if self.ibkr_conn and self.ibkr_conn.connected:
            return True

        self.ibkr_conn = IBKRConnection(paper_trading=paper_trading)
        ok = await self.ibkr_conn.connect()
        self.ibkr_ready = ok

        if ok:
            logger.info("[StrategyManager] IBKR-forbindelse oprettet og delt")
            # Giv forbindelsen til allerede-registrerede strategier
            for strategy in self._strategies.values():
                strategy._ib = self.ibkr_conn
        else:
            logger.warning("[StrategyManager] Kunne ikke forbinde til IBKR")

        return ok

    def get_ibkr(self) -> Optional[IBKRConnection]:
        """Hent den delte IBKR-forbindelse (eller None hvis ikke forbundet)."""
        return self.ibkr_conn if (self.ibkr_conn and self.ibkr_conn.connected) else None

    # -----------------------------------------------------------------------
    # Registrering
    # -----------------------------------------------------------------------

    def register(self, strategy: BaseStrategy) -> None:
        strategy._risk_manager = self.risk_manager
        strategy._broadcast_fn = self._broadcast
        strategy._ib           = self.ibkr_conn

        strategy._journal      = self.risk_manager._journal

        self._strategies[strategy.name] = strategy
        logger.info(f"Strategi registreret: '{strategy.name}' [{strategy.asset_class}]")

    def unregister(self, strategy_name: str) -> None:
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            return
        if strategy.status == StrategyStatus.RUNNING:
            logger.warning(f"Kan ikke fjerne kørende strategi '{strategy_name}'")
            return
        del self._strategies[strategy_name]

    # -----------------------------------------------------------------------
    # Start / Stop
    # -----------------------------------------------------------------------

    async def start_strategy(self, strategy_name: str) -> tuple[bool, str]:
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            return False, f"Strategi '{strategy_name}' ikke fundet"
        if not strategy.config.enabled:
            return False, f"Strategi '{strategy_name}' er deaktiveret"
        if strategy.status == StrategyStatus.RUNNING:
            return False, f"Strategi '{strategy_name}' kører allerede"
        if self.risk_manager._emergency_active:
            return False, "Nødstop er aktivt — deaktiver det først"
        if self.risk_manager._daily_limit_hit:
            return False, "Daglig tab-grænse er nået for i dag"

        success = await strategy.start()
        await self._broadcast_full_status()
        return success, "Startet" if success else "Pre-flight fejlede"

    async def stop_strategy(self, strategy_name: str, reason: str = "Manuelt stoppet") -> bool:
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            return False
        await strategy.stop(reason)
        await self._broadcast_full_status()
        return True

    async def pause_strategy(self, strategy_name: str) -> bool:
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            return False
        strategy.status = StrategyStatus.PAUSED
        await strategy._log("Strategi pauset", level="warning")
        await self._broadcast_full_status()
        return True

    # -----------------------------------------------------------------------
    # Nødstop
    # -----------------------------------------------------------------------

    async def emergency_stop_all(self, reason: str = "Nødstop aktiveret") -> None:
        logger.critical(f"⚠ EMERGENCY STOP ALL: {reason}")
        tasks = [
            strategy.emergency_stop()
            for strategy in self._strategies.values()
            if strategy.status == StrategyStatus.RUNNING
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await self._broadcast({
            "type":               "emergency_stop_complete",
            "reason":             reason,
            "strategies_stopped": len(tasks),
            "timestamp":          datetime.now().strftime("%H:%M:%S"),
        })
        await self._broadcast_full_status()

    async def _on_risk_emergency(self, reason: str) -> None:
        await self.emergency_stop_all(reason)

    # -----------------------------------------------------------------------
    # Konfiguration
    # -----------------------------------------------------------------------

    async def update_strategy_config(self, strategy_name: str, config_updates: dict) -> bool:
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            return False
        for key, value in config_updates.items():
            if hasattr(strategy.config, key):
                setattr(strategy.config, key, value)
        await self._broadcast_full_status()
        return True

    async def update_risk_config(self, config_updates: dict) -> None:
        for key, value in config_updates.items():
            if hasattr(self.risk_manager.config, key):
                setattr(self.risk_manager.config, key, value)
        await self._broadcast_full_status()

    # -----------------------------------------------------------------------
    # Ny handelsdag
    # -----------------------------------------------------------------------

    async def reset_for_new_day(self) -> None:
        self.risk_manager.reset_daily()
        for strategy in self._strategies.values():
            strategy.reset_daily_stats()
        logger.info("StrategyManager: Ny handelsdag — alle tællere nulstillet")
        await self._broadcast({"type": "new_day_reset", "timestamp": datetime.now().strftime("%H:%M:%S")})
        await self._broadcast_full_status()

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------

    def get_full_status(self) -> dict:
        return {
            "type":       "manager_status",
            "timestamp":  datetime.now().strftime("%H:%M:%S"),
            "risk":       self.risk_manager.get_status_dict(),
            "strategies": [s.get_status_dict() for s in self._strategies.values()],
        }

    async def _broadcast_full_status(self) -> None:
        await self._broadcast(self.get_full_status())

    async def _broadcast(self, msg: dict) -> None:
        if self._broadcast_fn:
            try:
                await self._broadcast_fn(msg)
            except Exception as e:
                logger.error(f"StrategyManager broadcast fejl: {e}")

    # -----------------------------------------------------------------------
    # WebSocket kommando-handler
    # -----------------------------------------------------------------------

    async def handle_command(self, command: dict) -> dict:
        """
        Håndterer kommandoer fra frontend StrategyManager vindue.

        { "command": "start",          "strategy": "Momentum ORB" }
        { "command": "stop",           "strategy": "Momentum ORB" }
        { "command": "pause",          "strategy": "Momentum ORB" }
        { "command": "emergency_stop" }
        { "command": "reset_emergency" }
        { "command": "get_status" }
        { "command": "update_config",  "strategy": "...", "config": {...} }
        { "command": "update_risk",    "config": {...} }
        { "command": "new_day" }
        """
        cmd = command.get("command")

        if cmd == "start":
            name = command.get("strategy")
            success, msg = await self.start_strategy(name)
            return {"response": "start", "strategy": name, "success": success, "message": msg}

        elif cmd == "stop":
            name = command.get("strategy")
            success = await self.stop_strategy(name)
            return {"response": "stop", "strategy": name, "success": success}

        elif cmd == "pause":
            name = command.get("strategy")
            success = await self.pause_strategy(name)
            return {"response": "pause", "strategy": name, "success": success}

        elif cmd == "emergency_stop":
            await self.emergency_stop_all("Manuelt nødstop fra UI (ALT+X)")
            return {"response": "emergency_stop", "success": True}

        elif cmd == "reset_emergency":
            self.risk_manager.reset_emergency()
            await self._broadcast_full_status()
            return {"response": "reset_emergency", "success": True}

        elif cmd == "get_status":
            return self.get_full_status()

        elif cmd == "update_config":
            name    = command.get("strategy")
            config  = command.get("config", {})
            success = await self.update_strategy_config(name, config)
            return {"response": "update_config", "strategy": name, "success": success}

        elif cmd == "update_risk":
            config = command.get("config", {})
            await self.update_risk_config(config)
            return {"response": "update_risk", "success": True}

        elif cmd == "new_day":
            await self.reset_for_new_day()
            return {"response": "new_day", "success": True}

        else:
            return {"response": "error", "message": f"Ukendt kommando: {cmd}"}

"""
risk_manager.py — Kombineret risikostyring på tværs af alle strategier

Placering: C:\\Projects\trading-dash\backend\risk_manager.py
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class RiskConfig:
    # PER-STRATEGI grænser — hver strategi opfører sig som om den var alene. Det
    # GLOBALE daglige tabsloft er FJERNET (2026-07-12): hver strategi har sin egen
    # konfigurerbare max daily loss (risk_config.py, håndhævet i request_order).
    max_positions_per_strategy: int   = 3      # Max åbne positioner pr. strategi
    max_exposure_per_strategy:  float = 20000.0  # Max $ i markedet pr. strategi
    # KONTO-BAGSTOPPER — højt sat, så normal drift aldrig rammer det; fanger
    # kun en strategi der er løbet løbsk:
    max_total_exposure:      float = 200000.0  # Samlet loft på tværs (bagstopper)
    max_total_positions:     int   = 50        # Samlet positionsloft (bagstopper)
    nlv_emergency_threshold: float = 5000.0    # Nødstop hvis NLV falder hertil
    block_duplicate_tickers: bool  = False     # Strategier MÅ dele samme ticker


@dataclass
class RejectionRecord:
    timestamp:     str
    strategy_name: str
    ticker:        str
    action:        str
    quantity:      int
    reason:        str


@dataclass
class ApprovalRecord:
    timestamp:     str
    strategy_name: str
    ticker:        str
    action:        str
    quantity:      int


class RiskManager:
    """
    Kombineret risikostyring for alle strategier.
    Ingen strategi må placere en ordre uden godkendelse herfra.
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()

        self._pnl_by_strategy:      dict[str, float] = {}
        self._total_pnl_today:      float = 0.0
        self._exposure_by_strategy: dict[str, float] = {}
        self._total_exposure:       float = 0.0
        # (strategy_name, ticker) → True. Nøglet på BEGGE, så samme ticker kan
        # være åben i flere strategier samtidig uden at overskrive hinanden.
        self._open_positions:       dict[tuple[str, str], bool] = {}

        self._approval_log:  list[ApprovalRecord]  = []
        self._rejection_log: list[RejectionRecord] = []

        self._current_nlv:      float = 0.0
        self._emergency_active: bool  = False

        self._emergency_stop_fn = None
        self._broadcast_fn      = None
        self._journal           = None

        # Cooldown: blokerer gentagne afvisninger af samme (strategi, ticker, grund)
        # for at undgå spam når samme breakout-betingelse hænger ved
        self._rejection_cooldown:  dict[tuple[str, str, str], datetime] = {}
        self.rejection_cooldown_s: int = 60

    # -----------------------------------------------------------------------
    # Godkend eller afvis ordre
    # -----------------------------------------------------------------------

    async def approve_order(self, order) -> tuple[bool, str]:
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Ryd udløbet cooldown og bloker hvis (strategi, ticker, grund) er i cooldown
        now = datetime.now()
        self._rejection_cooldown = {
            k: t for k, t in self._rejection_cooldown.items()
            if (now - t).total_seconds() < self.rejection_cooldown_s
        }

        if self._emergency_active:
            return False, "Nødstop er aktivt — alle ordrer blokeret"

        # (Globalt dagligt tabsloft FJERNET — hver strategi håndhæver sin egen
        # konfigurerbare max daily loss i BaseStrategy.request_order.)

        estimated_value = order.quantity * (order.limit_price or 10.0)
        strat = order.strategy_name

        # ── PER-STRATEGI: positionsantal ──
        strat_positions = sum(1 for (s, _t) in self._open_positions if s == strat)
        if order.action == "BUY" and strat_positions >= self.config.max_positions_per_strategy:
            reason = (f"{strat}: max positioner pr. strategi "
                      f"({self.config.max_positions_per_strategy})")
            silent = (strat, order.ticker, "max_pos_strat") in self._rejection_cooldown
            await self._log_rejection(timestamp, order, reason, silent=silent, cooldown_key="max_pos_strat")
            return False, reason

        # ── PER-STRATEGI: eksponering ──
        strat_exposure = self._exposure_by_strategy.get(strat, 0.0)
        if order.action == "BUY" and strat_exposure + estimated_value > self.config.max_exposure_per_strategy:
            reason = (f"{strat}: max eksponering pr. strategi overskredet "
                      f"(${strat_exposure:.0f} + ${estimated_value:.0f} "
                      f"> ${self.config.max_exposure_per_strategy:.0f})")
            silent = (strat, order.ticker, "max_exp_strat") in self._rejection_cooldown
            await self._log_rejection(timestamp, order, reason, silent=silent, cooldown_key="max_exp_strat")
            return False, reason

        # ── KONTO-BAGSTOPPER: samlet eksponering (højt sat) ──
        if order.action == "BUY" and self._total_exposure + estimated_value > self.config.max_total_exposure:
            reason = (f"KONTO-BAGSTOPPER: samlet eksponering overskredet "
                      f"(${self._total_exposure:.0f} + ${estimated_value:.0f} "
                      f"> ${self.config.max_total_exposure:.0f})")
            silent = (strat, order.ticker, "max_exposure") in self._rejection_cooldown
            await self._log_rejection(timestamp, order, reason, silent=silent, cooldown_key="max_exposure")
            return False, reason

        # ── KONTO-BAGSTOPPER: samlet positionsantal (højt sat) ──
        if order.action == "BUY" and len(self._open_positions) >= self.config.max_total_positions:
            reason = f"KONTO-BAGSTOPPER: samlet positionsantal nået ({self.config.max_total_positions})"
            silent = (strat, order.ticker, "max_positions") in self._rejection_cooldown
            await self._log_rejection(timestamp, order, reason, silent=silent, cooldown_key="max_positions")
            return False, reason

        # duplicate-ticker-blokering er bevidst FJERNET: strategier må dele ticker.

        if order.action == "BUY":
            self._open_positions[(strat, order.ticker)] = True
            self._total_exposure += estimated_value
            self._exposure_by_strategy[strat] = (
                self._exposure_by_strategy.get(strat, 0) + estimated_value
            )

        record = ApprovalRecord(
            timestamp=timestamp,
            strategy_name=order.strategy_name,
            ticker=order.ticker,
            action=order.action,
            quantity=order.quantity,
        )
        self._approval_log.append(record)
        if len(self._approval_log) > 100:
            self._approval_log.pop(0)

        await self._broadcast_approval(record)
        return True, "Godkendt"

    # -----------------------------------------------------------------------
    # P&L opdatering
    # -----------------------------------------------------------------------

    async def record_pnl(self, strategy_name: str, pnl: float) -> None:
        self._pnl_by_strategy[strategy_name] = (
            self._pnl_by_strategy.get(strategy_name, 0) + pnl
        )
        self._total_pnl_today += pnl

        await self._broadcast_risk_update()

    def release_exposure(self, strategy_name: str, ticker: str, estimated_value: float) -> None:
        key = (strategy_name, ticker)
        if key in self._open_positions:
            del self._open_positions[key]
        self._total_exposure = max(0, self._total_exposure - estimated_value)
        if strategy_name in self._exposure_by_strategy:
            self._exposure_by_strategy[strategy_name] = max(
                0, self._exposure_by_strategy[strategy_name] - estimated_value
            )

    # -----------------------------------------------------------------------
    # NLV opdatering
    # -----------------------------------------------------------------------

    async def update_nlv(self, nlv: float) -> None:
        self._current_nlv = nlv
        if nlv > 0 and nlv < self.config.nlv_emergency_threshold and not self._emergency_active:
            logger.critical(f"NLV under tærskel! ${nlv:.2f}")
            await self._trigger_emergency_stop(
                f"NLV faldet til ${nlv:.2f} — under tærskel ${self.config.nlv_emergency_threshold:.2f}"
            )

    # -----------------------------------------------------------------------
    # Nødstop
    # -----------------------------------------------------------------------

    async def trigger_manual_emergency_stop(self) -> None:
        await self._trigger_emergency_stop("Manuelt nødstop aktiveret (ALT+X)")

    async def _trigger_emergency_stop(self, reason: str) -> None:
        self._emergency_active = True
        logger.critical(f"⚠ NØDSTOP: {reason}")

        if self._journal:
            await self._journal.log_event(
                source     = "risk_manager",
                event_type = "emergency_stop",
                payload    = {
                    "reason":    reason,
                    "total_pnl": round(self._total_pnl_today, 2),
                },
            )

        await self._broadcast({
            "type":      "emergency_stop",
            "reason":    reason,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "total_pnl": round(self._total_pnl_today, 2),
        })
        if self._emergency_stop_fn:
            await self._emergency_stop_fn(reason)

    # -----------------------------------------------------------------------
    # Reset
    # -----------------------------------------------------------------------

    def reset_daily(self) -> None:
        self._pnl_by_strategy.clear()
        self._total_pnl_today = 0.0
        self._open_positions.clear()
        self._exposure_by_strategy.clear()
        self._total_exposure   = 0.0
        logger.info("RiskManager: Daglige tællere nulstillet")

    def reset_emergency(self) -> None:
        self._emergency_active = False
        logger.info("RiskManager: Nødstop ophævet manuelt")

    # -----------------------------------------------------------------------
    # Status snapshot
    # -----------------------------------------------------------------------

    def get_status_dict(self) -> dict:
        return {
            "type":               "risk_status",
            "total_pnl_today":    round(self._total_pnl_today, 2),
            "emergency_active":   self._emergency_active,
            "total_exposure":     round(self._total_exposure, 2),
            "max_total_exposure": self.config.max_total_exposure,
            "open_positions":     [f"{s}:{t}" for (s, t) in self._open_positions],
            "pnl_by_strategy":    {k: round(v, 2) for k, v in self._pnl_by_strategy.items()},
            "current_nlv":        round(self._current_nlv, 2),
            "nlv_threshold":      self.config.nlv_emergency_threshold,
            "recent_rejections": [
                {
                    "timestamp": r.timestamp,
                    "strategy":  r.strategy_name,
                    "ticker":    r.ticker,
                    "action":    r.action,
                    "reason":    r.reason,
                }
                for r in self._rejection_log[-10:]
            ],
        }

    # -----------------------------------------------------------------------
    # Interne hjælpere
    # -----------------------------------------------------------------------

    async def _log_rejection(self, timestamp: str, order, reason: str,
                             silent: bool = False, cooldown_key: str = None) -> None:
        # Tilføj til cooldown — brug cooldown_key (kategori) hvis angivet,
        # ellers den fulde reason-streng (bagudkompatibilitet)
        cd_key = (order.strategy_name, order.ticker, cooldown_key or reason)
        self._rejection_cooldown[cd_key] = datetime.now()

        if silent:
            return  # Stille blokering — ingen log, journal eller broadcast
        record = RejectionRecord(
            timestamp=timestamp,
            strategy_name=order.strategy_name,
            ticker=order.ticker,
            action=order.action,
            quantity=order.quantity,
            reason=reason,
        )
        self._rejection_log.append(record)
        if len(self._rejection_log) > 100:
            self._rejection_log.pop(0)

        logger.warning(f"Ordre AFVIST [{order.strategy_name}]: {order.action} {order.quantity} {order.ticker} — {reason}")

        if self._journal:
            await self._journal.log_event(
                source     = "risk_manager",
                event_type = "order_rejected",
                symbol     = order.ticker,
                payload    = {
                    "strategy": order.strategy_name,
                    "action":   order.action,
                    "quantity": order.quantity,
                    "reason":   reason,
                },
            )

        await self._broadcast({
            "type":      "order_rejected",
            "timestamp": timestamp,
            "strategy":  order.strategy_name,
            "ticker":    order.ticker,
            "action":    order.action,
            "quantity":  order.quantity,
            "reason":    reason,
        })

    async def _broadcast_approval(self, record: ApprovalRecord) -> None:
        if self._journal:
            await self._journal.log_event(
                source     = "risk_manager",
                event_type = "order_approved",
                symbol     = record.ticker,
                payload    = {
                    "strategy": record.strategy_name,
                    "action":   record.action,
                    "quantity": record.quantity,
                },
            )

        await self._broadcast({
            "type":      "order_approved",
            "timestamp": record.timestamp,
            "strategy":  record.strategy_name,
            "ticker":    record.ticker,
            "action":    record.action,
            "quantity":  record.quantity,
        })

    async def _broadcast_risk_update(self) -> None:
        await self._broadcast(self.get_status_dict())

    async def _broadcast(self, msg: dict) -> None:
        if self._broadcast_fn:
            try:
                await self._broadcast_fn(msg)
            except Exception as e:
                logger.error(f"RiskManager broadcast fejl: {e}")

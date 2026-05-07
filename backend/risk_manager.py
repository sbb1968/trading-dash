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
    daily_loss_limit:        float = 300.0     # $300 kombineret daglig grænse
    max_total_exposure:      float = 20000.0   # Max $ samlet i markedet
    max_total_positions:     int   = 6         # Max positioner på tværs
    nlv_emergency_threshold: float = 16000.0   # Nødstop hvis NLV falder hertil
    block_duplicate_tickers: bool  = True      # Forhindre samme ticker i 2 strategier


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
        self._open_tickers:         dict[str, str]   = {}  # ticker → strategy_name

        self._approval_log:  list[ApprovalRecord]  = []
        self._rejection_log: list[RejectionRecord] = []

        self._current_nlv:      float = 0.0
        self._daily_limit_hit:  bool  = False
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

        if self._daily_limit_hit:
            return False, f"Daglig tab-grænse nået (${self.config.daily_loss_limit})"

        if self._total_pnl_today <= -self.config.daily_loss_limit:
            self._daily_limit_hit = True
            await self._trigger_daily_limit_stop()
            return False, f"Daglig tab-grænse overskredet: ${self._total_pnl_today:.2f}"

        estimated_value = order.quantity * (order.limit_price or 10.0)
        if self._total_exposure + estimated_value > self.config.max_total_exposure:
            reason = (f"Max eksponering overskredet: "
                      f"${self._total_exposure:.0f} + ${estimated_value:.0f} "
                      f"> ${self.config.max_total_exposure:.0f}")
            silent = (order.strategy_name, order.ticker, reason) in self._rejection_cooldown
            await self._log_rejection(timestamp, order, reason, silent=silent)
            return False, reason

        if len(self._open_tickers) >= self.config.max_total_positions:
            reason = f"Max antal positioner nået ({self.config.max_total_positions})"
            silent = (order.strategy_name, order.ticker, reason) in self._rejection_cooldown
            await self._log_rejection(timestamp, order, reason, silent=silent)
            return False, reason

        if self.config.block_duplicate_tickers and order.action == "BUY":
            if order.ticker in self._open_tickers:
                existing = self._open_tickers[order.ticker]
                if existing != order.strategy_name:
                    reason = f"{order.ticker} er allerede åben i strategi '{existing}'"
                    silent = (order.strategy_name, order.ticker, reason) in self._rejection_cooldown
                    await self._log_rejection(timestamp, order, reason, silent=silent)
                    return False, reason

        if order.action == "BUY":
            self._open_tickers[order.ticker] = order.strategy_name
            self._total_exposure += estimated_value
            self._exposure_by_strategy[order.strategy_name] = (
                self._exposure_by_strategy.get(order.strategy_name, 0) + estimated_value
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

        if self._total_pnl_today <= -self.config.daily_loss_limit and not self._daily_limit_hit:
            self._daily_limit_hit = True
            await self._trigger_daily_limit_stop()

    def release_exposure(self, strategy_name: str, ticker: str, estimated_value: float) -> None:
        if ticker in self._open_tickers:
            del self._open_tickers[ticker]
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

    async def _trigger_daily_limit_stop(self) -> None:
        logger.warning(f"Daglig tab-grænse nået: ${self._total_pnl_today:.2f}")

        if self._journal:
            await self._journal.log_event(
                source     = "risk_manager",
                event_type = "daily_limit_reached",
                payload    = {
                    "total_pnl": round(self._total_pnl_today, 2),
                    "limit":     self.config.daily_loss_limit,
                },
            )

        await self._broadcast({
            "type":      "daily_limit_reached",
            "total_pnl": round(self._total_pnl_today, 2),
            "limit":     self.config.daily_loss_limit,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        })
        if self._emergency_stop_fn:
            await self._emergency_stop_fn("Daglig tab-grænse nået")

    # -----------------------------------------------------------------------
    # Reset
    # -----------------------------------------------------------------------

    def reset_daily(self) -> None:
        self._pnl_by_strategy.clear()
        self._total_pnl_today = 0.0
        self._open_tickers.clear()
        self._exposure_by_strategy.clear()
        self._total_exposure   = 0.0
        self._daily_limit_hit  = False
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
            "daily_loss_limit":   self.config.daily_loss_limit,
            "total_pnl_today":    round(self._total_pnl_today, 2),
            "daily_limit_hit":    self._daily_limit_hit,
            "emergency_active":   self._emergency_active,
            "total_exposure":     round(self._total_exposure, 2),
            "max_total_exposure": self.config.max_total_exposure,
            "open_tickers":       dict(self._open_tickers),
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

    async def _log_rejection(self, timestamp: str, order, reason: str, silent: bool = False) -> None:
        # Tilføj til cooldown så identiske gentagelser kan blokeres stille
        cd_key = (order.strategy_name, order.ticker, reason)
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

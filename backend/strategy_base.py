"""
strategy_base.py — Abstrakt BaseStrategy klasse
Alle trading strategier arver fra denne klasse.

Placering: C:\\Projects\\Trading_Dash\\backend\\strategy_base.py
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import asyncio
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-klasser
# ---------------------------------------------------------------------------

@dataclass
class OrderRequest:
    """En strategi's anmodning om at placere en ordre — skal godkendes af RiskManager."""
    strategy_name: str
    ticker:        str
    action:        str                   # "BUY" eller "SELL"
    quantity:      int
    order_type:    str                   # "MKT", "LMT"
    limit_price:   Optional[float] = None
    asset_class:   str = "equity"        # "equity", "forex", "cfd"
    reason:        str = ""              # Hvorfor vil strategien handle (til log)


@dataclass
class StrategyConfig:
    """Konfigurerbare parametre per strategi — sættes i StrategyManager UI."""
    max_loss_per_trade:  float = 100.0   # Max tab per handel i $
    max_daily_loss:      float = 150.0   # Max dagligt tab for denne strategi i $
    max_open_positions:  int   = 3       # Max samtidige positioner
    max_position_size:   float = 5000.0  # Max $ per position
    enabled:             bool  = True    # Kan slås fra i UI uden at fjerne strategien


@dataclass
class StrategyStats:
    """Live statistik der vises i StrategyManager UI."""
    trades_today:     int   = 0
    wins_today:       int   = 0
    losses_today:     int   = 0
    pnl_today:        float = 0.0
    open_positions:   int   = 0
    orders_rejected:  int   = 0
    last_trade_time:  Optional[str] = None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class StrategyStatus:
    IDLE    = "idle"      # Ikke startet
    RUNNING = "running"   # Aktiv og scanner markedet
    PAUSED  = "paused"    # Midlertidigt pauset af RiskManager
    STOPPED = "stopped"   # Manuelt stoppet
    ERROR   = "error"     # Fejl — kræver manuel genstart


# ---------------------------------------------------------------------------
# BaseStrategy
# ---------------------------------------------------------------------------

class BaseStrategy(ABC):
    """
    Abstrakt base-klasse for alle trading strategier.

    Implementer følgende metoder i din strategi:
        - pre_flight()  : Tjek at alt er klar
        - on_start()    : Køres når strategien startes
        - on_bar()      : Køres for hvert nyt price bar
        - on_stop()     : Køres når strategien stoppes

    Brug request_order() til at anmode om ordrer.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config  = config or StrategyConfig()
        self.stats   = StrategyStats()
        self.status  = StrategyStatus.IDLE

        self._risk_manager  = None
        self._broadcast_fn  = None
        self._ib            = None
        self._journal       = None

        self._open_positions: dict = {}
        self._start_time: Optional[datetime] = None

        # Lag B-diagnostik: husker hver akties sidste afvisnings-streng,
        # så vi KUN logger når begrundelsen ændrer sig. Nulstilles dagligt
        # via reset_diagnostics().
        self._last_rejection: dict[str, str] = {}
    # -----------------------------------------------------------------------
    # Abstrakte metoder
    # -----------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def asset_class(self) -> str:
        pass

    @abstractmethod
    async def pre_flight(self) -> tuple[bool, str]:
        pass

    @abstractmethod
    async def on_start(self) -> None:
        pass

    @abstractmethod
    async def on_bar(self, ticker: str, bar: dict) -> None:
        pass

    @abstractmethod
    async def on_stop(self) -> None:
        pass

    # -----------------------------------------------------------------------
    # Ordre-flow
    # -----------------------------------------------------------------------

    async def request_order(self, order: OrderRequest) -> bool:
        if self._risk_manager is None:
            logger.error(f"[{self.name}] Ingen RiskManager — ordre afvist")
            return False

        if self.status != StrategyStatus.RUNNING:
            return False

        if self.stats.open_positions >= self.config.max_open_positions:
            await self._log(f"Ordre afvist: Max positioner nået ({self.config.max_open_positions})")
            self.stats.orders_rejected += 1
            return False

        if self.stats.pnl_today <= -self.config.max_daily_loss:
            await self._log(f"Ordre afvist: Daglig tab-limit nået (${self.config.max_daily_loss})")
            self.status = StrategyStatus.PAUSED
            self.stats.orders_rejected += 1
            return False

        approved, reason = await self._risk_manager.approve_order(order)

        if not approved:
            self.stats.orders_rejected += 1
            # Send afvisning som algo_status så Live Algo-vinduet viser det
            if self._broadcast_fn:
                import inspect
                msg = {
                    "type":      "algo_status",
                    "status":    "trading",
                    "message":   f"⛔ Afvist {order.action} {order.quantity} {order.ticker} — {reason}",
                    "total_pnl": round(self.stats.pnl_today, 2),
                    "positions": self.stats.open_positions,
                    "trades":    self.stats.trades_today,
                    "time":      datetime.now().strftime("%H:%M:%S"),
                }
                if inspect.iscoroutinefunction(self._broadcast_fn):
                    await self._broadcast_fn(msg)
                else:
                    self._broadcast_fn(msg)
            return False

        await self._log(f"Ordre godkendt: {order.action} {order.quantity} {order.ticker}")
        return True

    # -----------------------------------------------------------------------
    # Position tracking
    # -----------------------------------------------------------------------

    def record_position_opened(self, ticker: str, entry_price: float,
                                quantity: int, side: str) -> None:
        self._open_positions[ticker] = {
            "entry_price": entry_price,
            "quantity":    quantity,
            "side":        side,
            "opened_at":   datetime.now().isoformat()
        }
        self.stats.open_positions = len(self._open_positions)

        if self._journal:
            import asyncio as _asyncio
            _asyncio.create_task(self._journal.log_event(
                source     = self.name,
                event_type = "position_opened",
                symbol     = ticker,
                payload    = {
                    "entry_price": entry_price,
                    "quantity":    quantity,
                    "side":        side,
                },
            ))

    def record_position_closed(self, ticker: str, exit_price: float) -> float:
        if ticker not in self._open_positions:
            return 0.0

        pos      = self._open_positions.pop(ticker)
        quantity = pos["quantity"]
        entry    = pos["entry_price"]

        pnl = (exit_price - entry) * quantity if pos["side"] == "long" \
              else (entry - exit_price) * quantity

        self.stats.pnl_today      += pnl
        self.stats.trades_today   += 1
        self.stats.open_positions  = len(self._open_positions)
        self.stats.last_trade_time = datetime.now().strftime("%H:%M:%S")

        if pnl > 0:
            self.stats.wins_today += 1
        else:
            self.stats.losses_today += 1

        if self._risk_manager:
            # Beregn estimeret position-værdi for at frigive eksponering
            estimated_value = entry * quantity
            self._risk_manager.release_exposure(self.name, ticker, estimated_value)
            asyncio.create_task(self._risk_manager.record_pnl(self.name, pnl))

        if self._journal:
            asyncio.create_task(self._journal.log_event(
                source     = self.name,
                event_type = "position_closed",
                symbol     = ticker,
                payload    = {
                    "entry_price": entry,
                    "exit_price":  exit_price,
                    "quantity":    quantity,
                    "side":        pos["side"],
                    "pnl":         round(pnl, 2),
                    "opened_at":   pos["opened_at"],
                    "closed_at":   datetime.now().isoformat(),
                },
            ))

        return pnl
    # -----------------------------------------------------------------------
    # Livscyklus
    # -----------------------------------------------------------------------

    async def start(self) -> bool:
        ok, msg = await self.pre_flight()
        if not ok:
            await self._log(f"Pre-flight fejlede: {msg}", level="error")
            self.status = StrategyStatus.ERROR
            return False

        self._start_time = datetime.now()
        self.status      = StrategyStatus.RUNNING
        await self._log("Strategi startet ✓")
        if self._journal:
            await self._journal.log_event(
                source     = self.name,
                event_type = "strategy_started",
                payload    = {
                    "asset_class": self.asset_class,
                    "config":      {
                        "max_loss_per_trade": self.config.max_loss_per_trade,
                        "max_daily_loss":     self.config.max_daily_loss,
                        "max_open_positions": self.config.max_open_positions,
                        "max_position_size":  self.config.max_position_size,
                    },
                },
            )
        await self.on_start()
        return True

    async def stop(self, reason: str = "Manuelt stoppet") -> None:
        self.status = StrategyStatus.STOPPED
        await self._log(f"Strategi stoppet: {reason}")
        if self._journal:
            await self._journal.log_event(
                source     = self.name,
                event_type = "strategy_stopped",
                payload    = {
                    "reason":       reason,
                    "trades_today": self.stats.trades_today,
                    "pnl_today":    round(self.stats.pnl_today, 2),
                },
            )
        await self.on_stop()

    async def emergency_stop(self) -> None:
        await self._log("⚠ NØDSTOP AKTIVERET", level="warning")

        if self._journal:
            await self._journal.log_event(
                source     = self.name,
                event_type = "strategy_emergency_stop",
                payload    = {
                    "trades_today":   self.stats.trades_today,
                    "pnl_today":      round(self.stats.pnl_today, 2),
                    "open_positions": self.stats.open_positions,
                },
            )

        self.status = StrategyStatus.STOPPED
        await self.on_stop()

    def reset_daily_stats(self) -> None:
        self.stats = StrategyStats()
        self._open_positions.clear()
        if self.status == StrategyStatus.PAUSED:
            self.status = StrategyStatus.IDLE

    # -----------------------------------------------------------------------
    # Status snapshot
    # -----------------------------------------------------------------------

    def get_status_dict(self) -> dict:
        return {
            "name":        self.name,
            "description": self.description,
            "asset_class": self.asset_class,
            "status":      self.status,
            "stats": {
                "trades_today":    self.stats.trades_today,
                "wins_today":      self.stats.wins_today,
                "losses_today":    self.stats.losses_today,
                "pnl_today":       round(self.stats.pnl_today, 2),
                "open_positions":  self.stats.open_positions,
                "orders_rejected": self.stats.orders_rejected,
                "last_trade_time": self.stats.last_trade_time,
            },
            "config": {
                "max_loss_per_trade": self.config.max_loss_per_trade,
                "max_daily_loss":     self.config.max_daily_loss,
                "max_open_positions": self.config.max_open_positions,
                "max_position_size":  self.config.max_position_size,
                "enabled":            self.config.enabled,
            },
            # Dagens univers (de udvalgte tickers). getattr med fallback fordi
            # universe kun sættes i de konkrete strategier (ORB/Konfluens), og
            # først når de er startet — før det er den tom.
            "universe": getattr(self, "universe", []),
        }

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------

    async def _log(self, message: str, level: str = "info") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg  = f"[{self.name}] {message}"

        if level == "error":
            logger.error(full_msg)
        elif level == "warning":
            logger.warning(full_msg)
        else:
            logger.info(full_msg)

        if self._broadcast_fn:
            import asyncio as _asyncio
            import inspect
            msg = {
                "type":      "strategy_log",
                "strategy":  self.name,
                "timestamp": timestamp,
                "message":   message,
                "level":     level,
            }
            if inspect.iscoroutinefunction(self._broadcast_fn):
                await self._broadcast_fn(msg)
            else:
                self._broadcast_fn(msg)

        # Gem også til journal så beskeden kan ses i historik-loggen bagefter
        # (ikke kun live via broadcast). Best-effort: journal-fejl må aldrig
        # nedbryde handelsflowet — derfor try/except og ingen re-raise.
        if self._journal:
            try:
                await self._journal.log_event(
                    source     = self.name,
                    event_type = "log",
                    payload    = {"message": message, "level": level},
                )
            except Exception as e:
                logger.error(f"[{self.name}] _log journal-skriv fejlede: {e}")

    # -----------------------------------------------------------------------
    # Diagnostik-logging (Lag A/B/C) — fælles for ALLE strategier.
    # Skrevet ÉN gang her; arves af enhver BaseStrategy-subklasse, nuværende
    # som fremtidig. Strategi-specifikt indhold leveres som argumenter; selve
    # journal-skrivningen og "kun ændringer"-mekanikken er fælles.
    # account_id/instance_id sættes automatisk af journal.log_event.
    # -----------------------------------------------------------------------

    async def log_universe(self, tickers: list[str], meta: Optional[dict] = None) -> None:
        """Lag A: Log dagens udvalgte univers. `meta` kan rumme strategi-
        specifikke felter (raw_count, pris-filter, open_prices, used_fallback)."""
        if not self._journal:
            return
        payload = {"tickers": list(tickers), "count": len(tickers)}
        if meta:
            payload.update(meta)
        try:
            await self._journal.log_event(
                source     = self.name,
                event_type = "universe_selected",
                payload    = payload,
            )
        except Exception as e:
            logger.error(f"[{self.name}] log_universe fejlede: {e}")

    async def log_rejection_change(self, ticker: str, detail: str) -> bool:
        """Lag B: Log HVORFOR en aktie ikke gav entry — men kun når
        begrundelsen har ÆNDRET sig siden sidst. Mekanikken er fælles;
        `detail`-strengen leveres af strategien (afvisningskriterier er
        strategi-specifikke). Returnerer True hvis et event blev logget."""
        if not self._journal:
            return False
        if self._last_rejection.get(ticker) == detail:
            return False
        self._last_rejection[ticker] = detail
        try:
            await self._journal.log_event(
                source     = self.name,
                event_type = "entry_rejected",
                payload    = {"ticker": ticker, "detail": detail},
                symbol     = ticker,
            )
            return True
        except Exception as e:
            logger.error(f"[{self.name}] log_rejection_change fejlede: {e}")
            return False

    async def log_daily_summary(self, summary: dict) -> None:
        """Lag C: Log dagens aggregerede diagnostik (ét event ved dagsslut).
        Strategien afleverer selv det aggregerede `summary`-dict."""
        if not self._journal:
            return
        try:
            await self._journal.log_event(
                source     = self.name,
                event_type = "daily_diagnostics",
                payload    = summary or {},
            )
        except Exception as e:
            logger.error(f"[{self.name}] log_daily_summary fejlede: {e}")

    async def log_bar_evaluation(
        self,
        ticker: str,
        bar_time_et: str,
        status: str,
        score: Optional[int] = None,
        short_form: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Lag B+: Log HVER bar-evaluering — ikke kun ændringer.

        Dette er det granulære spor der gør det muligt bagefter at se
        præcis hvad strategien så på et givet tidspunkt for en given
        ticker (fx for at sammenligne med Pine-script entries).

        Best-effort: må aldrig nedbryde handelsflowet.

        OBS: Skriver ét event pr. ny bar pr. ticker. Ved fx 18 tickers og
        ~72 bars/dag bliver det ~1300 events/dag. Det er håndterbart, men
        hvis du vil skrue ned: log kun når score >= et minimum (se
        BAR_EVAL_MIN_SCORE i Konfluens-kaldet).
        """
        if not self._journal:
            return
        try:
            await self._journal.log_event(
                source     = self.name,
                event_type = "bar_evaluation",
                payload    = {
                    "ticker":      ticker,
                    "bar_time_et": bar_time_et,
                    "status":      status,
                    "score":       score,
                    "short_form":  short_form,
                    "reason":      reason,
                },
                symbol     = ticker,
            )
        except Exception as e:
            logger.error(f"[{self.name}] log_bar_evaluation fejlede: {e}")

    async def log_heartbeat(self, snapshot: dict) -> None:
        """Vej 1: Periodisk snapshot af kumulative diagnostik-tællere.

        Skrives fx hvert 5. minut fra trading-loopet, så vi har data selv
        hvis backenden crasher før dagsslut. Strategien afleverer selv
        snapshot-dict (evaluations, scored_bars, entries, open_positions...).
        """
        if not self._journal:
            return
        try:
            await self._journal.log_event(
                source     = self.name,
                event_type = "diagnostics_heartbeat",
                payload    = snapshot or {},
            )
        except Exception as e:
            logger.error(f"[{self.name}] log_heartbeat fejlede: {e}")

    def reset_diagnostics(self) -> None:
        """Nulstil Lag B-tilstand. Kaldes ved dagsstart / reset_daily."""
        self._last_rejection.clear()
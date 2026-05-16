"""
tape_buffer.py
──────────────
Rullende 180-sekunders buffer for Time & Sales (tape) og Level 2 (orderbog)
pr. ticker. Subscriber automatisk når en ticker tilføjes til universe og
afmelder ved dagens slut.

Genbruger samme ib_async-kald som /ws/timesales og /ws/level2 i main.py,
men gemmer i hukommelsen i stedet for at sende over websocket.

Designprincipper:
  - Tape (reqTickByTickData) er gratis at subscribe på — alle tickers får det.
  - Depth (reqMktDepth) er begrænset af IBKR (typisk 3 samtidige) — vi
    forsøger alligevel for alle 25 og accepterer at de fleste fejler.
    Fejlede subscriptions logges som warning og ignoreres.
  - Buffers er bounded i tid (180 sek) og antal (max 1000 trades / 200 depth
    snapshots pr. ticker) for at undgå memory blowup på meget aktive aktier.
  - Aldrig kaste exception til kalderen — buffer-fejl må aldrig nedlægge
    handelsflowet.

Placering: C:\\Projects\\trading-dash\\backend\\tape_buffer.py
"""

from __future__ import annotations
import asyncio
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Buffer-grænser ────────────────────────────────────────────
BUFFER_SECONDS    = 180          # Hold 180 sekunders historik
MAX_TRADES_PER_TICKER = 1000     # Hard cap for ekstremt aktive aktier
MAX_DEPTH_PER_TICKER  = 200      # Hard cap for orderbog-opdateringer


def _is_valid(x) -> bool:
    if x is None:
        return False
    try:
        return not math.isnan(x)
    except (TypeError, ValueError):
        return False


@dataclass
class TradeTick:
    """Én trade fra Time & Sales."""
    ts: datetime                  # Tidspunkt (lokal/UTC — vi bruger system-tid)
    price: float
    size: int
    direction: str                # "up" (køb @ ask), "down" (salg @ bid), "neutral"


@dataclass
class DepthSnapshot:
    """Ét snapshot af orderbogen på et givent tidspunkt."""
    ts: datetime
    bids: list[tuple[float, int]]    # [(price, size), ...] sorteret højest først
    asks: list[tuple[float, int]]    # [(price, size), ...] sorteret lavest først


@dataclass
class TickerBuffer:
    """Buffer for én ticker — tape + depth."""
    trades: deque = field(default_factory=lambda: deque(maxlen=MAX_TRADES_PER_TICKER))
    depth_snapshots: deque = field(default_factory=lambda: deque(maxlen=MAX_DEPTH_PER_TICKER))
    last_depth_bids: list[tuple[float, int]] = field(default_factory=list)
    last_depth_asks: list[tuple[float, int]] = field(default_factory=list)
    depth_available: bool = False  # True hvis vi modtog mindst én depth-opdatering

    def add_trade(self, tick: TradeTick) -> None:
        self.trades.append(tick)

    def add_depth(self, snap: DepthSnapshot) -> None:
        self.depth_snapshots.append(snap)
        self.last_depth_bids = snap.bids
        self.last_depth_asks = snap.asks
        self.depth_available = True

    def prune(self, cutoff: datetime) -> None:
        """Fjern alle entries ældre end cutoff."""
        while self.trades and self.trades[0].ts < cutoff:
            self.trades.popleft()
        while self.depth_snapshots and self.depth_snapshots[0].ts < cutoff:
            self.depth_snapshots.popleft()


class TapeBuffer:
    """
    Holder buffers for mange tickers og administrerer IBKR-subscriptions.

    Brug:
        buf = TapeBuffer(ibkr_conn)
        await buf.subscribe("GME")
        ...
        snap = buf.snapshot("GME", lookback_sec=60)
        ...
        await buf.unsubscribe_all()
    """

    def __init__(self, ibkr_conn):
        self.conn = ibkr_conn
        self._buffers: dict[str, TickerBuffer] = {}
        self._tape_subs: dict[str, object] = {}    # ticker -> ib_async ticker obj
        self._depth_subs: dict[str, object] = {}   # ticker -> ib_async depth obj
        self._contracts: dict[str, object] = {}    # ticker -> Stock contract
        self._depth_failed: set[str] = set()       # tickers hvor depth fejlede
        self._error_handler_attached = False
        self._prune_task: Optional[asyncio.Task] = None
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Start baggrunds-pruning. Subscriptions sker via subscribe()."""
        if self._running:
            return
        self._running = True
        self._prune_task = asyncio.create_task(self._prune_loop())
        logger.info("[TapeBuffer] Startet")

    async def stop(self) -> None:
        """Stop alle subscriptions og baggrunds-tasks."""
        self._running = False
        if self._prune_task:
            self._prune_task.cancel()
            try:
                await self._prune_task
            except asyncio.CancelledError:
                pass
        await self.unsubscribe_all()
        logger.info("[TapeBuffer] Stoppet")

    # ── Subscribe / unsubscribe ─────────────────────────────

    async def subscribe(self, ticker: str) -> dict:
        """
        Start tape + depth subscription for ticker.

        Returnerer status-dict: {tape_ok: bool, depth_ok: bool, errors: list[str]}
        Failures kastes ALDRIG videre — vi accepterer dem og logger.
        """
        ticker = ticker.upper()
        if ticker in self._buffers:
            return {"tape_ok": True, "depth_ok": ticker not in self._depth_failed,
                    "errors": []}

        if not self.conn.connected:
            return {"tape_ok": False, "depth_ok": False,
                    "errors": ["IBKR ikke forbundet"]}

        self._buffers[ticker] = TickerBuffer()
        errors: list[str] = []

        from ib_async import Stock

        contract = Stock(ticker, "SMART", "USD")
        try:
            await self.conn.ib.qualifyContractsAsync(contract)
        except Exception as e:
            errors.append(f"qualify fejlede: {e}")
            del self._buffers[ticker]
            return {"tape_ok": False, "depth_ok": False, "errors": errors}

        self._contracts[ticker] = contract

        # Tape — virker næsten altid (ingen særskilt subscription)
        tape_ok = await self._subscribe_tape(ticker, contract, errors)

        # Depth — kan fejle pga. IBKR's 3-samtidige-grænse, vi accepterer
        depth_ok = await self._subscribe_depth(ticker, contract, errors)

        logger.info(
            f"[TapeBuffer] {ticker}: tape={'OK' if tape_ok else 'FAIL'} "
            f"depth={'OK' if depth_ok else 'FAIL'}"
        )
        return {"tape_ok": tape_ok, "depth_ok": depth_ok, "errors": errors}

    async def _subscribe_tape(self, ticker: str, contract, errors: list[str]) -> bool:
        try:
            tick_obj = self.conn.ib.reqTickByTickData(
                contract, "AllLast", numberOfTicks=0, ignoreSize=False
            )
        except Exception as e:
            errors.append(f"tape req fejlede: {e}")
            return False

        # Closure: opdater buffer hver gang nye ticks kommer
        buf = self._buffers[ticker]

        def on_update(t_obj):
            try:
                bid = t_obj.bid if _is_valid(t_obj.bid) else None
                ask = t_obj.ask if _is_valid(t_obj.ask) else None
                for t in t_obj.tickByTicks:
                    direction = "neutral"
                    if ask and t.price >= ask:
                        direction = "up"
                    elif bid and t.price <= bid:
                        direction = "down"
                    buf.add_trade(TradeTick(
                        ts=t.time if t.time else datetime.now(),
                        price=float(t.price),
                        size=int(t.size),
                        direction=direction,
                    ))
            except Exception as e:
                logger.debug(f"[TapeBuffer] {ticker} tape-callback fejl: {e}")

        tick_obj.updateEvent += on_update
        self._tape_subs[ticker] = tick_obj
        return True

    async def _subscribe_depth(self, ticker: str, contract, errors: list[str]) -> bool:
        # Attach global error-handler én gang
        if not self._error_handler_attached:
            self.conn.ib.errorEvent += self._on_ib_error
            self._error_handler_attached = True

        try:
            depth_obj = self.conn.ib.reqMktDepth(contract, numRows=5, isSmartDepth=True)
        except Exception as e:
            errors.append(f"depth req fejlede: {e}")
            self._depth_failed.add(ticker)
            return False

        buf = self._buffers[ticker]

        def on_depth_update(t_obj):
            try:
                bids = [
                    (float(b.price), int(b.size))
                    for b in (t_obj.domBids or [])
                    if _is_valid(b.price)
                ]
                asks = [
                    (float(a.price), int(a.size))
                    for a in (t_obj.domAsks or [])
                    if _is_valid(a.price)
                ]
                if not bids and not asks:
                    return
                buf.add_depth(DepthSnapshot(
                    ts=datetime.now(),
                    bids=bids,
                    asks=asks,
                ))
            except Exception as e:
                logger.debug(f"[TapeBuffer] {ticker} depth-callback fejl: {e}")

        depth_obj.updateEvent += on_depth_update
        self._depth_subs[ticker] = depth_obj
        return True

    def _on_ib_error(self, reqId, errorCode, errorString, contract):
        """Fang depth-subscription fejl og marker ticker som depth-failed."""
        # 309: Market depth requires subscription
        # 354: Requested market data is not subscribed
        # 10089/10090: Market depth subscription level not granted
        if errorCode in (309, 354, 10089, 10090):
            sym = getattr(contract, "symbol", None) if contract else None
            if sym:
                self._depth_failed.add(sym)
                # Stop depth-sub hvis den blev sat op
                depth_obj = self._depth_subs.pop(sym, None)
                if depth_obj is not None:
                    try:
                        c = self._contracts.get(sym)
                        if c is not None:
                            self.conn.ib.cancelMktDepth(c, isSmartDepth=True)
                    except Exception:
                        pass
                logger.warning(
                    f"[TapeBuffer] Depth ikke tilgængelig for {sym} "
                    f"(IBKR fejl {errorCode}) — fortsætter uden L2 for denne"
                )

    async def unsubscribe(self, ticker: str) -> None:
        ticker = ticker.upper()
        tick_obj = self._tape_subs.pop(ticker, None)
        depth_obj = self._depth_subs.pop(ticker, None)
        contract = self._contracts.pop(ticker, None)
        self._buffers.pop(ticker, None)
        self._depth_failed.discard(ticker)

        if contract is None:
            return

        try:
            if tick_obj is not None:
                self.conn.ib.cancelTickByTickData(contract, "AllLast")
        except Exception as e:
            logger.debug(f"[TapeBuffer] cancelTickByTickData {ticker}: {e}")

        try:
            if depth_obj is not None:
                self.conn.ib.cancelMktDepth(contract, isSmartDepth=True)
        except Exception as e:
            logger.debug(f"[TapeBuffer] cancelMktDepth {ticker}: {e}")

    async def unsubscribe_all(self) -> None:
        for ticker in list(self._buffers.keys()):
            await self.unsubscribe(ticker)

    # ── Pruning ──────────────────────────────────────────────

    async def _prune_loop(self) -> None:
        """Fjern entries ældre end BUFFER_SECONDS hvert 30. sekund."""
        try:
            while self._running:
                await asyncio.sleep(30)
                cutoff = datetime.now() - timedelta(seconds=BUFFER_SECONDS + 10)
                for buf in self._buffers.values():
                    buf.prune(cutoff)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[TapeBuffer] Prune-loop fejl: {e}")

    # ── Snapshot — bruges af forensics ──────────────────────

    def snapshot(self, ticker: str, lookback_sec: int = 60) -> dict:
        """
        Byg snapshot af tape og depth for de seneste `lookback_sec` sekunder.

        Returnerer struktureret dict klar til journal-payload.
        Hvis ticker ikke er subscribed eller buffer er tom, returneres
        nul-værdier (ikke None) så analyse-scripts kan stole på felternes
        eksistens.
        """
        ticker = ticker.upper()
        buf = self._buffers.get(ticker)

        if buf is None:
            return _empty_snapshot(reason="not_subscribed")

        now = datetime.now()
        cutoff = now - timedelta(seconds=lookback_sec)
        recent_trades = [t for t in buf.trades if t.ts >= cutoff]

        # ── Tape-aggregeringer ──
        if recent_trades:
            up_volume = sum(t.size for t in recent_trades if t.direction == "up")
            down_volume = sum(t.size for t in recent_trades if t.direction == "down")
            neutral_volume = sum(t.size for t in recent_trades if t.direction == "neutral")
            total_volume = up_volume + down_volume + neutral_volume

            largest_trade = max(recent_trades, key=lambda t: t.size)
            largest_size = largest_trade.size
            largest_direction = largest_trade.direction

            # Aggressor-ratio: up_vol / (up_vol + down_vol) — 0.5 = neutralt,
            # >0.6 = aggressiv buying, <0.4 = aggressiv selling
            directional = up_volume + down_volume
            aggressor_ratio = (up_volume / directional) if directional > 0 else None

            # Sidste 5 trades — kvalitativ "feel" for momentum
            last_5 = [
                {
                    "price": round(t.price, 4),
                    "size": t.size,
                    "direction": t.direction,
                    "ts": t.ts.isoformat() if t.ts else None,
                }
                for t in recent_trades[-5:]
            ]
        else:
            up_volume = down_volume = neutral_volume = total_volume = 0
            largest_size = 0
            largest_direction = None
            aggressor_ratio = None
            last_5 = []

        tape = {
            "lookback_sec":      lookback_sec,
            "trade_count":       len(recent_trades),
            "total_volume":      total_volume,
            "up_volume":         up_volume,
            "down_volume":       down_volume,
            "neutral_volume":    neutral_volume,
            "aggressor_ratio":   round(aggressor_ratio, 3) if aggressor_ratio is not None else None,
            "largest_trade_size": largest_size,
            "largest_trade_direction": largest_direction,
            "last_5_trades":     last_5,
        }

        # ── Depth-snapshot — seneste vi har set ──
        if buf.depth_available and (buf.last_depth_bids or buf.last_depth_asks):
            bids = buf.last_depth_bids[:5]
            asks = buf.last_depth_asks[:5]
            total_bid_size = sum(s for _, s in bids)
            total_ask_size = sum(s for _, s in asks)

            best_bid = bids[0][0] if bids else None
            best_ask = asks[0][0] if asks else None
            spread = (best_ask - best_bid) if (best_bid and best_ask) else None
            spread_pct = (spread / best_bid * 100.0) if (spread and best_bid) else None

            largest_bid = max(bids, key=lambda x: x[1]) if bids else None
            largest_ask = max(asks, key=lambda x: x[1]) if asks else None

            # Imbalance: total_bid / (total_bid + total_ask). >0.6 = bullish,
            # <0.4 = bearish, 0.5 = balanceret
            denom = total_bid_size + total_ask_size
            bid_ask_imbalance = (total_bid_size / denom) if denom > 0 else None

            depth = {
                "available":         True,
                "best_bid":          round(best_bid, 4) if best_bid else None,
                "best_ask":          round(best_ask, 4) if best_ask else None,
                "spread":            round(spread, 4) if spread is not None else None,
                "spread_pct":        round(spread_pct, 3) if spread_pct is not None else None,
                "total_bid_size":    total_bid_size,
                "total_ask_size":    total_ask_size,
                "bid_ask_imbalance": round(bid_ask_imbalance, 3) if bid_ask_imbalance is not None else None,
                "largest_bid_price": round(largest_bid[0], 4) if largest_bid else None,
                "largest_bid_size":  largest_bid[1] if largest_bid else None,
                "largest_ask_price": round(largest_ask[0], 4) if largest_ask else None,
                "largest_ask_size":  largest_ask[1] if largest_ask else None,
                "bid_levels":        [{"price": round(p, 4), "size": s} for p, s in bids],
                "ask_levels":        [{"price": round(p, 4), "size": s} for p, s in asks],
            }
        else:
            depth = {
                "available": False,
                "reason": "depth_failed" if ticker in self._depth_failed else "no_data",
            }

        return {"tape": tape, "depth": depth}


def _empty_snapshot(reason: str) -> dict:
    return {
        "tape": {
            "lookback_sec":      0,
            "trade_count":       0,
            "total_volume":      0,
            "up_volume":         0,
            "down_volume":       0,
            "neutral_volume":    0,
            "aggressor_ratio":   None,
            "largest_trade_size": 0,
            "largest_trade_direction": None,
            "last_5_trades":     [],
            "reason":            reason,
        },
        "depth": {"available": False, "reason": reason},
    }

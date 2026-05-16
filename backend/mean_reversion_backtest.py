"""
mean_reversion_backtest.py
──────────────────────────
RSI(2) Mean Reversion strategi — backtest motor.

Strategien (Connors-stil):
  Entry:
    - RSI(2) < 10 (kraftigt oversold)
    - Pris > 200-MA (trend-filter, kun long i uptrend)
    - Ingen åben position

  Exit (hvad end kommer først):
    - RSI(2) > 50 (mean reversion komplet) → TAKE PROFIT
    - Pris faldet -1% fra entry → STOP LOSS
    - Sidste bar af dagen (15:55 ET) → FORCE CLOSE

Friktion:
  - Slippage: $0.02 per aktie (entry + exit)
  - Fees: $0.005 per aktie per side

Brug:
    python mean_reversion_backtest.py
        Standard kørsel, alle tickers, alle data

    python mean_reversion_backtest.py --ticker SPY
        Kun én ticker

    python mean_reversion_backtest.py --export trades.csv
        Eksporter alle trades til CSV

    python mean_reversion_backtest.py --rsi-entry 5 --rsi-exit 70
        Custom parametre

Placering: C:\\Projects\\trading-dash\\backend\\mean_reversion_backtest.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

from historical_db import get_connection


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mr_backtest")


# ── Strategi-parametre (defaults — opdateret efter sweep) ────
RSI_PERIOD       = 2
RSI_ENTRY_LEVEL  = 10.0       # buy hvis RSI(2) < 10
RSI_EXIT_LEVEL   = 60.0       # sell hvis RSI(2) > 60 (sweep winner)
MA_PERIOD        = 200        # trend-filter
STOP_LOSS_PCT    = 0.01       # -1% fra entry (sweep winner)
CAPITAL_PER_TRADE = 5_000.0   # $ per handel
DEFAULT_TIMEFRAME = "15min"   # sweep viste 15min er klart bedst

# Friktion
SLIPPAGE_PER_SHARE = 0.02     # $ per aktie
FEE_PER_SHARE      = 0.005    # $ per aktie per side

# Tidspunkter (ET) — uses for time-based filtering
MARKET_OPEN_TIME       = dtime(9, 30)
MARKET_CLOSE_TIME      = dtime(16, 0)

# Force-close-tid afhænger af timeframe:
# 5-min: 15:55 (sidste 5-min bar)
# 15-min: 15:45 (sidste 15-min bar)
FORCE_CLOSE_BY_TIMEFRAME = {
    "5min":  dtime(15, 55),
    "15min": dtime(15, 45),
}

# Default (bagudkompat) — sættes ved import, kan ændres af Backtester
FORCE_CLOSE_TIME = FORCE_CLOSE_BY_TIMEFRAME[DEFAULT_TIMEFRAME]


# ─────────────────────────────────────────────────────────────────
# Data-klasser
# ─────────────────────────────────────────────────────────────────

@dataclass
class Bar:
    """Én OHLCV bar med beregnede indikatorer."""
    ticker:    str
    timestamp: datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int
    rsi2:      Optional[float] = None
    ma200:     Optional[float] = None


@dataclass
class Position:
    """En åben handel."""
    ticker:      str
    entry_time:  datetime
    entry_price: float        # inkluderer slippage
    shares:      int
    stop_price:  float
    fees_paid:   float        # entry-fee allerede betalt


@dataclass
class Trade:
    """En lukket handel — registreret i historikken."""
    ticker:        str
    entry_time:    datetime
    exit_time:     datetime
    entry_price:   float
    exit_price:    float
    shares:        int
    gross_pnl:     float
    fees:          float
    net_pnl:       float
    pnl_pct:       float
    exit_reason:   str         # "take_profit" / "stop_loss" / "force_close"
    duration_min:  int


# ─────────────────────────────────────────────────────────────────
# Indikator-beregning
# ─────────────────────────────────────────────────────────────────

class RSICalculator:
    """
    Wilder's RSI med rolling state.
    Bruger den klassiske Wilder smoothing — samme som TradingView.
    """

    def __init__(self, period: int = 2):
        self.period = period
        self.prev_close: Optional[float] = None
        self.avg_gain: Optional[float] = None
        self.avg_loss: Optional[float] = None
        self.bars_seen = 0

    def update(self, close: float) -> Optional[float]:
        """Føj en ny close til. Returnér RSI eller None hvis ikke nok data."""
        if self.prev_close is None:
            self.prev_close = close
            return None

        change = close - self.prev_close
        gain = max(change, 0)
        loss = max(-change, 0)

        self.bars_seen += 1

        if self.bars_seen <= self.period:
            # Akkumuler sum for initial average
            if self.avg_gain is None:
                self.avg_gain = gain
                self.avg_loss = loss
            else:
                self.avg_gain += gain
                self.avg_loss += loss

            if self.bars_seen == self.period:
                # Konvertér sum til gennemsnit
                self.avg_gain /= self.period
                self.avg_loss /= self.period
            else:
                self.prev_close = close
                return None
        else:
            # Wilder's smoothing
            self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period

        self.prev_close = close

        if self.avg_loss == 0:
            return 100.0 if self.avg_gain > 0 else 50.0

        rs = self.avg_gain / self.avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi


class MACalculator:
    """Simple Moving Average med rolling buffer."""

    def __init__(self, period: int = 200):
        self.period = period
        self.values: list[float] = []

    def update(self, value: float) -> Optional[float]:
        """Føj værdi. Returnér MA eller None hvis ikke nok data."""
        self.values.append(value)
        if len(self.values) < self.period:
            return None
        if len(self.values) > self.period:
            self.values.pop(0)
        return sum(self.values) / self.period


# ─────────────────────────────────────────────────────────────────
# Data-loading fra database
# ─────────────────────────────────────────────────────────────────

def load_bars(ticker: str) -> list[Bar]:
    """Hent alle 5-min bars for én ticker, sorteret kronologisk."""
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM bars_5min
            WHERE ticker = ?
              AND is_premarket = 0
            ORDER BY timestamp ASC
        """, (ticker,))
        rows = cur.fetchall()

    bars = []
    for r in rows:
        # Parse timestamp — handle both with og without timezone
        ts_str = r["timestamp"]
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            # Fallback hvis ikke ISO format
            ts = datetime.strptime(ts_str.split("+")[0].strip(), "%Y-%m-%d %H:%M:%S")

        bars.append(Bar(
            ticker=ticker,
            timestamp=ts,
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=int(r["volume"]),
        ))

    return bars


def get_available_tickers() -> list[str]:
    """Find alle tickers der har 5-min data."""
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT DISTINCT ticker FROM bars_5min
            ORDER BY ticker
        """)
        return [row["ticker"] for row in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────
# Backtest-engine
# ─────────────────────────────────────────────────────────────────

class Backtester:
    """
    Mean Reversion backtest engine for én ticker.

    Behandler bars i kronologisk rækkefølge, simulerer entry/exit
    og logger alle trades.
    """

    def __init__(
        self,
        rsi_entry: float = RSI_ENTRY_LEVEL,
        rsi_exit: float = RSI_EXIT_LEVEL,
        stop_pct: float = STOP_LOSS_PCT,
        capital: float = CAPITAL_PER_TRADE,
        slippage: float = SLIPPAGE_PER_SHARE,
        fee: float = FEE_PER_SHARE,
        timeframe: str = DEFAULT_TIMEFRAME,
    ):
        self.rsi_entry = rsi_entry
        self.rsi_exit  = rsi_exit
        self.stop_pct  = stop_pct
        self.capital   = capital
        self.slippage  = slippage
        self.fee       = fee
        self.timeframe = timeframe
        self.force_close_time = FORCE_CLOSE_BY_TIMEFRAME.get(timeframe, FORCE_CLOSE_TIME)

    def run(self, bars: list[Bar]) -> list[Trade]:
        """
        Kør backtest på en liste af bars (skal være kronologisk sorteret).
        Returnér liste af alle trades.
        """
        if not bars:
            return []

        ticker = bars[0].ticker
        trades: list[Trade] = []
        position: Optional[Position] = None

        rsi_calc = RSICalculator(period=RSI_PERIOD)
        ma_calc  = MACalculator(period=MA_PERIOD)

        for i, bar in enumerate(bars):
            # Opdater indikatorer
            bar.rsi2  = rsi_calc.update(bar.close)
            bar.ma200 = ma_calc.update(bar.close)

            # Bestem om dette er en intraday bar (vi handler kun under RTH)
            bar_time = bar.timestamp.time()
            if not (MARKET_OPEN_TIME <= bar_time <= MARKET_CLOSE_TIME):
                continue

            # ── EXIT-logik: tjek åben position ────────────────
            if position is not None:
                exit_decision = self._check_exit(position, bar)
                if exit_decision is not None:
                    exit_reason, exit_price = exit_decision
                    trade = self._close_position(position, bar, exit_reason, exit_price)
                    trades.append(trade)
                    position = None
                    # Vi tager ikke ny entry på samme bar som exit
                    continue

            # ── ENTRY-logik: kun hvis ingen position ───────────
            if position is None:
                if self._should_enter(bar):
                    position = self._open_position(bar)

        # Hvis position stadig åben ved sidste bar → force close
        if position is not None and bars:
            last_bar = bars[-1]
            trade = self._close_position(position, last_bar, "force_close_eof", last_bar.close)
            trades.append(trade)

        return trades

    def _should_enter(self, bar: Bar) -> bool:
        """Tjek alle entry-betingelser."""
        # Indikatorer skal være klar
        if bar.rsi2 is None or bar.ma200 is None:
            return False

        # Force-close-tid? Ingen ny entry sent på dagen
        if bar.timestamp.time() >= self.force_close_time:
            return False

        # RSI oversold
        if bar.rsi2 >= self.rsi_entry:
            return False

        # Pris over MA (trend-filter)
        if bar.close <= bar.ma200:
            return False

        return True

    def _check_exit(self, pos: Position, bar: Bar) -> Optional[tuple[str, float]]:
        """
        Tjek exit-betingelser. Returnér (reason, exit_price) eller None.

        Priority:
          1. Stop loss (intra-bar low rammer stop)
          2. Force-close (sidste bar af dagen)
          3. Take profit (RSI > exit level)
        """
        # Stop loss — tjek først (worst case)
        if bar.low <= pos.stop_price:
            # Exit ved stop-pris (konservativ antagelse om slippage)
            return ("stop_loss", pos.stop_price - self.slippage)

        # Force-close ved force-close-tid
        if bar.timestamp.time() >= self.force_close_time:
            return ("force_close", bar.close - self.slippage)

        # Take profit baseret på RSI
        if bar.rsi2 is not None and bar.rsi2 > self.rsi_exit:
            return ("take_profit", bar.close - self.slippage)

        return None

    def _open_position(self, bar: Bar) -> Position:
        """Åbn position ved bar's close (med slippage)."""
        entry_price = bar.close + self.slippage
        shares = max(1, int(self.capital / entry_price))
        stop_price = entry_price * (1.0 - self.stop_pct)
        fees = shares * self.fee

        return Position(
            ticker=bar.ticker,
            entry_time=bar.timestamp,
            entry_price=entry_price,
            shares=shares,
            stop_price=stop_price,
            fees_paid=fees,
        )

    def _close_position(
        self,
        pos: Position,
        bar: Bar,
        reason: str,
        exit_price: float,
    ) -> Trade:
        """Luk position og beregn P&L."""
        exit_fees = pos.shares * self.fee
        total_fees = pos.fees_paid + exit_fees

        gross_pnl = (exit_price - pos.entry_price) * pos.shares
        net_pnl   = gross_pnl - total_fees
        pnl_pct   = (exit_price - pos.entry_price) / pos.entry_price * 100

        duration_sec = (bar.timestamp - pos.entry_time).total_seconds()
        duration_min = max(1, int(duration_sec / 60))

        return Trade(
            ticker=pos.ticker,
            entry_time=pos.entry_time,
            exit_time=bar.timestamp,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            gross_pnl=gross_pnl,
            fees=total_fees,
            net_pnl=net_pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            duration_min=duration_min,
        )


# ─────────────────────────────────────────────────────────────────
# Statistik og rapporter
# ─────────────────────────────────────────────────────────────────

def calculate_stats(trades: list[Trade]) -> dict:
    """Beregn aggregeret statistik fra en liste af trades."""
    if not trades:
        return {
            "total_trades":   0,
            "wins":           0,
            "losses":         0,
            "win_rate":       0.0,
            "total_pnl":      0.0,
            "total_fees":     0.0,
            "gross_profit":   0.0,
            "gross_loss":     0.0,
            "profit_factor":  0.0,
            "avg_win":        0.0,
            "avg_loss":       0.0,
            "max_win":        0.0,
            "max_loss":       0.0,
            "avg_duration":   0,
            "exits_by_reason": {},
            "max_drawdown":   0.0,
        }

    wins   = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    total_pnl    = sum(t.net_pnl for t in trades)
    total_fees   = sum(t.fees    for t in trades)
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss   = abs(sum(t.net_pnl for t in losses))

    # Max drawdown (peak-to-trough på cumulative P&L)
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum_pnl += t.net_pnl
        if cum_pnl > peak:
            peak = cum_pnl
        dd = peak - cum_pnl
        if dd > max_dd:
            max_dd = dd

    # Exit-reason distribution
    exits: dict[str, int] = {}
    for t in trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1

    return {
        "total_trades":   len(trades),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       len(wins) / len(trades) * 100,
        "total_pnl":      total_pnl,
        "total_fees":     total_fees,
        "gross_profit":   gross_profit,
        "gross_loss":     gross_loss,
        "profit_factor":  gross_profit / gross_loss if gross_loss > 0 else 0.0,
        "avg_win":        gross_profit / len(wins)   if wins   else 0.0,
        "avg_loss":       -gross_loss  / len(losses) if losses else 0.0,
        "max_win":        max((t.net_pnl for t in trades), default=0.0),
        "max_loss":       min((t.net_pnl for t in trades), default=0.0),
        "avg_duration":   sum(t.duration_min for t in trades) // len(trades) if trades else 0,
        "exits_by_reason": exits,
        "max_drawdown":   max_dd,
    }


def print_ticker_stats(ticker: str, trades: list[Trade], stats: dict) -> None:
    """Print stats for én ticker."""
    print()
    print(f"  ── {ticker} ──────────────────────────────────────")
    print(f"    Total trades:       {stats['total_trades']}")
    if stats['total_trades'] == 0:
        return
    print(f"    Wins / Losses:      {stats['wins']} / {stats['losses']}")
    print(f"    Win rate:           {stats['win_rate']:.1f}%")
    print(f"    Total P&L:          ${stats['total_pnl']:>+10,.2f}")
    print(f"    Total fees:         ${stats['total_fees']:>10,.2f}")
    print(f"    Gross profit:       ${stats['gross_profit']:>+10,.2f}")
    print(f"    Gross loss:         ${-stats['gross_loss']:>+10,.2f}")
    print(f"    Profit factor:      {stats['profit_factor']:.2f}")
    print(f"    Avg win:            ${stats['avg_win']:>+10,.2f}")
    print(f"    Avg loss:           ${stats['avg_loss']:>+10,.2f}")
    print(f"    Max win:            ${stats['max_win']:>+10,.2f}")
    print(f"    Max loss:           ${stats['max_loss']:>+10,.2f}")
    print(f"    Avg duration:       {stats['avg_duration']} min")
    print(f"    Max drawdown:       ${stats['max_drawdown']:>10,.2f}")
    print(f"    Exits by reason:")
    for reason, count in sorted(stats['exits_by_reason'].items()):
        pct = count / stats['total_trades'] * 100
        print(f"      {reason:20s} {count:>4} ({pct:>5.1f}%)")


def print_combined_stats(all_trades: list[Trade]) -> None:
    """Print aggregeret statistik på tværs af alle tickers."""
    if not all_trades:
        print("\n  Ingen trades.")
        return

    stats = calculate_stats(all_trades)

    print()
    print("=" * 70)
    print("  Samlet på tværs af alle tickers")
    print("=" * 70)
    print(f"  Total trades:       {stats['total_trades']}")
    print(f"  Wins / Losses:      {stats['wins']} / {stats['losses']}")
    print(f"  Win rate:           {stats['win_rate']:.1f}%")
    print(f"  Total P&L:          ${stats['total_pnl']:>+10,.2f}")
    print(f"  Total fees:         ${stats['total_fees']:>10,.2f}")
    print(f"  Profit factor:      {stats['profit_factor']:.2f}")
    print(f"  Max drawdown:       ${stats['max_drawdown']:>10,.2f}")
    print(f"  Avg duration:       {stats['avg_duration']} min")
    print()

    # Edge-vurdering
    print(f"  ★ Edge-vurdering:")
    if stats['total_trades'] < 30:
        print(f"    ⚠  For få trades ({stats['total_trades']}) — statistisk usikkert")
    elif stats['profit_factor'] >= 1.5 and stats['win_rate'] >= 60:
        print(f"    ✓ STÆRK EDGE: PF={stats['profit_factor']:.2f}, WR={stats['win_rate']:.1f}%")
    elif stats['profit_factor'] >= 1.2 and stats['win_rate'] >= 55:
        print(f"    ✓ EDGE: PF={stats['profit_factor']:.2f}, WR={stats['win_rate']:.1f}%")
    elif stats['profit_factor'] >= 1.0:
        print(f"    ◐ MARGINAL: PF={stats['profit_factor']:.2f}, WR={stats['win_rate']:.1f}%")
    else:
        print(f"    ✗ INGEN EDGE: PF={stats['profit_factor']:.2f}, WR={stats['win_rate']:.1f}%")


# ─────────────────────────────────────────────────────────────────
# CSV export
# ─────────────────────────────────────────────────────────────────

def export_trades_csv(trades: list[Trade], output_path: Path) -> int:
    if not trades:
        return 0

    with output_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "entry_time", "exit_time", "entry_price", "exit_price",
            "shares", "gross_pnl", "fees", "net_pnl", "pnl_pct",
            "exit_reason", "duration_min",
        ])
        for t in trades:
            w.writerow([
                t.ticker,
                t.entry_time.isoformat(),
                t.exit_time.isoformat(),
                round(t.entry_price, 4),
                round(t.exit_price, 4),
                t.shares,
                round(t.gross_pnl, 2),
                round(t.fees, 2),
                round(t.net_pnl, 2),
                round(t.pnl_pct, 3),
                t.exit_reason,
                t.duration_min,
            ])

    return len(trades)


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Mean Reversion RSI(2) backtest")
    parser.add_argument("--ticker", type=str, help="Kør kun for én ticker")
    parser.add_argument("--rsi-entry", type=float, default=RSI_ENTRY_LEVEL,
                        help=f"RSI entry threshold (default: {RSI_ENTRY_LEVEL})")
    parser.add_argument("--rsi-exit", type=float, default=RSI_EXIT_LEVEL,
                        help=f"RSI exit threshold (default: {RSI_EXIT_LEVEL})")
    parser.add_argument("--stop-pct", type=float, default=STOP_LOSS_PCT,
                        help=f"Stop loss procent (default: {STOP_LOSS_PCT})")
    parser.add_argument("--capital", type=float, default=CAPITAL_PER_TRADE,
                        help=f"Kapital per trade (default: ${CAPITAL_PER_TRADE})")
    parser.add_argument("--timeframe", choices=["5min", "15min"], default=DEFAULT_TIMEFRAME,
                        help=f"Bar-timeframe (default: {DEFAULT_TIMEFRAME})")
    parser.add_argument("--export", type=str, help="Eksporter trades til CSV")
    args = parser.parse_args()

    # Bestem tickers at backteste
    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = get_available_tickers()

    if not tickers:
        logger.error("Ingen tickers med 5-min data fundet. Kør download_intraday_ibkr.py først.")
        return 1

    logger.info(f"Backtester {len(tickers)} ticker(s): {', '.join(tickers)}")
    logger.info(f"Timeframe: {args.timeframe}")
    logger.info(f"Parametre: RSI entry < {args.rsi_entry}, exit > {args.rsi_exit}, "
                f"stop {args.stop_pct*100:.2f}%, capital ${args.capital:,.0f}")
    print()

    backtester = Backtester(
        rsi_entry=args.rsi_entry,
        rsi_exit=args.rsi_exit,
        stop_pct=args.stop_pct,
        capital=args.capital,
        timeframe=args.timeframe,
    )

    print("=" * 70)
    print("  Mean Reversion Backtest — Per Ticker")
    print("=" * 70)

    # Import aggregering hvis vi bruger 15-min
    if args.timeframe == "15min":
        from mr_param_sweep import aggregate_to_15min

    all_trades: list[Trade] = []
    for ticker in tickers:
        logger.info(f"Loading bars for {ticker}...")
        bars_raw = load_bars(ticker)
        if not bars_raw:
            logger.warning(f"  {ticker}: ingen bars fundet")
            continue

        # Aggreger hvis 15-min
        if args.timeframe == "15min":
            bars = aggregate_to_15min(bars_raw)
            logger.info(f"  {ticker}: {len(bars_raw):,} 5-min bars → {len(bars):,} 15-min bars")
        else:
            bars = bars_raw
            logger.info(f"  {ticker}: {len(bars):,} bars")

        trades = backtester.run(bars)
        stats = calculate_stats(trades)
        print_ticker_stats(ticker, trades, stats)
        all_trades.extend(trades)

    # Samlet stats
    print_combined_stats(all_trades)

    # CSV export
    if args.export:
        path = Path(args.export)
        count = export_trades_csv(all_trades, path)
        print()
        logger.info(f"✓ Eksporteret {count} trades → {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
mr_param_sweep.py
─────────────────
Parameter sweep for RSI(2) Mean Reversion strategi.

Tester systematisk forskellige kombinationer af:
  - RSI exit threshold: [50, 60, 70, 80]
  - Stop loss procent:  [0.5%, 0.75%, 1.0%]
  - Timeframe:          [5-min, 15-min]

= 24 kombinationer totalt (3 tickers × 24 = 72 backtests)

Aggregerer 15-min bars in-memory fra 5-min data — ingen ny download.

Brug:
    python mr_param_sweep.py
        Kør hele sweep, vis ranking

    python mr_param_sweep.py --export results.csv
        Eksporter alle resultater til CSV

    python mr_param_sweep.py --top 5
        Vis top N resultater (default 10)

Placering: C:\\Projects\\trading-dash\\backend\\mr_param_sweep.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from itertools import product
from pathlib import Path

# Importer fra eksisterende backtest-modul
from mean_reversion_backtest import (
    Bar, Trade, Backtester,
    load_bars, get_available_tickers, calculate_stats,
)

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mr_sweep")


# ── Parameter-grid ───────────────────────────────────────────
RSI_EXIT_VALUES = [50.0, 60.0, 70.0, 80.0]
STOP_PCT_VALUES = [0.005, 0.0075, 0.010]
TIMEFRAMES      = ["5min", "15min"]


# ─────────────────────────────────────────────────────────────────
# Aggregering: 5-min → 15-min
# ─────────────────────────────────────────────────────────────────

def aggregate_to_15min(bars_5min: list[Bar]) -> list[Bar]:
    """
    Aggreger 5-min bars til 15-min bars.

    En 15-min bar består af 3 konsekutive 5-min bars hvor:
      - open  = første bar's open
      - close = sidste bar's close
      - high  = max af alle tre
      - low   = min af alle tre
      - volume = sum

    Vi grupperer efter "minutes since midnight / 15" — så bars
    starter præcis ved 09:30, 09:45, 10:00, osv.
    """
    if not bars_5min:
        return []

    aggregated: list[Bar] = []
    current_bucket: list[Bar] = []
    current_bucket_id: tuple = None

    for bar in bars_5min:
        # Bucket-ID: (dato, time_15min_aligned)
        # 15-min aligned betyder vi runder ned til nærmeste 15-min interval
        minutes_since_midnight = bar.timestamp.hour * 60 + bar.timestamp.minute
        bucket_minute = (minutes_since_midnight // 15) * 15
        bucket_id = (
            bar.timestamp.date(),
            bucket_minute,
        )

        if current_bucket_id is None:
            current_bucket_id = bucket_id

        if bucket_id != current_bucket_id:
            # Flush nuværende bucket
            if current_bucket:
                aggregated.append(_make_15min_bar(current_bucket, current_bucket_id))
            current_bucket = []
            current_bucket_id = bucket_id

        current_bucket.append(bar)

    # Flush sidste bucket
    if current_bucket:
        aggregated.append(_make_15min_bar(current_bucket, current_bucket_id))

    return aggregated


def _make_15min_bar(bars: list[Bar], bucket_id: tuple) -> Bar:
    """Lav én 15-min bar fra liste af 5-min bars i samme bucket."""
    date, bucket_minute = bucket_id

    # Timestamp = starten af bucket'en (fx 09:30, 09:45)
    hour = bucket_minute // 60
    minute = bucket_minute % 60
    ts = datetime.combine(date, dtime(hour, minute))

    # Bevar timezone hvis original havde det
    if bars[0].timestamp.tzinfo is not None:
        ts = ts.replace(tzinfo=bars[0].timestamp.tzinfo)

    return Bar(
        ticker=bars[0].ticker,
        timestamp=ts,
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=bars[-1].close,
        volume=sum(b.volume for b in bars),
    )


# ─────────────────────────────────────────────────────────────────
# Sweep
# ─────────────────────────────────────────────────────────────────

@dataclass
class SweepResult:
    """Ét resultat fra én parameter-kombination."""
    timeframe:     str
    rsi_exit:      float
    stop_pct:      float
    total_trades:  int
    wins:          int
    losses:        int
    win_rate:      float
    total_pnl:     float
    profit_factor: float
    avg_win:       float
    avg_loss:      float
    max_drawdown:  float
    avg_duration:  int

    @property
    def label(self) -> str:
        return f"{self.timeframe} RSI>{self.rsi_exit:.0f} Stop{self.stop_pct*100:.2f}%"


def run_sweep(
    bars_by_ticker_by_tf: dict[str, dict[str, list[Bar]]],
) -> list[SweepResult]:
    """
    Kør sweep over alle parameter-kombinationer.

    bars_by_ticker_by_tf: {ticker: {timeframe: [bars]}}

    Returnér liste af SweepResult, én per kombination.
    """
    results: list[SweepResult] = []
    combinations = list(product(TIMEFRAMES, RSI_EXIT_VALUES, STOP_PCT_VALUES))
    total = len(combinations)

    for i, (tf, rsi_exit, stop_pct) in enumerate(combinations, 1):
        # Saml trades på tværs af tickere for denne kombination
        all_trades: list[Trade] = []

        backtester = Backtester(
            rsi_entry=10.0,         # vi varierer ikke entry
            rsi_exit=rsi_exit,
            stop_pct=stop_pct,
            timeframe=tf,           # KRITISK: bruger korrekt force_close-tid per tf
        )

        for ticker, by_tf in bars_by_ticker_by_tf.items():
            bars = by_tf[tf]
            trades = backtester.run(bars)
            all_trades.extend(trades)

        # Beregn aggregeret stats
        stats = calculate_stats(all_trades)

        result = SweepResult(
            timeframe=tf,
            rsi_exit=rsi_exit,
            stop_pct=stop_pct,
            total_trades=stats["total_trades"],
            wins=stats["wins"],
            losses=stats["losses"],
            win_rate=stats["win_rate"],
            total_pnl=stats["total_pnl"],
            profit_factor=stats["profit_factor"],
            avg_win=stats["avg_win"],
            avg_loss=stats["avg_loss"],
            max_drawdown=stats["max_drawdown"],
            avg_duration=stats["avg_duration"],
        )
        results.append(result)

        logger.info(f"[{i:>2}/{total}] {result.label:30s}  "
                    f"trades={result.total_trades:>4}  "
                    f"PF={result.profit_factor:>5.2f}  "
                    f"WR={result.win_rate:>5.1f}%  "
                    f"P&L=${result.total_pnl:>+9.2f}")

    return results


# ─────────────────────────────────────────────────────────────────
# Rapporter
# ─────────────────────────────────────────────────────────────────

def print_ranking(results: list[SweepResult], top_n: int = 10) -> None:
    """Print ranking sorteret efter profit factor."""

    # Sort by PF (descending), with tiebreaker on total_pnl
    sorted_results = sorted(
        results,
        key=lambda r: (r.profit_factor, r.total_pnl),
        reverse=True,
    )

    print()
    print("=" * 100)
    print(f"  Parameter Sweep — Top {min(top_n, len(sorted_results))} Resultater (sorteret efter Profit Factor)")
    print("=" * 100)
    print()
    print(f"  {'#':>3}  {'Konfiguration':<32s}  {'Trades':>7s}  {'WR%':>6s}  "
          f"{'PF':>5s}  {'P&L':>10s}  {'AvgWin':>7s}  {'AvgLoss':>7s}  {'MaxDD':>8s}")
    print(f"  {'-' * 3}  {'-' * 32}  {'-' * 7}  {'-' * 6}  "
          f"{'-' * 5}  {'-' * 10}  {'-' * 7}  {'-' * 7}  {'-' * 8}")

    for i, r in enumerate(sorted_results[:top_n], 1):
        # Highlight profitable konfigurationer
        marker = "★" if r.profit_factor >= 1.2 and r.win_rate >= 55 else " "
        print(f"  {i:>3}{marker} {r.label:<32s}  "
              f"{r.total_trades:>7d}  "
              f"{r.win_rate:>5.1f}%  "
              f"{r.profit_factor:>5.2f}  "
              f"${r.total_pnl:>+9.2f}  "
              f"${r.avg_win:>+6.2f}  "
              f"${r.avg_loss:>+6.2f}  "
              f"${r.max_drawdown:>7.2f}")


def print_summary_by_timeframe(results: list[SweepResult]) -> None:
    """Vis hvilken timeframe gennemsnitligt performer bedst."""
    print()
    print("=" * 70)
    print("  Performance per Timeframe (gennemsnit på tværs af alle parametre)")
    print("=" * 70)
    print()

    for tf in TIMEFRAMES:
        tf_results = [r for r in results if r.timeframe == tf]
        if not tf_results:
            continue
        avg_pf  = sum(r.profit_factor for r in tf_results) / len(tf_results)
        avg_wr  = sum(r.win_rate      for r in tf_results) / len(tf_results)
        avg_pnl = sum(r.total_pnl     for r in tf_results) / len(tf_results)
        best_pf = max(r.profit_factor for r in tf_results)
        avg_tr  = sum(r.total_trades  for r in tf_results) / len(tf_results)

        print(f"  {tf:8s}  konfigs={len(tf_results)}  "
              f"avg trades={avg_tr:>6.0f}  "
              f"avg PF={avg_pf:.2f}  "
              f"best PF={best_pf:.2f}  "
              f"avg WR={avg_wr:.1f}%  "
              f"avg P&L=${avg_pnl:+.2f}")


def print_conclusion(results: list[SweepResult]) -> None:
    """Endelig vurdering."""
    print()
    print("=" * 70)
    print("  Konklusion")
    print("=" * 70)
    print()

    # Find bedste resultat
    valid = [r for r in results if r.total_trades >= 30]
    if not valid:
        print("  ⚠ For få trades i alle konfigurationer for at vurdere")
        return

    best = max(valid, key=lambda r: r.profit_factor)

    print(f"  Bedste konfiguration:")
    print(f"    {best.label}")
    print(f"    Trades:        {best.total_trades}")
    print(f"    Win rate:      {best.win_rate:.1f}%")
    print(f"    Profit factor: {best.profit_factor:.2f}")
    print(f"    Total P&L:     ${best.total_pnl:+,.2f}")
    print(f"    Max drawdown:  ${best.max_drawdown:,.2f}")
    print(f"    Avg duration:  {best.avg_duration} min")
    print()

    # Edge-vurdering
    if best.profit_factor >= 1.5 and best.win_rate >= 60:
        print(f"  ✓ STÆRK EDGE fundet — strategi er værd at forfølge live")
    elif best.profit_factor >= 1.2 and best.win_rate >= 55:
        print(f"  ✓ EDGE fundet — strategien har potentiale")
    elif best.profit_factor >= 1.0:
        print(f"  ◐ MARGINAL — break-even, kræver forfining")
    else:
        print(f"  ✗ INGEN EDGE i nogen konfiguration")
        print(f"     RSI(2) mean reversion virker ikke på dette marked / timeframe")

    # Hvor mange konfigurationer er profitable?
    profitable = [r for r in valid if r.profit_factor > 1.0]
    print(f"\n  Profitable konfigurationer: {len(profitable)} af {len(valid)}")


def export_results_csv(results: list[SweepResult], output_path: Path) -> int:
    """Eksporter alle resultater til CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "timeframe", "rsi_exit", "stop_pct",
            "total_trades", "wins", "losses", "win_rate",
            "total_pnl", "profit_factor", "avg_win", "avg_loss",
            "max_drawdown", "avg_duration",
        ])
        for r in results:
            w.writerow([
                r.timeframe, r.rsi_exit, r.stop_pct,
                r.total_trades, r.wins, r.losses, round(r.win_rate, 2),
                round(r.total_pnl, 2), round(r.profit_factor, 3),
                round(r.avg_win, 2), round(r.avg_loss, 2),
                round(r.max_drawdown, 2), r.avg_duration,
            ])
    return len(results)


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Mean Reversion parameter sweep")
    parser.add_argument("--top", type=int, default=10, help="Vis top N resultater")
    parser.add_argument("--export", type=str, help="Eksporter alle resultater til CSV")
    args = parser.parse_args()

    tickers = get_available_tickers()
    if not tickers:
        logger.error("Ingen 5-min data fundet. Kør download_intraday_ibkr.py først.")
        return 1

    logger.info(f"Tickers: {', '.join(tickers)}")
    logger.info(f"Parameter-grid: {len(RSI_EXIT_VALUES)} RSI × {len(STOP_PCT_VALUES)} Stop × {len(TIMEFRAMES)} TF "
                f"= {len(RSI_EXIT_VALUES) * len(STOP_PCT_VALUES) * len(TIMEFRAMES)} kombinationer")

    # Load bars for alle tickers, både 5-min og 15-min
    bars_by_ticker_by_tf: dict[str, dict[str, list[Bar]]] = {}
    for ticker in tickers:
        logger.info(f"Loading bars for {ticker}...")
        bars_5min = load_bars(ticker)
        bars_15min = aggregate_to_15min(bars_5min)
        bars_by_ticker_by_tf[ticker] = {
            "5min":  bars_5min,
            "15min": bars_15min,
        }
        logger.info(f"  {ticker}: 5-min={len(bars_5min):,}  15-min={len(bars_15min):,}")

    print()
    logger.info("Kører sweep...")
    print()

    results = run_sweep(bars_by_ticker_by_tf)

    # Rapport
    print_ranking(results, top_n=args.top)
    print_summary_by_timeframe(results)
    print_conclusion(results)

    # Eksport
    if args.export:
        path = Path(args.export)
        n = export_results_csv(results, path)
        print()
        logger.info(f"✓ Eksporteret {n} resultater → {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

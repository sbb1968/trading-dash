"""
mr_oos_test.py
──────────────
Out-of-sample (OOS) validering af RSI(2) Mean Reversion strategi.

Tager 12 måneders 15-min data og splitter:
  - Først 70% (≈ 8.4 mdr) → in-sample (IS), bruges til parameter-optimering
  - Sidste 30% (≈ 3.6 mdr) → out-of-sample (OOS), bruges KUN til validering

Step 1: Sweep alle 12 parameter-kombinationer på IS
Step 2: Find bedste konfiguration (efter profit factor)
Step 3: Kør samme konfiguration på OOS
Step 4: Sammenlign IS vs OOS → afgør om edge er ægte

Brug:
    python mr_oos_test.py

Placering: C:\\Projects\\trading-dash\\backend\\mr_oos_test.py
"""

from __future__ import annotations

import logging
import sys
from itertools import product

from mean_reversion_backtest import (
    Bar, Trade, Backtester,
    load_bars, get_available_tickers, calculate_stats,
)
from mr_param_sweep import aggregate_to_15min


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("oos_test")


# ── Parameter-grid (samme som sweep, kun 15-min) ──────────────
RSI_EXIT_VALUES = [50.0, 60.0, 70.0, 80.0]
STOP_PCT_VALUES = [0.005, 0.0075, 0.010]

# Split: 70% in-sample, 30% out-of-sample
IS_FRACTION = 0.70


# ─────────────────────────────────────────────────────────────────
# Data-split
# ─────────────────────────────────────────────────────────────────

def split_bars(bars: list[Bar], is_fraction: float = IS_FRACTION) -> tuple[list[Bar], list[Bar]]:
    """Split bars i in-sample og out-of-sample baseret på tid."""
    if not bars:
        return [], []

    # Bars er allerede sorteret kronologisk
    n_is = int(len(bars) * is_fraction)
    return bars[:n_is], bars[n_is:]


# ─────────────────────────────────────────────────────────────────
# Sweep KUN på in-sample
# ─────────────────────────────────────────────────────────────────

def sweep_in_sample(
    is_bars_by_ticker: dict[str, list[Bar]],
) -> list[dict]:
    """
    Sweep alle 12 kombinationer på in-sample data.
    Returnér liste af resultater sorteret efter profit factor.
    """
    results = []
    combinations = list(product(RSI_EXIT_VALUES, STOP_PCT_VALUES))

    logger.info(f"Sweep på in-sample: {len(combinations)} kombinationer")

    for i, (rsi_exit, stop_pct) in enumerate(combinations, 1):
        all_trades: list[Trade] = []

        backtester = Backtester(
            rsi_entry=10.0,
            rsi_exit=rsi_exit,
            stop_pct=stop_pct,
            timeframe="15min",
        )

        for ticker, bars in is_bars_by_ticker.items():
            trades = backtester.run(bars)
            all_trades.extend(trades)

        stats = calculate_stats(all_trades)
        result = {
            "rsi_exit":      rsi_exit,
            "stop_pct":      stop_pct,
            "trades":        stats["total_trades"],
            "win_rate":      stats["win_rate"],
            "profit_factor": stats["profit_factor"],
            "total_pnl":     stats["total_pnl"],
            "max_drawdown":  stats["max_drawdown"],
            "label":         f"RSI>{rsi_exit:.0f} Stop{stop_pct*100:.2f}%",
        }
        results.append(result)

        logger.info(f"  [{i:>2}/{len(combinations)}] {result['label']:25s}  "
                    f"trades={result['trades']:>4}  "
                    f"PF={result['profit_factor']:>5.2f}  "
                    f"WR={result['win_rate']:>5.1f}%  "
                    f"P&L=${result['total_pnl']:>+9.2f}")

    # Sortér efter profit factor (descending)
    results.sort(key=lambda r: r["profit_factor"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────
# Test på out-of-sample
# ─────────────────────────────────────────────────────────────────

def test_on_oos(
    config: dict,
    oos_bars_by_ticker: dict[str, list[Bar]],
) -> dict:
    """Kør én konfiguration på OOS data."""
    all_trades: list[Trade] = []

    backtester = Backtester(
        rsi_entry=10.0,
        rsi_exit=config["rsi_exit"],
        stop_pct=config["stop_pct"],
        timeframe="15min",
    )

    for ticker, bars in oos_bars_by_ticker.items():
        trades = backtester.run(bars)
        all_trades.extend(trades)

    stats = calculate_stats(all_trades)
    return {
        "rsi_exit":      config["rsi_exit"],
        "stop_pct":      config["stop_pct"],
        "label":         config["label"],
        "trades":        stats["total_trades"],
        "win_rate":      stats["win_rate"],
        "profit_factor": stats["profit_factor"],
        "total_pnl":     stats["total_pnl"],
        "max_drawdown":  stats["max_drawdown"],
    }


# ─────────────────────────────────────────────────────────────────
# Sammenligning og konklusion
# ─────────────────────────────────────────────────────────────────

def print_comparison(is_result: dict, oos_result: dict) -> None:
    """Print side-by-side sammenligning af IS vs OOS."""
    print()
    print("=" * 80)
    print("  Out-of-Sample Validering — Sammenligning")
    print("=" * 80)
    print()
    print(f"  Konfiguration: {is_result['label']}")
    print()
    print(f"  {'Metric':<20s}  {'In-Sample':>15s}  {'Out-of-Sample':>15s}  {'Diff':>10s}")
    print(f"  {'-' * 20}  {'-' * 15}  {'-' * 15}  {'-' * 10}")

    # Trades
    print(f"  {'Total trades':<20s}  {is_result['trades']:>15d}  {oos_result['trades']:>15d}")

    # Win rate
    diff_wr = oos_result['win_rate'] - is_result['win_rate']
    print(f"  {'Win rate':<20s}  {is_result['win_rate']:>14.1f}%  {oos_result['win_rate']:>14.1f}%  "
          f"{diff_wr:>+9.1f}%")

    # Profit factor (det vigtigste)
    diff_pf = oos_result['profit_factor'] - is_result['profit_factor']
    print(f"  {'Profit factor':<20s}  {is_result['profit_factor']:>15.2f}  {oos_result['profit_factor']:>15.2f}  "
          f"{diff_pf:>+10.2f}")

    # P&L
    diff_pnl = oos_result['total_pnl'] - is_result['total_pnl']
    print(f"  {'Total P&L':<20s}  ${is_result['total_pnl']:>+13.2f}  ${oos_result['total_pnl']:>+13.2f}  "
          f"${diff_pnl:>+8.2f}")

    # Max drawdown
    print(f"  {'Max drawdown':<20s}  ${is_result['max_drawdown']:>13.2f}  ${oos_result['max_drawdown']:>13.2f}")

    print()


def print_conclusion(is_result: dict, oos_result: dict) -> None:
    """Endelig vurdering baseret på OOS resultat."""
    print("=" * 80)
    print("  Konklusion")
    print("=" * 80)
    print()

    oos_pf = oos_result["profit_factor"]
    oos_trades = oos_result["trades"]
    pf_drop = is_result["profit_factor"] - oos_pf
    pf_drop_pct = (pf_drop / is_result["profit_factor"] * 100) if is_result["profit_factor"] > 0 else 0

    print(f"  In-sample PF:        {is_result['profit_factor']:.2f}")
    print(f"  Out-of-sample PF:    {oos_pf:.2f}")
    print(f"  PF-fald:             {pf_drop:+.2f}  ({pf_drop_pct:+.0f}%)")
    print()

    if oos_trades < 30:
        print(f"  ⚠  KUN {oos_trades} TRADES PÅ OOS — statistisk usikkert resultat")
        print(f"     Vi har for lidt data til at konkludere noget med sikkerhed")
        print()

    if oos_pf >= 1.20:
        print(f"  ✓ ÆGTE EDGE — strategien holder out-of-sample")
        print(f"    PF >= 1.20 på data modellen aldrig har set")
        print(f"    Næste skridt: overvej live deployment med small position size")
    elif oos_pf >= 1.05:
        print(f"  ◐ SVAG EDGE — strategien er marginalt profitabel på OOS")
        print(f"    PF mellem 1.05 og 1.20 antyder mulig edge men ikke robust")
        print(f"    Risikabelt at deploye uden videre forfining")
    elif oos_pf >= 0.95:
        print(f"  ✗ BREAK-EVEN — strategien har ingen reel edge")
        print(f"    PF ~1.0 på OOS betyder vi vinder/taber tilfældigt")
        print(f"    Live deployment ville sandsynligvis tabe penge på friktion")
    else:
        print(f"  ✗ OVERFIT — strategien fejler out-of-sample")
        print(f"    IS-resultatet var en illusion fra parameter-tuning")
        print(f"    Live deployment ville TABE penge")

    # Sanity-tjek: hvis PF faldt mere end 30%, er det overfitting-tegn
    if pf_drop_pct > 30:
        print()
        print(f"  ⚠  Bemærk: PF faldt {pf_drop_pct:.0f}% fra IS til OOS")
        print(f"     Det er et stærkt tegn på overfitting eller markedsregime-skift")

    print()


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    tickers = get_available_tickers()
    if not tickers:
        logger.error("Ingen 5-min data fundet")
        return 1

    logger.info(f"Tickers: {', '.join(tickers)}")

    # Load og aggregér til 15-min, så split per ticker
    is_bars_by_ticker: dict[str, list[Bar]] = {}
    oos_bars_by_ticker: dict[str, list[Bar]] = {}

    for ticker in tickers:
        bars_5min = load_bars(ticker)
        bars_15min = aggregate_to_15min(bars_5min)
        is_bars, oos_bars = split_bars(bars_15min)

        is_bars_by_ticker[ticker] = is_bars
        oos_bars_by_ticker[ticker] = oos_bars

        # Vis periode-info for én ticker
        if is_bars and oos_bars:
            logger.info(f"  {ticker}: {len(bars_15min):,} bars total → "
                        f"IS={len(is_bars):,} ({is_bars[0].timestamp.date()} → {is_bars[-1].timestamp.date()}), "
                        f"OOS={len(oos_bars):,} ({oos_bars[0].timestamp.date()} → {oos_bars[-1].timestamp.date()})")

    print()

    # Step 1+2: Sweep på in-sample, find bedste
    logger.info("Step 1: Parameter-sweep på in-sample data...")
    print()
    is_results = sweep_in_sample(is_bars_by_ticker)

    print()
    print("=" * 80)
    print("  Top 5 In-Sample Konfigurationer")
    print("=" * 80)
    print()
    print(f"  {'#':>2}  {'Konfiguration':<25s}  {'Trades':>7s}  {'WR%':>6s}  {'PF':>5s}  {'P&L':>10s}")
    print(f"  {'-' * 2}  {'-' * 25}  {'-' * 7}  {'-' * 6}  {'-' * 5}  {'-' * 10}")
    for i, r in enumerate(is_results[:5], 1):
        print(f"  {i:>2}  {r['label']:<25s}  {r['trades']:>7d}  "
              f"{r['win_rate']:>5.1f}%  {r['profit_factor']:>5.2f}  ${r['total_pnl']:>+9.2f}")

    # Step 3: Test bedste på OOS
    best_is = is_results[0]
    print()
    logger.info(f"Step 2: Test bedste konfiguration ({best_is['label']}) på OOS data...")

    oos_result = test_on_oos(best_is, oos_bars_by_ticker)

    # Step 4+5: Sammenlign og konkluder
    print_comparison(best_is, oos_result)
    print_conclusion(best_is, oos_result)

    return 0


if __name__ == "__main__":
    sys.exit(main())

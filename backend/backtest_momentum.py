"""
backtest_momentum.py  v5
────────────────────────
Strategi-agnostisk backtest engine.

Kan teste enhver Strategy der overholder strategies.base.Strategy-protokollen.
Default: MomentumORBStrategy. Tilføj nye strategier i strategies/__init__.py
så finder denne fil dem automatisk.

For hver strategi kører backtesten alle dens varianter (parameter sweep) og
producerer:
  - Terminal-tabel med alle key metrics
  - Én CSV pr. variant med alle trades
  - Samlet sammenligning-CSV med alle varianter

Kør:
    python backtest_momentum.py                   # default: momentum_orb
    python backtest_momentum.py momentum_orb      # explicit
    python backtest_momentum.py mean_reversion    # når den findes
"""

from __future__ import annotations

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, time as dtime
import warnings
warnings.filterwarnings("ignore")

from strategies import get_strategy, list_strategies
from strategies.base import Bar
from strategies.momentum_orb.exit import (
    REASON_STOP, REASON_TARGET, REASON_TRAIL, REASON_FORCE_CLOSE,
)

DATA_DIR = Path(__file__).parent / "data"

# Capital pr. handel — matcher live algo's CAPITAL_PER_TRADE
CAPITAL_PER_TRADE = 2_500


# ─────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────

def to_et(df: pd.DataFrame) -> pd.DataFrame:
    """Konvertér index til ET-tidsstempler."""
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.tz_convert("America/New_York")


def load_all_data() -> dict[str, pd.DataFrame]:
    """Indlæs alle 5-min CSV-filer fra data/-mappen."""
    data = {}
    for f in sorted(DATA_DIR.glob("*_5m_*.csv")):
        ticker = f.stem.split("_")[0]
        if ticker in ("SPY", "QQQ", "IWM"):
            continue
        try:
            df = pd.read_csv(f, index_col=0)
            df.columns = [c.lower() for c in df.columns]
            needed = {"open", "high", "low", "close", "volume"}
            if not needed.issubset(df.columns):
                continue
            for col in needed:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=list(needed))
            if len(df) < 20:
                continue
            df = to_et(df)
            data[ticker] = df
        except Exception as e:
            print(f"  Fejl: {f.name}: {e}")
    return data


def df_to_bars(df: pd.DataFrame) -> list[Bar]:
    """Konvertér en pandas DataFrame til en liste af Bar-objekter."""
    return [
        Bar(timestamp=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]))
        for ts, row in df.iterrows()
    ]


# ─────────────────────────────────────────────────────────────────────
# Backtest kerne — strategi-agnostisk
# ─────────────────────────────────────────────────────────────────────

def backtest_ticker_variant(
    strategy,                # Strategy
    ticker: str,
    df: pd.DataFrame,
    variant_key: str,
    capital: float = CAPITAL_PER_TRADE,
) -> list[dict]:
    """
    Backtest én ticker mod én variant.

    Strategi-agnostisk — kalder kun protokol-metoder.
    """
    trades = []

    # Hent variant-config for at sende med til build_day_context
    variant_config = strategy.variants[variant_key]

    for date, day_df in df.groupby(df.index.date):
        day_df = day_df.sort_index().between_time("09:30", "16:00")
        if len(day_df) < 8:
            continue

        bars = df_to_bars(day_df)

        # NY: send variant-config med så ORB-vindue mm. kan justeres pr. variant
        context = strategy.build_day_context(ticker, bars, config=variant_config)
        if context is None:
            continue

        # Hent handelsvindue-start fra context (kan være variant-aware)
        handelsvindue_start = context.get("trade_start", dtime(9, 45))

        strategy.entry.reset_for_day(date, context)

        position = None

        for bar in bars:
            # Åben position → tjek exit
            if position is not None:
                # Send bade bar.high og bar.low sa shorts kan opdatere lowest_low
                strategy.exit.update(position, bar.high, variant_key, low_seen=bar.low)
                exit_decision = strategy.exit.check_exit_bar(position, bar, variant_key)
                if exit_decision is not None:
                    # PnL spejlvendes for short (gevinst hvis exit < entry)
                    if position.side == "long":
                        pnl_pct = (exit_decision.exit_price - position.entry_price) / position.entry_price
                        max_gain_pct = (position.state.highest_high - position.entry_price) / position.entry_price * 100
                        extremum     = position.state.highest_high
                    else:  # short
                        pnl_pct = (position.entry_price - exit_decision.exit_price) / position.entry_price
                        max_gain_pct = (position.entry_price - position.state.lowest_low) / position.entry_price * 100
                        extremum     = position.state.lowest_low
                    trades.append({
                        "ticker":       ticker,
                        "date":         str(date),
                        "variant":      variant_key,
                        "side":         position.side,
                        "entry_time":   position.entry_time.strftime("%H:%M"),
                        "exit_time":    bar.timestamp.strftime("%H:%M"),
                        "entry_price":  round(position.entry_price, 4),
                        "exit_price":   round(exit_decision.exit_price, 4),
                        "exit_reason":  exit_decision.reason,
                        "orb_high":     round(position.metadata.get("orb_high", 0), 4),
                        "orb_low":      round(position.metadata.get("orb_low",  0), 4),
                        "highest_high": round(extremum, 4),   # long: highest_high, short: lowest_low
                        "max_gain_pct": round(max_gain_pct, 2),
                        "pnl_pct":      round(pnl_pct * 100, 2),
                        "pnl_usd":      round(capital * pnl_pct, 2),
                        "duration_min": max(1, int((bar.timestamp - position.entry_time).total_seconds() / 60)),
                    })
                    position = None
                continue

            # Ingen åben position → tjek entry
            if bar.time_et < handelsvindue_start:
                continue

            signal = strategy.entry.check_entry(ticker, bar, context)
            if signal is not None:
                shares = max(1, int(capital / signal.entry_price))
                position = strategy.exit.open_position(signal, shares, variant_key)

    return trades


# ─────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────

def calc_stats(trades: list[dict], variant_key: str, variant_name: str) -> dict:
    """Beregn alle metrics for én variants trades."""
    if not trades:
        return {
            "variant": variant_key, "name": variant_name,
            "trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "sharpe_like": 0.0,
            "n_stop": 0, "n_target": 0, "n_trail": 0, "n_force": 0,
        }

    df   = pd.DataFrame(trades)
    wins = df[df["pnl_usd"] > 0]
    loss = df[df["pnl_usd"] < 0]
    gp   = float(wins["pnl_usd"].sum()) if len(wins) > 0 else 0.0
    gl   = abs(float(loss["pnl_usd"].sum())) if len(loss) > 0 else 0.0
    eq   = df["pnl_usd"].cumsum()
    mdd  = float((eq - eq.cummax()).min()) if len(eq) > 0 else 0.0

    total_pnl = float(df["pnl_usd"].sum())
    sharpe    = (total_pnl / abs(mdd)) if mdd < 0 else 0.0

    by_reason = df["exit_reason"].value_counts().to_dict()

    return {
        "variant":       variant_key,
        "name":          variant_name,
        "trades":        len(df),
        "win_rate":      round(len(wins) / len(df) * 100, 1),
        "total_pnl":     round(total_pnl, 2),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 9.99,
        "max_drawdown":  round(mdd, 2),
        "avg_win":       round(float(wins["pnl_usd"].mean()), 2) if len(wins) > 0 else 0.0,
        "avg_loss":      round(float(loss["pnl_usd"].mean()), 2) if len(loss) > 0 else 0.0,
        "sharpe_like":   round(sharpe, 2),
        "n_stop":        int(by_reason.get(REASON_STOP, 0)),
        "n_target":      int(by_reason.get(REASON_TARGET, 0)),
        "n_trail":       int(by_reason.get(REASON_TRAIL, 0)),
        "n_force":       int(by_reason.get(REASON_FORCE_CLOSE, 0)),
    }


# ─────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────

def print_comparison_table(stats_list: list[dict], strategy_name: str):
    W = 116
    print("═" * W)
    print(f"  PARAMETER SWEEP — {strategy_name} (capital ${CAPITAL_PER_TRADE}/trade)")
    print("═" * W)
    header = (f"  {'Variant':10s} {'Trades':>6} {'Win%':>6} {'TotalP&L':>11} "
              f"{'PF':>5} {'MaxDD':>10} {'AvgWin':>9} {'AvgLoss':>9} "
              f"{'Sharpe':>7} {'St/Tg/Tr/Fc':>14}")
    print(header)
    print(f"  {'─'*10} {'─'*6} {'─'*6} {'─'*11} {'─'*5} {'─'*10} "
          f"{'─'*9} {'─'*9} {'─'*7} {'─'*14}")

    for s in stats_list:
        if s["trades"] == 0:
            print(f"  {s['variant']:10s}  ingen handler")
            continue
        pf_str = f"{s['profit_factor']:.2f}" if s['profit_factor'] < 9 else "  ∞"
        st_tg_tr_fc = f"{s['n_stop']}/{s['n_target']}/{s['n_trail']}/{s['n_force']}"
        print(f"  {s['variant']:10s} {s['trades']:>6} {s['win_rate']:>5.1f}% "
              f"${s['total_pnl']:>10,.2f} {pf_str:>5} "
              f"${s['max_drawdown']:>9,.2f} ${s['avg_win']:>8,.2f} ${s['avg_loss']:>8,.2f} "
              f"{s['sharpe_like']:>7.2f} {st_tg_tr_fc:>14}")

    print("═" * W)
    print("\n  Variant-detaljer:")
    for s in stats_list:
        print(f"    {s['variant']:10s} = {s['name']}")
    print()


def print_equity(trades: list[dict], label: str):
    if not trades:
        return
    eq   = pd.DataFrame(trades)["pnl_usd"].cumsum().values
    h, w = 6, 54
    mn, mx = eq.min(), eq.max()
    rng  = mx - mn or 1
    cols = np.array_split(eq, min(w, len(eq)))
    grid = [[" "] * len(cols) for _ in range(h)]
    for xi, col in enumerate(cols):
        val = float(np.mean(col))
        yi  = min(h-1, max(0, int((val - mn) / rng * (h-1))))
        grid[h-1-yi][xi] = "█"
        for row in range(yi):
            grid[h-1-row][xi] = "▓"
    print(f"\n  Equity-kurve ({label}):")
    print(f"  ${mx:>8,.0f} ┐")
    for row in grid:
        print("           │" + "".join(row))
    print(f"  ${mn:>8,.0f} └" + "─" * len(cols))


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main():
    strategy_key = sys.argv[1] if len(sys.argv) > 1 else "momentum_orb"

    available = list_strategies()
    if strategy_key not in available:
        print(f"Ukendt strategi: {strategy_key!r}")
        print(f"Tilgængelige: {', '.join(available)}")
        return

    strategy = get_strategy(strategy_key)

    # Nogle strategier er ikke OHLC-engine-baserede (fx Europa-reversion, der er
    # en ren z-score-regel på 15-min futures). De har ingen variant-sweep/entry-
    # exit-engines og backtestes med deres eget værktøj — redirigér i stedet for
    # at crashe på manglende .variants.
    if not hasattr(strategy, "variants"):
        print(f"\n⚠ {strategy.name} backtestes ikke med backtest_momentum.py "
              f"(den er ikke OHLC-engine-baseret).")
        print("   Brug i stedet:  python meanrev_backtest.py")
        return

    print(f"\n🔬 Backtester strategi: {strategy.name}")
    print(f"   {strategy.description}\n")

    files = list(DATA_DIR.glob("*_5m_*.csv"))
    if not files:
        print("Ingen 5-minut data fundet. Kør: python download_data.py")
        return

    print(f"Indlæser {len(files)} filer fra {DATA_DIR}...")
    data = load_all_data()
    print(f"Klar med {len(data)} tickers: {', '.join(sorted(data.keys()))}\n")

    all_trades_by_variant: dict[str, list[dict]] = {}
    all_stats: list[dict] = []

    for variant_key, variant_cfg in strategy.variants.items():
        print(f"  Kører variant {variant_key}: {variant_cfg.name}...")

        # Ny strategi-instans pr. variant så entry-state ikke deles
        strat_instance = get_strategy(strategy_key)

        trades = []
        for ticker, df in data.items():
            trades.extend(
                backtest_ticker_variant(strat_instance, ticker, df, variant_key)
            )
        all_trades_by_variant[variant_key] = trades
        stats = calc_stats(trades, variant_key, variant_cfg.name)
        all_stats.append(stats)
        print(f"    → {stats['trades']} handler, P&L: ${stats['total_pnl']:+,.2f}")

    print()
    print_comparison_table(all_stats, strategy.name)

    # Output-filer
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    for variant_key, trades in all_trades_by_variant.items():
        if not trades:
            continue
        out = DATA_DIR / f"backtest_{strategy_key}_{variant_key}_{timestamp}.csv"
        pd.DataFrame(trades).to_csv(out, index=False)
        print(f"  📄 {variant_key}: gemt {len(trades)} trades → {out.name}")

    summary_df  = pd.DataFrame(all_stats)
    summary_out = DATA_DIR / f"backtest_{strategy_key}_comparison_{timestamp}.csv"
    summary_df.to_csv(summary_out, index=False)
    print(f"  📊 sammenligning gemt → {summary_out.name}")

    valid_stats = [s for s in all_stats if s["trades"] > 0]
    if valid_stats:
        best = max(valid_stats, key=lambda s: s["sharpe_like"])
        print(f"\n  ✓ Bedste Sharpe-lignende ratio: variant {best['variant']} ({best['name']})")
        print(f"    Trades: {best['trades']}  Win: {best['win_rate']}%  "
              f"P&L: ${best['total_pnl']:,.2f}  PF: {best['profit_factor']:.2f}  "
              f"MaxDD: ${best['max_drawdown']:,.2f}")
        print(f"    Exit-mix: {best['n_stop']} stop / {best['n_target']} target / "
              f"{best['n_trail']} trail / {best['n_force']} force-close")
        print_equity(all_trades_by_variant[best["variant"]], best["variant"])
        print("\n  (Du beslutter selv hvilken variant der vinder — alle metrics er ovenfor)\n")


if __name__ == "__main__":
    main()

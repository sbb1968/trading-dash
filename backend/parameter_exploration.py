"""
parameter_exploration.py
────────────────────────
3-phase parameter exploration der finder bedste exit-variant for MomentumORB.

Filosofi:
  Et fuldt grid på alle dimensioner ville være 540+ kombinationer. I stedet
  bruger vi staged exploration: lås gode værdier fra Phase 1, leg så videre
  i Phase 2 osv. Det giver os ~32 kombinationer i alt — meget mere håndterligt
  og mindre overfitting-risiko.

Phase 1 — target × vol_mult sweep (20 kombinationer):
  target: [0.010, 0.015, 0.020, 0.025, 0.030]
  vol_mult: [1.5, 2.0, 3.0, 5.0]
  (stop fast på ORB Mid, intet BE/trail — vi isolerer entry-styrke + target-pasning)
  → Plukker top-3 efter Sharpe-lignende ratio

Phase 2 — stop_mode × BE-trigger (9 kombinationer × 3 baselines = 27 max):
  Med top-3 fra Phase 1 låst:
    stop_mode: [orb_mid, orb_low, fixed_pct]
    breakeven_trigger_pct: [disabled, 0.01, 0.015, 0.02]
  → Plukker top-3 igen

Phase 3 — trail (3 kombinationer × 3 baselines = 9 max):
  Med top-3 fra Phase 2 låst:
    trail: [disabled, 1.5%, 2.0%]
  → Plukker overall top-3

Hele pipelinen køres mod hvert valgt univers-filter (all/mild/medium/aggressive).

Kør:
    python parameter_exploration.py
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, replace
from itertools import product
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

from strategies.momentum_orb.config import VariantConfig, VARIANTS
from backtest_momentum import backtest_ticker_variant, CAPITAL_PER_TRADE
from universe_filter import (
    load_all_data, split_data_in_time, evaluate_universe,
    DEFAULT_TRAIN_RATIO,
)
from strategies.momentum_orb.exit import (
    REASON_STOP, REASON_TARGET, REASON_TRAIL, REASON_FORCE_CLOSE,
)

DATA_DIR = Path(__file__).parent / "data"

# Farver
GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RESET = "\033[0m"

try:
    import colorama
    colorama.just_fix_windows_console()
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────────────
# Variant-fabrik: bygger en VariantConfig fra parameter-dict
# ─────────────────────────────────────────────────────────────────────

def make_variant(name: str, **kwargs) -> VariantConfig:
    """
    Skab en VariantConfig fra en dict af parameter-overrides.

    Bruger sensible defaults for alt der ikke specificeres.
    """
    defaults = {
        "name":                  name,
        "stop_mode":             "orb_mid",
        "fixed_stop_pct":        0.02,
        "target_pct":            0.04,
        "breakeven_enabled":     True,
        "breakeven_trigger_pct": 0.03,
        "trail_enabled":         True,
        "trail_activate_pct":    0.04,
        "trail_distance_pct":    0.015,
        "vol_mult":              1.5,
        "rsi_max":               80.0,
        "orb_end_minutes":       14,
        "retest_timeout_sec":    300,
    }
    defaults.update(kwargs)
    return VariantConfig(**defaults)


# ─────────────────────────────────────────────────────────────────────
# Phase 1 — target × vol_mult sweep
# ─────────────────────────────────────────────────────────────────────

def phase1_grid() -> list[tuple[str, VariantConfig]]:
    """Generér 5×4=20 varianter for Phase 1."""
    targets   = [0.010, 0.015, 0.020, 0.025, 0.030]
    vol_mults = [1.5, 2.0, 3.0, 5.0]

    grid = []
    for target, vm in product(targets, vol_mults):
        key  = f"P1_tgt{int(target*1000):03d}_vol{vm:.1f}"
        name = f"Target +{target*100:.1f}%, vol {vm:.1f}x"
        # Phase 1: ingen BE/trail — vi isolerer entry × target effekt
        config = make_variant(
            name=name,
            target_pct=target,
            vol_mult=vm,
            stop_mode="orb_mid",
            breakeven_enabled=False,
            trail_enabled=False,
        )
        grid.append((key, config))
    return grid


# ─────────────────────────────────────────────────────────────────────
# Phase 2 — stop_mode × BE-trigger (oven på top-3 fra Phase 1)
# ─────────────────────────────────────────────────────────────────────

def phase2_grid(base: VariantConfig) -> list[tuple[str, VariantConfig]]:
    """Generér Phase 2 varianter baseret på én Phase 1-vinder."""
    stop_modes = [
        ("orb_mid",   {}),
        ("orb_low",   {}),
        ("fixed_pct", {"fixed_stop_pct": 0.01}),
        ("fixed_pct", {"fixed_stop_pct": 0.02}),
    ]
    be_options = [
        (False, 0.0),
        (True,  0.010),
        (True,  0.015),
        (True,  0.020),
    ]

    grid = []
    for (stop_mode, stop_kwargs), (be_enabled, be_trigger) in product(stop_modes, be_options):
        # Læsbart navn
        stop_label = stop_mode
        if stop_mode == "fixed_pct":
            stop_label = f"fix{int(stop_kwargs['fixed_stop_pct']*100)}%"
        be_label = "BE-off" if not be_enabled else f"BE@{be_trigger*100:.1f}%"

        key = f"P2_{stop_label}_{be_label}"
        config = replace(
            base,
            name=f"{base.name} | {stop_label} {be_label}",
            stop_mode=stop_mode,
            breakeven_enabled=be_enabled,
            breakeven_trigger_pct=be_trigger if be_enabled else 0.03,
            **stop_kwargs,
        )
        grid.append((key, config))
    return grid


# ─────────────────────────────────────────────────────────────────────
# Phase 3 — trail (oven på top-3 fra Phase 2)
# ─────────────────────────────────────────────────────────────────────

def phase3_grid(base: VariantConfig) -> list[tuple[str, VariantConfig]]:
    """Generér Phase 3 varianter baseret på én Phase 2-vinder."""
    trail_options = [
        (False, 0.04, 0.015),
        (True,  0.04, 0.015),
        (True,  0.04, 0.020),
    ]

    grid = []
    for trail_enabled, activate_pct, distance_pct in trail_options:
        trail_label = "trail-off" if not trail_enabled else f"trail{distance_pct*100:.1f}%"
        key = f"P3_{trail_label}"
        config = replace(
            base,
            name=f"{base.name} | {trail_label}",
            trail_enabled=trail_enabled,
            trail_activate_pct=activate_pct,
            trail_distance_pct=distance_pct,
        )
        grid.append((key, config))
    return grid


# ─────────────────────────────────────────────────────────────────────
# Helper: kør backtest med en specifik VariantConfig
# ─────────────────────────────────────────────────────────────────────

class _AdHocStrategy:
    """
    Tynd Strategy-implementering der bruger én specifik VariantConfig.

    Vi laver den ad hoc fordi parameter exploration tester varianter der ikke
    findes i den faste VARIANTS-dict. Den genbruger MomentumORBEntry og -Exit
    fra strategies.momentum_orb.
    """

    def __init__(self, config: VariantConfig):
        from strategies.momentum_orb.entry import MomentumORBEntry
        from strategies.momentum_orb.exit  import MomentumORBExit
        from strategies.momentum_orb.strategy import MomentumORBStrategy

        # Brug en frisk Strategy-instans men override variants med vores ene
        self._strat = MomentumORBStrategy()
        # Override entry og exit så de bruger config-driven variant
        self._config = config

    @property
    def variants(self):
        # Returnér én "ad_hoc" variant med vores config
        return {"ad_hoc": self._config}

    @property
    def entry(self):
        return self._strat.entry

    @property
    def exit(self):
        # MomentumORBExit's _config-lookup checker den passed variants dict
        # men hvis vi sætter den én gang her ved init
        from strategies.momentum_orb.exit import MomentumORBExit
        return MomentumORBExit({"ad_hoc": self._config})

    def build_day_context(self, ticker, bars, config=None):
        # Vi tvinger vores config ind, uanset hvad caller sender
        return self._strat.build_day_context(ticker, bars, config=self._config)


def run_one_config(
    config: VariantConfig,
    train_data: dict[str, pd.DataFrame],
    tickers: list[str],
    bars_cache: Optional[dict] = None,
) -> tuple[list[dict], dict]:
    """
    Kør backtest for én VariantConfig mod et univers af tickers.

    bars_cache: hvis givet, bruges pre-konverterede bars (meget hurtigere).
                Skal være dict ticker → list[(date, list[Bar])].

    Returnerer (trades, stats).
    """
    strategy = _AdHocStrategy(config)
    trades = []
    for ticker in tickers:
        if ticker not in train_data:
            continue
        df = train_data[ticker]
        if df.empty:
            continue
        # Hvis cache er givet, brug den. Ellers fald tilbage til normal backtest
        if bars_cache is not None and ticker in bars_cache:
            ticker_trades = _backtest_with_cache(
                strategy, ticker, bars_cache[ticker], "ad_hoc", config
            )
        else:
            ticker_trades = backtest_ticker_variant(
                strategy, ticker, df, "ad_hoc",
            )
        trades.extend(ticker_trades)
    stats = calc_stats(trades, config.name)
    return trades, stats


def build_bars_cache(
    data: dict[str, pd.DataFrame],
) -> dict[str, list[tuple]]:
    """
    Pre-konvertér alle DataFrames til Bar-objekter, grupperet pr. dag.

    Returnerer dict: ticker → [(date, list[Bar]), ...]

    Dette spares 5-10x kørselstid for exploration fordi konvertering ikke
    gentages for hver parameter-kombination.
    """
    from backtest_momentum import df_to_bars
    cache = {}
    for ticker, df in data.items():
        if df.empty:
            cache[ticker] = []
            continue
        days = []
        for date, day_df in df.groupby(df.index.date):
            day_df = day_df.sort_index().between_time("09:30", "16:00")
            if len(day_df) < 8:
                continue
            bars = df_to_bars(day_df)
            days.append((date, bars))
        cache[ticker] = days
    return cache


def _backtest_with_cache(
    strategy,
    ticker: str,
    cached_days: list[tuple],
    variant_key: str,
    config: VariantConfig,
) -> list[dict]:
    """
    Backtest mod pre-konverterede bars (samme logik som backtest_ticker_variant,
    bare uden df-konvertering).
    """
    from datetime import time as dtime
    trades = []

    for date, bars in cached_days:
        # build_day_context med vores config
        context = strategy.build_day_context(ticker, bars, config=config)
        if context is None:
            continue

        handelsvindue_start = context.get("trade_start", dtime(9, 45))
        strategy.entry.reset_for_day(date, context)

        position = None
        for bar in bars:
            if position is not None:
                strategy.exit.update(position, bar.high, variant_key)
                exit_decision = strategy.exit.check_exit_bar(position, bar, variant_key)
                if exit_decision is not None:
                    pnl_pct = (exit_decision.exit_price - position.entry_price) / position.entry_price
                    trades.append({
                        "ticker":       ticker,
                        "date":         str(date),
                        "variant":      variant_key,
                        "entry_time":   position.entry_time.strftime("%H:%M"),
                        "exit_time":    bar.timestamp.strftime("%H:%M"),
                        "entry_price":  round(position.entry_price, 4),
                        "exit_price":   round(exit_decision.exit_price, 4),
                        "exit_reason":  exit_decision.reason,
                        "orb_high":     round(position.metadata.get("orb_high", 0), 4),
                        "orb_low":      round(position.metadata.get("orb_low", 0), 4),
                        "highest_high": round(position.state.highest_high, 4),
                        "max_gain_pct": round((position.state.highest_high - position.entry_price) / position.entry_price * 100, 2),
                        "pnl_pct":      round(pnl_pct * 100, 2),
                        "pnl_usd":      round(CAPITAL_PER_TRADE * pnl_pct, 2),
                        "duration_min": max(1, int((bar.timestamp - position.entry_time).total_seconds() / 60)),
                    })
                    position = None
                continue

            if bar.time_et < handelsvindue_start:
                continue

            signal = strategy.entry.check_entry(ticker, bar, context)
            if signal is not None:
                shares = max(1, int(CAPITAL_PER_TRADE / signal.entry_price))
                position = strategy.exit.open_position(signal, shares, variant_key)

    return trades


def calc_stats(trades: list[dict], name: str) -> dict:
    """Beregn standard-metrics for en samling trades."""
    if not trades:
        return {
            "name": name, "trades": 0, "total_pnl": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0,
            "sharpe_like": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "n_stop": 0, "n_target": 0, "n_trail": 0, "n_force": 0,
        }

    df = pd.DataFrame(trades)
    wins = df[df["pnl_usd"] > 0]
    loss = df[df["pnl_usd"] < 0]
    gp = float(wins["pnl_usd"].sum()) if len(wins) > 0 else 0.0
    gl = abs(float(loss["pnl_usd"].sum())) if len(loss) > 0 else 0.0
    eq = df["pnl_usd"].cumsum()
    mdd = float((eq - eq.cummax()).min()) if len(eq) > 0 else 0.0
    total = float(df["pnl_usd"].sum())
    sharpe = (total / abs(mdd)) if mdd < 0 else (10.0 if total > 0 else 0.0)
    by_reason = df["exit_reason"].value_counts().to_dict()

    return {
        "name":          name,
        "trades":        len(df),
        "win_rate":      round(len(wins) / len(df) * 100, 1),
        "total_pnl":     round(total, 2),
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
# Phase-runners
# ─────────────────────────────────────────────────────────────────────

def run_phase(
    phase_name: str,
    grid: list[tuple[str, VariantConfig]],
    train_data: dict[str, pd.DataFrame],
    tickers: list[str],
    top_n: int = 3,
    bars_cache: Optional[dict] = None,
) -> list[tuple[str, VariantConfig, dict]]:
    """
    Kør en hel phase mod train_data og returnér top-N varianter sorteret efter
    Sharpe-lignende ratio.
    """
    print(f"  Kører {phase_name} ({len(grid)} kombinationer)...", flush=True)
    results = []
    for key, config in grid:
        _, stats = run_one_config(config, train_data, tickers, bars_cache=bars_cache)
        results.append((key, config, stats))

    # Sorter — kun varianter med >0 trades og positiv Sharpe er reelle
    # Hvis intet positivt findes, returner top-N bedste (mindst negative)
    results.sort(key=lambda r: r[2]["sharpe_like"], reverse=True)
    return results[:top_n]


# ─────────────────────────────────────────────────────────────────────
# Output-helpers
# ─────────────────────────────────────────────────────────────────────

def print_top_table(label: str, top: list[tuple[str, VariantConfig, dict]]):
    """Print en lille tabel over top-N varianter."""
    print(f"\n  {BOLD}Top {len(top)} fra {label}:{RESET}")
    print(f"    {'Key':<22s} {'Trades':>6s} {'Win%':>6s} {'P&L':>9s} "
          f"{'PF':>5s} {'MaxDD':>9s} {'Sharpe':>7s} {'St/Tg/Tr/Fc':>13s}")
    for key, _config, stats in top:
        pnl_color = GREEN if stats["total_pnl"] > 0 else RED
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] < 9 else "  ∞"
        mix = f"{stats['n_stop']}/{stats['n_target']}/{stats['n_trail']}/{stats['n_force']}"
        print(f"    {key:<22s} {stats['trades']:>6d} {stats['win_rate']:>5.1f}% "
              f"{pnl_color}${stats['total_pnl']:>+7,.2f}{RESET} "
              f"{pf_str:>5s} ${stats['max_drawdown']:>+7,.2f} "
              f"{stats['sharpe_like']:>7.2f} {mix:>13s}")


# ─────────────────────────────────────────────────────────────────────
# Hovedeksploration for ÉT filter
# ─────────────────────────────────────────────────────────────────────

def explore_for_universe(
    filter_name: str,
    tickers: list[str],
    train_data: dict[str, pd.DataFrame],
    bars_cache: Optional[dict] = None,
) -> tuple[Optional[VariantConfig], dict]:
    """
    Kør hele 3-phase exploration mod ét ticker-univers.

    Returnerer (best_config, best_stats).
    """
    print(f"\n{BOLD}{'━' * 90}{RESET}")
    print(f"{BOLD}  Filter: {filter_name.upper()}  |  Tickers ({len(tickers)}): "
          f"{', '.join(tickers)}{RESET}")
    print(f"{BOLD}{'━' * 90}{RESET}")

    if not tickers:
        print(f"  {RED}Ingen tickers — springer over{RESET}")
        return None, {}

    # Phase 1: target × vol_mult
    p1_top = run_phase("Phase 1 (target × vol_mult)",
                       phase1_grid(), train_data, tickers, top_n=3,
                       bars_cache=bars_cache)
    print_top_table("Phase 1", p1_top)

    # Phase 2: byg ovenpå top-3 fra Phase 1
    p2_all = []
    for key, base_config, _ in p1_top:
        p2_grid = phase2_grid(base_config)
        p2_results = run_phase(f"Phase 2 oven på {key} ({len(p2_grid)} komb.)",
                               p2_grid, train_data, tickers, top_n=999,
                               bars_cache=bars_cache)
        p2_all.extend(p2_results)

    p2_all.sort(key=lambda r: r[2]["sharpe_like"], reverse=True)
    p2_top = p2_all[:3]
    print_top_table("Phase 2 (top-3 på tværs)", p2_top)

    # Phase 3: byg ovenpå top-3 fra Phase 2
    p3_all = []
    for key, base_config, _ in p2_top:
        p3_grid = phase3_grid(base_config)
        p3_results = run_phase(f"Phase 3 oven på {key} ({len(p3_grid)} komb.)",
                               p3_grid, train_data, tickers, top_n=999,
                               bars_cache=bars_cache)
        p3_all.extend(p3_results)

    p3_all.sort(key=lambda r: r[2]["sharpe_like"], reverse=True)
    p3_top = p3_all[:3]
    print_top_table("Phase 3 (FINAL top-3)", p3_top)

    if p3_top:
        best_key, best_config, best_stats = p3_top[0]
        return best_config, best_stats
    return None, {}


# ─────────────────────────────────────────────────────────────────────
# Out-of-sample validering
# ─────────────────────────────────────────────────────────────────────

def validate_out_of_sample(
    config: VariantConfig,
    test_data: dict[str, pd.DataFrame],
    tickers: list[str],
    filter_name: str,
    bars_cache: Optional[dict] = None,
) -> dict:
    """
    Kør den valgte vinder-config mod out-of-sample test_data.
    Det fortæller os om vinderen faktisk virker eller var et tilfælde.
    """
    trades, stats = run_one_config(config, test_data, tickers, bars_cache=bars_cache)
    print(f"  {filter_name:<12s} → {stats['trades']} trades, "
          f"P&L: ${stats['total_pnl']:+,.2f}, "
          f"Win: {stats['win_rate']:.1f}%, "
          f"PF: {stats['profit_factor']:.2f}, "
          f"Sharpe: {stats['sharpe_like']:.2f}")
    return stats


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main():
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  PARAMETER EXPLORATION — MomentumORB{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")

    # Indlæs data
    print(f"\nIndlæser data...")
    data = load_all_data()
    print(f"  → {len(data)} tickers: {', '.join(sorted(data.keys()))}")

    # Kør universe-evaluation for at få filtre + train/test split
    filters, ticker_metrics, test_data = evaluate_universe(data)

    # Træningsdata = de samme dage som universe_filter brugte
    train_data = {}
    for ticker, df in data.items():
        train_df, _ = split_data_in_time(df)
        train_data[ticker] = train_df

    print(f"\n{BOLD}Filtre fundet af universe_filter:{RESET}")
    for filt_name, tickers in filters.items():
        print(f"  {filt_name:<12s} → {len(tickers):>2d} tickers: {', '.join(tickers)}")

    # Pre-konvertér bars for både train og test data (sparer 5-10x kørselstid)
    import time
    print(f"\n{BOLD}Pre-konvertérer bars (cache for hurtigere exploration)...{RESET}")
    t0 = time.time()
    train_cache = build_bars_cache(train_data)
    test_cache  = build_bars_cache(test_data)
    print(f"  Cache bygget på {(time.time()-t0):.1f}s")

    # Kør exploration mod hvert filter
    winners = {}   # filter_name → (config, stats)
    t_explore = time.time()
    for filt_name in ["all", "mild", "medium", "aggressive"]:
        if not filters[filt_name]:
            continue
        best_config, best_stats = explore_for_universe(
            filt_name, filters[filt_name], train_data, bars_cache=train_cache
        )
        if best_config is not None:
            winners[filt_name] = (best_config, best_stats)
    print(f"\n  {DIM}Exploration tog {(time.time()-t_explore):.1f}s{RESET}")

    # ── Out-of-sample validering ──────────────────────────────────
    print(f"\n{BOLD}{'═' * 90}{RESET}")
    print(f"{BOLD}  OUT-OF-SAMPLE VALIDERING — top-vinderen pr. filter mod test-data{RESET}")
    print(f"{BOLD}{'═' * 90}{RESET}")
    print(f"\n  {DIM}Test-data er de 30% af dagene vi gemte — som vinderen ALDRIG har set.{RESET}")
    print(f"  {DIM}Hvis vinderen falder fra hinanden her, var den overfittet.{RESET}\n")

    out_of_sample_stats = {}
    for filt_name, (config, train_stats) in winners.items():
        oos_stats = validate_out_of_sample(
            config, test_data, filters[filt_name], filt_name,
            bars_cache=test_cache,
        )
        out_of_sample_stats[filt_name] = oos_stats

    # ── Final sammenligning ───────────────────────────────────────
    print(f"\n{BOLD}{'═' * 90}{RESET}")
    print(f"{BOLD}  FINAL SAMMENLIGNING — Train vs. Out-of-Sample{RESET}")
    print(f"{BOLD}{'═' * 90}{RESET}")
    print(f"\n  {'Filter':<12s} {'Train P&L':>12s} {'Train Sharpe':>14s} "
          f"{'OOS P&L':>12s} {'OOS Sharpe':>12s}  {'Verdict':<25s}")
    print(f"  {'-'*12} {'-'*12} {'-'*14} {'-'*12} {'-'*12}  {'-'*25}")

    for filt_name, (config, train_stats) in winners.items():
        if filt_name not in out_of_sample_stats:
            continue
        oos = out_of_sample_stats[filt_name]

        # Verdict
        if oos["total_pnl"] > 0 and train_stats["total_pnl"] > 0:
            verdict = f"{GREEN}REEL — virker også OOS{RESET}"
        elif oos["total_pnl"] > 0:
            verdict = f"{GREEN}OOS positiv (heldigt?){RESET}"
        elif oos["total_pnl"] > train_stats["total_pnl"] * 0.3:
            verdict = f"{YELLOW}OK — delvis stabilitet{RESET}"
        else:
            verdict = f"{RED}OVERFITTET — falder fra hinanden OOS{RESET}"

        train_pnl_color = GREEN if train_stats["total_pnl"] > 0 else RED
        oos_pnl_color   = GREEN if oos["total_pnl"] > 0 else RED

        print(f"  {filt_name:<12s} "
              f"{train_pnl_color}${train_stats['total_pnl']:>+10,.2f}{RESET} "
              f"{train_stats['sharpe_like']:>14.2f} "
              f"{oos_pnl_color}${oos['total_pnl']:>+10,.2f}{RESET} "
              f"{oos['sharpe_like']:>12.2f}  {verdict}")

    # Vinder-konfigurationer
    print(f"\n{BOLD}  Vinder-konfigurationer (parametre):{RESET}")
    for filt_name, (config, _) in winners.items():
        print(f"\n  {BOLD}{filt_name.upper()}:{RESET} {config.name}")
        print(f"    target={config.target_pct*100:.1f}%   vol_mult={config.vol_mult}x   "
              f"stop_mode={config.stop_mode}", end="")
        if config.stop_mode == "fixed_pct":
            print(f" ({config.fixed_stop_pct*100:.0f}%)", end="")
        print()
        be_str = f"+{config.breakeven_trigger_pct*100:.1f}%" if config.breakeven_enabled else "OFF"
        trail_str = (f"act +{config.trail_activate_pct*100:.1f}% "
                     f"dist {config.trail_distance_pct*100:.1f}%") if config.trail_enabled else "OFF"
        print(f"    BE={be_str}   trail={trail_str}")

    # Gem alt
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_rows = []
    for filt_name, (config, train_stats) in winners.items():
        oos = out_of_sample_stats.get(filt_name, {})
        out_rows.append({
            "filter":             filt_name,
            "n_tickers":          len(filters[filt_name]),
            "tickers":            ",".join(filters[filt_name]),
            "config_name":        config.name,
            "target_pct":         config.target_pct,
            "vol_mult":           config.vol_mult,
            "stop_mode":          config.stop_mode,
            "fixed_stop_pct":     config.fixed_stop_pct,
            "be_enabled":         config.breakeven_enabled,
            "be_trigger":         config.breakeven_trigger_pct,
            "trail_enabled":      config.trail_enabled,
            "trail_distance":     config.trail_distance_pct,
            "train_trades":       train_stats.get("trades", 0),
            "train_pnl":          train_stats.get("total_pnl", 0),
            "train_winrate":      train_stats.get("win_rate", 0),
            "train_sharpe":       train_stats.get("sharpe_like", 0),
            "oos_trades":         oos.get("trades", 0),
            "oos_pnl":            oos.get("total_pnl", 0),
            "oos_winrate":        oos.get("win_rate", 0),
            "oos_sharpe":         oos.get("sharpe_like", 0),
        })
    out_path = DATA_DIR / f"exploration_results_{timestamp}.csv"
    pd.DataFrame(out_rows).to_csv(out_path, index=False)
    print(f"\n  📊 Detaljer gemt → {out_path.name}")

    print(f"\n{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Exploration færdig.{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}\n")


if __name__ == "__main__":
    main()

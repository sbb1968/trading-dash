"""
universe_filter.py
──────────────────
Identificér hvilke tickers der er værd at handle baseret på en warm-up-periode.

Filosofi:
  Vi har 60 dages data. Vi bruger første 70% (≈42 dage) til at vurdere hver
  ticker. De sidste 30% (≈18 dage) gemmes til "out-of-sample" validering i
  parameter exploration — så vi ikke narrer os selv med overfitting.

Algoritme:
  1. Split data 70/30 i tid
  2. Kør backtest på 70%-delen med variant A (neutralt udgangspunkt)
  3. For hver ticker beregn: median max_gain_pct, total P&L, win rate
  4. Generér 3 filter-tærskler:
       - mild:      drop tickers i nederste 25% af kombineret score
       - medium:    drop nederste 50%
       - aggressiv: kun behold tickers hvor ALLE 3 metrics > median

Output:
  - Terminal-tabel der viser per-ticker metrics og hvilke filtre dropper hvad
  - CSV med detaljer
  - Returnerer 4 ticker-lister: alle, mild, medium, aggressiv

Kør:
    cd C:\\Projects\\Trading_Dash\\backend
    python universe_filter.py
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

from strategies import get_strategy
from strategies.base import Bar
from backtest_momentum import (
    load_all_data, df_to_bars, backtest_ticker_variant,
    CAPITAL_PER_TRADE,
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

# Default split-ratio
DEFAULT_TRAIN_RATIO = 0.70

# Variant brugt til at evaluere ticker-kvalitet
EVAL_VARIANT = "A"


def split_data_in_time(
    df: pd.DataFrame,
    train_ratio: float = DEFAULT_TRAIN_RATIO,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split en DataFrame i tid baseret på unikke handelsdage.

    Returnerer (train_df, test_df) hvor:
      train_df = bars fra de første N% af handelsdagene
      test_df  = bars fra de sidste (100-N)% af handelsdagene

    Vi splitter på DAGE, ikke på rækker, så hver dag forbliver intakt.
    """
    if df.empty:
        return df, df

    unique_days = sorted(df.index.normalize().unique())
    if len(unique_days) < 4:
        # For lidt data — returnér alt som train, intet som test
        return df, df.iloc[0:0]

    split_idx = max(1, int(len(unique_days) * train_ratio))
    split_day = unique_days[split_idx]

    train_df = df[df.index.normalize() < split_day]
    test_df  = df[df.index.normalize() >= split_day]
    return train_df, test_df


def per_ticker_metrics(trades: list[dict]) -> dict[str, dict]:
    """
    Beregn (median_max_gain, total_pnl, win_rate, n_trades) pr. ticker.

    Returnerer dict: ticker → metrics
    """
    if not trades:
        return {}

    df = pd.DataFrame(trades)
    result = {}
    for ticker, grp in df.groupby("ticker"):
        wins = grp[grp["pnl_usd"] > 0]
        result[ticker] = {
            "n_trades":         len(grp),
            "median_max_gain":  float(grp["max_gain_pct"].median()),
            "mean_max_gain":    float(grp["max_gain_pct"].mean()),
            "total_pnl":        float(grp["pnl_usd"].sum()),
            "mean_pnl":         float(grp["pnl_usd"].mean()),
            "win_rate":         len(wins) / len(grp) if len(grp) > 0 else 0.0,
        }
    return result


def rank_tickers(metrics: dict[str, dict]) -> dict[str, dict]:
    """
    Tildel hver ticker en "score" baseret på de 3 metrics.

    Vi normaliserer hver metric til 0-1 (z-score er for følsom på små data)
    og summer dem til en samlet score.

    Returnerer dict udvidet med 'score' og 'rank'.
    """
    if not metrics:
        return metrics

    tickers = list(metrics.keys())

    # Hent rå tal
    median_gains = np.array([metrics[t]["median_max_gain"] for t in tickers])
    total_pnls   = np.array([metrics[t]["total_pnl"]       for t in tickers])
    win_rates    = np.array([metrics[t]["win_rate"]        for t in tickers])

    def normalize(arr: np.ndarray) -> np.ndarray:
        """Skalér til 0-1 baseret på min/max."""
        rng = arr.max() - arr.min()
        if rng == 0:
            return np.full_like(arr, 0.5)
        return (arr - arr.min()) / rng

    n_median = normalize(median_gains)
    n_pnl    = normalize(total_pnls)
    n_win    = normalize(win_rates)

    scores = (n_median + n_pnl + n_win) / 3.0

    # Tilskriv score + rank (1 = bedst)
    score_order = np.argsort(-scores)   # descending
    ranks = {tickers[idx]: i + 1 for i, idx in enumerate(score_order)}

    for ticker, score in zip(tickers, scores):
        metrics[ticker]["score"] = float(score)
        metrics[ticker]["rank"]  = ranks[ticker]

    return metrics


def apply_filters(metrics: dict[str, dict]) -> dict[str, list[str]]:
    """
    Beregn tre filter-niveauer baseret på score og median-tærskler.

    Returnerer dict:
      "all":        alle tickers
      "mild":       fjern nederste 25% efter score
      "medium":     fjern nederste 50% efter score
      "aggressive": kun tickers hvor ALLE 3 raw metrics er over median
    """
    if not metrics:
        return {"all": [], "mild": [], "medium": [], "aggressive": []}

    tickers = list(metrics.keys())
    n = len(tickers)

    # Sorter efter score (højest først)
    sorted_by_score = sorted(tickers, key=lambda t: -metrics[t]["score"])

    # Mild: top 75%
    mild_count = max(1, int(round(n * 0.75)))
    mild = sorted_by_score[:mild_count]

    # Medium: top 50%
    medium_count = max(1, int(round(n * 0.50)))
    medium = sorted_by_score[:medium_count]

    # Aggressiv: kun tickers hvor alle 3 raw metrics > median
    median_gain = np.median([metrics[t]["median_max_gain"] for t in tickers])
    median_pnl  = np.median([metrics[t]["total_pnl"]       for t in tickers])
    median_win  = np.median([metrics[t]["win_rate"]        for t in tickers])

    aggressive = [
        t for t in tickers
        if metrics[t]["median_max_gain"] > median_gain
        and metrics[t]["total_pnl"]       > median_pnl
        and metrics[t]["win_rate"]        > median_win
    ]

    return {
        "all":        sorted(tickers),
        "mild":       sorted(mild),
        "medium":     sorted(medium),
        "aggressive": sorted(aggressive),
    }


def print_metrics_table(metrics: dict[str, dict], filters: dict[str, list[str]]):
    """Pretty-print metrics og hvilke filtre der dropper hvilke tickers."""
    print(f"\n{BOLD}{'─' * 95}{RESET}")
    print(f"{BOLD}  Per-ticker metrics fra warm-up data (variant {EVAL_VARIANT}){RESET}")
    print(f"{BOLD}{'─' * 95}{RESET}")
    print(f"  {'Ticker':<8s} {'#':>4s} {'MedGain%':>10s} {'TotalP&L':>10s} "
          f"{'Win%':>7s} {'Score':>7s} {'Rank':>5s}   {'Mild':>5s} {'Med':>5s} {'Aggr':>5s}")
    print(f"  {'-'*8} {'-'*4} {'-'*10} {'-'*10} {'-'*7} {'-'*7} {'-'*5}   {'-'*5} {'-'*5} {'-'*5}")

    sorted_tickers = sorted(metrics.keys(), key=lambda t: metrics[t]["rank"])
    for ticker in sorted_tickers:
        m = metrics[ticker]
        in_mild = "✓" if ticker in filters["mild"] else " "
        in_med  = "✓" if ticker in filters["medium"] else " "
        in_aggr = "✓" if ticker in filters["aggressive"] else " "

        pnl_color = GREEN if m["total_pnl"] > 0 else RED
        gain_color = GREEN if m["median_max_gain"] >= 0.5 else (YELLOW if m["median_max_gain"] >= 0.2 else RED)

        print(f"  {ticker:<8s} {m['n_trades']:>4d} "
              f"{gain_color}{m['median_max_gain']:>9.2f}%{RESET} "
              f"{pnl_color}${m['total_pnl']:>8,.2f}{RESET} "
              f"{m['win_rate']*100:>6.1f}% "
              f"{m['score']:>7.3f} "
              f"{m['rank']:>5d}     "
              f"{in_mild:>3s}   {in_med:>3s}   {in_aggr:>3s}")

    # Resume af filtre
    print(f"\n{BOLD}  Filter-resumé:{RESET}")
    for filt_name, tickers in filters.items():
        if filt_name == "all":
            continue
        kept = len(tickers)
        total = len(filters["all"])
        dropped = total - kept
        print(f"    {filt_name:>10s}: beholder {kept}/{total} tickers "
              f"({DIM}dropper: {', '.join(set(filters['all']) - set(tickers)) or 'ingen'}{RESET})")


def evaluate_universe(
    data: dict[str, pd.DataFrame],
    train_ratio: float = DEFAULT_TRAIN_RATIO,
) -> tuple[dict[str, list[str]], dict[str, dict], dict[str, pd.DataFrame]]:
    """
    Hovedfunktion: evaluér hvert ticker baseret på warm-up data.

    Returnerer:
      filters    — dict med 4 ticker-lister (all / mild / medium / aggressive)
      metrics    — per-ticker metrics dict
      test_data  — data fra ud-af-sample-perioden (gemmes til validering)
    """
    print(f"\n{BOLD}Splitter data {int(train_ratio*100)}/{int((1-train_ratio)*100)} "
          f"(warm-up / out-of-sample){RESET}")

    # Split data
    train_data = {}
    test_data  = {}
    for ticker, df in data.items():
        train, test = split_data_in_time(df, train_ratio=train_ratio)
        train_data[ticker] = train
        test_data[ticker]  = test

    # Vis split-info
    sample_ticker = next(iter(train_data))
    train_days = train_data[sample_ticker].index.normalize().nunique()
    test_days  = test_data[sample_ticker].index.normalize().nunique() if not test_data[sample_ticker].empty else 0
    print(f"  Warm-up:        {train_days} dage")
    print(f"  Out-of-sample:  {test_days} dage (gemmes til parameter exploration)")

    # Kør backtest mod warm-up data
    print(f"\n{BOLD}Kører backtest mod warm-up data (variant {EVAL_VARIANT})...{RESET}")
    strategy = get_strategy("momentum_orb")
    all_trades = []
    for ticker, df in train_data.items():
        trades = backtest_ticker_variant(strategy, ticker, df, EVAL_VARIANT)
        all_trades.extend(trades)
    print(f"  → {len(all_trades)} trades")

    if not all_trades:
        print(f"  {RED}Ingen trades — kan ikke evaluere{RESET}")
        return ({"all": [], "mild": [], "medium": [], "aggressive": []},
                {}, test_data)

    # Beregn metrics og ranks
    metrics = per_ticker_metrics(all_trades)
    metrics = rank_tickers(metrics)
    filters = apply_filters(metrics)

    return filters, metrics, test_data


def save_results(metrics: dict[str, dict], filters: dict[str, list[str]],
                 out_path: Path):
    """Gem per-ticker metrics + filter-mappings som CSV."""
    rows = []
    for ticker, m in sorted(metrics.items(), key=lambda x: x[1]["rank"]):
        rows.append({
            "ticker":           ticker,
            "n_trades":         m["n_trades"],
            "median_max_gain":  round(m["median_max_gain"], 3),
            "mean_max_gain":    round(m["mean_max_gain"], 3),
            "total_pnl":        round(m["total_pnl"], 2),
            "mean_pnl":         round(m["mean_pnl"], 2),
            "win_rate":         round(m["win_rate"], 3),
            "score":            round(m["score"], 4),
            "rank":             m["rank"],
            "in_mild":          ticker in filters["mild"],
            "in_medium":        ticker in filters["medium"],
            "in_aggressive":    ticker in filters["aggressive"],
        })
    pd.DataFrame(rows).to_csv(out_path, index=False)


def main():
    print(f"{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  Universe Filter — find tickers det er værd at handle{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}")

    # Indlæs data
    files = list(DATA_DIR.glob("*_5m_*.csv"))
    if not files:
        print(f"{RED}Ingen 5-min CSV-filer fundet i {DATA_DIR}{RESET}")
        return

    print(f"\nIndlæser data fra {DATA_DIR}...")
    data = load_all_data()
    print(f"  → {len(data)} tickers: {', '.join(sorted(data.keys()))}")

    # Evaluer
    filters, metrics, test_data = evaluate_universe(data)
    print_metrics_table(metrics, filters)

    # Gem CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = DATA_DIR / f"universe_filter_{timestamp}.csv"
    save_results(metrics, filters, out_path)
    print(f"\n  📊 Detaljer gemt → {out_path.name}")

    # Konklusion + næste skridt
    print(f"\n{BOLD}{'─' * 70}{RESET}")
    print(f"{BOLD}  Konklusion{RESET}")
    print(f"{BOLD}{'─' * 70}{RESET}")

    if not metrics:
        return

    best_ticker = min(metrics.keys(), key=lambda t: metrics[t]["rank"])
    worst_ticker = max(metrics.keys(), key=lambda t: metrics[t]["rank"])

    print(f"\n  {GREEN}Bedste:{RESET}  {best_ticker} "
          f"(score: {metrics[best_ticker]['score']:.3f}, "
          f"P&L: ${metrics[best_ticker]['total_pnl']:+.2f})")
    print(f"  {RED}Værste:{RESET}  {worst_ticker} "
          f"(score: {metrics[worst_ticker]['score']:.3f}, "
          f"P&L: ${metrics[worst_ticker]['total_pnl']:+.2f})")

    if filters["aggressive"]:
        print(f"\n  Aggressivt filter beholder: {', '.join(filters['aggressive'])}")
    else:
        print(f"\n  {YELLOW}Aggressivt filter beholdt ingen tickers — kriteriet er for stramt.{RESET}")

    print(f"\n  {BOLD}Step 2 er færdig.{RESET} Næste skridt: parameter exploration mod hvert filter.")


if __name__ == "__main__":
    main()

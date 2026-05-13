"""
diagnose_strategy.py
────────────────────
Diagnostisk analyse af MomentumORB-strategiens performance.

Svarer på 6 spørgsmål:
  1. Hvilken periode dækker data?
  2. Hvor mange dage havde overhovedet et breakout-mønster?
  3. Hvor mange dage nåede til entry (efter retest)?
  4. Hvor langt nåede prisen typisk EFTER entry?
  5. Er 09:45-10:30-vinduet for kort? (kig på max_gain_pct og hvornår det nås)
  6. Hvilke tickers/dage var de bedste? Hvilke var værst?

Bruger backtest-resultaterne fra sidste kørsel — kører ikke selv backtest.

Kør:
    cd C:\\Projects\\Trading_Dash\\backend
    python diagnose_strategy.py
"""

from __future__ import annotations
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, time as dtime
import warnings
warnings.filterwarnings("ignore")

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


def banner(title):
    print(f"\n{BOLD}{'─' * 70}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─' * 70}{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Find den nyeste backtest-resultatfil pr. variant
# ─────────────────────────────────────────────────────────────────────

def find_latest_results() -> dict[str, Path]:
    """Find seneste backtest_momentum_orb_<variant>_<timestamp>.csv pr. variant."""
    variants = ["baseline", "A", "B", "C", "D"]
    latest = {}
    for v in variants:
        files = sorted(DATA_DIR.glob(f"backtest_momentum_orb_{v}_*.csv"))
        if files:
            latest[v] = files[-1]   # nyeste sidst pga. timestamp
    return latest


# ─────────────────────────────────────────────────────────────────────
# Q1 — Hvilken periode dækker data?
# ─────────────────────────────────────────────────────────────────────

def q1_data_period():
    banner("Q1 — Hvilken periode dækker CSV-data?")

    csv_files = sorted(DATA_DIR.glob("*_5m_*.csv"))
    if not csv_files:
        print("Ingen 5-min CSV-filer fundet")
        return

    earliest = None
    latest = None
    per_ticker = {}

    for f in csv_files:
        ticker = f.stem.split("_")[0]
        # Spring backtest-output-filer over
        if ticker == "backtest":
            continue
        if ticker in ("SPY", "QQQ", "IWM"):
            continue
        try:
            df = pd.read_csv(f, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True)
            t_min = df.index.min()
            t_max = df.index.max()
            per_ticker[ticker] = (t_min, t_max, len(df))
            if earliest is None or t_min < earliest:
                earliest = t_min
            if latest is None or t_max > latest:
                latest = t_max
        except Exception as e:
            print(f"  Fejl ved {f.name}: {e}")

    print(f"  Tidligste bar: {earliest}")
    print(f"  Seneste bar:   {latest}")
    print(f"  Periode:       {(latest - earliest).days} dage")
    print()
    print(f"  Pr. ticker:")
    print(f"  {'Ticker':8s}  {'Fra':12s}  {'Til':12s}  {'Bars':>6s}")
    for ticker, (mn, mx, n) in sorted(per_ticker.items()):
        print(f"  {ticker:8s}  {mn.strftime('%Y-%m-%d')}    "
              f"{mx.strftime('%Y-%m-%d')}    {n:>6}")

    # Vurder om data er gammelt
    now = datetime.now(latest.tzinfo)
    age_days = (now - latest).days
    if age_days > 30:
        print(f"\n  {YELLOW}! Data er {age_days} dage gammelt — markedet kan have ændret sig{RESET}")
    else:
        print(f"\n  {GREEN}✓ Data er kun {age_days} dage gammelt{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Q2-Q4 — Trade-statistikker fra backtest-output
# ─────────────────────────────────────────────────────────────────────

def q2_breakout_frequency():
    """Tæl hvor ofte vi overhovedet kom til entry."""
    banner("Q2/Q3 — Hvor ofte når vi til entry?")

    # Brug baseline-variant til at tælle entries
    files = find_latest_results()
    if "baseline" not in files:
        print("Ingen baseline-CSV fundet — kør backtest_momentum.py først")
        return

    df = pd.read_csv(files["baseline"])

    # Beregn handelsdage pr. ticker via 5-min CSV
    csv_files = sorted(DATA_DIR.glob("*_5m_*.csv"))
    total_days = 0
    days_per_ticker = {}
    for f in csv_files:
        ticker = f.stem.split("_")[0]
        if ticker == "backtest":
            continue
        if ticker in ("SPY", "QQQ", "IWM"):
            continue
        try:
            raw = pd.read_csv(f, index_col=0)
            raw.index = pd.to_datetime(raw.index, utc=True)
            n_days = raw.index.normalize().nunique()
            days_per_ticker[ticker] = n_days
            total_days += n_days
        except Exception:
            pass

    entries_per_ticker = df.groupby("ticker").size().to_dict()

    print(f"  Total dage på tværs af tickers: {total_days}")
    print(f"  Total entries i baseline:       {len(df)}")
    if total_days > 0:
        rate = len(df) / total_days * 100
        print(f"  Entry-rate:                     {rate:.1f}% af handelsdage")

    print(f"\n  Pr. ticker:")
    print(f"  {'Ticker':8s}  {'Dage':>5s}  {'Entries':>8s}  {'Rate':>6s}")
    for ticker in sorted(days_per_ticker.keys()):
        days = days_per_ticker[ticker]
        ents = entries_per_ticker.get(ticker, 0)
        rate = ents / days * 100 if days > 0 else 0
        color = GREEN if rate > 30 else (YELLOW if rate > 15 else RED)
        print(f"  {ticker:8s}  {days:>5}  {ents:>8}  {color}{rate:>5.1f}%{RESET}")


def q4_max_gain_distribution():
    """Hvor langt nåede prisen efter entry?"""
    banner("Q4 — Hvor langt nåede prisen efter entry?")

    files = find_latest_results()
    if "A" not in files:
        print("Ingen variant A-CSV fundet")
        return

    df = pd.read_csv(files["A"])
    if "max_gain_pct" not in df.columns:
        print("max_gain_pct kolonne mangler i CSV")
        return

    mg = df["max_gain_pct"]
    print(f"  Total entries: {len(df)}")
    print(f"  Max gain (% over entry) — statistik:")
    print(f"    Min:    {mg.min():>6.2f}%")
    print(f"    25%:    {mg.quantile(0.25):>6.2f}%")
    print(f"    Median: {mg.median():>6.2f}%")
    print(f"    75%:    {mg.quantile(0.75):>6.2f}%")
    print(f"    Max:    {mg.max():>6.2f}%")
    print(f"    Snit:   {mg.mean():>6.2f}%")

    print(f"\n  Distribution (hvor mange handler nåede X% peak gain):")
    bins = [-100, 0, 0.5, 1, 2, 3, 4, 5, 10, 100]
    labels = ["<0%", "0-0.5%", "0.5-1%", "1-2%", "2-3%", "3-4%", "4-5%", "5-10%", ">10%"]
    counts = pd.cut(mg, bins=bins, labels=labels).value_counts().sort_index()
    total = counts.sum()
    for label, count in counts.items():
        pct = count / total * 100
        bar = "█" * int(pct / 2)   # 1 tegn pr. 2%
        print(f"    {label:>8s}  {count:>4}  ({pct:>5.1f}%)  {bar}")

    # Vigtig observation
    pct_above_3 = (mg >= 3.0).sum() / len(mg) * 100
    pct_above_4 = (mg >= 4.0).sum() / len(mg) * 100
    print(f"\n  {BOLD}Nøgletal:{RESET}")
    print(f"    Handler der nåede +3% (BE-trigger):   {pct_above_3:>5.1f}%")
    print(f"    Handler der nåede +4% (target/trail): {pct_above_4:>5.1f}%")

    if pct_above_4 < 10:
        print(f"\n  {RED}! Under 10% af handlerne nåede +4%. "
              f"Det forklarer hvorfor 95% lukkes ved force-close.{RESET}")
    elif pct_above_4 < 25:
        print(f"\n  {YELLOW}! Kun {pct_above_4:.0f}% når +4%. "
              f"Target er muligvis sat for højt for tidsvinduet.{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Q5 — Hvornår på dagen nås peak gain?
# ─────────────────────────────────────────────────────────────────────

def q5_when_peak_reached():
    """
    Vis fordeling af duration_min — hvor længe trades typisk varer.
    Hvis de fleste varer ~35 min betyder det at force-close lukker dem.
    """
    banner("Q5 — Trade duration (er 45-min vinduet for kort?)")

    files = find_latest_results()
    if "A" not in files:
        print("Ingen variant A-CSV fundet")
        return

    df = pd.read_csv(files["A"])

    dur = df["duration_min"]
    print(f"  Trade duration (minutter fra entry til exit):")
    print(f"    Min:    {dur.min():>3} min")
    print(f"    Median: {dur.median():>3.0f} min")
    print(f"    Max:    {dur.max():>3} min")
    print(f"    Snit:   {dur.mean():>3.0f} min")

    print(f"\n  Distribution:")
    bins = [0, 5, 10, 15, 20, 30, 45, 999]
    labels = ["0-5min", "5-10min", "10-15min", "15-20min", "20-30min", "30-45min", "45min+"]
    counts = pd.cut(dur, bins=bins, labels=labels).value_counts().sort_index()
    total = counts.sum()
    for label, count in counts.items():
        pct = count / total * 100
        bar = "█" * int(pct / 2)
        print(f"    {label:>10s}  {count:>4}  ({pct:>5.1f}%)  {bar}")

    # Andel der rammer force-close (duration ≥ 35 min cirka — afhænger af entry-tid)
    by_reason = df["exit_reason"].value_counts()
    print(f"\n  Exit-årsager:")
    for reason, count in by_reason.items():
        pct = count / len(df) * 100
        print(f"    {reason:>15s}  {count:>4}  ({pct:>5.1f}%)")


# ─────────────────────────────────────────────────────────────────────
# Q6 — Top tickers / dage
# ─────────────────────────────────────────────────────────────────────

def q6_best_and_worst():
    banner("Q6 — Hvilke tickers/dage var bedst/værst?")

    files = find_latest_results()
    if "A" not in files:
        print("Ingen variant A-CSV fundet")
        return

    df = pd.read_csv(files["A"])

    print(f"  {BOLD}Pr. ticker:{RESET}")
    grp = df.groupby("ticker").agg(
        trades=("pnl_usd", "count"),
        total_pnl=("pnl_usd", "sum"),
        avg_pnl=("pnl_usd", "mean"),
        win_rate=("pnl_usd", lambda x: (x > 0).sum() / len(x) * 100),
    ).sort_values("total_pnl", ascending=False)

    print(f"  {'Ticker':8s}  {'Trades':>6s}  {'P&L':>10s}  {'Snit':>8s}  {'Win%':>6s}")
    for ticker, row in grp.iterrows():
        color = GREEN if row["total_pnl"] > 0 else RED
        print(f"  {ticker:8s}  {row['trades']:>6.0f}  "
              f"{color}${row['total_pnl']:>8,.2f}{RESET}  "
              f"${row['avg_pnl']:>6.2f}  {row['win_rate']:>5.1f}%")

    print(f"\n  {BOLD}Top 5 dage:{RESET}")
    daily = df.groupby("date").agg(
        trades=("pnl_usd", "count"),
        total_pnl=("pnl_usd", "sum"),
    ).sort_values("total_pnl", ascending=False)
    for date, row in daily.head(5).iterrows():
        print(f"    {date}  ({int(row['trades'])} trades): "
              f"{GREEN}${row['total_pnl']:>+8,.2f}{RESET}")

    print(f"\n  {BOLD}Bund 5 dage:{RESET}")
    for date, row in daily.tail(5).iterrows():
        print(f"    {date}  ({int(row['trades'])} trades): "
              f"{RED}${row['total_pnl']:>+8,.2f}{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Konklusion + anbefalinger
# ─────────────────────────────────────────────────────────────────────

def conclude():
    banner("Konklusion og anbefalinger")

    files = find_latest_results()
    if "A" not in files:
        return

    df = pd.read_csv(files["A"])
    mg = df["max_gain_pct"]
    pct_above_3 = (mg >= 3.0).sum() / len(mg) * 100
    pct_above_4 = (mg >= 4.0).sum() / len(mg) * 100
    force_close_pct = (df["exit_reason"] == "force_close").sum() / len(df) * 100

    issues = []
    suggestions = []

    if force_close_pct > 70:
        issues.append(f"{force_close_pct:.0f}% af handler lukkes ved force-close — "
                      f"target/stop/trail aktiveres næsten aldrig")

    if pct_above_4 < 15:
        issues.append(f"Kun {pct_above_4:.0f}% af handler når +4% — "
                      f"target er sat for højt for tidsvinduet")
        suggestions.append("Test target på +2% eller +3% i stedet for +4%")

    if pct_above_3 < 25:
        issues.append(f"Kun {pct_above_3:.0f}% når +3% — "
                      f"break-even-trigger er sjælden")
        suggestions.append("Test BE-trigger ved +1% eller +1.5%")

    avg_max_gain = mg.mean()
    if avg_max_gain < 2.0:
        issues.append(f"Gennemsnitlig peak gain er kun {avg_max_gain:.1f}% — "
                      f"signal-kvaliteten er svag")
        suggestions.append("Overvej strammere entry (højere vol-mult, "
                          "mindre RSI-tærskel) eller anderledes universe")

    if df["duration_min"].median() > 30:
        suggestions.append("Test udvidet handelsvindue (fx til 11:30 eller 13:00) "
                          "for at give trades mere tid")

    if issues:
        print(f"  {RED}{BOLD}Identificerede problemer:{RESET}")
        for i, issue in enumerate(issues, 1):
            print(f"    {i}. {issue}")

    if suggestions:
        print(f"\n  {YELLOW}{BOLD}Mulige eksperimenter:{RESET}")
        for i, sug in enumerate(suggestions, 1):
            print(f"    {i}. {sug}")

    print(f"\n  {BOLD}Næste skridt:{RESET}")
    print(f"    Vi kan lave et 'parameter exploration'-script der tester")
    print(f"    forskellige kombinationer af target/BE/window og finder")
    print(f"    hvilke der teoretisk har positiv P&L.")
    print(f"\n    Eller — vi kan acceptere at MomentumORB ikke virker på")
    print(f"    dette univers og denne periode, og kigge på andre strategier")
    print(f"    (mean reversion, volume spike) der er på din ML-roadmap.")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main():
    print(f"{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  Trading Dash — Strategi-diagnostik{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}")

    files = find_latest_results()
    if not files:
        print(f"\n{RED}Ingen backtest-resultater fundet. "
              f"Kør først:{RESET}\n    python backtest_momentum.py")
        return

    print(f"\n  Bruger backtest-resultater fra:")
    for v, f in sorted(files.items()):
        print(f"    {v:10s}  {f.name}")

    q1_data_period()
    q2_breakout_frequency()
    q4_max_gain_distribution()
    q5_when_peak_reached()
    q6_best_and_worst()
    conclude()

    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  Diagnostik færdig{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")


if __name__ == "__main__":
    main()

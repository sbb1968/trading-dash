"""
show_forensics.py
─────────────────
Hurtig SQL-baseret analyse af trade_forensics events i journalen.

Kør:
    python show_forensics.py              # alle events
    python show_forensics.py --winners    # kun vindere
    python show_forensics.py --losers     # kun tabere
    python show_forensics.py --compare    # side-by-side vinder vs taber

Placering: C:\\Projects\\trading-dash\\backend\\show_forensics.py
"""

import json
import sqlite3
import sys
from pathlib import Path
from statistics import mean, median

DB_PATH = Path(__file__).parent / "trading_dash.db"

# Farver
BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def fetch_forensics() -> list[dict]:
    """Hent alle trade_forensics events fra journalen."""
    if not DB_PATH.exists():
        print(f"Ingen database: {DB_PATH}")
        return []

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT id, ts_local, symbol, payload_json
        FROM events
        WHERE event_type = 'trade_forensics'
        ORDER BY id ASC
        """
    ).fetchall()
    conn.close()

    out = []
    for r in rows:
        try:
            payload = json.loads(r[3])
            payload["_id"] = r[0]
            payload["_ts"] = r[1]
            payload["_symbol"] = r[2]
            out.append(payload)
        except json.JSONDecodeError:
            continue
    return out


def pair_entries_and_exits(events: list[dict]) -> list[tuple[dict, dict]]:
    """
    Match entry-events med deres tilsvarende exit-events.

    Antagelse: events kommer kronologisk; entry for X efterfølges af exit for X
    før næste entry for X (vi har max 1 åben pos pr. ticker ad gangen).
    """
    pairs: list[tuple[dict, dict]] = []
    open_entries: dict[str, dict] = {}  # ticker -> latest entry event

    for ev in events:
        ticker = ev.get("ticker")
        phase = ev.get("phase")
        if not ticker or not phase:
            continue

        if phase == "entry":
            open_entries[ticker] = ev
        elif phase == "exit":
            entry = open_entries.pop(ticker, None)
            if entry is not None:
                pairs.append((entry, ev))

    return pairs


def print_summary(pairs: list[tuple[dict, dict]]) -> None:
    if not pairs:
        print(f"{YELLOW}Ingen matchede trade-par fundet endnu.{RESET}")
        return

    winners = [p for p in pairs if p[1].get("trade_metrics", {}).get("pnl", 0) > 0]
    losers  = [p for p in pairs if p[1].get("trade_metrics", {}).get("pnl", 0) < 0]

    print(f"{BOLD}Trade Forensics — Oversigt{RESET}")
    print("=" * 70)
    print(f"  Total handler:  {len(pairs)}")
    print(f"  {GREEN}Vindere:        {len(winners)}{RESET}")
    print(f"  {RED}Tabere:         {len(losers)}{RESET}")
    if pairs:
        win_rate = len(winners) / len(pairs) * 100
        print(f"  Win rate:       {win_rate:.1f}%")


def print_one(pair: tuple[dict, dict], idx: int = None) -> None:
    entry, exit = pair
    em = entry.get("indicators", {})
    xm = exit.get("indicators", {})
    et = entry.get("tape", {})
    xt = exit.get("tape", {})
    ed = entry.get("depth", {})
    xd = exit.get("depth", {})
    es = entry.get("setup", {})
    tm = exit.get("trade_metrics", {})

    pnl = tm.get("pnl", 0)
    color = GREEN if pnl > 0 else RED if pnl < 0 else RESET
    marker = "✅" if pnl > 0 else "❌" if pnl < 0 else "•"

    prefix = f"#{idx} " if idx is not None else ""
    print(f"\n{BOLD}{prefix}{marker} {entry.get('ticker')}  "
          f"{color}${tm.get('pnl', 0):+.2f} ({tm.get('pnl_pct', 0):+.2f}%){RESET}  "
          f"{DIM}reason={tm.get('reason')}  duration={tm.get('duration_sec')}s{RESET}")
    print(f"  Entry: ${tm.get('entry_price'):.4f} @ {entry.get('time_et')}")
    print(f"  Exit:  ${tm.get('exit_price'):.4f} @ {exit.get('time_et')}")

    print(f"  {BOLD}Setup:{RESET}  "
          f"ORB H={es.get('orb_high')} L={es.get('orb_low')} "
          f"range={es.get('orb_range_pct')}%  "
          f"breakout={es.get('breakout_strength_pct')}%  "
          f"rel_vol={es.get('rel_vol_last_bar')}x")

    print(f"  {BOLD}Indikatorer (entry → exit):{RESET}")
    print(f"    RSI:           {em.get('rsi_14')} → {xm.get('rsi_14')}")
    print(f"    MACD hist:     {em.get('macd_hist')} → {xm.get('macd_hist')}")
    print(f"    EMA 9 / 20:    {em.get('ema_9')} / {em.get('ema_20')}")
    print(f"    BB pos %:      {em.get('bb_position_pct')} → {xm.get('bb_position_pct')}")
    print(f"    VWAP dist %:   {em.get('vwap_distance_pct')} → {xm.get('vwap_distance_pct')}")

    print(f"  {BOLD}Tape (60s før entry):{RESET} "
          f"trades={et.get('trade_count')}  "
          f"vol={et.get('total_volume')}  "
          f"aggressor={et.get('aggressor_ratio')}  "
          f"largest={et.get('largest_trade_size')}({et.get('largest_trade_direction')})")

    if ed.get("available"):
        print(f"  {BOLD}L2 (entry):{RESET}  "
              f"spread={ed.get('spread')} ({ed.get('spread_pct')}%)  "
              f"bid_size={ed.get('total_bid_size')}  "
              f"ask_size={ed.get('total_ask_size')}  "
              f"imbalance={ed.get('bid_ask_imbalance')}")
    else:
        print(f"  {BOLD}L2 (entry):{RESET}  {DIM}ikke tilgængelig ({ed.get('reason')}){RESET}")


def compare_winners_vs_losers(pairs: list[tuple[dict, dict]]) -> None:
    """Sammenlign median-værdier for vindere vs tabere."""
    winners = [p for p in pairs if p[1].get("trade_metrics", {}).get("pnl", 0) > 0]
    losers  = [p for p in pairs if p[1].get("trade_metrics", {}).get("pnl", 0) < 0]

    if not winners or not losers:
        print(f"{YELLOW}Behøver mindst 1 vinder og 1 taber for sammenligning.{RESET}")
        print(f"Vindere: {len(winners)}  Tabere: {len(losers)}")
        return

    def safe_median(vals):
        clean = [v for v in vals if v is not None]
        return round(median(clean), 3) if clean else None

    def extract(pairs_list, getter):
        return [getter(p) for p in pairs_list]

    metrics = [
        ("RSI på entry",               lambda p: p[0].get("indicators", {}).get("rsi_14")),
        ("MACD histogram på entry",    lambda p: p[0].get("indicators", {}).get("macd_hist")),
        ("BB position % på entry",     lambda p: p[0].get("indicators", {}).get("bb_position_pct")),
        ("VWAP dist % på entry",       lambda p: p[0].get("indicators", {}).get("vwap_distance_pct")),
        ("Breakout strength %",        lambda p: p[0].get("setup", {}).get("breakout_strength_pct")),
        ("Rel vol sidste bar",         lambda p: p[0].get("setup", {}).get("rel_vol_last_bar")),
        ("Tape aggressor-ratio",       lambda p: p[0].get("tape", {}).get("aggressor_ratio")),
        ("Tape total volume (60s)",    lambda p: p[0].get("tape", {}).get("total_volume")),
        ("Tape largest trade",         lambda p: p[0].get("tape", {}).get("largest_trade_size")),
        ("L2 spread % på entry",       lambda p: p[0].get("depth", {}).get("spread_pct")),
        ("L2 bid/ask imbalance",       lambda p: p[0].get("depth", {}).get("bid_ask_imbalance")),
    ]

    print(f"\n{BOLD}Median-værdier — Vindere vs Tabere{RESET}")
    print("=" * 70)
    print(f"  {BOLD}{'Metric':<32s}  {'Vindere':>14s}  {'Tabere':>14s}{RESET}")
    print(f"  {'-' * 32}  {'-' * 14}  {'-' * 14}")

    for name, getter in metrics:
        w_med = safe_median(extract(winners, getter))
        l_med = safe_median(extract(losers, getter))
        marker = ""
        if w_med is not None and l_med is not None:
            if abs(w_med - l_med) > 0.01:
                marker = f" {GREEN}↑{RESET}" if w_med > l_med else f" {RED}↓{RESET}"
        print(f"  {name:<32s}  {str(w_med):>14s}  {str(l_med):>14s}{marker}")

    print(f"\n  {DIM}↑ = vindere har højere median, ↓ = lavere. Mønstre kræver "
          f"30+ handler før de er troværdige.{RESET}")


def main():
    args = sys.argv[1:]
    events = fetch_forensics()
    pairs = pair_entries_and_exits(events)

    print_summary(pairs)

    if "--compare" in args:
        compare_winners_vs_losers(pairs)
        return

    if "--winners" in args:
        pairs = [p for p in pairs if p[1].get("trade_metrics", {}).get("pnl", 0) > 0]
    elif "--losers" in args:
        pairs = [p for p in pairs if p[1].get("trade_metrics", {}).get("pnl", 0) < 0]

    for i, p in enumerate(pairs, 1):
        print_one(p, idx=i)


if __name__ == "__main__":
    main()

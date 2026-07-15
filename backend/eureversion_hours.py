#!/usr/bin/env python3
"""eureversion_hours.py — timefordeling + aabningstids-optimering for EUreversion.

Importerer run_backtest/load_15min/stats fra eureversion_backtest (roerer ikke kernen).
Kanonisk regel (lookback 30, z>=2/<=0.5/>=3.5, EU-session, min_vol 50-pct). Timefordeling +
IS(2026 H1)/OOS(jun2024-dec2025) for alle-timer vs. optimerede timer. Rent offline.
"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
import pytz
ET = pytz.timezone("America/New_York")
from eureversion_backtest import load_15min, run_backtest, stats

DATA = Path("data_harvest/mes_m2k_stitched")
SESSION, LB, EZ, XZ, SZ = "europaeisk", 30, 2.0, 0.5, 3.5
COST = 2.0
IS0, IS1 = date(2026, 1, 1), date(2026, 6, 30)
OO0, OO1 = date(2024, 6, 21), date(2025, 12, 31)


def min_vol(bars):
    v = sorted(b.volume for b in bars if b.volume > 0)
    return v[len(v) // 2] if v else None


def pf(trades, cost=COST):
    return stats(trades, cost)


def in_range(t, a, b):
    d = t.entry_ts.astimezone(ET).date()
    return a <= d <= b


def hour(t):
    return t.entry_ts.astimezone(ET).hour


def main():
    bars = {s: load_15min(DATA / f"{s}_15min.csv") for s in ("MES", "M2K")}
    mv = {s: min_vol(bars[s]) for s in bars}
    # fulde handler (hele sessionen, 2 aar)
    allt = {s: run_backtest(bars[s], SESSION, LB, EZ, XZ, SZ, mv[s]) for s in bars}

    print("=" * 74)
    print("  EUreversion — TIMEFORDELING (2026 H1, entry ET-time) @%gbp" % COST)
    print("=" * 74)
    print(f"  {'ET':>3}{'DK':>4}{'n':>5}{'avg%':>8}{'sum%':>8}{'PF':>7}   (begge instrumenter)")
    is_all = [t for s in allt for t in allt[s] if in_range(t, IS0, IS1)]
    byh = {}
    for t in is_all:
        byh.setdefault(hour(t), []).append(t)
    good = []
    for h in sorted(byh):
        st = pf(byh[h]); dk = (h + 6) % 24
        star = " *" if (st["pf"] > 1 and st["n"] >= 8) else ""
        if star:
            good.append(h)
        print(f"  {h:>3}{dk:>4}{st['n']:>5}{st['avg']:>+8.3f}{st['sum']:>+8.1f}{st['pf']:>7.2f}{star}")
    print(f"\n  Gode timer (PF>1, n>=8) paa 2026 H1: ET {sorted(good)}  (DK {[(h+6)%24 for h in sorted(good)]})")

    def report(label, a, b):
        print(f"\n── {label} ──")
        print(f"  {'':16}{'n':>5}{'WR%':>6}{'avg%':>8}{'sum%':>8}{'PF':>7}")
        for tag, hrs in [("alle EU-timer", None), (f"optimerede timer", set(good))]:
            tr = []
            for s in bars:
                t2 = run_backtest(bars[s], SESSION, LB, EZ, XZ, SZ, mv[s], entry_hours=hrs)
                tr += [t for t in t2 if in_range(t, a, b)]
            st = pf(tr)
            print(f"  {tag:16}{st['n']:>5}{st['wr']:>6.1f}{st['avg']:>+8.3f}{st['sum']:>+8.1f}{st['pf']:>7.2f}")

    report("IN-SAMPLE 2026 H1", IS0, IS1)
    report("OUT-OF-SAMPLE jun2024-dec2025", OO0, OO1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

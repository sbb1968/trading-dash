import csv
from datetime import date
from pathlib import Path
S, E = date(2026, 4, 1), date(2026, 5, 29)
names, seen = [], set()
for ln in Path("phase_b_tickers.txt").read_text().splitlines():
    t = ln.split("#", 1)[0].strip().upper()
    if t and t not in seen:
        seen.add(t); names.append(t)
cache = Path("bar_cache")
def covers(t):
    for p in cache.glob(f"{t}_*_1min.csv"):
        parts = p.name[:-len("_1min.csv")].rsplit("_", 2)
        if len(parts) != 3:
            continue
        try:
            s, e = date.fromisoformat(parts[1]), date.fromisoformat(parts[2])
        except ValueError:
            continue
        if s <= S and e >= E:
            return True
    return False
have = [t for t in names if covers(t)]
miss = [t for t in names if t not in have]
print(f"navne: {len(names)}   daekket: {len(have)}   mangler: {len(miss)}")
if miss:
    print("mangler:")
    for i in range(0, len(miss), 10):
        print("  " + " ".join(miss[i:i+10]))
    print("-> genoptag (sikkert vindue): python download_midcap_bars.py --tickers phase_b_tickers.txt --start 2026-04-01 --end 2026-05-29")
else:
    print("-> alle daekket. Fase B kan koere: washout_reclaim_backtest -> washout_portfolio_sim -> washout_regime pr. pulje")

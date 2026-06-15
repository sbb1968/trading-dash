# diag_pool_cache.py — kør: python diag_pool_cache.py
import json, glob, os, re
from collections import defaultdict

CACHE = "bar_cache"
cached = defaultdict(set)
for f in glob.glob(os.path.join(CACHE, "*_1min.csv")):
    m = re.match(r"(.+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_1min\.csv$", os.path.basename(f))
    if m:
        cached[(m.group(2), m.group(3))].add(m.group(1))

print("=== eksisterende 1-min cache-vinduer ===")
for (s, e), tks in sorted(cached.items()):
    print(f"  {s}_{e}: {len(tks)} tickers")

pools = {
    "baseline_04": "pool_baseline_2026-04.json",
    "baseline_05": "pool_baseline_2026-05.json",
    "bredt_04":    "pool_bredt_prisbaand_2026-04.json",
    "bredt_05":    "pool_bredt_prisbaand_2026-05.json",
    "momentum_04": "pool_momentum_alignet_2026-04.json",
    "momentum_05": "pool_momentum_alignet_2026-05.json",
}
print("\n=== pool vs cache (kun vinduer med >30% overlap vises) ===")
for name, path in pools.items():
    if not os.path.exists(path):
        print(f"\n{name}: MANGLER ({path})"); continue
    d = json.load(open(path, encoding="utf-8"))
    tickers = set().union(*[set(v) for v in d.values()]) if d else set()
    dates = sorted(d.keys())
    print(f"\n{name}: {len(tickers)} unikke tickers · dage {dates[0]}..{dates[-1]} ({len(dates)} dage)")
    for (s, e), ctk in sorted(cached.items()):
        inter = tickers & ctk
        pct = 100 * len(inter) / len(tickers) if tickers else 0
        if pct > 30:
            print(f"   vs cache {s}_{e}: {len(inter)}/{len(tickers)} ({pct:.0f}%) cachet")

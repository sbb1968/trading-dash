"""
vis_univers.py — viser dagens resterende univers for en strategi,
hentet fra journalen (trading_dash.db).

Brug:
    python vis_univers.py                # default: Konfluens
    python vis_univers.py "Momentum ORB" # anden strategi

Henter den seneste 'universe_selected'-event (Lag A: log_universe)
og viser hvilke aktier der overlevede pris-filteret.
"""
import sqlite3
import json
import sys

strategy = sys.argv[1] if len(sys.argv) > 1 else "Konfluens"

conn = sqlite3.connect("trading_dash.db")
row = conn.execute(
    """
    SELECT ts_local, instance_id, source, payload_json
    FROM events
    WHERE event_type = 'universe_selected'
      AND source LIKE ?
    ORDER BY id DESC
    LIMIT 1
    """,
    (f"%{strategy}%",),
).fetchone()

if not row:
    print(f"Ingen 'universe_selected'-event fundet for '{strategy}'.")
    print("(Strategien har måske ikke scannet endnu, eller kører på en anden maskine.)")
    sys.exit(0)

ts_local, instance_id, source, payload_json = row
p = json.loads(payload_json)

tickers = p.get("tickers", [])
count = p.get("count", len(tickers))
raw = p.get("raw_count", "?")
pmin = p.get("price_min")
pmax = p.get("price_max")
open_prices = p.get("open_prices", {})

print(f"Strategi:    {source}")
print(f"Maskine:     {instance_id}")
print(f"Tidspunkt:   {ts_local}")
print(f"Prisfilter:  ${pmin}-${pmax}")
print(f"Univers:     {count} tilbage af {raw} rå fra TradingView")
print(f"Aktier:      {', '.join(tickers) if tickers else '(ingen)'}")

if open_prices:
    print("\nÅbningspriser for de tilbageværende:")
    for t in tickers:
        pr = open_prices.get(t)
        if pr is not None:
            print(f"  {t:<8} ${pr:.2f}")

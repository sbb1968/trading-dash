"""Vis alle handler fra i dag — sorteret efter tid."""
import sqlite3
from datetime import date

c = sqlite3.connect("trading_dash.db")
today = date.today().isoformat()

print(f"=== Handler for {today} ===")
print()

q = """
SELECT ts_local, event_type, symbol, payload_json
FROM events
WHERE ts_local LIKE ? || '%'
  AND event_type IN ('position_opened', 'position_closed', 'order_rejected')
ORDER BY id
"""

for r in c.execute(q, (today,)):
    ts = r[0][11:19]  # kun tid, ikke dato
    print(f"  {ts}  {r[1]:18}  {r[2] or '':6}  {r[3]}")
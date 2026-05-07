import sqlite3

c = sqlite3.connect("trading_dash.db")
for r in c.execute("SELECT id, ts_local, source, event_type, symbol, payload_json FROM events WHERE id > 23 ORDER BY id"):
    print(r)
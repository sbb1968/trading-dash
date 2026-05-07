import sqlite3

c = sqlite3.connect("trading_dash.db")

print("=== Events fordelt på type ===")
for r in c.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY 2 DESC"):
    print(f"  {r[1]:>4}  {r[0]}")

print()
print("=== Lukkede positioner ===")
for r in c.execute("SELECT ts_local, symbol, payload_json FROM events WHERE event_type='position_closed' ORDER BY id"):
    print(f"  {r[0]}  {r[1]}  {r[2]}")

print()
print("=== Afviste ordrer ===")
for r in c.execute("SELECT ts_local, symbol, payload_json FROM events WHERE event_type='order_rejected' ORDER BY id"):
    print(f"  {r[0]}  {r[1]}  {r[2]}")
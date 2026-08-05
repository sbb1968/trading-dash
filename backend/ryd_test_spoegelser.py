"""Fjern spoegelses-ordrer og -handler skabt af test_manual_trade_endpoints.py.

Testen mocker IBKR men skriver i den RIGTIGE ordre-log og database. Fejler den
undervejs, ryddes der ikke op, og der bliver en aaben position i journalen som
IBKR ikke kender.

Kendetegn (testens egne fixtures):  CLOV @ 2.50  ·  AMC @ 4.20  ·  source manual
Koer uden argument = toerloeb. Koer med  --slet  for at gennemfoere.
"""
import json, sqlite3, sys, shutil
from pathlib import Path

SLET = "--slet" in sys.argv
FIX = {("CLOV", 2.50), ("AMC", 4.20), ("GME", 15.50)}

log_sti = Path("orders_log.json")
log = json.loads(log_sti.read_text(encoding="utf-8")) if log_sti.exists() else []
o_kand = [e for e in log
          if e.get("ticker") in ("CLOV", "AMC", "GME")
          and e.get("source") == "manual"
          and e.get("status") == "Submitted"
          and not e.get("filled")]
print(f"ORDRE-LOG: {len(log)} poster — {len(o_kand)} spoegelser")
for e in o_kand:
    print(f"   {e['placed_at'][:19]}  {e['action']:4s} {e['shares']}x {e['ticker']}  id={e['order_id']}")

c = sqlite3.connect("trading_dash.db"); c.row_factory = sqlite3.Row
t_kand = [dict(r) for r in c.execute(
    "SELECT trade_id,symbol,shares,entry_price,exit_price,exit_time_utc,entry_time_et "
    "FROM trades WHERE source='manual' AND symbol IN ('CLOV','AMC','GME')")
    if (r["symbol"], r["entry_price"]) in FIX]
print(f"\nTRADES: {len(t_kand)} spoegelses-handel(er)")
for t in t_kand:
    aaben = "AABEN" if t["exit_time_utc"] is None else "lukket"
    print(f"   {t['entry_time_et'][:19]}  {t['shares']}x {t['symbol']:5s} "
          f"entry={t['entry_price']}  {aaben}  {t['trade_id'][:8]}")

if not SLET:
    print("\n>>> TOERLOEB. Intet aendret. Koer med  --slet  for at gennemfoere.")
    sys.exit(0)

if o_kand:
    shutil.copy(log_sti, str(log_sti) + ".bak")
    rest = [e for e in log if e not in o_kand]
    log_sti.write_text(json.dumps(rest, indent=2, default=str), encoding="utf-8")
    print(f"\nSLETTET {len(o_kand)} ordre(r). Backup: orders_log.json.bak")
if t_kand:
    for t in t_kand:
        c.execute("DELETE FROM trades WHERE trade_id=?", (t["trade_id"],))
        c.execute("DELETE FROM events WHERE symbol=? AND source='manual' "
                  "AND json_extract(payload_json,'$.trade_id')=?", (t["symbol"], t["trade_id"]))
    c.commit()
    print(f"SLETTET {len(t_kand)} handel(er) + tilhoerende events")
print("\nFaerdig.")

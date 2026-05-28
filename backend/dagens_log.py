"""
dagens_log.py — fuldt overblik over dagens journal-events.

Brug:
    python dagens_log.py                # alle strategier, i dag
    python dagens_log.py Konfluens      # filtrer på én strategi
    python dagens_log.py Konfluens 2026-05-27   # specifik dato

Viser i rækkefølge:
  1. Strategi-livscyklus (start/stop/emergency)
  2. Univers (Lag A): hvad blev valgt
  3. Handler: åbnede og lukkede positioner med P&L
  4. Lag C: trade forensics — hvad gik godt/skidt på hver handel
  5. Lag B: afvisninger — hvorfor entries IKKE blev taget
  6. Ordrer: godkendte, afviste, IBKR-fejl
  7. System-events: emergency stops, daily limit, IBKR connect-problemer
"""
import sqlite3
import json
import sys
from collections import defaultdict
from datetime import date

# -------- argumenter --------
strategy_filter = None
date_filter = date.today().isoformat()

for arg in sys.argv[1:]:
    if arg.count("-") == 2 and len(arg) == 10:  # ser ud som dato
        date_filter = arg
    else:
        strategy_filter = arg

# -------- forbind --------
conn = sqlite3.connect("trading_dash.db")
conn.row_factory = sqlite3.Row

# Find alt fra valgt dato. ts_local er ISO med tidszone, så vi matcher på prefix.
where = ["ts_local LIKE ?"]
params = [f"{date_filter}%"]
if strategy_filter:
    where.append("source LIKE ?")
    params.append(f"%{strategy_filter}%")

base_sql = f"""
    SELECT id, ts_local, instance_id, source, event_type, symbol, payload_json
    FROM events
    WHERE {' AND '.join(where)}
    ORDER BY id ASC
"""

rows = conn.execute(base_sql, params).fetchall()

if not rows:
    print(f"Ingen events fundet for {date_filter}"
          + (f" / {strategy_filter}" if strategy_filter else ""))
    sys.exit(0)

# Grupper efter event_type
by_type = defaultdict(list)
for r in rows:
    by_type[r["event_type"]].append(r)

# -------- helpers --------
def t(ts):
    # "2026-05-27T11:31:28.592215+02:00" -> "11:31:28"
    return ts.split("T")[1][:8] if "T" in ts else ts

def p(row):
    try:
        return json.loads(row["payload_json"]) if row["payload_json"] else {}
    except Exception:
        return {}

def section(title):
    print(f"\n{'='*72}\n  {title}\n{'='*72}")

def empty(name):
    print(f"  (ingen {name})")

# -------- 1. Livscyklus --------
section(f"DAGENS LOG — {date_filter}"
        + (f" — {strategy_filter}" if strategy_filter else ""))
print(f"Maskine: {rows[0]['instance_id']}   Events i alt: {len(rows)}")

section("1. STRATEGI-LIVSCYKLUS")
for ev in by_type.get("strategy_started", []) + \
          by_type.get("strategy_stopped", []) + \
          by_type.get("strategy_emergency_stop", []):
    kind = ev["event_type"].replace("strategy_", "").upper()
    print(f"  {t(ev['ts_local'])}  {ev['source']:<15} {kind}")
if not any(by_type.get(k) for k in ("strategy_started","strategy_stopped","strategy_emergency_stop")):
    empty("livscyklus-events")

# -------- 2. Univers (Lag A) --------
section("2. UNIVERS (Lag A)")
for ev in by_type.get("universe_selected", []):
    d = p(ev)
    tickers = d.get("tickers", [])
    raw = d.get("raw_count", "?")
    print(f"  {t(ev['ts_local'])}  {ev['source']}: {len(tickers)} af {raw} rå")
    print(f"     {', '.join(tickers)}")
if not by_type.get("universe_selected"):
    empty("universe-events")

# -------- 3. Handler --------
section("3. HANDLER")
opens = by_type.get("position_opened", [])
closes = by_type.get("position_closed", [])
print(f"  Åbnede: {len(opens)}   Lukkede: {len(closes)}")

total_pnl = 0.0
wins = losses = 0
for ev in closes:
    d = p(ev)
    pnl = d.get("pnl_usd") or d.get("pnl") or 0
    try:
        pnl = float(pnl)
    except Exception:
        pnl = 0
    total_pnl += pnl
    if pnl > 0: wins += 1
    elif pnl < 0: losses += 1
    sym = ev["symbol"] or d.get("symbol", "?")
    reason = d.get("exit_reason") or d.get("reason", "")
    print(f"  {t(ev['ts_local'])}  {sym:<6} P&L ${pnl:>+8.2f}  ({reason})")

if closes:
    print(f"\n  TOTAL: ${total_pnl:+.2f}   Wins: {wins}   Losses: {losses}"
          + (f"   Win rate: {wins/(wins+losses)*100:.1f}%" if (wins+losses) else ""))
elif not opens:
    empty("handler")

# -------- 4. Trade forensics (Lag C) --------
section("4. TRADE FORENSICS (Lag C)")
forensics = by_type.get("trade_forensics", [])
if forensics:
    print(f"  {len(forensics)} forensics-events. Vis detaljer? Brug:")
    print(f"     python dagens_log.py {strategy_filter or ''} {date_filter} --forensics")
    for ev in forensics[:5]:
        d = p(ev)
        sym = ev["symbol"] or d.get("symbol", "?")
        verdict = d.get("verdict") or d.get("summary", "")
        print(f"  {t(ev['ts_local'])}  {sym:<6} {verdict[:80]}")
    if len(forensics) > 5:
        print(f"  ... og {len(forensics)-5} mere")
else:
    empty("forensics")

# -------- 5. Afvisninger (Lag B) --------
section("5. ENTRY-AFVISNINGER (Lag B)")
rejects = by_type.get("entry_rejected", [])
if rejects:
    # Aggreger på (symbol, reason) så vi ikke drukner i støj
    agg = defaultdict(int)
    for ev in rejects:
        d = p(ev)
        sym = ev["symbol"] or d.get("symbol", "?")
        reason = d.get("reason") or d.get("detail") or "?"
        agg[(sym, reason[:60])] += 1
    print(f"  {len(rejects)} afvisninger på {len(agg)} unikke kombinationer:")
    for (sym, reason), n in sorted(agg.items(), key=lambda x: -x[1])[:20]:
        print(f"    {sym:<6} ×{n:<4} {reason}")
    if len(agg) > 20:
        print(f"    ... og {len(agg)-20} flere kombinationer")
else:
    empty("afvisninger")

# -------- 6. Ordrer --------
section("6. ORDRER")
approved = by_type.get("order_approved", [])
order_rej = by_type.get("order_rejected", [])
placed = by_type.get("ibkr_order_placed", [])
errors = by_type.get("ibkr_order_error", [])
print(f"  Godkendte: {len(approved)}   Afviste: {len(order_rej)}   "
      f"Placeret hos IBKR: {len(placed)}   IBKR-fejl: {len(errors)}")
for ev in errors:
    d = p(ev)
    sym = ev["symbol"] or "?"
    msg = d.get("error") or d.get("message", "?")
    print(f"    FEJL  {t(ev['ts_local'])}  {sym}: {msg[:100]}")

# -------- 7. System-events --------
section("7. SYSTEM-EVENTS")
sys_events = (by_type.get("emergency_stop", []) +
              by_type.get("daily_limit_reached", []) +
              by_type.get("ibkr_connect_attempt", []))
if sys_events:
    for ev in sys_events:
        d = p(ev)
        msg = d.get("message") or d.get("reason") or json.dumps(d)[:80]
        print(f"  {t(ev['ts_local'])}  {ev['event_type']:<25} {msg}")
else:
    empty("system-events")

# -------- 8. Diagnostik (Lag C — daily_diagnostics) --------
section("8. DIAGNOSTIK (Lag C)")
diags = by_type.get("daily_diagnostics", [])
if diags:
    for ev in diags:
        d = p(ev)
        reason = d.get("shutdown_reason", "?")
        print(f"  {t(ev['ts_local'])}  {ev['source']}  (stop: {reason})")
        evals = d.get("evaluations", 0)
        scored = d.get("scored_bars", 0)
        entries = d.get("entries", 0)
        trades = d.get("trades", 0)
        usize = d.get("universe_size", 0)
        print(f"     Univers:     {usize} aktier")
        print(f"     Evalueringer: {evals}   Scorede bars: {scored}")
        print(f"     Entries:     {entries}   Handler: {trades}")
        peak = d.get("peak_score")
        if peak is not None:
            print(f"     Peak score:  {peak}/6")
        mm = d.get("most_missing_condition")
        if mm:
            print(f"     Mangler oftest: {mm}")
        mbc = d.get("missing_by_condition")
        if mbc:
            print(f"     Pr. betingelse (antal bars hvor den manglede):")
            for cond, n in mbc.items():
                print(f"        {cond:<14} {n}")
        msp = d.get("max_score_per_ticker")
        if msp:
            print(f"     Max score pr. ticker:")
            for tk, sc in sorted(msp.items(), key=lambda x: -x[1]):
                print(f"        {tk:<8} {sc}/6")
        # Sanity-tjek: nul evalueringer trods univers = bar-feed mistanke
        if usize > 0 and evals == 0:
            print(f"     ⚠ ADVARSEL: {usize} aktier i universet men 0 evalueringer.")
            print(f"       → Mistanke om bar-feed-problem (ingen nye bars nåede frem).")
        elif evals > 0 and entries == 0 and (peak is not None and peak >= 5):
            print(f"     ⚠ NB: Evaluerede men ingen entries, trods peak score {peak}/6.")
            print(f"       → Mulig signal-divergens vs Pine (kun 1 betingelse manglede).")
else:
    empty("diagnostik")
    print("     (Strategien stoppede aldrig pænt — eller try/finally-patch mangler.)")

# -------- 9. Heartbeat (Vej 1 — periodiske snapshots) --------
section("9. HEARTBEAT (periodiske snapshots)")
beats = by_type.get("diagnostics_heartbeat", [])
if beats:
    print(f"  {len(beats)} heartbeats. Seneste 8:")
    for ev in beats[-8:]:
        d = p(ev)
        print(f"  {t(ev['ts_local'])}  evals={d.get('evaluations',0):<5} "
              f"scored={d.get('scored_bars',0):<5} entries={d.get('entries',0):<3} "
              f"pos={d.get('open_positions','?')}")
else:
    empty("heartbeats")
    print("     (Heartbeat-logging ikke aktiveret endnu — se patch.)")

print()

"""
sammenlign_pine.py
──────────────────
Sammenlign Pine-script entry-tidspunkter med Konfluens' bar-data
for at isolere om forskellen er bar-feed (Konfluens "så" ikke baren)
eller signal-divergens (Konfluens så baren men evaluerede den anderledes).

Sådan bruger du den:

  1. Tilføj entry-tidspunkter fra Pine-scriptet i PINE_ENTRIES nedenfor
     (ticker, tidspunkt i ET, og din kommentar).

  2. Kør:  python sammenlign_pine.py

  Den vil for hver Pine-entry:
   (a) hente 5min-baren fra IBKR via Konfluens' egen forbindelse
   (b) finde universet for dagen og vise om aktien overhovedet var i det
   (c) lede efter en 'bar_evaluation'-event i journalen for samme bar
       (kun relevant når vi har tilføjet bar-level logging — indtil da
        siger den 'ingen evaluering logget', hvilket bekræfter at vi
        skal tilføje den logging)

Det viser dig sort på hvidt: var QTTB i universet kl. 12:30 ET? Hentede
Konfluens den bar? Evaluerede den? Med hvilken score?
"""
import sqlite3
import json
import sys
from datetime import date, datetime
from typing import Optional

# ─── Indtast Pine-entries her ────────────────────────────────────────
# Format: (ticker, dato, tidspunkt_ET_HH:MM, retning, kommentar)
PINE_ENTRIES = [
    ("QTTB", "2026-05-27", "09:35", "long",  "S=5 [TV·HCL] entry +134"),
    ("QTTB", "2026-05-27", "11:00", "exit",  "SX=3 [OL·E·] exit -134"),
    ("QTTB", "2026-05-27", "12:30", "long",  "S=4 [TV·HC·] entry +210"),
    ("QTTB", "2026-05-27", "13:30", "exit",  "SX=3 [O··EV] exit -210"),
]


def find_universe(conn: sqlite3.Connection, dato: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT ts_local, payload_json FROM events
           WHERE event_type='universe_selected'
             AND source LIKE '%Konfluens%'
             AND ts_local LIKE ?
           ORDER BY id DESC LIMIT 1""",
        (f"{dato}%",),
    ).fetchone()
    if not row:
        return None
    p = json.loads(row[1])
    return {"ts_local": row[0], "tickers": p.get("tickers", []),
            "open_prices": p.get("open_prices", {})}


def find_bar_eval(conn: sqlite3.Connection, ticker: str, dato: str,
                  tid_et: str) -> Optional[dict]:
    """Søg efter bar_evaluation-event nær det givne tidspunkt.
       Returnerer None hvis bar-level logging ikke er aktiveret endnu."""
    rows = conn.execute(
        """SELECT ts_local, payload_json FROM events
           WHERE event_type='bar_evaluation'
             AND symbol=?
             AND ts_local LIKE ?
           ORDER BY id""",
        (ticker, f"{dato}%"),
    ).fetchall()
    if not rows:
        return None
    # Find tættest på tid_et — simpel match på ET-time
    target_h, target_m = map(int, tid_et.split(":"))
    target_min = target_h * 60 + target_m
    best = None
    best_diff = 999
    for ts_local, payload_json in rows:
        p = json.loads(payload_json)
        bar_et = p.get("bar_time_et", "")
        if not bar_et or ":" not in bar_et:
            continue
        try:
            h, m = map(int, bar_et.split(":")[:2])
        except ValueError:
            continue
        diff = abs((h * 60 + m) - target_min)
        if diff < best_diff:
            best_diff = diff
            best = (ts_local, p, bar_et)
    if best and best_diff <= 5:
        return {"journal_ts": best[0], "bar_et": best[2], "payload": best[1]}
    return None


def main():
    conn = sqlite3.connect("trading_dash.db")

    print("=" * 72)
    print("Sammenligning: Pine-entries vs Konfluens-data")
    print("=" * 72)

    for ticker, dato, tid_et, retning, kommentar in PINE_ENTRIES:
        print(f"\n── {ticker} {dato} {tid_et} ET ({retning}) ──")
        print(f"   Pine: {kommentar}")

        univ = find_universe(conn, dato)
        if not univ:
            print(f"   ⚠ Intet univers fundet for {dato}")
            continue

        if ticker not in univ["tickers"]:
            print(f"   ❌ {ticker} var IKKE i universet ({len(univ['tickers'])} aktier)")
            print(f"      → Konfluens kunne aldrig handle den uanset signal")
            continue

        open_price = univ["open_prices"].get(ticker)
        op_str = f"${open_price:.2f}" if open_price else "?"
        print(f"   ✓ {ticker} var i universet (åbning: {op_str})")

        eval_result = find_bar_eval(conn, ticker, dato, tid_et)
        if eval_result is None:
            print(f"   ⚠ Ingen 'bar_evaluation'-event fundet for dette tidspunkt.")
            print(f"     Det betyder ÉN af to ting:")
            print(f"       (a) Bar-level logging er ikke aktiveret endnu  ← mest sandsynligt")
            print(f"       (b) Konfluens evaluerede aldrig denne bar       ← det vi mistænker")
            print(f"     Tilføj bar_evaluation-logging i Konfluens for at skelne.")
        else:
            p = eval_result["payload"]
            print(f"   ✓ Bar evalueret kl. {eval_result['bar_et']} ET")
            print(f"      Status:     {p.get('status', '?')}")
            print(f"      Score:      {p.get('score', '?')}/6")
            print(f"      Short form: {p.get('short_form', '?')}")
            print(f"      Reason:     {p.get('reason', '?')}")
            if p.get("status") == "signal" and retning == "long":
                print(f"      → MATCH: Konfluens ville også have taget entry her")
            elif retning == "long":
                print(f"      → DIVERGENS: Pine tog entry, Konfluens afviste")

    print()


if __name__ == "__main__":
    main()

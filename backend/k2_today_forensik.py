#!/usr/bin/env python3
"""
k2_today_forensik.py — hvorfor blev K2 datablind i dag? (READ-ONLY)
===================================================================
Genskaber dagens K2-tidslinje fra trading_dash.db. Noeglesignalet er HULLET i
bar_evaluation-stroemmen: K2 journaliserer hver bar-evaluering, saa den datablinde
periode = perioden UDEN bar_evaluation-events. Robust uanset om timeout'ene blev
logget. Viser ogsaa status-skift, datablind-logs og dagens handler.

Strengt read-only (sqlite mode=ro) — sikker ved siden af den koerende K2.
Skema: events(ts_utc, ts_local, source, event_type, symbol, payload_json),
       trades(source, symbol, side, entry_time_et, exit_time_et, pnl, ...).

    python k2_today_forensik.py
    python k2_today_forensik.py --source "Konfluens 2" --gap-min 3
"""
from __future__ import annotations
import argparse, json, sqlite3
from datetime import date, datetime, timedelta


def ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def pts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        try:
            return datetime.fromisoformat(str(s)[:26])
        except ValueError:
            return None


def hm(dt):
    return dt.strftime("%H:%M:%S") if dt else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="trading_dash.db")
    ap.add_argument("--source", default="Konfluens 2")
    ap.add_argument("--day", default=None)
    ap.add_argument("--gap-min", type=float, default=3.0, help="min huller i bar-stroem (minutter)")
    ap.add_argument("--limit", type=int, default=20000)
    a = ap.parse_args()
    day = date.fromisoformat(a.day) if a.day else date.today()
    con = ro(a.db)

    print("=" * 80)
    print(f"  K2 DAGENS-FORENSIK · source='{a.source}' · dag {day} · READ-ONLY")
    print("=" * 80)

    rows = con.execute(
        'SELECT ts_local, ts_utc, source, event_type, symbol, payload_json '
        'FROM events ORDER BY id DESC LIMIT ?', (a.limit,)).fetchall()

    evs = []
    for r in rows:
        if a.source and r["source"] != a.source:
            continue
        t = pts(r["ts_local"]) or pts(r["ts_utc"])
        if not t or t.date() != day:
            continue
        try:
            pl = json.loads(r["payload_json"]) if r["payload_json"] else {}
            if not isinstance(pl, dict):
                pl = {}
        except (json.JSONDecodeError, TypeError):
            pl = {}
        evs.append((t, r["event_type"] or "", r["symbol"] or "", pl))
    evs.sort(key=lambda x: x[0])

    if not evs:
        print(f"\n  INGEN '{a.source}'-events for {day} i de seneste {a.limit} rows.")
        print("  -> haev --limit, eller K2 loggede ikke til DB i dag.")
        return 0

    by_type = {}
    for _, ty, _, _ in evs:
        by_type[ty] = by_type.get(ty, 0) + 1
    print(f"\n  K2-events i dag: {len(evs)}   {hm(evs[0][0])} .. {hm(evs[-1][0])}")
    print("  Pr. type: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1])))

    # ── HOVEDSIGNAL: huller i bar_evaluation-stroemmen = datablinde vinduer ──
    print("\n" + "-" * 80)
    print(f"  BAR-EVALUERINGS-STROEM  (huller > {a.gap_min:g} min = DATABLIND)")
    print("-" * 80)
    bars = [t for t, ty, _, _ in evs if ty == "bar_evaluation"]
    if not bars:
        print("   (ingen bar_evaluation-events — K2 evaluerede aldrig; data nede fra start?)")
    else:
        print(f"   Foerste bar-eval: {hm(bars[0])}   Sidste: {hm(bars[-1])}   Antal: {len(bars)}")
        gaps = []
        for i in range(1, len(bars)):
            d = (bars[i] - bars[i - 1]).total_seconds() / 60.0
            if d > a.gap_min:
                gaps.append((bars[i - 1], bars[i], d))
        if gaps:
            print(f"\n   DATABLINDE VINDUER ({len(gaps)}):")
            for s, e, d in gaps:
                print(f"      {hm(s)}  →  {hm(e)}   ({d:.0f} min uden bar-evaluering)")
            big = max(gaps, key=lambda g: g[2])
            print(f"\n   => Stoerste hul: {hm(big[0])} → {hm(big[1])} ({big[2]:.0f} min). "
                  f"Det er den datablinde periode.")
        else:
            print("   Ingen huller — K2 evaluerede kontinuerligt (ingen datablind periode i dag).")

    # ── status-skift ──
    print("\n" + "-" * 80)
    print("  STATUS-SKIFT  (start / koerer / stop / fejl)")
    print("-" * 80)
    sts = [(t, pl.get("status", ""), pl.get("message", "")) for t, ty, _, pl in evs if ty == "status"]
    if sts:
        for t, s, m in sts:
            print(f"   {hm(t)}  [{s:<10}] {str(m)[:88]}")
    else:
        print("   (ingen status-events i vinduet)")

    # ── datablind-noegleord paa tvaers af events ──
    print("\n" + "-" * 80)
    print("  DATABLIND-LOGS  (timeout / svarede ikke / historical / reconnect / 162)")
    print("-" * 80)
    kw = ("timeout", "svarede ikke", "historical", "historik", "reconnect",
          "genforbind", "162", "datablind", "pacing", "farm")
    hits = []
    for t, ty, sym, pl in evs:
        blob = json.dumps(pl, ensure_ascii=False).lower()
        if any(k in blob for k in kw):
            hits.append((t, ty, pl.get("message") or pl.get("reason") or json.dumps(pl, ensure_ascii=False)[:80]))
    if hits:
        for t, ty, m in hits[:40]:
            print(f"   {hm(t)}  [{ty:<14}] {str(m)[:80]}")
        if len(hits) > 40:
            print(f"   ... (+{len(hits) - 40} flere)")
    else:
        print("   INGEN — timeout'ene var konsol-only (data-laget under journaliseringen).")
        print("   Bar-evaluerings-hullet ovenfor er da det praecise datablinde vindue.")

    # ── dagens handler ──
    print("\n" + "-" * 80)
    print("  HANDLER I DAG")
    print("-" * 80)
    tr = con.execute(
        'SELECT source, symbol, side, shares, entry_time_et, entry_price, '
        'exit_time_et, exit_price, exit_reason, pnl, current_price, current_stage '
        'FROM trades ORDER BY rowid DESC LIMIT 200').fetchall()
    n = 0
    for r in tr:
        if a.source and r["source"] != a.source:
            continue
        et = pts(r["entry_time_et"])
        if not et or et.date() != day:
            continue
        n += 1
        if r["exit_time_et"]:
            print(f"   {hm(et)} ET  {r['symbol']:<6} {r['side']:<5} {r['shares']}stk "
                  f"@ {r['entry_price']}  → exit {r['exit_price']} ({r['exit_reason']})  "
                  f"P&L {r['pnl']}")
        else:
            print(f"   {hm(et)} ET  {r['symbol']:<6} {r['side']:<5} {r['shares']}stk "
                  f"@ {r['entry_price']}  ÅBEN  (nu {r['current_price']}, {r['current_stage']})")
    if n == 0:
        print("   (ingen handler i dag)")
    else:
        print(f"\n   I alt {n} handel/handler i dag.")
    print("-" * 80)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

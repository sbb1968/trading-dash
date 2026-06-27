#!/usr/bin/env python3
"""
trendjoin_forensik.py — hvad fandt Trend Join Long's 30-min-scan i dag? (READ-ONLY)
====================================================================================
Genskaber dagens top-% gainer-scanninger fra trading_dash.db. Strategien skriver ét
'trendjoin_scan'-event pr. 30-min-runde med ALLE fundne top % gainers, deres change%,
max-change/top-gainer, og verdikt pr. aktie (optaget / ingen_katalysator / under_gap /
d2_fejl / ...). Scriptet viser dem pænt, så du kan se præcis hvad pipelinen så hver runde
— inkl. max-change hver 30. min — plus hvem der blev optaget og dagens handler.

Strengt read-only (sqlite mode=ro) — sikker ved siden af den kørende strategi.
Skema: events(ts_utc, ts_local, source, event_type, symbol, payload_json),
       trades(source, symbol, side, entry_time_et, exit_time_et, pnl, ...).

    python trendjoin_forensik.py
    python trendjoin_forensik.py --day 2026-06-27
    python trendjoin_forensik.py --full        # vis ALLE gainers pr. scan (ikke kun top 15)

Placering: C:\\Projects\\trading_dash\\backend\\trendjoin_forensik.py
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime

SOURCE = "Trend Join Long"

# Verdikt → kort etiket (rækkefølge styrer ikke noget; bare pæn visning)
VERDICT_LABEL = {
    "OPTAGET":              "✅ OPTAGET",
    "ingen_katalysator":   "📰✗ ingen katalysator",
    "d2_fejl_under_sma200":"📉 under SMA200 (D2)",
    "mangler_daglig_data": "❓ mangler daglig data",
    "under_gap":           "· under gap-tærskel",
    "under_pris":          "· under min-pris",
    "tidl_afvist":         "· tidl. afvist i dag",
    "i_pulje":             "↺ allerede i pulje",
    "handlet_i_dag":       "✓ handlet i dag",
}


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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Trend Join Long — 30-min-scan-forensik (read-only)")
    ap.add_argument("--db", default="trading_dash.db")
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default i dag)")
    ap.add_argument("--full", action="store_true", help="vis ALLE gainers pr. scan (ikke kun top 15)")
    ap.add_argument("--top", type=int, default=15, help="antal gainers vist pr. scan (default 15)")
    ap.add_argument("--limit", type=int, default=40000)
    a = ap.parse_args()
    day = date.fromisoformat(a.day) if a.day else date.today()

    con = ro(a.db)
    print("=" * 84)
    print(f"  TREND JOIN LONG — SCAN-FORENSIK · source='{a.source}' · dag {day} · READ-ONLY")
    print("=" * 84)

    rows = con.execute(
        "SELECT ts_local, ts_utc, source, event_type, payload_json "
        "FROM events ORDER BY id DESC LIMIT ?", (a.limit,)).fetchall()

    scans = []
    for r in rows:
        if r["source"] != a.source or r["event_type"] != "trendjoin_scan":
            continue
        t = pts(r["ts_local"]) or pts(r["ts_utc"])
        if not t or t.date() != day:
            continue
        try:
            pl = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except (json.JSONDecodeError, TypeError):
            pl = {}
        if isinstance(pl, dict):
            scans.append((t, pl))
    scans.sort(key=lambda x: x[0])

    if not scans:
        print(f"\n  INGEN 'trendjoin_scan'-events for {day}.")
        print("  -> strategien kørte ikke i dag, eller den loggede ikke (hæv --limit / tjek --day).")
        _print_trades(con, a.source, day)
        con.close()
        return 0

    # ── Per-scan tidslinje (max-change hver 30. min + gapper-verdikter) ──
    show_n = 10_000 if a.full else a.top
    freq: dict[str, dict] = {}
    for t, pl in scans:
        gappers = pl.get("gappers") or []
        mc = pl.get("max_change")
        top = pl.get("top_gapper") or "—"
        added = pl.get("added") or []
        mc_s = f"{mc:+.1f}% ({top})" if mc is not None else "—"
        print("\n" + "─" * 84)
        print(f"  🔁 {hm(t)} ET · {pl.get('num_gappers', len(gappers))} gainers · "
              f"MAX-CHANGE {mc_s} · {len(added)} optaget · pulje {pl.get('universe_size','?')}")
        print("─" * 84)
        # sortér efter change faldende (som scanneren)
        gl = sorted(gappers, key=lambda g: -(g.get("change") or 0))
        for g in gl[:show_n]:
            sym = g.get("sym", "?")
            ch = g.get("change", 0)
            px = g.get("price", 0)
            verdict = VERDICT_LABEL.get(g.get("verdict", ""), g.get("verdict", ""))
            detail = g.get("detail", "")
            line = f"   {sym:<6} {ch:>+7.1f}%  ${px:>8.2f}   {verdict}"
            if detail:
                line += f"   «{detail[:70]}»"
            print(line)
            # frekvens-optælling
            f = freq.setdefault(sym, {"count": 0, "max_change": ch, "optaget": False,
                                      "last_verdict": g.get("verdict", "")})
            f["count"] += 1
            f["max_change"] = max(f["max_change"], ch)
            f["last_verdict"] = g.get("verdict", "")
            if g.get("verdict") == "OPTAGET":
                f["optaget"] = True
        if len(gl) > show_n:
            print(f"   ... (+{len(gl) - show_n} flere — kør med --full for alle)")

    # ── Dagens max-change-kurve (én linje pr. scan) ──
    print("\n" + "=" * 84)
    print("  MAX-CHANGE PR. 30-MIN-SCAN")
    print("=" * 84)
    for t, pl in scans:
        mc = pl.get("max_change")
        mc_s = f"{mc:>+6.1f}% ({pl.get('top_gapper','—')})" if mc is not None else "    — "
        print(f"   {hm(t)}   max {mc_s:<22} gainers {pl.get('num_gappers','?'):>3}   "
              f"optaget {len(pl.get('added') or [])}")

    # ── Top-gainer-frekvens (hvilke navne dukkede oftest op + blev de optaget) ──
    print("\n" + "=" * 84)
    print("  TOP-GAINER-FREKVENS I DAG  (unikke navne set på tværs af scans)")
    print("=" * 84)
    ordered = sorted(freq.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["max_change"]))
    optaget_n = sum(1 for _, f in ordered if f["optaget"])
    print(f"   {len(ordered)} unikke gainers · {optaget_n} blev OPTAGET\n")
    for sym, f in ordered[:40]:
        flag = "✅" if f["optaget"] else "  "
        print(f"   {flag} {sym:<6} set {f['count']:>2}x · max {f['max_change']:>+6.1f}% · "
              f"sidst: {VERDICT_LABEL.get(f['last_verdict'], f['last_verdict'])}")
    if len(ordered) > 40:
        print(f"   ... (+{len(ordered) - 40} flere)")

    _print_trades(con, a.source, day)
    con.close()
    return 0


def _print_trades(con, source, day):
    print("\n" + "=" * 84)
    print("  HANDLER I DAG")
    print("=" * 84)
    try:
        tr = con.execute(
            "SELECT source, symbol, side, shares, entry_time_et, entry_price, "
            "exit_time_et, exit_price, exit_reason, pnl, current_price, current_stage "
            "FROM trades ORDER BY rowid DESC LIMIT 300").fetchall()
    except sqlite3.Error as e:
        print(f"   (kunne ikke læse trades: {e})")
        return
    n = 0
    for r in tr:
        if r["source"] != source:
            continue
        et = pts(r["entry_time_et"])
        if not et or et.date() != day:
            continue
        n += 1
        if r["exit_time_et"]:
            print(f"   {hm(et)} ET  {r['symbol']:<6} {r['side']:<5} {r['shares']}stk "
                  f"@ {r['entry_price']} → exit {r['exit_price']} ({r['exit_reason']})  P&L {r['pnl']}")
        else:
            print(f"   {hm(et)} ET  {r['symbol']:<6} {r['side']:<5} {r['shares']}stk "
                  f"@ {r['entry_price']}  ÅBEN (nu {r['current_price']}, {r['current_stage']})")
    if n == 0:
        print("   (ingen handler i dag)")
    else:
        print(f"\n   I alt {n} handel/handler i dag.")


if __name__ == "__main__":
    raise SystemExit(main())

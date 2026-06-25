#!/usr/bin/env python3
"""
manual_reconcile.py — manuel reconcile af en strategis aabne positioner
═══════════════════════════════════════════════════════════════════════
Sikrer at K2 (eller Europa-reversion / BuyTheDip) kan starte UDEN gamle aabne
positioner fra en tidligere session — uden at vente paa strategiens egen opstarts-
reconcile. Den GENBRUGER strategiens EGEN `_reconcile_orphans()` (bit-for-bit samme
adfaerd som opstarts-reconcile), saa der er ingen divergens: lukker praecis strategiens
egen andel (source=self.name, orderRef=self.name), markerer fantomer lukket, og roerer
ALDRIG positioner uden journal-spor (anden strategi/manuel).

TO tilstande:
  • DEFAULT (uden --execute) = PREVIEW, READ-ONLY: viser hvilke aabne rows der findes
    pr. strategi (filtrerbart paa --day) UDEN at forbinde til IBKR eller roere noget.
  • --execute = forbind til IBKR og koer den faktiske reconcile (lukker/markerer).

SIKKERHED:
  - Koer IKKE mens strategien handler. Tool'et tjekker backendens /status og afbryder
    hvis en strategi koerer (algo.running) — overstyr kun med --force.
  - Egen tilfaeldig client-id (IBKRConnection) -> kicker ikke backendens forbindelse.
  - Lukker KUN strategiens egne journal-rows; delt-aktiv-sikker.

Brug (fra backend/):
    python manual_reconcile.py                              # preview ALLE (read-only)
    python manual_reconcile.py --source K2                  # preview kun K2
    python manual_reconcile.py --source K2 --day 2026-06-23 # preview K2's aabne fra dagen
    python manual_reconcile.py --source K2 --execute        # LUK K2's aabne (faithful)
    python manual_reconcile.py --source all --execute       # ryd alle strategier

Placering: C:\\projects\\trading_dash\\backend\\manual_reconcile.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import json
import logging
import sqlite3
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

ALL_SOURCES = ["Konfluens 2", "Europa-reversion", "BuyTheDip"]
ALIASES = {
    "konfluens 2": "Konfluens 2", "konfluens": "Konfluens 2", "k2": "Konfluens 2",
    "europa-reversion": "Europa-reversion", "europa": "Europa-reversion",
    "eurev": "Europa-reversion", "eureversion": "Europa-reversion",
    "buythedip": "BuyTheDip", "btd": "BuyTheDip", "buy-the-dip": "BuyTheDip",
}


def resolve_sources(raw):
    if not raw or any(s.strip().lower() == "all" for s in raw):
        return list(ALL_SOURCES)
    out, unknown = [], []
    for s in raw:
        c = ALIASES.get(s.strip().lower())
        (out if c else unknown).append(c or s)
    if unknown:
        print(f"  Ukendt source: {', '.join(unknown)} — kendte: {ALL_SOURCES} (eller 'all')")
    return list(dict.fromkeys(out))


def open_rows(db, source, day=None):
    """Aabne journal-rows for en source (exit_time_et IS NULL), valgfrit filtreret paa
    entry-dag. READ-ONLY (egen mode=ro-forbindelse)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    sql = ("SELECT symbol, side, shares, entry_price, entry_time_et FROM trades "
           "WHERE source=? AND (exit_time_et IS NULL OR exit_time_et='') ")
    p = [source]
    if day:
        sql += "AND substr(entry_time_et,1,10)=? "
        p.append(day)
    sql += "ORDER BY entry_time_et"
    try:
        return con.execute(sql, p).fetchall()
    except sqlite3.Error as e:
        print(f"   (query-fejl: {e})")
        return []
    finally:
        con.close()


def preview(db, sources, day):
    print("=" * 74)
    print("  MANUEL RECONCILE — PREVIEW (read-only, roerer intet)")
    print("=" * 74)
    total = 0
    for src in sources:
        rows = open_rows(db, src, day)
        tag = f" · dag {day}" if day else ""
        print(f"\n  {src}{tag} — {len(rows)} aaben(e):")
        for r in rows:
            ep = f"@ {r['entry_price']} " if r["entry_price"] is not None else ""
            print(f"     {r['symbol']:<7} {r['side']:<6} {r['shares']} stk {ep}· entry {r['entry_time_et']}")
        if not rows:
            print("     (ingen)")
        total += len(rows)
    print(f"\n  I alt {total} aabne. Koer med --execute for at reconcile (luk/markér).")
    return total


def backend_algo_running():
    """True/False fra backendens /status (algo.running); None hvis ikke naaet."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
        return bool(d.get("algo", {}).get("running"))
    except Exception:
        return None


async def execute(db, sources, force):
    from accounts import load_identity
    from journal import Journal
    from ibkr_connect import IBKRConnection
    from strategy_base import StrategyConfig
    from algo_confluence2 import Confluence2Live
    from algo_europa_reversion import EuropaReversionLive
    from algo_buythedip import BuyTheDipLive

    KLASS = {
        "Konfluens 2":      Confluence2Live,
        "Europa-reversion": EuropaReversionLive,
        "BuyTheDip":        BuyTheDipLive,
    }

    # ── Sikkerhed: koerer en strategi lige nu? ──
    running = backend_algo_running()
    if running and not force:
        print("  ❌ AFBRUDT: en strategi koerer lige nu (backend algo.running=true).")
        print("     Stop den foerst i Studio/Live Algo — eller brug --force hvis bevidst.")
        return 1
    if running is None:
        print("  (backend /status ikke naaet — antager backend nede, fortsaetter)")
    elif force and running:
        print("  ⚠ --force: fortsaetter selvom en strategi koerer (kan konflikte).")

    identity = load_identity()
    print(f"  Konto: {identity.ibkr_account} ({'paper' if identity.paper_trading else 'LIVE'})")

    conn = IBKRConnection(paper_trading=identity.paper_trading)
    ok = await conn.connect()
    if not ok:
        print("  ❌ Kunne ikke forbinde til IBKR (er TWS/Gateway oppe paa rette port?).")
        return 1
    journal = Journal(db)
    await journal.init()        # AABNER aiosqlite-forbindelsen (_db) — ellers ser reconcile
                                # ingen aabne rows og behandler positionerne som fremmede.

    rc = 0
    try:
        for src in sources:
            before = len(open_rows(db, src))
            print(f"\n── {src}: {before} aaben(e) foer reconcile ──")
            if before == 0:
                print("   (intet at rydde op)")
                continue
            strat = KLASS[src](conn, StrategyConfig())
            strat._journal = journal           # samme wiring som StrategyManager
            if strat.name != src:
                print(f"   ⚠ navn-mismatch ({strat.name!r} != {src!r}) — springer over (sikkerhed)")
                rc = 1
                continue
            await strat._reconcile_orphans()   # strategiens EGEN reconcile (uaendret)
            after = len(open_rows(db, src))
            print(f"   -> {before - after} lukket/markeret · {after} stadig aaben")
            if after:
                print("   ⚠ stadig aabne — IBKR fyldte muligvis ikke (luk manuelt i TWS, "
                      "eller koer igen om lidt). Row forbliver aaben = ingen falsk bogfoering.")
                rc = 1
    finally:
        conn.disconnect()
        # Lad evt. afkoblings-trigget journal-skrivning (sen ordre-status-callback) lande
        # MENS journalen stadig er aaben -> undgaa "Cannot operate on a closed database".
        try:
            await asyncio.sleep(0.4)
        except Exception:
            pass
        try:
            await journal.close()
        except Exception:
            pass
    return rc


def main():
    ap = argparse.ArgumentParser(
        description="Manuel reconcile af en strategis aabne positioner (default = read-only preview)")
    ap.add_argument("--source", nargs="+", default=["all"],
                    help="strategi(er): 'Konfluens 2'/K2, 'Europa-reversion'/eurev, BuyTheDip/btd, "
                         "eller 'all' (default)")
    ap.add_argument("--day", default=None,
                    help="filtrér preview paa entry-dag YYYY-MM-DD (paavirker IKKE --execute, "
                         "der reconcilerer ALLE aabne for kilden = faithful)")
    ap.add_argument("--db", default="trading_dash.db")
    ap.add_argument("--execute", action="store_true",
                    help="koer den faktiske reconcile (luk/markér) — uden dette = read-only preview")
    ap.add_argument("--force", action="store_true",
                    help="koer --execute selvom en strategi rapporteres koerende (algo.running)")
    a = ap.parse_args()

    sources = resolve_sources(a.source)
    if not sources:
        return 2

    if not a.execute:
        preview(a.db, sources, a.day)
        return 0

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.day:
        print("  NB: --execute reconcilerer ALLE aabne rows for kilden (faithful clean-start); "
              "--day filtrerer kun preview.")
    return asyncio.run(execute(a.db, sources, a.force))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
diag_cogt_orders.py - READ-ONLY diagnostik: aabne ordrer + positioner + journal.
═══════════════════════════════════════════════════════════════════════════════
Koeber/saelger/annullerer INTET. Egen random client-id (IBKRConnection) -> kicker
IKKE backendens forbindelse, kraever INGEN backend-genstart (samme princip som
manual_reconcile). Sikkert at koere midt i eksperimentet/handelsvinduet.

Viser bl.a. de hvilende STRATEGI-GTC-ordrer som /orders/list skjuler (det endpoint
er afgraenset til manuelle ordrer). Brug det til at se om der ligger 1 eller 2 SELL
paa COGT.

Koer paa ALGOSERVEREN fra backend/:
    python diag_cogt_orders.py
Send HELE outputtet retur.
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import sqlite3
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

DB_PATH = "trading_dash.db"
ACTIVE = {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted"}


def _dump_journal_open():
    """Aabne journal-rows (alle sources) — READ-ONLY (mode=ro)."""
    print("\n=== AABNE JOURNAL-ROWS (alle strategier) ===")
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT source, symbol, side, shares, entry_price, entry_time_et "
            "FROM trades WHERE exit_time_et IS NULL OR exit_time_et='' "
            "ORDER BY source, entry_time_et"
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        print(f"  (journal-laesning fejlede: {e})")
        return
    if not rows:
        print("  (ingen aabne rows)")
        return
    for r in rows:
        ep = f"@ {r['entry_price']}" if r["entry_price"] is not None else "@ ?"
        print(f"  [{r['source']}] {r['symbol']:<6} {r['side']:<6} {r['shares']} stk "
              f"{ep} · entry {r['entry_time_et']}")


async def main():
    from accounts import load_identity, aktiv_konto
    from ibkr_connect import IBKRConnection

    identity = load_identity()
    print("=" * 72)
    print("  COGT / ORDRE-DIAGNOSTIK (READ-ONLY — roerer intet)")
    print("=" * 72)
    now = datetime.now()
    try:
        from zoneinfo import ZoneInfo
        et = datetime.now(ZoneInfo("America/New_York"))
        dk = datetime.now(ZoneInfo("Europe/Copenhagen"))
        print(f"  Tid: {dk:%Y-%m-%d %H:%M} dansk / {et:%H:%M} ET")
    except Exception:
        print(f"  Tid: {now:%Y-%m-%d %H:%M} (lokal)")
    print(f"  Konto: {aktiv_konto()} ({'paper' if identity.paper_trading else 'LIVE'})")

    conn = IBKRConnection(paper_trading=identity.paper_trading)
    if not await conn.connect():
        print("  FEJL: kunne ikke forbinde til IBKR (TWS/Gateway oppe paa rette port?).")
        return 1
    try:
        ib = conn.ib

        # 1) ALLE aabne ordrer paa tvaers af klienter (inkl. strategi-GTC).
        try:
            await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), timeout=8)
        except Exception as e:
            print(f"  (reqAllOpenOrders fejlede: {type(e).__name__}: {e} — bruger openTrades)")
        trades = list(ib.openTrades() or [])
        print(f"\n=== AABNE ORDRER (alle klienter) — {len(trades)} ===")
        if not trades:
            print("  (ingen aabne ordrer)")
        for t in trades:
            o, s, c = t.order, t.orderStatus, t.contract
            flag = "  <-- AKTIV" if s.status in ACTIVE else ""
            print(f"  {c.symbol:<6} {o.action:<4} qty={o.totalQuantity:g} {o.orderType:<4} "
                  f"tif={o.tif:<3} status={s.status:<12} rest={s.remaining:g} "
                  f"ref={o.orderRef!r} orderId={o.orderId} permId={o.permId} "
                  f"clientId={o.clientId}{flag}")

        # 2) Positioner (netto pr. ticker).
        print("\n=== POSITIONER ===")
        pos = conn.get_positions()
        if not pos:
            print("  (ingen positioner)")
        for p in pos:
            if p.get("position"):
                print(f"  {p['ticker']:<6} {p['position']:+g} @ {p['avg_cost']:.4f}")

        # 3) COGT-snapshot (pris) — tolerant; pre-market giver tit timeout.
        print("\n=== COGT SNAPSHOT ===")
        try:
            snap = await asyncio.wait_for(conn.get_snapshot("COGT"), timeout=8)
            if snap:
                print(f"  last={snap.get('last')} bid={snap.get('bid')} "
                      f"ask={snap.get('ask')} vol={snap.get('volume')}")
            else:
                print("  (intet snapshot — marked lukket/ingen data)")
        except Exception as e:
            print(f"  (snapshot timeout/fejl: {type(e).__name__} — marked nok lukket)")

    finally:
        conn.disconnect()

    # 4) Journal (efter disconnect — ren sqlite-laesning, uafhaengig af IBKR).
    _dump_journal_open()

    print("\n=== FAERDIG — send hele outputtet retur ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

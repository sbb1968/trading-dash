#!/usr/bin/env python3
"""
cogt_flatten.py - flad den resterende COGT-position ud (oprydning efter over-sell).
═══════════════════════════════════════════════════════════════════════════════════
Den oprindelige over-sell efterlod en COGT-position (long ELLER short, afh. af hvor
oprydningen endte). Dette script LUKKER den med ÉN markedsordre i den modsatte retning.
Idempotent-reconcile-fixet (4ee58d7) forhindrer FREMTIDIGE over-sells, men rydder ikke
den EKSISTERENDE position — det goer dette.

SIKKERHED:
  - DEFAULT = PREVIEW: viser position + plan, sender INGEN ordre.
  - --execute = sender PRAECIS ÉN MKT-ordre der flader COGT ud (BUY hvis short, SELL hvis long).
  - Roerer KUN COGT. AFBRYDER hvis der hviler aktive COGT-ordrer (lad dem resolve / cancel
    foerst med cogt_fix_dupe_orders.py), saa vi ikke dobbelt-handler.
  - Egen forbindelse (som cogt_fix_dupe_orders) — koer i et sikkert vindue (weekend / uden
    for session). Paper-konto (place_paper_order er paper-vagtet).

Koer paa ALGOSERVEREN (hvor K2/COGT-positionen er) fra backend/:
    python cogt_flatten.py              # preview
    python cogt_flatten.py --execute    # flad COGT ud (én MKT-ordre)
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

SYMBOL = "COGT"
ACTIVE = {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted"}


def _fmt(t):
    o, s = t.order, t.orderStatus
    return (f"{t.contract.symbol} {o.action} qty={o.totalQuantity:g} {o.orderType} "
            f"status={s.status} rest={s.remaining:g} clientId={o.clientId}")


async def main(execute: bool) -> int:
    from accounts import load_identity
    from ibkr_connect import IBKRConnection

    identity = load_identity()
    print("=" * 72)
    print(f"  COGT FLATTEN (oprydning efter over-sell) — {'EXECUTE' if execute else 'PREVIEW (roerer intet)'}")
    print("=" * 72)
    print(f"  Konto: {identity.ibkr_account} ({'paper' if identity.paper_trading else 'LIVE'})")

    conn = IBKRConnection(paper_trading=identity.paper_trading)
    if not await conn.connect():
        print("  FEJL: kunne ikke forbinde til IBKR.")
        return 1
    try:
        ib = conn.ib
        try:
            await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), timeout=8)
        except Exception as e:
            print(f"  (reqAllOpenOrders fejlede: {type(e).__name__}: {e} — bruger openTrades)")

        pos = {p["ticker"].upper(): p["position"] for p in conn.get_positions() if p.get("position")}
        net = pos.get(SYMBOL, 0)
        print(f"\n  Position {SYMBOL}: {net:+g}")

        if net == 0:
            print("\n  -> COGT er allerede flad. Intet at goere. Faerdig.")
            return 0

        # Hvilende aktive COGT-ordrer? Lad dem resolve foerst (undgaa dobbelt-handel).
        active_cogt = [t for t in (ib.openTrades() or [])
                       if t.orderStatus.status in ACTIVE
                       and t.contract.symbol.upper() == SYMBOL]
        if active_cogt:
            print(f"\n  ⛔ AFBRYDER: {len(active_cogt)} aktiv(e) COGT-ordre(r) hviler allerede:")
            for t in active_cogt:
                print(f"    {_fmt(t)}")
            print("     De kan fylde og aendre positionen. Annuller dem foerst")
            print("     (python cogt_fix_dupe_orders.py --execute, eller i TWS), og koer saa igen.")
            return 1

        action = "BUY" if net < 0 else "SELL"
        qty = int(abs(net))
        print(f"\n  PLAN: {action} {qty} {SYMBOL} (MKT) -> flader positionen til 0.")

        if not execute:
            print("\n  (PREVIEW) Sendte INGEN ordre. Koer igen med --execute for at flade ud.")
            return 0

        if not identity.paper_trading:
            print("\n  ⛔ Konto er IKKE paper — dette script flader kun paper-konti. Afbryder.")
            return 1

        result = await conn.place_paper_order(
            SYMBOL, action, qty, source="cogt_flatten",
            order_ref="cogt_flatten", await_fill_sec=8)
        print(f"\n  -> Ordre sendt: {result}")
        await asyncio.sleep(2)
        net_after = {p["ticker"].upper(): p["position"]
                     for p in conn.get_positions() if p.get("position")}.get(SYMBOL, 0)
        print(f"  Position {SYMBOL} efter: {net_after:+g}")
        if net_after == 0:
            print("\n  ✅ COGT er fladet ud.")
        else:
            filled = (result or {}).get("filled", 0)
            if filled and filled > 0:
                print("\n  (delvist fyldt — koer igen for resten, eller vent paa fyldning)")
            else:
                print("\n  ⚠ Ikke fyldt endnu (markedet kan vaere lukket — MKT fylder ved aabning).")
                print("     Positionen staar uaendret; ordren fylder ved US-aabning. Tjek i TWS.")
    finally:
        conn.disconnect()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Flad den resterende COGT-position ud (preview-default)")
    ap.add_argument("--execute", action="store_true", help="send flatten-ordren (uden = preview)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.execute)))

#!/usr/bin/env python3
"""
cogt_fix_dupe_orders.py - annuller DUPLIKAT GTC-SELL paa COGT, behold praecis EEN.
═══════════════════════════════════════════════════════════════════════════════
Der ligger flere identiske hvilende SELL-ordrer paa COGT (orphan-duplikater fra foer
dup-fixet). Tre x SELL 14 mod en position paa 14 = oversaelg -> short ved aabningen.
Dette script annullerer alle paa naer EEN, saa positionen lukkes praecis een gang
naar US-markedet aabner.

SIKKERHED:
  - DEFAULT = PREVIEW: viser hvad der ville ske, annullerer INTET.
  - --execute = annuller alle aktive COGT SELL paa naer den foerste (behold qty=position).
  - Egen random client-id (IBKRConnection) -> kicker IKKE backendens forbindelse,
    kraever INGEN backend-genstart (sikkert midt i eksperimentet).
  - Roerer KUN COGT SELL-ordrer. Placerer ALDRIG noget.

Koer paa ALGOSERVEREN fra backend/:
    python cogt_fix_dupe_orders.py              # preview
    python cogt_fix_dupe_orders.py --execute    # annuller duplikater
Send outputtet retur.
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
            f"tif={o.tif} status={s.status} rest={s.remaining:g} "
            f"orderId={o.orderId} permId={o.permId} clientId={o.clientId}")


async def main(execute: bool) -> int:
    from accounts import load_identity
    from ibkr_connect import IBKRConnection

    identity = load_identity()
    print("=" * 72)
    print(f"  COGT DUPLIKAT-ORDRE-FIX — {'EXECUTE (annullerer)' if execute else 'PREVIEW (roerer intet)'}")
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

        # Position (sanity: behold en SELL der matcher antallet vi ejer).
        pos = {p["ticker"].upper(): p["position"] for p in conn.get_positions() if p.get("position")}
        held = pos.get(SYMBOL, 0)
        print(f"\n  Position {SYMBOL}: {held:+g}")

        active = [t for t in (ib.openTrades() or [])
                  if t.contract.symbol.upper() == SYMBOL
                  and t.order.action.upper() == "SELL"
                  and t.orderStatus.status in ACTIVE]
        print(f"\n  Aktive {SYMBOL} SELL-ordrer: {len(active)}")
        for t in active:
            print(f"    {_fmt(t)}")

        if len(active) <= 1:
            print("\n  -> 0 eller 1 ordre — intet at rydde op. Faerdig.")
            return 0

        keep = active[0]
        drop = active[1:]
        print(f"\n  PLAN: behold 1 (permId={keep.order.permId}, qty={keep.order.totalQuantity:g}), "
              f"annuller {len(drop)}:")
        for t in drop:
            print(f"    ANNULLER permId={t.order.permId} orderId={t.order.orderId} clientId={t.order.clientId}")

        if held and abs(keep.order.totalQuantity) != abs(held):
            print(f"\n  ⚠ ADVARSEL: beholdt ordre (qty={keep.order.totalQuantity:g}) matcher IKKE "
                  f"positionen ({held:+g}) — tjek manuelt i TWS foer --execute.")

        if not execute:
            print("\n  (PREVIEW) Annullerede INTET. Koer igen med --execute for at udfoere.")
            return 0

        for t in drop:
            try:
                ib.cancelOrder(t.order)
                print(f"    -> cancelOrder sendt: permId={t.order.permId}")
            except Exception as e:
                print(f"    -> FEJL ved annullering permId={t.order.permId}: {type(e).__name__}: {e}")
        await asyncio.sleep(3)   # lad annulleringer lande

        # Bekraeft sluttilstand.
        try:
            await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), timeout=8)
        except Exception:
            pass
        still = [t for t in (ib.openTrades() or [])
                 if t.contract.symbol.upper() == SYMBOL
                 and t.order.action.upper() == "SELL"
                 and t.orderStatus.status in ACTIVE]
        print(f"\n  EFTER: {len(still)} aktiv {SYMBOL} SELL tilbage:")
        for t in still:
            print(f"    {_fmt(t)}")
        if len(still) == 1:
            print("\n  ✅ Praecis EEN SELL tilbage — fylder ved US-aabning (15:30 DK) og lukker de 14.")
            print("     Koer derefter: python manual_reconcile.py --source K2 --execute  (lukker journal-row)")
        else:
            print(f"\n  ⚠ {len(still)} tilbage (forventede 1) — tjek i TWS.")
    finally:
        conn.disconnect()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Annuller duplikat COGT SELL-ordrer (behold een)")
    ap.add_argument("--execute", action="store_true", help="udfoer annulleringen (uden = preview)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.execute)))

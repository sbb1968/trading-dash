#!/usr/bin/env python3
"""
cogt_fix_dupe_orders.py - ryd DUPLIKAT GTC-SELL paa COGT via global cancel.
═══════════════════════════════════════════════════════════════════════════════
Der ligger flere identiske hvilende SELL-ordrer paa COGT (orphan-duplikater fra foer
dup-fixet), lagt af FORSKELLIGE klienter (clientId 79 + 88). En API-klient kan KUN
annullere ordrer den selv lagde (cancelOrder pr. orderId -> Error 10147 paa tvaers af
klienter). Derfor bruger vi reqGlobalCancel, der annullerer paa tvaers af klienter.

reqGlobalCancel rammer ALLE aabne ordrer paa kontoen -> for ikke at ramme en anden
strategis hvilende ordre AFBRYDER scriptet, hvis der findes aktive ordrer der IKKE er
COGT. (Diag bekraeftede at der lige nu KUN er COGT-ordrer.) Bagefter laegger
manual_reconcile praecis EEN ren SELL (dup-vagten ser ingen hvilende -> lige en).

SIKKERHED:
  - DEFAULT = PREVIEW: viser hvad der ville ske, annullerer INTET.
  - --execute = reqGlobalCancel (KUN hvis alle aktive ordrer er COGT).
  - Egen random client-id -> kicker IKKE backenden, kraever INGEN genstart.
  - Placerer ALDRIG noget (det goer manual_reconcile bagefter).

Koer paa ALGOSERVEREN fra backend/:
    python cogt_fix_dupe_orders.py              # preview
    python cogt_fix_dupe_orders.py --execute    # global cancel (kun hvis kun COGT aktivt)
Derefter:
    python manual_reconcile.py --source K2 --execute   # laegger EEN ren SELL
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


def _active(ib):
    return [t for t in (ib.openTrades() or []) if t.orderStatus.status in ACTIVE]


async def main(execute: bool) -> int:
    from accounts import load_identity, aktiv_konto
    from ibkr_connect import IBKRConnection

    identity = load_identity()
    print("=" * 72)
    print(f"  COGT DUPLIKAT-ORDRE-FIX (global cancel) — {'EXECUTE' if execute else 'PREVIEW (roerer intet)'}")
    print("=" * 72)
    print(f"  Konto: {aktiv_konto()} ({'paper' if identity.paper_trading else 'LIVE'})")

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
        print(f"\n  Position {SYMBOL}: {pos.get(SYMBOL, 0):+g}")

        active = _active(ib)
        cogt = [t for t in active if t.contract.symbol.upper() == SYMBOL and t.order.action.upper() == "SELL"]
        other = [t for t in active if t not in cogt]

        print(f"\n  Aktive ordrer i alt: {len(active)}  (COGT SELL: {len(cogt)}, andet: {len(other)})")
        for t in active:
            print(f"    {_fmt(t)}")

        if len(cogt) <= 1 and not other:
            print(f"\n  -> {len(cogt)} COGT SELL — intet at rydde op. Faerdig.")
            return 0

        if other:
            print("\n  ⛔ AFBRYDER: der er aktive ordrer der IKKE er COGT SELL (se 'andet' ovenfor).")
            print("     reqGlobalCancel ville ramme dem. Annuller COGT-duplikaterne manuelt i TWS,")
            print("     eller stop de andre strategier foerst. Scriptet roerer INTET.")
            return 1

        # Her: alle aktive ordrer er COGT SELL -> sikkert at global-cancele.
        print(f"\n  PLAN: reqGlobalCancel -> annullerer alle {len(cogt)} COGT SELL (intet andet aktivt).")
        print("        Bagefter laegger 'manual_reconcile --source K2 --execute' EEN ren SELL.")

        if not execute:
            print("\n  (PREVIEW) Annullerede INTET. Koer igen med --execute for at udfoere.")
            return 0

        ib.reqGlobalCancel()
        print("\n  -> reqGlobalCancel sendt. Venter paa at annulleringer lander...")
        await asyncio.sleep(4)

        try:
            await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), timeout=8)
        except Exception:
            pass
        still = _active(ib)
        print(f"\n  EFTER: {len(still)} aktiv(e) ordre(r) tilbage:")
        for t in still:
            print(f"    {_fmt(t)}")
        if not still:
            print("\n  ✅ Alle hvilende ordrer annulleret. Position COGT staar stadig aaben (14).")
            print("     NAESTE: python manual_reconcile.py --source K2 --execute")
            print("       -> dup-vagten ser ingen hvilende ordre og laegger PRAECIS EEN ren SELL,")
            print("          der fylder ved US-aabning (15:30 DK) og lukker de 14.")
        else:
            print("\n  ⚠ Der er stadig aktive ordrer — tjek i TWS.")
    finally:
        conn.disconnect()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Global-cancel COGT duplikat-SELL (kun hvis kun COGT aktivt)")
    ap.add_argument("--execute", action="store_true", help="udfoer reqGlobalCancel (uden = preview)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.execute)))

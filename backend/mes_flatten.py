#!/usr/bin/env python3
"""
mes_flatten.py - flad den forældreløse MES-position ud (engangs-oprydning).
═══════════════════════════════════════════════════════════════════════════════════
Europa-reversion åbnede en MES-position; en backend-genstart (hvor positions-feedet
var koldt) fik reconkilen til at markere journal-rowen lukket UDEN at sende en ordre
→ den ægte MES-position blev forældreløs (ingen journal-spor). Ingen reconcile rører
en journal-løs orphan (observe-only), så den skal ryddes op manuelt her.

VIGTIGT — futures: Vi flader positionen ud på dens NØJAGTIGE kontrakt (conId fra
reqPositions), IKKE en re-resolvet front-måned. Er måneden rullet, ville en SELL på
front-måneden åbne en NY short i stedet for at lukke — det undgår vi ved at genbruge
selve positionens kontrakt.

SIKKERHED:
  - DEFAULT = PREVIEW: viser position + plan, sender INGEN ordre.
  - --execute = sender PRAECIS ÉN MKT-ordre der flader MES ud (SELL hvis long, BUY hvis short).
  - Roerer KUN MES. AFBRYDER hvis der hviler aktive MES-ordrer (lad dem resolve / cancel
    foerst i TWS), saa vi ikke dobbelt-handler.
  - Egen forbindelse (tilfaeldig clientId — kolliderer ikke med den koerende backend).
  - Paper-vagtet: afbryder hvis kontoen ikke er paper.

Koer paa ALGOSERVEREN (hvor MES-positionen er, konto DUO...) fra backend/:
    python mes_flatten.py              # preview
    python mes_flatten.py --execute    # flad MES ud (én MKT-ordre)
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

SYMBOL = "MES"
ACTIVE = {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted"}


def _fmt(t):
    o, s = t.order, t.orderStatus
    return (f"{t.contract.symbol} {o.action} qty={o.totalQuantity:g} {o.orderType} "
            f"status={s.status} rest={s.remaining:g} clientId={o.clientId}")


async def main(execute: bool) -> int:
    from accounts import load_identity
    from ibkr_connect import IBKRConnection, FUTURES_EXCHANGE
    from ib_async import MarketOrder

    identity = load_identity()
    print("=" * 72)
    print(f"  MES FLATTEN (orphan-oprydning) — {'EXECUTE' if execute else 'PREVIEW (roerer intet)'}")
    print("=" * 72)
    print(f"  Konto: {identity.ibkr_account} ({'paper' if identity.paper_trading else 'LIVE'})")

    conn = IBKRConnection(paper_trading=identity.paper_trading)
    if not await conn.connect():
        print("  FEJL: kunne ikke forbinde til IBKR.")
        return 1
    try:
        ib = conn.ib

        # Autoritativt positions-read (reqPositions -> positionEnd), IKKE cachen.
        try:
            poss = await asyncio.wait_for(ib.reqPositionsAsync(), timeout=10)
        except Exception as e:
            print(f"  FEJL: reqPositions svarede ikke ({type(e).__name__}: {e}). Afbryder.")
            return 1

        # Find MES-positionen på DENNE konto (nøjagtig kontrakt følger med).
        mes = [p for p in (poss or [])
               if (p.contract.symbol or "").upper() == SYMBOL
               and p.position != 0
               and (not identity.ibkr_account or p.account == identity.ibkr_account)]

        if not mes:
            print(f"\n  -> Ingen åben {SYMBOL}-position på {identity.ibkr_account}. Intet at goere. Faerdig.")
            return 0
        if len(mes) > 1:
            print(f"\n  ⛔ AFBRYDER: fandt {len(mes)} separate {SYMBOL}-positioner (flere maaneder?):")
            for p in mes:
                print(f"    conId={p.contract.conId} {p.contract.localSymbol or ''} "
                      f"exp={p.contract.lastTradeDateOrContractMonth or '?'} qty={p.position:+g}")
            print("     Uklart hvilken der skal lukkes — ryd op i TWS manuelt.")
            return 1

        position = mes[0]
        contract = position.contract
        net = position.position
        print(f"\n  Position {SYMBOL}: {net:+g}  "
              f"(conId={contract.conId} {contract.localSymbol or ''} "
              f"exp={contract.lastTradeDateOrContractMonth or '?'} avgCost={position.avgCost:g})")

        # Hvilende aktive MES-ordrer? Lad dem resolve foerst (undgaa dobbelt-handel).
        try:
            await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), timeout=8)
        except Exception as e:
            print(f"  (reqAllOpenOrders fejlede: {type(e).__name__}: {e} — bruger openTrades)")
        active_mes = [t for t in (ib.openTrades() or [])
                      if t.orderStatus.status in ACTIVE
                      and (t.contract.symbol or "").upper() == SYMBOL]
        if active_mes:
            print(f"\n  ⛔ AFBRYDER: {len(active_mes)} aktiv(e) {SYMBOL}-ordre(r) hviler allerede:")
            for t in active_mes:
                print(f"    {_fmt(t)}")
            print("     De kan fylde og aendre positionen. Annuller dem foerst i TWS, og koer saa igen.")
            return 1

        action = "SELL" if net > 0 else "BUY"
        qty = int(abs(net))
        print(f"\n  PLAN: {action} {qty} {SYMBOL} (MKT) paa NOEJAGTIG kontrakt conId={contract.conId} "
              f"-> flader positionen til 0.")

        if not execute:
            print("\n  (PREVIEW) Sendte INGEN ordre. Koer igen med --execute for at flade ud.")
            return 0

        if not identity.paper_trading or not conn.paper:
            print("\n  ⛔ Konto er IKKE paper — dette script flader kun paper-konti. Afbryder.")
            return 1

        # Berig kontrakten (bevarer conId -> nøjagtig samme kontrakt) saa den er routbar.
        if not contract.exchange:
            contract.exchange = FUTURES_EXCHANGE.get(SYMBOL, "CME")
        try:
            await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=8)
        except Exception as e:
            print(f"  (kvalificering advarsel: {type(e).__name__}: {e} — fortsaetter paa conId)")

        order = MarketOrder(action, qty)
        order.orderRef = "mes_flatten"
        trade = ib.placeOrder(contract, order)

        # Vent paa fyldning (op til 10s) — MES er likvid paa CME Globex.
        _waited = 0.0
        _TERMINAL = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
        while _waited < 10:
            await asyncio.sleep(0.5)
            _waited += 0.5
            st = trade.orderStatus
            if (st.filled or 0) >= qty or st.status in _TERMINAL:
                break
        print(f"\n  -> Ordre sendt: status={trade.orderStatus.status} "
              f"filled={trade.orderStatus.filled:g} avg={trade.orderStatus.avgFillPrice or 0:g} "
              f"ref={trade.order.orderRef}")

        # Bekraeft position efter via et frisk reqPositions.
        await asyncio.sleep(1.5)
        try:
            poss2 = await asyncio.wait_for(ib.reqPositionsAsync(), timeout=10)
            net_after = sum(p.position for p in (poss2 or [])
                            if (p.contract.symbol or "").upper() == SYMBOL
                            and (not identity.ibkr_account or p.account == identity.ibkr_account))
        except Exception:
            net_after = None
        print(f"  Position {SYMBOL} efter: {net_after if net_after is not None else '?'}")
        if net_after == 0:
            print("\n  ✅ MES er fladet ud.")
        elif net_after is None:
            print("\n  ⚠ Kunne ikke bekraefte position efter — tjek i Studio/Konto eller TWS.")
        else:
            print("\n  ⚠ Ikke (helt) fyldt endnu. MKT fylder ved likviditet; tjek igen om lidt.")
    finally:
        conn.disconnect()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Flad den forældreløse MES-position ud (preview-default)")
    ap.add_argument("--execute", action="store_true", help="send flatten-ordren (uden = preview)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.execute)))

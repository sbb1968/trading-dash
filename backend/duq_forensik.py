#!/usr/bin/env python3
r"""
duq_forensik.py — hent handelshistorik fra brokeren, READ-ONLY
════════════════════════════════════════════════════════════════════════════════
Anledningen: journalen for DUQ441063 er forkert fra 17-08 og frem, og rettelsen
kraever brokerens egne tal. Client Portal er laast bag Ibens password, saa
spoergsmaalet er om TWS-forbindelsen kan levere det samme.

    python duq_forensik.py                                  # lokal TWS
    python duq_forensik.py --host ibenspc --port 7497       # over netvaerket

⚠ DEN SKRIVER INTET OG SENDER INTET. Ingen ordrer, ingen annulleringer, ingen
aendringer i journalen. Egen client-id (21) saa den ikke sparker backenden (200)
eller ordre-forbindelsen (201).

────────────────────────────────────────────────────────────────────────────────
⚠ DET VIGTIGSTE: DEN MAA IKKE PRAESENTERE ÉN DAG SOM "HISTORIKKEN"

IBKR's `reqExecutions` er dokumenteret til at levere **indevaerende handelsdag**.
Er det rigtigt, kan dette vaerktoej IKKE erstatte aktivitetsopgoerelsen — og saa
skal det SIGE det, ikke udskrive dagens tre linjer under overskriften "historik".
Det ville vaere praecis den fejlklasse resten af projektet er fuld af: et svar der
ser komplet ud fordi ingen spurgte hvad det daekkede.

Derfor MAALER den raekkevidden i stedet for at antage den, med kontrolfikstur i
begge retninger (samme moenster som ES-proben, T5):

  KENDT-POSITIV   19-08 BUY 1 MES @ 7705,25 (Soerens manuelle tilbagekoeb).
                  Kommer den ikke med, virker opslaget slet ikke, og INTET
                  resultat kan bruges.
  RAEKKEVIDDE     18-08 BUY 1 MES @ 7730,75 og SELL 1 MES @ 7716,00.
                  Kommer de med, raekker opslaget mindst én dag tilbage.
                  Kommer de IKKE med, er graensen bekraeftet, og opgoerelsen er
                  stadig noedvendig.

Begge er kendt fra journalen og ordre-trackeren; de er ikke gaet.
────────────────────────────────────────────────────────────────────────────────
⚠ EFTERSKRIFT 19-08: ORDRE-TRACKEREN VISTE SIG AT VAERE DEN BEDRE KILDE.

`/orders/list?period_hours=336` paa maskinen selv gav ALLE 29 fills 11.-18. august,
hver med `bekraeftet: true` (verificeret mod IBKR), og hovedbogen bygget af dem
lukker paa position 0 — samme som brokeren viser. Den behoevede ingen
broker-forbindelse overhovedet.

Dette vaerktoej er derfor ikke laengere det primaere. Det er stadig det eneste der
kan se ordrer afgivet DIREKTE i TWS uden om Trading Dash (trackeren kender dem
ikke), og det kan bekraefte hovedbogen mod brokerens egne tal. Koer det til dét —
ikke som erstatning for et opslag der er billigere.
"""
from __future__ import annotations

import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import ibkr_client_ids

HER = Path(__file__).resolve().parent
UD_DIR = HER / "duq_forensik_output"
CLIENT_ID = ibkr_client_ids.SCRIPTS["duq_forensik.py"]

# ── Kontrolfikstur ──────────────────────────────────────────────────────────
# (dato, side, antal, pris) — alle kendt fra journalen/trackeren, ikke gaettet.
KENDT_POSITIV = ("2026-08-19", "BOT", 1, 7705.25)
RAEKKEVIDDE_PROEVE = [("2026-08-18", "BOT", 1, 7730.75),
                      ("2026-08-18", "SLD", 1, 7716.00)]


def _f(x, n=2):
    return "—" if x is None else f"{x:,.{n}f}".replace(",", " ")


async def hent(args) -> int:
    from ib_async import IB, ExecutionFilter

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id,
                              timeout=20, readonly=True)
    except Exception as e:
        print(f"FEJL: kunne ikke forbinde til {args.host}:{args.port} ({e})")
        print("  · koerer TWS/Gateway der?")
        print("  · tillader den API-forbindelser fra denne maskine?")
        print("    (TWS: Global Configuration -> API -> Trusted IPs)")
        return 2

    try:
        # ── V1: er det den rigtige konto? ───────────────────────────────────
        # ⚠ Samme vagt som ordre_forbindelse. Et forensik-svar fra den FORKERTE
        # konto ville se fuldstaendig rigtigt ud og foere hele rettelsen paa
        # afveje. Haard fejl, ikke en advarsel.
        konti = list(ib.managedAccounts() or [])
        print(f"Forbundet {args.host}:{args.port} clientId={args.client_id} "
              f"(readonly) · konti: {konti or '(ingen)'}")
        if args.konto not in konti:
            print(f"\nFEJL: forbindelsen styrer {konti or '(ingen)'}, ikke {args.konto}.")
            print("Intet resultat — et opslag paa den forkerte konto er vaerre end intet.")
            return 3

        # ── Positioner + kontovaerdier ──────────────────────────────────────
        poss = [p for p in (await ib.reqPositionsAsync() or [])
                if p.account == args.konto and p.position != 0]
        print("\nPOSITIONER")
        if not poss:
            print("  (flad)")
        for p in poss:
            print(f"  {p.contract.localSymbol or p.contract.symbol:12} "
                  f"{p.position:+g}  snit {_f(p.avgCost)}")

        vaerdier = {v.tag: v.value for v in ib.accountValues(args.konto)
                    if v.currency in ("USD", "")}
        print("\nKONTO")
        for tag in ("NetLiquidation", "TotalCashValue", "RealizedPnL",
                    "UnrealizedPnL"):
            if tag in vaerdier:
                print(f"  {tag:18} {vaerdier[tag]}")

        # ── Udfoersler ──────────────────────────────────────────────────────
        f = ExecutionFilter()
        f.acctCode = args.konto
        if args.fra:
            # IBKR's format: "yyyymmdd hh:mm:ss". Vi saetter den tidligt paa dagen.
            f.time = args.fra.replace("-", "") + " 00:00:00"
        fills = await ib.reqExecutionsAsync(f)
        raekker = []
        for fl in fills:
            e, c = fl.execution, fl.commissionReport
            raekker.append({
                "tid": str(e.time),
                "dato": str(e.time)[:10],
                "symbol": fl.contract.localSymbol or fl.contract.symbol,
                "side": e.side,                       # BOT / SLD
                "antal": e.shares,
                "pris": e.price,
                "konto": e.acctNumber,
                "exec_id": e.execId,
                "ordre_id": e.orderId,
                "perm_id": e.permId,
                "kurtage": getattr(c, "commission", None),
                "realiseret": getattr(c, "realizedPNL", None),
            })
        raekker.sort(key=lambda r: r["tid"])

        print(f"\nUDFOERSLER — {len(raekker)} stk")
        if raekker:
            print(f"  {'tid':21}{'symbol':10}{'side':6}{'antal':>6}{'pris':>11}"
                  f"{'kurtage':>9}{'realiseret':>12}  ordre")
            print("  " + "-" * 88)
            for r in raekker:
                print(f"  {r['tid'][:19]:21}{r['symbol']:10}{r['side']:6}"
                      f"{r['antal']:>6g}{_f(r['pris']):>11}{_f(r['kurtage']):>9}"
                      f"{_f(r['realiseret']):>12}  {r['ordre_id']}")

        # ── ⚠ RAEKKEVIDDEN MAALES, IKKE ANTAGES ─────────────────────────────
        datoer = sorted({r["dato"] for r in raekker})
        print("\nRAEKKEVIDDE — maalt, ikke antaget")
        print(f"  datoer i svaret: {', '.join(datoer) if datoer else '(ingen)'}")

        def findes(dato, side, antal, pris) -> bool:
            return any(r["dato"] == dato and r["side"] == side
                       and abs(r["antal"] - antal) < 1e-9
                       and abs(r["pris"] - pris) < 0.01 for r in raekker)

        positiv = findes(*KENDT_POSITIV)
        print(f"  kendt-positiv {KENDT_POSITIV[0]} {KENDT_POSITIV[1]} "
              f"{KENDT_POSITIV[2]} @ {KENDT_POSITIV[3]}: "
              f"{'FUNDET' if positiv else 'MANGLER'}")
        bagud = [findes(*k) for k in RAEKKEVIDDE_PROEVE]
        for k, ok in zip(RAEKKEVIDDE_PROEVE, bagud):
            print(f"  raekkevidde   {k[0]} {k[1]} {k[2]} @ {k[3]}: "
                  f"{'FUNDET' if ok else 'mangler'}")

        print("\nKONKLUSION")
        if not positiv:
            # ⚠ Kvalificerer den kendt-positive ikke, er HELE svaret ubrugeligt.
            # Det er samme regel som ES-proben: en probe der ikke kan finde det
            # den ved findes, maa ikke bruges til at udtale sig om det den ikke
            # fandt.
            print("  ⚠ DEN KENDT-POSITIVE MANGLER. Opslaget virker ikke som ventet —")
            print("    hverken 'ingen historik' eller 'ingen handler' kan udledes.")
            print("    Kassér resultatet og find ud af hvorfor foer noget bruges.")
            kode = 1
        elif all(bagud):
            print("  ✅ Opslaget raekker MINDST tilbage til 18-08.")
            print("    Journalen kan rettes mod disse tal — men tjek foerst om")
            print("    datoerne ovenfor daekker hele perioden du skal bruge.")
            kode = 0
        else:
            print("  ⚠ KUN INDEVAERENDE HANDELSDAG. reqExecutions leverer ikke")
            print("    historik bagud, og dette vaerktoej kan derfor IKKE erstatte")
            print("    aktivitetsopgoerelsen. Det du ser ovenfor er i dag, ikke")
            print("    kontoens historie.")
            print("    Veje videre: Flex Web Service (kraever token fra Client")
            print("    Portal), eller opgoerelsen naar Iben er tilbage.")
            kode = 4

        # ── Aabne og fuldfoerte ordrer (samme forbehold om raekkevidde) ──────
        aabne = [t for t in (await ib.reqAllOpenOrdersAsync() or [])
                 if t.order.account in ("", args.konto)]
        print(f"\nAABNE ORDRER — {len(aabne)} stk")
        for t in aabne:
            o, s = t.order, t.orderStatus
            print(f"  {t.contract.localSymbol or t.contract.symbol:12} {o.action:5} "
                  f"{o.totalQuantity:g} {o.orderType:5} {s.status:12} "
                  f"fyldt {s.filled:g}/{o.totalQuantity:g}  id={o.orderId}")

        try:
            fuldfoerte = await asyncio.wait_for(
                ib.reqCompletedOrdersAsync(apiOnly=False), timeout=20)
        except Exception as e:
            fuldfoerte = []
            print(f"\nFULDFOERTE ORDRER: kunne ikke hentes ({e})")
        else:
            print(f"\nFULDFOERTE ORDRER — {len(fuldfoerte)} stk")
            for t in fuldfoerte:
                o, s = t.order, t.orderStatus
                print(f"  {t.contract.localSymbol or t.contract.symbol:12} "
                      f"{o.action:5} {o.totalQuantity:g} {s.status:12} "
                      f"snit {_f(getattr(s, 'avgFillPrice', None))}  id={o.orderId}")

        # ── Gem raa udfoersler ──────────────────────────────────────────────
        if raekker:
            UD_DIR.mkdir(parents=True, exist_ok=True)
            stempel = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            sti = UD_DIR / f"udfoersler_{args.konto}_{stempel}.csv"
            with sti.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(raekker[0].keys()))
                w.writeheader()
                w.writerows(raekker)
            print(f"\nGemt: {sti}")
        return kode
    finally:
        ib.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="READ-ONLY broker-opslag paa en konto. Skriver og sender intet.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497,
                    help="TWS paper 7497 · IB Gateway paper 4002 (porten siger "
                         "IKKE hvilket program der lytter)")
    ap.add_argument("--konto", default="DUQ441063")
    ap.add_argument("--fra", help="YYYY-MM-DD — proev at bede om udfoersler fra "
                                  "denne dato (IBKR ignorerer den formentlig)")
    ap.add_argument("--client-id", dest="client_id", type=int, default=CLIENT_ID)
    args = ap.parse_args()
    return asyncio.run(hent(args))


if __name__ == "__main__":
    sys.exit(main())

"""
ibkr_session_probe.py — blokeres kun markedsdata, eller hele sessionen?
════════════════════════════════════════════════════════════════════════════════
Indiciet: nyhedsfeeden kørte mens kurserne stod stille. Kapabiliteterne fejler
altså uafhængigt af hinanden — og hvilke der fejler, afgør om Iben kan handle
manuelt på konto 2 gennem Trading Dash uden markedsdata.

Fem trin, i rækkefølge. Går alle igennem, er sagen løst.

    1  connect
    2  reqAccountSummary        kommer der tal?
    3  reqPositions             svarer den?
    4  placeOrder               limit, 1 stk., pris der ALDRIG fylder
    5  cancelOrder              straks, og bekræftet

⚠ FEJLKODEN ER BEVISET, ikke beskrivelsen. Hver eneste IBKR-fejl opsamles ordret
med kode, reqId og tekst. Forskellen mellem "354 not subscribed" (markedsdata) og
en sessionsafvisning er hele spørgsmålet, og de bliver konstant blandet sammen
til "det virkede ikke".

────────────────────────────────────────────────────────────────────────────────
FORUDSÆTNING: en Gateway skal køre på DENNE maskine, logget ind som `fasteriben2`.
TWS optager typisk 7497; starter du IB Gateway ved siden af, bruger den 4002 for
paper. Angiv den port Gatewayen faktisk lytter på.

    python ibkr_session_probe.py --port 4002

────────────────────────────────────────────────────────────────────────────────
⚠ SIKKERHED. Scriptet nægter at sende en ordre medmindre ALT dette holder:

  · kontonummeret begynder med D (paper). En live-konto afbryder scriptet.
  · kontoen er præcis den forventede (--konto, default DUQ441063)
  · 1 stk., og en købslimit på $1 — den fylder ikke på nogen aktie vi handler
  · ordren annulleres straks, og annulleringen VERIFICERES før scriptet slutter

Det er paper, men vanen er god: et script der kan sende en ordre, skal kunne
forklare hvorfor den er harmløs.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock, LimitOrder

import ibkr_client_ids

FEJL: list[str] = []
IBKR_FEJL: list[tuple] = []          # (reqId, kode, tekst) — ordret


def trin(nr, navn, ok, detalje=""):
    mark = "OK  " if ok else "FEJL"
    print(f"  {mark} {nr}. {navn}" + (f"  —  {detalje}" if detalje else ""))
    if not ok:
        FEJL.append(f"{nr}. {navn}: {detalje}")
    return ok


async def hoved(args) -> int:
    ib = IB()

    # ⚠ ORDRET OPSAMLING. ib_async's error-event er den eneste kilde til IBKR's
    # egne koder; uden den ville vi kun have vores egen fortolkning.
    def paa_fejl(reqId, kode, tekst, kontrakt):
        IBKR_FEJL.append((reqId, kode, tekst))
        print(f"       ↳ IBKR {kode} (reqId {reqId}): {tekst}")
    ib.errorEvent += paa_fejl

    print("=" * 78)
    print(f"SESSIONS-PROBE  ·  {args.host}:{args.port}  ·  clientId={args.client_id}")
    print(f"forventet konto: {args.konto}")
    print("=" * 78)

    # ── 1. connect ───────────────────────────────────────────────────────────
    print("\n1. connect")
    try:
        await asyncio.wait_for(
            ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=20),
            timeout=30)
        trin(1, "connect", ib.isConnected(), f"styrede konti: {ib.managedAccounts()}")
    except Exception as e:
        trin(1, "connect", False, f"{type(e).__name__}: {e}")
        print("\n→ Forbindelsen kom ikke op. Kører Gatewayen på den port, og er "
              "API'et slået til?")
        return 1

    styrede = [a.strip().upper() for a in (ib.managedAccounts() or [])]
    konto = args.konto.strip().upper()
    if konto not in styrede:
        trin(1, "kontoen findes i sessionen", False,
             f"{konto} er ikke blandt {styrede}")
        print(f"\n→ Gatewayen er ikke logget ind som den bruger der ejer {konto}.")
        ib.disconnect()          # ib_async: synkron, ikke disconnectAsync
        return 1

    # ── 2. reqAccountSummary ─────────────────────────────────────────────────
    print("\n2. reqAccountSummary")
    tal = {}
    try:
        rader = await asyncio.wait_for(ib.accountSummaryAsync(konto), timeout=25)
        tal = {r.tag: r.value for r in rader}
        nlv = tal.get("NetLiquidation")
        trin(2, "reqAccountSummary", bool(tal),
             f"{len(tal)} felter · NetLiquidation={nlv}")
    except Exception as e:
        trin(2, "reqAccountSummary", False, f"{type(e).__name__}: {e}")

    # ── 3. reqPositions ──────────────────────────────────────────────────────
    print("\n3. reqPositions")
    try:
        pos = await asyncio.wait_for(ib.reqPositionsAsync(), timeout=25)
        egne = [p for p in pos if (p.account or "").upper() == konto]
        # ⚠ Tomt svar er IKKE en fejl. En flad konto har nul positioner, og
        # forskellen mellem "svarede tomt" og "svarede ikke" er hele pointen:
        # kaldet gik igennem uden undtagelse, altså svarede den.
        trin(3, "reqPositions", True,
             f"{len(egne)} positioner på {konto} (tomt svar = flad konto, ikke fejl)")
    except Exception as e:
        trin(3, "reqPositions", False, f"{type(e).__name__}: {e}")

    # ── 4-5. ordre + annullering ─────────────────────────────────────────────
    print("\n4-5. placeOrder + cancelOrder")

    if not konto.startswith("D"):
        trin(4, "SIKKERHEDSPORT", False,
             f"{konto} ligner IKKE en paper-konto (D-præfiks) — sender INGEN ordre")
        ib.disconnect()          # ib_async: synkron, ikke disconnectAsync
        return 1
    print(f"     sikkerhedsport: {konto} er paper (D-præfiks) · "
          f"{args.antal} stk. · limit ${args.limit:.2f} — fylder ikke")

    try:
        kontrakt = Stock(args.ticker, "SMART", "USD")
        k = await asyncio.wait_for(ib.qualifyContractsAsync(kontrakt), timeout=20)
        if not k or not getattr(kontrakt, "conId", 0):
            trin(4, "qualifyContracts", False, f"{args.ticker} kunne ikke kvalificeres")
            ib.disconnect()          # ib_async: synkron, ikke disconnectAsync
            return 1

        ordre = LimitOrder("BUY", args.antal, args.limit)
        ordre.account = konto
        ordre.tif = "DAY"          # aldrig GTC — en ordre der ikke fylder i dag skal dø
        ordre.orderRef = "sessions_probe"

        handel = ib.placeOrder(kontrakt, ordre)
        await asyncio.sleep(3)
        st = handel.orderStatus
        sendt = bool(getattr(handel.order, "orderId", 0))
        trin(4, "placeOrder", sendt,
             f"orderId={handel.order.orderId} status={st.status} filled={st.filled}")

        # ⚠ ANNULLÉR ALTID, ogsaa hvis trin 4 saa forkert ud. En ordre vi ikke kan
        # forklare, skal ikke ligge og vente paa at blive forstaaet.
        ib.cancelOrder(ordre)
        await asyncio.sleep(3)
        st2 = handel.orderStatus
        annulleret = st2.status in ("Cancelled", "ApiCancelled", "PendingCancel")
        trin(5, "cancelOrder", annulleret, f"status={st2.status}")
        if not annulleret and st2.status != "Filled":
            print(f"     ⚠ ORDREN ER IKKE BEKRÆFTET ANNULLERET (status={st2.status}). "
                  f"Tjek den i TWS før du forlader maskinen.")
        if st2.filled:
            print(f"     ⚠⚠ ORDREN FYLDTE {st2.filled} — limit var ikke lav nok. "
                  f"Luk positionen manuelt.")
    except Exception as e:
        trin(4, "placeOrder/cancelOrder", False, f"{type(e).__name__}: {e}")

    ib.disconnect()          # ib_async: synkron, ikke disconnectAsync
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Blokeres kun markedsdata, eller hele sessionen? Fem trin.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True,
                    help="Gatewayens API-port. IB Gateway paper = 4002, TWS paper = 7497")
    ap.add_argument("--konto", default="DUQ441063", help="forventet kontonummer")
    ap.add_argument("--ticker", default="AAPL")
    ap.add_argument("--antal", type=int, default=1)
    ap.add_argument("--limit", type=float, default=1.00,
                    help="koebslimit — skal vaere absurd lav, saa den aldrig fylder")
    ap.add_argument("--client-id", dest="client_id", type=int,
                    default=ibkr_client_ids.SCRIPTS.get("ibkr_session_probe.py", 5))
    args = ap.parse_args()

    kode = asyncio.run(hoved(args))

    print("\n" + "=" * 78)
    print("IBKR-FEJL, ORDRET")
    if IBKR_FEJL:
        for reqId, k, tekst in IBKR_FEJL:
            print(f"  {k:>5}  (reqId {reqId})  {tekst}")
    else:
        print("  (ingen)")

    print("\nRESULTAT")
    if FEJL:
        print(f"  {len(FEJL)} trin fejlede:")
        for f in FEJL:
            print("   -", f)
        print("\n  → Notér hvilket trin og hvilken kode. Det afgoer om det er")
        print("    HANDELSADGANG eller SESSION der mangler.")
    else:
        print("  Alle fem trin gik igennem.")
        print("  → Iben KAN handle paa konto 2 gennem Trading Dash uden markedsdata.")
    print("=" * 78)
    return 1 if FEJL else kode


if __name__ == "__main__":
    sys.exit(main())

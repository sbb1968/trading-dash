"""
maal_futures_kurtage.py — hvad koster én kontrakt i kurtage, målt frem for slået op
════════════════════════════════════════════════════════════════════════════════
Fylder `kurtage_est` i futures_katalog.py. Read-only: ingen ordrer, kun aflæsning.

⚠ HVORFOR MÅLE, NÅR IBKR HAR TO API'ER DER BURDE SVARE
Begge er prøvet, begge svigter på vores build (målt 31-08-2026):

  · `commissionReport` fra reqExecutions kom tilbage med **0.0** på en fill der
    beviseligt havde kostet kurtage.
  · `whatIfOrder` returnerer **tomt** på denne TWS — også med markedet åbent,
    også på MES, også på AAPL. Det er ikke lukket marked; det er builden.

Så tallet kan ikke hentes. Det kan derimod udledes, og udledningen er eksakt.

⚠ REGNESTYKKET
IBKR lægger kurtagen ind i positionens `avgCost` (som er NOTIONEL for futures):

    avgCost = fyldningspris × multiplikator × antal + kurtage_i_alt

Kender vi fyldningsprisen fra `reqExecutions`, er resten kurtage:

    BOT 1 MES @ 7704,00
    avgCost      38520,61
    7704 × 5   = 38520,00
    ─────────────────────
    differens        0,61   ← kurtage for én kontrakt, én side

⚠ FORUDSÆTNINGEN SKAL HOLDE, ELLERS ER TALLET FORKERT
Udledningen kræver at ALLE fills der udgør positionen er kendte. Er positionen
åbnet i en tidligere session, kender `reqExecutions` dem ikke, og differensen
bliver et vilkårligt tal. Derfor afviser scriptet at måle når fills og position
ikke stemmer — frem for at rapportere et tal det ikke kan stå inde for.

    python maal_futures_kurtage.py
    python maal_futures_kurtage.py --symbol MES
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import io
import sys
from collections import defaultdict

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from ib_async import IB, ExecutionFilter

import futures_katalog
import ibkr_client_ids

HOST = "127.0.0.1"
PAPER_PORTE = {4002, 7497}
CLIENT_ID = ibkr_client_ids.for_script("maal_futures_kurtage.py")

TOLERANCE = 0.005      # afrundingsstøj i avgCost


async def maal(port: int, kun_symbol: str | None) -> int:
    if port not in PAPER_PORTE:
        raise SystemExit(f"AFBRUDT: {port} er ikke en paper-port {sorted(PAPER_PORTE)}")

    ib = IB()
    await ib.connectAsync(HOST, port, clientId=CLIENT_ID, timeout=25)
    konti = ib.managedAccounts()
    if not konti or any(not k.startswith("DU") for k in konti):
        ib.disconnect()
        raise SystemExit(f"AFBRUDT: ikke en paper-konto ({konti})")
    print(f"forbundet · {konti} · port {port}\n")

    fills = await ib.reqExecutionsAsync(ExecutionFilter())
    poss = await ib.reqPositionsAsync()

    # Saml fills pr. symbol: samlet antal og samlet notional (uden kurtage).
    kendte: dict[str, dict] = defaultdict(lambda: {"antal": 0.0, "notional": 0.0})
    for f in fills:
        c, e = f.contract, f.execution
        if c.secType != "FUT":
            continue
        fortegn = 1.0 if e.side.upper().startswith("B") else -1.0
        mult = float(c.multiplier or 1)
        k = kendte[c.symbol.upper()]
        k["antal"] += fortegn * float(e.shares)
        k["notional"] += fortegn * float(e.shares) * float(e.avgPrice) * mult

    fundet = 0
    for p in poss:
        c = p.contract
        if c.secType != "FUT":
            continue
        sym = c.symbol.upper()
        if kun_symbol and sym != kun_symbol.upper():
            continue
        # ⚠ reqPositions leverer OGSAA lukkede positioner med antal 0. De har
        # avgCost 0, og differensen ville blive divideret med nul. En lukket
        # position kan ikke maales — kurtagen ligger i realiseret P&L, ikke i
        # kostbasis.
        if float(p.position) == 0:
            print(f"  {sym}  position 0 — LUKKET. Kurtagen ligger i realiseret "
                  f"P&L, ikke i avgCost. Kan ikke maales her.\n")
            continue
        mult = float(c.multiplier or 1)
        k = kendte.get(sym)

        print(f"  {sym}  position {p.position:g}  avgCost {p.avgCost:.2f}  multiplier {mult:g}")
        if not k:
            print("     ⚠ INGEN fills kendt for symbolet — positionen er fra en "
                  "tidligere session. Kan ikke måles.\n")
            continue

        # ⚠ Kontrollen der gør tallet troværdigt: stemmer de fills vi kender
        # overens med den position der faktisk ligger? Gør de ikke, mangler der
        # fills, og differensen ville være pure fantasi.
        if abs(k["antal"] - float(p.position)) > 1e-9:
            print(f"     ⚠ fills siger {k['antal']:g} kontrakter, positionen er "
                  f"{p.position:g} — der mangler fills. MÅLER IKKE.\n")
            continue

        notional_uden = k["notional"]
        # avgCost er pr. kontrakt hos IBKR; gang op til positionens samlede kostbasis.
        kostbasis = float(p.avgCost) * abs(float(p.position))
        differens = abs(kostbasis) - abs(notional_uden)
        pr_kontrakt = differens / abs(float(p.position))

        print(f"     fills           {k['antal']:g} kontrakter, notional {notional_uden:,.2f}")
        print(f"     kostbasis       {kostbasis:,.2f}")
        print(f"     differens       {differens:,.2f}")
        if abs(differens) < TOLERANCE:
            print("     ⚠ differens ≈ 0. Enten er kurtagen ikke bogført endnu, "
                  "eller kontoen er kurtagefri. Ingen måling.\n")
            continue

        gemt = futures_katalog.kurtage_pr_side(sym)
        print(f"     → KURTAGE PR. KONTRAKT PR. SIDE: {pr_kontrakt:.4f} USD")
        print(f"       rundtur for {abs(p.position):g} kontrakt(er): "
              f"{2 * pr_kontrakt * abs(float(p.position)):.2f} USD")
        if gemt is None:
            print(f"       kataloget har INTET for {sym} — sæt "
                  f"kurtage_est={round(pr_kontrakt, 2)} i futures_katalog.py")
        elif abs(gemt - pr_kontrakt) > 0.02:
            print(f"       ⚠ kataloget siger {gemt} — AFVIGER. Opdatér "
                  f"futures_katalog.py og sæt ny dato.")
        else:
            print(f"       kataloget siger {gemt} — stemmer ✔")
        print()
        fundet += 1

    if not fundet:
        print("  Ingen futures-position kunne måles.")
        print("  Målingen kræver en position hvis fills ligger i DENNE session.")
    ib.disconnect()
    return 0 if fundet else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Mål futures-kurtage ud af avgCost.")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--symbol", default=None, help="kun dette symbol, fx MES")
    a = ap.parse_args()
    sys.exit(asyncio.run(maal(a.port, a.symbol)))


if __name__ == "__main__":
    main()

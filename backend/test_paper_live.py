"""
test_paper_live.py — paper og live maa aldrig blandes
════════════════════════════════════════════════════════════════════════════════
Paper og live skal kunne koere SAMTIDIG: algoerne videre paa paper mens der
handles live manuelt. Derfor kan maskinen ikke afgoere hvad en given handel var
— maerket skal komme fra den FORBINDELSE ordren gik igennem.

Fem ting holdes fast:

  1  Maerket kommer fra forbindelsen, ikke fra identity.
  2  Kalderen kan overstyre, og overstyringen vinder.
  3  ⚠ Er konto og flag uenige, siges det hoejt. En live-konto stemplet som
     paper ville lægge rigtige penge oven i paper-statistikken, og et 0/1-flag
     kan ikke selv afsloere at det er forkert.
  4  Filtret adskiller de to, og aggregatet regner kun paa det valgte.
  5  ⚠ Ufiltreret siger summary "blandet", saa ingen praesenterer én win rate
     der i virkeligheden daekker to uforenelige ting.

Afsnit 6 viser at kontrollerne kan fejle.

    python test_paper_live.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytz

import journal as journal_mod
import trade_queries
from journal import Journal

ET = pytz.timezone("America/New_York")
FEJL: list[str] = []
DB_FIL = "test_paper_live.db"


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


class LogFanger(logging.Handler):
    """Opsamler ERROR-linjer, saa vi kan bevise at advarslen faktisk udsendes."""
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.linjer: list[str] = []

    def emit(self, record):
        self.linjer.append(record.getMessage())


async def hoved():
    for f in (DB_FIL, DB_FIL + "-wal", DB_FIL + "-shm"):
        if os.path.exists(f):
            os.remove(f)

    j = Journal(DB_FIL)
    await j.init()
    nu = datetime.now(ET)

    async def handel(sym, pnl, konto=None, paper=None, minutter=0):
        tid = nu - timedelta(hours=2, minutes=minutter)
        tid_ud = tid + timedelta(minutes=20)
        tid_ = await j.log_trade_open(
            source="Konfluens 2", symbol=sym, side="long", shares=10,
            entry_price=100.0, entry_time=tid,
            ibkr_account=konto, paper=paper)
        await j.log_trade_close(trade_id=tid_, exit_price=100 + pnl / 10,
                                exit_time=tid_ud, exit_reason="target", pnl=pnl)
        return tid_

    print("\n1. Maerket kommer fra FORBINDELSEN, ikke fra maskinen")
    # Maskinen er sat op til paper (identity.paper_trading=True paa denne boks),
    # men forbindelsen er live. Uden opslaget ville raekken blive stemplet paper.
    journal_mod.saet_konto_kilde(lambda: ("U23100448", False))
    tid_live = await handel("AAPL", 50.0)
    r = await trade_queries.get_trade_by_id(j.db, tid_live)
    kraev(r["paper"] == 0, f"stemplet LIVE (paper={r['paper']}) selv om maskinen "
                           f"er en paper-maskine")
    kraev(r["ibkr_account"] == "U23100448", f"og med live-kontoen: {r['ibkr_account']}")

    journal_mod.saet_konto_kilde(lambda: ("DUO509856", True))
    tid_paper = await handel("MSFT", -20.0, minutter=5)
    r = await trade_queries.get_trade_by_id(j.db, tid_paper)
    kraev(r["paper"] == 1, "og en paper-forbindelse stempler paper")

    print("\n2. Kalderen kan overstyre")
    tid_o = await handel("TSLA", 10.0, konto="U23100448", paper=False, minutter=10)
    r = await trade_queries.get_trade_by_id(j.db, tid_o)
    kraev(r["paper"] == 0 and r["ibkr_account"] == "U23100448",
          "eksplicit paper=False vinder over forbindelsen")

    print("\n3. ⚠ Uenighed mellem konto og flag siges hoejt")
    fanger = LogFanger()
    logging.getLogger("journal").addHandler(fanger)
    await handel("NVDA", 5.0, konto="U23100448", paper=True, minutter=15)
    kraev(any("UENIGE" in l for l in fanger.linjer),
          f"live-konto stemplet paper -> ERROR i loggen ({len(fanger.linjer)} linjer)")
    fanger.linjer.clear()
    await handel("AMD", 5.0, konto="DUO509856", paper=True, minutter=20)
    kraev(not fanger.linjer, "og en ENIG kombination giver ingen larm")
    logging.getLogger("journal").removeHandler(fanger)

    print("\n4. Filtret adskiller, og aggregatet regner kun paa det valgte")
    alle = await trade_queries.list_trades(j.db, limit=100)
    kun_live = await trade_queries.list_trades(j.db, paper=False, limit=100)
    kun_paper = await trade_queries.list_trades(j.db, paper=True, limit=100)
    kraev(len(alle) == 5, f"fem handler i alt (fik {len(alle)})")
    kraev(len(kun_live) == 2, f"to live (fik {len(kun_live)})")
    kraev(len(kun_paper) == 3, f"tre paper (fik {len(kun_paper)})")
    kraev(len(kun_live) + len(kun_paper) == len(alle),
          "de to dele udgoer helheden — ingen raekke falder mellem stolene")

    s_live  = await trade_queries.trades_summary(j.db, paper=False)
    s_paper = await trade_queries.trades_summary(j.db, paper=True)
    kraev(abs(s_live["total_pnl"] - 60.0) < 0.01,
          f"live-P&L = {s_live['total_pnl']} (50 + 10)")
    kraev(abs(s_paper["total_pnl"] - (-10.0)) < 0.01,
          f"paper-P&L = {s_paper['total_pnl']} (-20 + 5 + 5)")
    kraev(s_live["typer"] == ["live"] and s_paper["typer"] == ["paper"],
          "hvert aggregat siger hvilken type det er lavet af")
    kraev(not s_live["blandet"] and not s_paper["blandet"],
          "og ingen af dem er blandede")

    print("\n5. ⚠ Ufiltreret ADVARER om at tallene blander")
    s_alle = await trade_queries.trades_summary(j.db)
    kraev(s_alle["blandet"] is True, "blandet = True")
    kraev(sorted(s_alle["typer"]) == ["live", "paper"],
          f"begge typer navngives: {s_alle['typer']}")
    kraev(abs(s_alle["total_pnl"] - 50.0) < 0.01,
          f"summen ER 50 — men det tal betyder ingenting, og derfor staar "
          f"advarslen ved siden af det")

    print("\n6. Events baerer samme maerke")
    journal_mod.saet_konto_kilde(lambda: ("U23100448", False))
    await j.log_event(source="Konfluens 2", event_type="trade_forensics",
                      symbol="AAPL", payload={"hvorfor": "test"})
    ev = await j.get_events(event_type="trade_forensics")
    kraev(len(ev) == 1 and ev[0]["paper"] is False,
          f"forensik-eventet er LIVE: {ev[0]['paper'] if ev else '(ingen)'}")
    kraev(ev[0]["ibkr_account"] == "U23100448", "og baerer kontoen")

    print("\n7. ⚠ Falsifikation — kontrollerne kan faktisk fejle")
    # Afsnit 1 ville vaere groent uanset hvad, hvis identity ogsaa var live.
    from accounts import identity
    kraev(identity.paper_trading is True,
          "maskinen ER en paper-maskine — ellers beviste afsnit 1 ingenting "
          "om at maerket kommer fra forbindelsen")

    # Afsnit 4: filtret skal faktisk skaere. Var alle raekker samme type, ville
    # 'de to dele udgoer helheden' holde trivielt.
    kraev(len(kun_live) > 0 and len(kun_paper) > 0,
          "der ER baade live- og paper-raekker at skille ad")

    # Afsnit 5: 'blandet' skal kunne vaere False. Vist i afsnit 4, gentaget her
    # som den eksplicitte modpol.
    s_tom = await trade_queries.trades_summary(j.db, symbol="FINDESIKKE")
    kraev(s_tom["blandet"] is False and s_tom["typer"] == [],
          "et tomt saet er ikke blandet — flaget er ikke bare altid sandt")

    await j.close()
    for f in (DB_FIL, DB_FIL + "-wal", DB_FIL + "-shm"):
        if os.path.exists(f):
            os.remove(f)


asyncio.run(hoved())

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")

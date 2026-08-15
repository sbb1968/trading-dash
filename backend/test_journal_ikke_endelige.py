"""
test_journal_ikke_endelige.py — ét ubrugeligt tal må ikke tage hele vinduet
════════════════════════════════════════════════════════════════════════════════
DEN FEJL DER BLEV MELDT: `GET /journal/events` svarede 500 for ethvert
tidsvindue der indeholdt et `universe_selected`-event. Studios Log-fane var
tom for hele dage, og en diagnose af noget helt andet måtte skæres i
femminutters-skiver for at komme uden om den.

RODÅRSAGEN, navngivet:

    json.dumps(payload, default=str)        # allow_nan=True som standard
        → en NaN skrives som det BARE TOKEN `NaN`, som ikke er gyldig JSON
    json.loads(payload_json)                # accepterer det alligevel
        → læsningen ser rigtig ud
    Starlette JSONResponse                  # allow_nan=False
        → ValueError → 500 for HELE svaret

Konkret felt: `universe_selected` fra Konfluens 2 / BuyTheDip,
`payload.meta.rows[*]` — de rå skærmer-rækker fra TradingView.

⚠ DE TO PRØVER DÆKKER HVER SIN HALVDEL, og den anden er den vigtige:
  1. Skrivesiden: en ny NaN må ikke havne i databasen.
  2. Læsesiden: de events der ALLEREDE ligger med NaN skal kunne leveres.
     Rettes kun skrivesiden, virker rettelsen først for fremtidige events —
     og algoserverens Log-fane bliver ved med at være tom for august.

    python test_journal_ikke_endelige.py
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from journal import Journal

FEJL = 0


def kraev(ok: bool, hvad: str) -> None:
    global FEJL
    print(f"  {'OK  ' if ok else 'FEJL'} {hvad}")
    if not ok:
        FEJL += 1


# ⚠ DET DER FAKTISK GÅR GALT I PRODUKTION. Starlette serialiserer med
# allow_nan=False; kan det ikke lade sig gøre, bliver hele svaret 500.
# Prøven skal derfor måle PRÆCIS dét — ikke "ser payloaden fornuftig ud".
def kan_leveres(objekt) -> bool:
    try:
        json.dumps(objekt, allow_nan=False)
        return True
    except (ValueError, TypeError):
        return False


async def koer() -> None:
    db_sti = Path(tempfile.mkdtemp()) / "proeve.db"
    j = Journal(str(db_sti))
    await j.init()

    # ── 1. Skrivesiden ──────────────────────────────────────────────────────
    await j.log_event(
        source="Konfluens 2", event_type="universe_selected",
        payload={"tickers": ["AAPL"],
                 "meta": {"rows": [{"symbol": "AAPL", "rvol": float("nan"),
                                    "change": 1.5},
                                   {"symbol": "MSFT", "rvol": 2.0}],
                          "pool_size": float("inf")}})
    await j.log_event(source="Konfluens 2", event_type="status",
                      payload={"message": "et helt almindeligt event"})

    raa = sqlite3.connect(str(db_sti)).execute(
        "SELECT payload_json FROM events WHERE event_type='universe_selected'"
    ).fetchone()[0]
    kraev("NaN" not in raa and "Infinity" not in raa,
          f"skrivesiden lægger ingen NaN i databasen ({raa[:70]}…)")

    # ── 2. Læsesiden — den historiske række ─────────────────────────────────
    # ⚠ SKREVET UDEN OM log_event, med den RÅ tekst som den ligger på
    # algoserveren. Det er præcis den tilstand rettelsen skal kunne håndtere,
    # og den kan ikke fremkaldes gennem det rettede API.
    c = sqlite3.connect(str(db_sti))
    c.execute(
        "INSERT INTO events (ts_utc, ts_local, account_id, instance_id, source, "
        "event_type, ibkr_account, paper, symbol, payload_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), datetime.now().astimezone().isoformat(),
         "proeve", "proeve", "BuyTheDip", "universe_selected", "DU1", 1, None,
         '{"meta": {"rows": [{"symbol": "TSLA", "rvol": NaN}], '
         '"pool_size": Infinity}, "tickers": ["TSLA"]}'))
    c.commit(); c.close()

    events = await j.get_events(limit=100)
    kraev(len(events) == 3, f"alle tre events leveres ({len(events)})")

    # ⚠ KERNEN: hele vinduet skal kunne serialiseres. Før rettelsen kastede
    # denne linje, og det var dét brugeren så som en tom Log-fane.
    kraev(kan_leveres({"events": events}),
          "HELE vinduet kan serialiseres med allow_nan=False")

    # ── 3. Sprængradius: det gode event må ikke lide under det dårlige ──────
    godt = [e for e in events if e["event_type"] == "status"]
    kraev(len(godt) == 1 and godt[0]["payload"].get("message"),
          "det almindelige event er urørt og har stadig sit indhold")

    hist = [e for e in events if e["source"] == "BuyTheDip"][0]
    kraev(hist["payload"]["tickers"] == ["TSLA"],
          "resten af det ramte events payload er BEVARET, ikke kasseret")
    kraev(hist["payload"]["meta"]["rows"][0]["symbol"] == "TSLA",
          "…også inde i den række der indeholdt tallet")

    # ── 4. Feltet skal NAVNGIVES, ikke bare fjernes ────────────────────────
    # ⚠ Et felt der stille bliver til null, er en fejl der er skjult i stedet
    # for rettet. Stien skal stå der, så kilden kan findes uden at gætte.
    stier = hist["payload"].get("_ikke_endelige_felter") or []
    kraev(any("rows[0].rvol" in s for s in stier),
          f"stien til feltet står i svaret ({stier})")
    kraev(any("pool_size" in s for s in stier),
          "…og feltet uden for listen er også med")
    kraev(hist["payload"]["meta"]["rows"][0]["rvol"] is None,
          "selve værdien er None, ikke en NaN der bare er flyttet")

    # ── 5. Ingen støj når der intet er at rense ────────────────────────────
    kraev("_ikke_endelige_felter" not in godt[0]["payload"],
          "et rent event får IKKE et tomt rense-felt sat på")

    # ⚠ Luk forbindelsen eksplicit. aiosqlite holder en baggrundstraad, og uden
    # en lukning haenger processen ved afslutning — hvad der i en
    # falsifikationskoersel ligner "proeven bestod" fordi den aldrig naaede at
    # melde noget.
    if j._db is not None:
        await j._db.close()



if __name__ == "__main__":
    print("journal — ikke-endelige tal i payloads\n")
    asyncio.run(koer())
    print(f"\n⚠ {FEJL} FEJL" if FEJL else "\nAlle bestod.")
    sys.exit(1 if FEJL else 0)

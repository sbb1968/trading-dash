"""
test_notes_routing.py — en note foelger sin handel, ikke sin browser
════════════════════════════════════════════════════════════════════════════════
Studio koerer ALTID paa algoserveren, uanset hvilken maskine eller telefon man
sidder ved. Derfor havde "gem note" en indbygget skaevhed: raekkerne blev LAEST
fra den valgte maskines replikerede arkiv, men skrivningen gik til algoserverens
EGEN database, hvor det trade_id ikke findes. UPDATE ramte nul raekker, endpointet
svarede 200, og noten var vaek uden en eneste fejl nogen steder.

Testen holder fire ting fast:

  A  Vaelges en ANDEN maskine, skrives noten DER — og ikke lokalt.
  B  Er den maskine slukket, kommer der en FEJL. Aldrig en tavs succes.
  C  Vaelges denne maskine (eller ingen), skrives lokalt som foer.
  D  "Alle maskiner samlet" slaar ejeren op foerst.

Hver kontrol vises ogsaa at KUNNE fejle (afsnit 5). En kontrol hvis udfald er
afgjort paa forhaand er projektets tilbagevendende fejlklasse — den maa ikke
snige sig ind i den test der skal fange den.

    python test_notes_routing.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import HTTPException

import main

FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# ── Attrapper ────────────────────────────────────────────────────────────────
# Bygget mod den FAKTISKE brug i main: async with ClientSession(...) as s,
# async with s.patch(...) as r, r.status / r.raise_for_status() / await r.json().

class FakeResp:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self):
        return self._data


class FakeSession:
    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def patch(self, url, **kw):
        SENDT.append(("PATCH", url, kw.get("json")))
        if NEDE:
            raise OSError("maskinen svarer ikke")
        return FakeResp(SVAR_STATUS, {"ok": True, "trade_id": "T1",
                                      "skrevet_paa": "fjern"})

    def get(self, url, **kw):
        SENDT.append(("GET", url, None))
        if NEDE:
            raise OSError("maskinen svarer ikke")
        return FakeResp(SVAR_STATUS, {"trade_id": "T1", "notes": "LIVE"})


class FakeAiohttp:
    ClientSession = FakeSession

    @staticmethod
    def ClientTimeout(**kw):
        return None


SENDT: list = []
NEDE = False
SVAR_STATUS = 200
LOKALT: list = []
LOKAL_SVAR = True


async def fake_local_write(journal, trade_id, notes):
    LOKALT.append((trade_id, notes))
    return LOKAL_SVAR


def nulstil():
    SENDT.clear()
    LOKALT.clear()


main.aiohttp = FakeAiohttp
main.trade_queries.update_notes_via_journal = fake_local_write
import dataclasses
# source_id er et GEMT felt, ikke en property — det udledes én gang i accounts.py.
# dataclasses.replace(account_id=..., instance_role=...) genberegner det derfor
# IKKE, og testen ville koere videre med den her maskines eget id. Saetter man det
# id lig den peer man tester imod, holder peer_url() den for "mig selv", slaar
# videresendelsen fra, og hele testen bliver groen paa den gamle opfoersel.
main.identity = dataclasses.replace(main.identity, account_id="algo",
                                    instance_role="server",
                                    source_id="algoserver")
kraev(main.identity.source_id == "algoserver",
      f"identiteten er byttet til algoserveren: {main.identity.source_id}")
kraev(main.identity.source_id != "soren_workstation",
      "og den ER forskellig fra den peer der testes imod — ellers maalte "
      "afsnit 1-2 ingenting")
main.load_peers = lambda: [
    {"id": "soren_workstation", "name": "Søren", "url": "http://win11sbb:8000",
     "enabled": True},
    {"id": "slukket_maskine", "name": "Slukket", "url": "http://ingen:8000",
     "enabled": False},
]

REQ = main.UpdateNotesRequest(notes="min note")


async def patch(archive):
    return await main.journal_update_notes("T1", REQ, archive=archive)


# ── 1. Videresendelse ────────────────────────────────────────────────────────
print("\n1. En anden maskine er valgt -> noten skrives DER")
nulstil()
svar = asyncio.run(patch("soren_workstation"))
kraev(len(SENDT) == 1 and SENDT[0][0] == "PATCH", f"ét kald sendt videre: {SENDT}")
kraev(SENDT and SENDT[0][1] == "http://win11sbb:8000/journal/trades/T1",
      "til ejerens egen URL")
kraev("archive" not in (SENDT[0][1] if SENDT else ""),
      "UDEN ?archive= — ellers ville modtageren videresende igen i ring")
kraev(SENDT and SENDT[0][2] == {"notes": "min note"}, "med notens tekst")
kraev(LOKALT == [], "⚠ og INTET blev skrevet lokalt — det var hele fejlen")
kraev(svar.get("skrevet_paa") == "soren_workstation",
      f"svaret siger hvor noten landede: {svar.get('skrevet_paa')}")

# ── 2. Maskinen er slukket ───────────────────────────────────────────────────
print("\n2. Ejeren svarer ikke -> fejl, ALDRIG tavs succes")
nulstil()
NEDE = True
try:
    asyncio.run(patch("soren_workstation"))
    kraev(False, "der kom et svar — burde have kastet")
except HTTPException as e:
    kraev(e.status_code == 503, f"503 Service Unavailable (fik {e.status_code})")
    kraev("IKKE gemt" in e.detail, f"beskeden siger det lige ud: {e.detail[:60]}")
kraev(LOKALT == [], "og noten blev IKKE i stedet lagt lokalt hvor den ikke hoerer til")
NEDE = False

# ── 3. Egen maskine ──────────────────────────────────────────────────────────
print("\n3. Denne maskine (eller ingen valgt) -> lokal skrivning som foer")
for arkiv, hvad in ((None, "ingen maskine valgt"), ("", "tom vaerdi"),
                    ("algoserver", "mit eget source_id")):
    nulstil()
    svar = asyncio.run(patch(arkiv))
    kraev(LOKALT == [("T1", "min note")], f"{hvad} -> skrevet lokalt")
    kraev(SENDT == [], f"{hvad} -> intet netvaerkskald")
    kraev(svar.get("skrevet_paa") == "algoserver", f"{hvad} -> kvitteret som lokal")

print("\n4. En ukendt eller deaktiveret maskine falder tilbage til lokal")
nulstil()
asyncio.run(patch("slukket_maskine"))
kraev(SENDT == [], "deaktiveret peer (enabled=false) videresendes ikke")

print("\n5. Lokal skrivning der rammer nul raekker -> 404")
nulstil()
LOKAL_SVAR = False
try:
    asyncio.run(patch(None))
    kraev(False, "200 OK paa en note der ikke blev gemt — den oprindelige fejl")
except HTTPException as e:
    kraev(e.status_code == 404, f"404 (fik {e.status_code})")
LOKAL_SVAR = True

# ── 6. Kontrollerne kan faktisk fejle ────────────────────────────────────────
print("\n6. ⚠ Falsifikation — kontrollerne er ikke afgjort paa forhaand")
# Uden videresendelse (den gamle opfoersel) SKAL afsnit 1 falde. Kan den ikke
# det, maaler den ikke noget.
_gemt = main.peer_url
main.peer_url = lambda s: None          # simulér koden FOER rettelsen
nulstil()
asyncio.run(patch("soren_workstation"))
kraev(LOKALT != [] and SENDT == [],
      "med videresendelsen slaaet fra skrives der lokalt — dvs. afsnit 1 "
      "ville have fejlet, og maaler altsaa noget virkeligt")
main.peer_url = _gemt

# Og attrappen for "slukket" skal reelt kunne naa igennem naar den er taendt.
nulstil()
asyncio.run(patch("soren_workstation"))
kraev(SENDT != [], "attrappen sender faktisk naar maskinen er oppe — "
                   "afsnit 2's fejl kom fra nedetiden, ikke fra en doed attrap")

# ── 7. Laesestien ────────────────────────────────────────────────────────────
print("\n7. Laesning: ejeren foerst, arkivet som reserve")
nulstil()
svar = asyncio.run(main.journal_trade_detail("T1", archive="soren_workstation"))
kraev(SENDT and SENDT[0][0] == "GET", "ejeren spoerges live foerst")
kraev(svar.get("notes") == "LIVE" and svar.get("_kilde") == "live",
      f"og hans svar bruges: {svar.get('_kilde')} — replikeringen er op til "
      f"to minutter bagud, saa en netop gemt note ville ellers forsvinde igen")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")

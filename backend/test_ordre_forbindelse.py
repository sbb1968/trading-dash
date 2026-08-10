"""
test_ordre_forbindelse.py — kan de tre vagter udløse?
════════════════════════════════════════════════════════════════════════════════
Opsætningen skal senere til Ibens maskine, hvor den før eller siden vil stå ved
siden af en rigtig konto. En vagt der aldrig er set udløse, er en formodning.

    V1  kontobekræftelse   Gatewayen styrer en ANDEN konto end den konfigurerede
    V2  paper-bekræftelse  kontoen ligner live (ikke D-præfiks)
    V3  client-id          fra registret, ikke et tilfældigt tal

⚠ Hver vagt afprøves BEGGE veje: den skal spærre på det forkerte tilfælde og
slippe det rigtige igennem. En vagt der altid spærrer, er lige så ubrugelig som
en der aldrig gør — den bliver bare slået fra i stedet for overset.

    python test_ordre_forbindelse.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import accounts
import ibkr_client_ids
import ordre_forbindelse as of

FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# ── Attrapper ───────────────────────────────────────────────────────────────
class FalskIB:
    def __init__(self, styrede): self._s = list(styrede)
    def managedAccounts(self): return self._s


class FalskConn:
    """Bygget som IBKRConnection ville se ud efter connect()."""
    oprettet: list = []

    def __init__(self, styrede, ok=True, **kw):
        self.ib = FalskIB(styrede)
        self._ok = ok
        self.connected = False
        self.kw = kw
        FalskConn.oprettet.append(kw)

    async def connect(self):
        self.connected = self._ok
        return self._ok

    async def disconnect(self):
        self.connected = False


def med(styrede, profil, ok=True):
    """Kør of.hent() mod en attrap-forbindelse og en given profil."""
    of._forbindelse = None
    FalskConn.oprettet.clear()
    accounts.ordre_forbindelse = lambda: dict(profil)
    of.IBKRConnection = lambda **kw: FalskConn(styrede, ok, **kw)
    return asyncio.get_event_loop().run_until_complete(_hent())


async def _hent():
    try:
        c = await of.hent()
        return ("ok", c)
    except of.OrdreForbindelseFejl as e:
        return ("spaerret", str(e))


PAPER = {"host": "127.0.0.1", "port": 4002, "konto": "DUQ441063",
         "bruger": "fasteriben2", "tillad_live": False}

_gemt_of = accounts.ordre_forbindelse
_gemt_conn = of.IBKRConnection
asyncio.set_event_loop(asyncio.new_event_loop())

print("\n0. Det rigtige tilfælde slipper igennem")
udfald, c = med(["DUQ441063"], PAPER)
kraev(udfald == "ok", f"korrekt opsætning forbinder ({udfald})")
kraev(FalskConn.oprettet and FalskConn.oprettet[0]["port"] == 4002,
      f"porten fra profilen bruges: {FalskConn.oprettet[0].get('port') if FalskConn.oprettet else '?'}")
kraev(FalskConn.oprettet and FalskConn.oprettet[0]["account"] == "DUQ441063",
      "kontoen bindes til forbindelsen")

print("\n1. ⚠ V1 — Gatewayen styrer en ANDEN konto")
udfald, besked = med(["DUO509856"], PAPER)
kraev(udfald == "spaerret", f"spærret ({udfald})")
kraev("FORKERT KONTO" in str(besked), f"og den siger hvorfor: {str(besked)[:70]}")
kraev("DUO509856" in str(besked) and "DUQ441063" in str(besked),
      "beskeden nævner BEGGE konti — ellers kan man ikke se hvad der er galt")

print("\n2. ⚠ V1 — Gatewayen styrer INGEN konto")
udfald, besked = med([], PAPER)
kraev(udfald == "spaerret", "spærret ved tom kontoliste")

print("\n3. ⚠ V2 — konfigurationen peger på en LIVE-konto")
live = dict(PAPER, konto="U23100448")
udfald, besked = med(["U23100448"], live)
kraev(udfald == "spaerret", f"spærret ({udfald})")
kraev("LIVE" in str(besked), f"og den siger hvorfor: {str(besked)[:70]}")
kraev(not FalskConn.oprettet,
      "⚠ og der blev ALDRIG oprettet en forbindelse — det billige tjek kom først, "
      "så vi aldrig åbner en session mod noget vi ikke ville røre")

print("\n4. ⚠ V2 — konfigurationen siger paper, men Gatewayen styrer live")
udfald, besked = med(["U23100448"], PAPER)
kraev(udfald == "spaerret", "spærret")
kraev("LIVE" in str(besked) or "FORKERT KONTO" in str(besked),
      f"virkeligheden tjekkes, ikke kun konfigurationen: {str(besked)[:60]}")

print("\n5. V2 slipper live igennem når det er EKSPLICIT tilladt")
tilladt = dict(PAPER, konto="U23100448", tillad_live=True)
udfald, c = med(["U23100448"], tilladt)
kraev(udfald == "ok", f"tillad_live=True forbinder ({udfald})")
kraev(FalskConn.oprettet and FalskConn.oprettet[0]["paper_trading"] is False,
      "og forbindelsen ved at den ikke er paper")

print("\n6. V3 — client-id kommer fra registret")
kraev(of.CLIENT_ID == ibkr_client_ids.ORDRE == 201,
      f"ORDRE = {of.CLIENT_ID}, ikke et tilfældigt tal")
kraev(of.CLIENT_ID != ibkr_client_ids.BACKEND,
      "og den er forskellig fra backendens — begge kører på SAMME maskine")
kraev(of.CLIENT_ID not in ibkr_client_ids.SCRIPTS.values(),
      "og kolliderer ikke med noget script")

print("\n7. Forbindelsen der ikke kommer op")
udfald, besked = med(["DUQ441063"], PAPER, ok=False)
kraev(udfald == "spaerret", "spærret når Gatewayen ikke svarer")
kraev("Gateway" in str(besked), f"og den peger på Gatewayen: {str(besked)[:60]}")

print("\n8. orderRef markerer manuel oprindelse")
ref = of.order_ref("iben")
kraev(ref.startswith("manuel:"), f"orderRef = {ref}")
kraev("iben" in ref, "og hvem det var — ellers er handlen ejerløs som SHAZ")

print("\n9. Uden profil er der ingen adskillelse — og det siges")
accounts.ordre_forbindelse = lambda: None
of._forbindelse = None
kraev(not of.konfigureret(), "konfigureret() er False uden profil")
udfald, besked = asyncio.get_event_loop().run_until_complete(_hent())
kraev(udfald == "spaerret" and "delte forbindelse" in str(besked),
      f"og hent() forklarer hvad der så sker: {str(besked)[:60]}")

accounts.ordre_forbindelse = _gemt_of
of.IBKRConnection = _gemt_conn

print("\n" + "=" * 74)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent — alle tre vagter kan baade spaerre og slippe igennem.")

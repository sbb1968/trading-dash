"""
test_ordre_kaploeb.py — to samtidige kaldere maa aldrig blive til to clientId 201
════════════════════════════════════════════════════════════════════════════════
⚠ FEJLEN DENNE TEST HOLDER LUKKET (maalt 10-09-2026, Ibens workstation).

Gatewayen paa 4002 KOERTE. Vi laaste den ude selv.

Tre kaldere deler `hent()`: /orders/list (Ordre-vinduet, flere kald i sekundet),
/account/dash-snapshot (watchlisten, hvert 5. sekund) og ordrevejen. `hent()`
laaste ikke, og den tildelte modulets `_forbindelse` FOER `await connect()`:

    1. A bygger et objekt, venter paa connect().
    2. B kommer ind under den await, ser at objektet ikke er `connected`,
       og bygger sit eget — med samme clientId 201.
    3. Den der taber faar 326 "client id is already in use", og dens oprydning
       satte `_forbindelse = None` — altsaa ogsaa VINDEREN, der stadig var
       forbundet og stadig holdt 201. Foraeldreloes: ingen kunne naa den,
       ingen lukkede den.
    4. Derefter fik hvert forsoeg 326, fordi vi selv sad paa id'et.

I backend_2026-09-10_15-52.log ses det raat: 15:52:25 lykkedes en forbindelse
("Konto: ['DUQ441063']") og blev kasseret i samme sekund. 15:52:47 lykkedes
endnu en — den holdt syv sekunder. De fire backend-sessioner den dag overlappede
ikke, saa kollisionen kom ikke fra to processer. Den kom herfra.

Begge symptomer Iben saa faldt ud af netop dette:
  · "kan ikke handle — manglende adgang"  (main.py, ordrevejen)
  · P/L stod paa "—"                       (dash-snapshot -> ok:false)

⚠ AFKOELINGEN ALENE VAR IKKE NOK, og det er derfor testen findes. Den daempede
frekvensen, men ordrevejen kalder `hent(tving=True)` og gaar med vilje uden om
afkoelingen — saa et menneske der trykkede Saelg kunne stadig kollidere med en
poller. Laasen er det der lukker det; afkoelingen sparer forsoeg.

    python test_ordre_kaploeb.py
"""
from __future__ import annotations

import asyncio
import logging
import sys

import accounts
import ordre_forbindelse as OF

logging.disable(logging.CRITICAL)

KONTO = "DUQ441063"
CONNECT_TID = 0.05      # connect() skal TAGE tid — ellers er der intet kapløb at vise

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


class FalskIB:
    def __init__(self, konti: list[str]) -> None:
        self._konti = konti

    def managedAccounts(self) -> list[str]:
        return self._konti


class FalskForbindelse:
    """Taeller hvor mange klienter der bygges, og hvor mange der er LEVENDE.

    `levende` er det tal der betyder noget: hver levende klient holder clientId
    201. Bliver den nogensinde > 1, er fejlen tilbage.
    """
    bygget = 0
    forsoeg = 0
    levende = 0
    konti: list[str] = [KONTO]
    lykkes = True

    def __init__(self, **kw) -> None:
        type(self).bygget += 1
        self.connected = False
        self.ib = FalskIB(type(self).konti)
        self.port = kw.get("port")
        self.account = kw.get("account")

    async def connect(self) -> bool:
        type(self).forsoeg += 1
        await asyncio.sleep(CONNECT_TID)          # her opstod kapløbet
        if not type(self).lykkes:
            return False
        self.connected = True
        type(self).levende += 1
        return True

    def disconnect(self) -> None:
        if self.connected:
            type(self).levende -= 1
        self.connected = False

    @classmethod
    def nulstil(cls, *, konti: list[str] | None = None, lykkes: bool = True) -> None:
        cls.bygget = cls.forsoeg = cls.levende = 0
        cls.konti = konti if konti is not None else [KONTO]
        cls.lykkes = lykkes


async def _ryd() -> None:
    """Modultilstand mellem delproever. `luk()` alene ville disconnecte den
    falske klient og forstyrre taellingen, saa vi nulstiller direkte."""
    OF._forbindelse = None
    OF._sidste_fejl = ""
    OF._sidste_fejl_tid = 0.0


async def koer() -> None:
    accounts.ordre_forbindelse = lambda: {          # type: ignore[assignment]
        "host": "127.0.0.1", "port": 4002, "konto": KONTO, "bruger": "test"}
    OF.IBKRConnection = FalskForbindelse            # type: ignore[assignment]

    # ── 1. Ti pollere paa én gang giver ÉN klient ─────────────────────────
    # Det er hele sagen. Foer laasen ville flere af dem bygge hver sit objekt
    # med clientId 201, og taberens oprydning kassere vinderen.
    FalskForbindelse.nulstil()
    await _ryd()
    svar = await asyncio.gather(*[OF.hent() for _ in range(10)])
    kraev(FalskForbindelse.bygget == 1,
          f"10 samtidige kaldere byggede ÉN klient (byggede {FalskForbindelse.bygget})")
    kraev(FalskForbindelse.levende == 1,
          f"kun ét levende clientId 201 (levende: {FalskForbindelse.levende})")
    kraev(all(s is svar[0] for s in svar),
          "alle ti fik den SAMME forbindelse")
    kraev(OF._forbindelse is svar[0],
          "vinderen er offentliggjort — ikke kasseret af en taber")

    # ── 2. Et menneske midt i en pollers forsoeg ──────────────────────────
    # tving=True springer AFKOELINGEN over, men ikke laasen. Ellers ville
    # praecis det tryk paa Saelg udloese den anden 201.
    FalskForbindelse.nulstil()
    await _ryd()
    poller = asyncio.create_task(OF.hent())
    await asyncio.sleep(CONNECT_TID / 2)            # ind mens connect() koerer
    menneske = await OF.hent(tving=True)
    kraev(FalskForbindelse.bygget == 1,
          f"tving=True byggede ikke en klient nr. 2 (byggede {FalskForbindelse.bygget})")
    kraev(menneske is await poller, "mennesket og polleren deler forbindelse")

    # ── 3. En eksisterende forbindelse genbruges uden at bygge nyt ────────
    FalskForbindelse.nulstil()
    await _ryd()
    foerste = await OF.hent()
    igen    = await OF.hent()
    kraev(FalskForbindelse.bygget == 1 and igen is foerste,
          "en levende forbindelse genbruges (ingen ny socket)")

    # ── 4. Fejler forbindelsen, stormer de ventende ikke afsted ───────────
    # ~170 forsoeg paa 14 minutter var symptomet. Afkoelingen tjekkes INDE i
    # laasen, saa de fire der stod i koe arver den frem for at starte forfra.
    FalskForbindelse.nulstil(lykkes=False)
    await _ryd()
    resultater = await asyncio.gather(*[OF.hent() for _ in range(5)],
                                      return_exceptions=True)
    kraev(FalskForbindelse.forsoeg == 1,
          f"5 samtidige kaldere gav ÉT forbindelsesforsoeg (gav {FalskForbindelse.forsoeg})")
    kraev(all(isinstance(r, OF.OrdreForbindelseFejl) for r in resultater),
          "alle fem fik en spaerring at vide — ingen fik None")
    kraev(FalskForbindelse.levende == 0,
          "det mislykkede objekt holder ikke clientId 201")
    kraev(OF._forbindelse is None,
          "en fejlet oprettelse efterlader ingen global")

    # ── 5. Vagt V1: forkert konto spaerrer OG afkoeler ────────────────────
    # Foer satte en vagt-afvisning ikke afkoelingen, saa watchlistens polling
    # forbandt og lukkede igen hvert 5. sekund mod en Gateway vi allerede
    # vidste vi ikke ville bruge.
    FalskForbindelse.nulstil(konti=["DUN748991"])
    await _ryd()
    try:
        await OF.hent()
        kraev(False, "forkert konto burde spaerre")
    except OF.OrdreForbindelseFejl as e:
        kraev("FORKERT KONTO" in str(e), "V1 siger tydeligt at kontoen er forkert")
    kraev(FalskForbindelse.levende == 0, "V1 lukkede forbindelsen efter sig")
    kraev(OF._forbindelse is None, "V1 efterlod ingen global mod en lukket forbindelse")
    kraev(OF._sidste_fejl != "", "V1-afvisningen saetter afkoelingen")

    bygget_foer = FalskForbindelse.bygget
    try:
        await OF.hent()
    except OF.OrdreForbindelseFejl as e:
        kraev("proever igen om" in str(e),
              "naeste poller afvises af afkoelingen, ikke af en ny session")
    kraev(FalskForbindelse.bygget == bygget_foer,
          "en afkoelet poller bygger ikke en ny klient mod den forkerte Gateway")

    await _ryd()


def main() -> int:
    print("  ── ordreforbindelsens kapløb (clientId 201) ──")
    asyncio.run(koer())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())

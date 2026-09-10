"""
test_ordre_afkoeling.py — en poller maa ikke kunne hamre paa Gatewayen
════════════════════════════════════════════════════════════════════════════════
10-09-2026 faldt Ibens Gateway kl. 15:59. Da den kom op igen, blev
ordreforbindelsen STADIG afvist — og "grønt i Gateway" så ud til at modsige
koden.

⚠ AARSAGEN VAR EN RETRY-STORM, OG DEN VAR SELVFORSKYLDT.
`hent()` laaser ikke: den nulstiller `_forbindelse` og proever forfra ved naeste
kald. Det er rigtigt naar et menneske kalder. Men watchlisten begyndte
10-09 at hente /account/dash-snapshot hvert 5. sekund, og paa en maskine med
`ordre_forbindelse` gaar det kald GENNEM `hent()`. Resultat: ~170
forbindelsesforsoeg paa 14 minutter, alle med clientId 201.

Og hvert forsoeg ryddede ikke op efter sig. `IBKRConnection.connect()` fangede
undtagelsen og returnerede False uden at kalde `disconnect()`, saa klienten blev
liggende med sit clientId. Naeste forsoeg med samme id kunne da blive afvist af
den grund alene — laenge efter at den oprindelige aarsag var vaek.

To rettelser, og testen daekker begge:
  1. connect() og hent() lukker det mislykkede objekt
  2. AFKOELING_SEK bremser pollere — men `tving=True` gaar udenom, saa et
     menneske der trykker Saelg ikke skal vente

⚠ TESTEN SKAL SE BEGGE UDFALD. En afkoeling der ALTID afviser ville bestaa en
test der kun kigger efter "hurtigt svar" — og saa kunne Iben ikke saelge.
Derfor maales baade at det andet forsoeg er hurtigt OG at tving=True er
langsomt (fordi det faktisk forsoeger).

    python test_ordre_afkoeling.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time

import accounts
import ordre_forbindelse as OF

# Stille — de forventede forbindelsesfejl er selve fiksturet, ikke stoej.
logging.disable(logging.CRITICAL)

# ⚠ FIKSTUR: en port hvor der GARANTERET ikke lytter noget. Uden den ville
# testen afhaenge af om en Gateway tilfaeldigvis koerer paa maskinen.
DOED_PORT = 4999

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


async def koer() -> None:
    accounts.ordre_forbindelse = lambda: {          # type: ignore[assignment]
        "host": "127.0.0.1", "port": DOED_PORT,
        "konto": "DUQ441063", "bruger": "test"}
    OF.AFKOELING_SEK = 3.0
    await OF.luk()

    # ── 1. Foerste forsoeg: et RIGTIGT forsoeg, altsaa langsomt ────────────
    t = time.monotonic()
    try:
        await OF.hent()
        kraev(False, "foerste forsoeg burde fejle mod en doed port")
        return
    except OF.OrdreForbindelseFejl:
        d1 = time.monotonic() - t
    kraev(d1 > 0.3, f"1. forsoeg forsoeger faktisk at forbinde ({d1:.1f} s)")

    # ── 2. Andet forsoeg: afkoelet, altsaa oejeblikkeligt ──────────────────
    t = time.monotonic()
    try:
        await OF.hent()
    except OF.OrdreForbindelseFejl as e:
        d2 = time.monotonic() - t
        besked = str(e)
    kraev(d2 < 0.3, f"2. forsoeg er afkoelet — ingen ny socket ({d2:.2f} s)")
    # ⚠ Fejlteksten skal sige at der er afkoeling, ellers ligner det bare
    # samme fejl igen og fejlsoegningen gaar det forkerte sted hen.
    kraev("proever igen om" in besked,
          "afkoelingen siger HVORNAAR den proever igen")

    # ── 3. tving=True: et menneske venter, saa der forsoeges igen ──────────
    t = time.monotonic()
    try:
        await OF.hent(tving=True)
    except OF.OrdreForbindelseFejl:
        d3 = time.monotonic() - t
    kraev(d3 > 0.3, f"tving=True gaar UDEN OM afkoelingen ({d3:.1f} s)")

    # ── 4. Efter afkoelingen proeves der af sig selv igen ──────────────────
    await asyncio.sleep(OF.AFKOELING_SEK + 0.2)
    t = time.monotonic()
    try:
        await OF.hent()
    except OF.OrdreForbindelseFejl:
        d4 = time.monotonic() - t
    kraev(d4 > 0.3, f"efter afkoelingen forsoeges der igen ({d4:.1f} s)")

    # ── 5. Rydder et mislykket forsoeg op efter sig? ───────────────────────
    kraev(OF._forbindelse is None,
          "det mislykkede objekt er sluppet (ingen klient med clientId 201 tilbage)")

    await OF.luk()
    kraev(OF._sidste_fejl == "", "luk() nulstiller afkoelingen")


def main() -> int:
    print("  ── ordreforbindelsens afkoeling ──")
    asyncio.run(koer())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())

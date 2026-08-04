"""
test_nyse_kalender.py — verificér handelskalenderen mod kendte fakta
═══════════════════════════════════════════════════════════════════════════════════
Kalenderen er rekonstrueret fra regler, ikke hentet fra NYSE. Den er derfor kun
noget vaerd i det omfang den er holdt op mod dage vi VED hvad var. Testene her er
den kontrol.

    python test_nyse_kalender.py
"""
from __future__ import annotations

import sys
from datetime import date

from nyse_kalender import (er_halv_dag, er_handelsdag, forventede_rth_minutter,
                           handelsdage)

FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


print("\n[1] Aarets antal handelsdage (kendte facitter)")
# NYSE-aar ligger paa 250-253 dage; disse tal er velkendte.
for aar, forventet in [(2019, 252), (2020, 253), (2021, 252), (2022, 251),
                       (2023, 250), (2024, 252), (2025, 250)]:
    n = len(handelsdage(date(aar, 1, 1), date(aar, 12, 31)))
    paastand(n == forventet, f"{aar}: {n} handelsdage (forventet {forventet})")

print("\n[2] Faste helligdage")
for d, navn in [(date(2024, 1, 1), "nytaarsdag"),
                (date(2024, 1, 15), "MLK"),
                (date(2024, 2, 19), "Washingtons foedselsdag"),
                (date(2024, 3, 29), "Langfredag"),
                (date(2024, 5, 27), "Memorial Day"),
                (date(2024, 6, 19), "Juneteenth"),
                (date(2024, 7, 4), "4. juli"),
                (date(2024, 9, 2), "Labor Day"),
                (date(2024, 11, 28), "Thanksgiving"),
                (date(2024, 12, 25), "juledag")]:
    paastand(not er_handelsdag(d), f"{d} lukket ({navn})")

print("\n[3] Observationsregler")
paastand(er_handelsdag(date(2021, 12, 31)),
         "2021-12-31 AABEN — nytaarsdag paa loerdag observeres ikke")
paastand(not er_handelsdag(date(2023, 1, 2)),
         "2023-01-02 lukket — nytaarsdag paa soendag rykker til mandag")
paastand(not er_handelsdag(date(2021, 7, 5)),
         "2021-07-05 lukket — 4. juli paa soendag rykker til mandag")
paastand(not er_handelsdag(date(2020, 7, 3)),
         "2020-07-03 lukket — 4. juli paa loerdag rykker til fredag")
paastand(not er_handelsdag(date(2021, 12, 24)),
         "2021-12-24 lukket — juledag paa loerdag rykker til fredag")
paastand(er_handelsdag(date(1997, 1, 20)),
         "1997-01-20 AABEN — MLK foerst fra 1998")
paastand(not er_handelsdag(date(1998, 1, 19)), "1998-01-19 lukket — foerste MLK")
paastand(er_handelsdag(date(2021, 6, 18)),
         "2021-06-18 AABEN — Juneteenth foerst fra 2022")

print("\n[4] Ad-hoc lukninger")
for d, navn in [(date(2001, 9, 11), "11. september"),
                (date(2001, 9, 14), "11. september, sidste dag"),
                (date(2012, 10, 30), "Sandy"),
                (date(2018, 12, 5), "Bush"),
                (date(2025, 1, 9), "Carter")]:
    paastand(not er_handelsdag(d), f"{d} lukket ({navn})")

print("\n[5] Halve dage")
for d in [date(2024, 11, 29), date(2023, 11, 24), date(2019, 11, 29)]:
    paastand(er_halv_dag(d), f"{d} halv dag (dagen efter Thanksgiving)")
for d, hvorfor in [(date(2024, 7, 3), "4. juli paa torsdag"),
                   (date(2019, 7, 3), "4. juli paa torsdag"),
                   (date(2025, 7, 3), "4. juli paa fredag"),
                   (date(2024, 12, 24), "juleaften paa tirsdag"),
                   (date(2019, 12, 24), "juleaften paa tirsdag"),
                   (date(2018, 12, 24), "juleaften paa mandag")]:
    paastand(er_halv_dag(d), f"{d} halv dag ({hvorfor})")
paastand(not er_halv_dag(date(2021, 7, 2)),
         "2021-07-02 HEL dag — 4. juli faldt i weekenden")
paastand(not er_halv_dag(date(2008, 12, 26)),
         "2008-12-26 HEL dag — praksis for dagen efter juledag ophoert")
paastand(er_halv_dag(date(2003, 12, 26)),
         "2003-12-26 halv dag — mens praksis stadig gjaldt")

print("\n[6] Forventede RTH-minutter")
paastand(forventede_rth_minutter(date(2024, 6, 18)) == 390, "almindelig dag = 390 min")
paastand(forventede_rth_minutter(date(2024, 11, 29)) == 210, "halv dag = 210 min")
paastand(forventede_rth_minutter(date(2024, 12, 25)) == 0, "helligdag = 0 min")
paastand(forventede_rth_minutter(date(2024, 6, 15)) == 0, "loerdag = 0 min")

print("\n[7] En halv dag kan ikke ogsaa vaere lukket")
for aar in range(1993, 2027):
    for d in sorted(__import__("nyse_kalender").halve_dage(aar)):
        if not er_handelsdag(d):
            FEJL.append(f"{d} er baade halv dag og lukket")
paastand(not [f for f in FEJL if "baade halv dag" in f],
         "ingen dato er baade halv dag og lukket, 1993-2026")

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")

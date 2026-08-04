"""
test_vol_serier.py — L1 og L2 skal fange præcis det de er bygget til
═══════════════════════════════════════════════════════════════════════════════════
L1's fejl giver ikke mistaenkelige tal. Den giver plausible tal der er systematisk
forkerte. Derfor er den eneste maade at vide at vaernet virker, at fodre det den
konkrete situation: fjern 2011-09-12 fra tre serier, behold den i SPY, og kraev at
INTET forskydes.

Testen bruger den VIRKELIGE dato fra de virkelige data, ikke en opdigtet.

    python test_vol_serier.py
"""
from __future__ import annotations

import math
import sys
from datetime import date

from nyse_kalender import er_handelsdag, handelsdage
from vol_serier import (Raekke, Sammenstillingsfejl, droppede_dage, sammenstil,
                        tilstoedende, vurder_status)

FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


# Den virkelige situation: alle tre CBOE-indeks mangler 2011-09-12, SPY har den.
HULLET = date(2011, 9, 12)
DAGE = handelsdage(date(2011, 9, 1), date(2011, 9, 30))

spy = {d: 100.0 + i for i, d in enumerate(DAGE)}
vix = {d: 20.0 + i for i, d in enumerate(DAGE) if d != HULLET}

print(f"\n{len(DAGE)} handelsdage i september 2011 · hullet: {HULLET}")
paastand(HULLET in spy and HULLET not in vix,
         "fikstur: SPY har dagen, VIX mangler den — som i de virkelige data")

print("\n[1] L1 — et hul forskyder INTET")
r = sammenstil({"SPY": spy, "VIX": vix})
pr_dag = {x.dag: x for x in r}

hul = pr_dag[HULLET]
paastand(math.isnan(hul.vaerdier["VIX"]), "VIX er nan paa hul-dagen")
paastand(hul.vaerdier["SPY"] == spy[HULLET], "SPY er uroert paa hul-dagen")
paastand(hul.manglende == ["VIX"], "og det staar hvilken serie der mangler")

# DEN AFGOERENDE PAASTAND: dagen EFTER hullet skal have sin EGEN vaerdi, ikke den
# foerste tilgaengelige efter forskydning. Ved positionssammenstilling ville VIX her
# baere vaerdien fra dagen derefter — og alt fremefter ville vaere en dag forkert.
efter = [d for d in DAGE if d > HULLET]
for d in efter:
    if pr_dag[d].vaerdier["VIX"] != vix[d]:
        FEJL.append(f"VIX forskudt paa {d}")
paastand(not [f for f in FEJL if "forskudt" in f],
         f"alle {len(efter)} dage EFTER hullet baerer deres egen vaerdi — ingen forskydning")

# Og modsat: hvis sammenstillingen VAR positionsbaseret, ville dette fange den.
positionelt = list(vix.values())
d_efter = efter[0]
paastand(positionelt[DAGE.index(d_efter)] != vix[d_efter],
         "kontrol: en POSITIONEL opstilling ville have givet den forkerte vaerdi her "
         "— saa testen kan faktisk fejle")

print("\n[2] L1 — ingen funktion i modulet tager en liste og et indeks")
import inspect
import vol_serier
kilde = inspect.getsource(vol_serier)
paastand("dict[date," in kilde, "signaturerne er noeglet paa dato")
paastand(tilstoedende(r, "SPY")[0][0] == DAGE[0],
         "tilstoedende() returnerer (dato, vaerdi) — datoen foelger med")

print("\n[3] L2 — CFE-only-dage DROPPES, ikke degraderes")
# De fire virkelige dage hvor VX havde data men NYSE var lukket.
CFE_DAGE = [date(2024, 1, 15), date(2024, 5, 27), date(2024, 6, 19), date(2025, 1, 9)]
for d in CFE_DAGE:
    paastand(not er_handelsdag(d), f"{d} er ikke en NYSE-handelsdag")

vx = {d: 15.0 for d in handelsdage(date(2024, 1, 2), date(2024, 1, 31))}
vx[CFE_DAGE[0]] = 99.0        # MLK — CFE aabent, NYSE lukket
r2 = sammenstil({"VX": vx}, date(2024, 1, 2), date(2024, 1, 31))
dage2 = {x.dag for x in r2}
paastand(CFE_DAGE[0] not in dage2,
         "MLK-dagen er DROPPET af sammenstillingen — ikke med som DEGRADED")
paastand(all(er_handelsdag(x.dag) for x in r2),
         "hver eneste raekke er en NYSE-handelsdag")

dropped = droppede_dage({"VX": vx})
paastand(CFE_DAGE[0] in dropped and dropped[CFE_DAGE[0]] == ["VX"],
         "men den RAPPORTERES som droppet — at droppe i tavshed er ikke i orden")

print("\n[4] L2 — de to tilfaelde maa ikke forveksles")
# NYSE aaben + serie mangler = DEGRADED. NYSE lukket = slet ikke en raekke.
st, kf = vurder_status(hul, ["SPY", "VIX"], i_dag=HULLET)
paastand(st == "DEGRADED", f"NYSE aaben, VIX mangler -> {st}")
paastand(0 < kf < 1, f"konfidens nedsat, ikke nul: {kf}")

hel = pr_dag[DAGE[0]]
st, kf = vurder_status(hel, ["SPY", "VIX"], i_dag=DAGE[0])
paastand(st == "OK" and kf == 1.0, "alle komponenter til stede -> OK, konfidens 1,0")

print("\n[5] STALE — klodsen skal NAEGTE at udtale sig, ikke gentage i gaar")
st, kf = vurder_status(hel, ["SPY", "VIX"], i_dag=DAGE[0] + __import__("datetime").timedelta(days=30))
paastand(st == "STALE" and kf == 0.0, f"30 dage gammel -> {st}, konfidens {kf}")

tom = Raekke(dag=DAGE[0], vaerdier={"SPY": float("nan"), "VIX": float("nan")},
             manglende=["SPY", "VIX"])
st, kf = vurder_status(tom, ["SPY", "VIX"], i_dag=DAGE[0])
paastand(st == "STALE", "alt mangler -> STALE, ikke DEGRADED med konfidens 0")

print("\n[6] En anden kalender afvises frem for at blive gaettet")
try:
    sammenstil({"VX": vx}, kalender="CFE")
    paastand(False, "kalender='CFE' afvises")
except Sammenstillingsfejl as e:
    paastand("NYSE" in str(e), f"afvist med begrundelse: {str(e)[:60]}…")

print("\n[7] Tom eller manglende serie fejler frem for at give tomme raekker")
paastand(sammenstil({}) == [], "ingen serier -> tom liste, ikke et nedbrud")
try:
    from vol_serier import laes_serie
    laes_serie("FINDESIKKE", "1 day")
    paastand(False, "en serie der ikke findes rejser Sammenstillingsfejl")
except Sammenstillingsfejl:
    paastand(True, "en serie der ikke findes rejser Sammenstillingsfejl")

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")

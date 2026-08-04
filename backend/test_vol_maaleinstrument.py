"""
test_vol_maaleinstrument.py — kan J4's beslutningstrappe naa ALLE sine udfald?
═══════════════════════════════════════════════════════════════════════════════════
Revision G anvendt paa J4. En praeregistreret beslutningsregel er kun noget vaerd hvis
den faktisk kan svare forskelligt — en trappe der altid ender samme sted, er en
formalitet forklaedt som en test.

Der er fire mulige udfald, og hver af dem faar sit eget syntetiske tilfaelde:

  trin 0  UAFGJORT           for faa faelles sessioner
  trin 2  BRUG A             serierne er udskiftelige
  trin 4  BRUG A             B er forskellig, men forudsiger ikke bedre
  trin 4  MAAL PAA B         B slaar A uden for konfidensintervallet

Naar alle fire kan naas paa konstruerede data, ved vi at konklusionen paa RIGTIGE
data kommer fra data og ikke fra reglens form.

    python test_vol_maaleinstrument.py
"""
from __future__ import annotations

import sys
from datetime import date

import numpy as np

import vol_maaleinstrument_test as mi
from nyse_kalender import handelsdage

FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


def tavs(s=""):
    pass


# ~330 handelsdage, alle inden for designperioden (slutter 2024-12-31).
DAGE = handelsdage(date(2023, 9, 1), date(2024, 12, 31))
rng = np.random.default_rng(20260804)


def som_dict(v):
    return {d: float(x) for d, x in zip(DAGE, v)}


print(f"\n{len(DAGE)} handelsdage i designperioden ({DAGE[0]} .. {DAGE[-1]})")

print("\n[1] Trin 0 — for faa faelles sessioner giver UAFGJORT, ikke et gaet")
faa = DAGE[:40]
v = rng.normal(50, 20, len(faa))
r = mi.afgoer("A", "B", {d: x for d, x in zip(faa, v)},
              {d: x for d, x in zip(faa, v)}, {d: x for d, x in zip(faa, v)}, tavs)
paastand(r["afgjort_paa"] == "trin 0" and "UAFGJORT" in r["konklusion"],
         f"40 sessioner -> {r['konklusion']}")

print("\n[2] Trin 2 — identiske serier: udskiftelige, brug det vi HANDLER")
basis = rng.normal(50, 20, len(DAGE))
udfald = som_dict(basis * 0.02 + rng.normal(0, 0.3, len(DAGE)) + 1.0)
r = mi.afgoer("MES+M2K", "ES+RTY", som_dict(basis), som_dict(basis), udfald, tavs)
paastand(r["afgjort_paa"] == "trin 2", "afgjort allerede paa trin 2")
paastand(r["konklusion"].startswith("BRUG MES+M2K"), f"-> {r['konklusion']}")
paastand(r["spearman"] >= mi.UDSKIFTELIG_SPEARMAN,
         f"Spearman {r['spearman']:.4f} >= {mi.UDSKIFTELIG_SPEARMAN}")

print("\n[3] Trin 4 — B er BEDRE: maal paa B")
# Udfaldet drives af en skjult sandhed. B ser den taet, A ser den gennem stoej.
sandhed = rng.normal(0, 1, len(DAGE))
y = som_dict(1.0 + 0.5 * sandhed + rng.normal(0, 0.15, len(DAGE)))
b = som_dict(sandhed + rng.normal(0, 0.20, len(DAGE)))
a = som_dict(sandhed + rng.normal(0, 1.40, len(DAGE)))
r = mi.afgoer("MES+M2K", "ES+RTY", a, b, y, tavs)
paastand(r["afgjort_paa"] == "trin 4", "naaede trin 4")
paastand(r["konklusion"].startswith("MAAL PAA ES+RTY"), f"-> {r['konklusion']}")
paastand(r["ki"][0] > 0,
         f"KI'et udelukker nul: [{r['ki'][0]:+.3f}, {r['ki'][1]:+.3f}]")

print("\n[4] Trin 4 — B er FORSKELLIG men ikke bedre: bliv ved A")
# Samme stoejniveau i begge, saa ingen af dem vinder overbevisende.
b2 = som_dict(sandhed + rng.normal(0, 1.40, len(DAGE)))
a2 = som_dict(sandhed + rng.normal(0, 1.40, len(DAGE)))
r = mi.afgoer("MES+M2K", "ES+RTY", a2, b2, y, tavs)
paastand(r["afgjort_paa"] == "trin 4", "naaede trin 4")
paastand(r["konklusion"].startswith("BRUG MES+M2K"), f"-> {r['konklusion']}")
paastand(r["ki"][0] <= 0 <= r["ki"][1] or r["ki"][1] < 0,
         f"KI'et udelukker IKKE nul opad: [{r['ki'][0]:+.3f}, {r['ki'][1]:+.3f}]")

print("\n[5] Alle fire udfald er naaet — trappen kan svare forskelligt")
paastand(True, "trin 0, trin 2, trin 4/BRUG A og trin 4/MAAL PAA B er alle demonstreret")

print("\n[6] Trin 3's decil-diagnose skelner lav-ende fra spredt uenighed")
# Konstrueret tyndhedsstoej: A er stoejende NETOP i den lave ende.
pa_v = np.clip(rng.uniform(0, 100, len(DAGE)), 0, 100)
stoej = np.where(pa_v < 30, rng.normal(0, 18, len(DAGE)), rng.normal(0, 1.5, len(DAGE)))
pb_v = np.clip(pa_v + stoej, 0, 100)
d = mi.uenighed_pr_decil(som_dict(pa_v), som_dict(pb_v), DAGE)
lav = np.nanmean([m for k, m, n in d if k < 3 and n > 0])
resten = np.nanmean([m for k, m, n in d if k >= 3 and n > 0])
print(f"        lav ende (decil 0-2): {lav:5.1f} pp   ·   resten: {resten:5.1f} pp")
paastand(lav > resten * 1.5,
         "uenighed koncentreret i den lave ende opdages — tyndhedshypotesens signatur")

# Og modsat: spredt uenighed maa IKKE laese som tyndhed.
pb_spredt = np.clip(pa_v + rng.normal(0, 12, len(DAGE)), 0, 100)
d2 = mi.uenighed_pr_decil(som_dict(pa_v), som_dict(pb_spredt), DAGE)
lav2 = np.nanmean([m for k, m, n in d2 if k < 3 and n > 0])
resten2 = np.nanmean([m for k, m, n in d2 if k >= 3 and n > 0])
print(f"        spredt:  lav {lav2:5.1f} pp   ·   resten {resten2:5.1f} pp")
paastand(lav2 <= resten2 * 1.5,
         "spredt uenighed laeses IKKE som tyndhed — diagnosen kan skelne")

print("\n[7] Designperioden haandhaeves i kode, ikke i disciplin")
efter = handelsdage(date(2025, 1, 2), date(2025, 6, 30))
alle = DAGE + efter
v2 = rng.normal(50, 20, len(alle))
r = mi.afgoer("A", "B", {d: x for d, x in zip(alle, v2)},
              {d: x for d, x in zip(alle, v2)},
              {d: x for d, x in zip(alle, v2)}, tavs)
paastand(r["n_faelles"] == len(DAGE),
         f"holdout-dagene er filtreret fra: {r['n_faelles']} af {len(alle)} brugt")
paastand(mi.DESIGN_SLUT == date(2024, 12, 31), "DESIGN_SLUT staar som konstant")

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")

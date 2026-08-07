"""
test_vol_percentil.py — kigger percentilen fremad?
═══════════════════════════════════════════════════════════════════════════════════
Percentilmodulet er fundamentet under alle tre lag. Én fejl her forplanter sig til
hver eneste tilstand motoren nogensinde udsteder, og den farligste af dem — et
look-ahead-laek — goer ALLE resultater for gode uden at noget fejler.

Derfor testes ikke bare at tallene er rigtige, men at en LAEKKENDE udgave bliver
fanget. Et vaern der aldrig er set fyre, er ikke et vaern (Revision H3).

    python test_vol_percentil.py
"""
from __future__ import annotations

import bisect
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vol_percentil as vp

FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


def serie_af(vaerdier) -> dict:
    """{dato: vaerdi} med én dag pr. vaerdi, startende 2010-01-01."""
    d0 = date(2010, 1, 1)
    return {d0 + timedelta(days=i): float(v) for i, v in enumerate(vaerdier)}


print("\n1. percentil_af — grundregning")
kraev(vp.percentil_af([1, 2, 3, 4], 0) == 0.0, "under alt -> 0")
kraev(vp.percentil_af([1, 2, 3, 4], 5) == 100.0, "over alt -> 100")
kraev(vp.percentil_af([1, 2, 3, 4], 2.5) == 50.0, "midt imellem -> 50")

# Midtpunkts-konventionen. VIX-familien kvoteres i hele hundrededele, saa
# gentagne vaerdier er hverdag; uden halv-vaegt ville de give systematisk for
# lave percentiler.
kraev(vp.percentil_af([1, 1, 1, 1], 1) == 50.0,
      "kun identiske vaerdier -> 50, ikke 0 (midtpunkts-konvention)")
kraev(vp.percentil_af([1, 2, 2, 3], 2) == 50.0, "lig med to af fire -> 50")

try:
    vp.percentil_af([], 1)
    kraev(False, "tomt grundlag skal kaste")
except vp.Percentilfejl:
    kraev(True, "tomt grundlag kaster frem for at gaette")

print("\n2. DEN AFGOERENDE: dagen selv maa ALDRIG vaere med i sit eget grundlag")
# Stigende serie. Er dagen selv med, er den altid den hoejeste hidtil, og
# percentilen ville ligge under 100 pga. halv-vaegten. Er den IKKE med, er den
# hoejere end alt i grundlaget -> praecis 100.
punkter = vp.beregn(serie_af(range(1, 401)), burnin=10)
sidste = punkter[-1]
kraev(sidste.pct == 100.0,
      f"stigende serie: sidste dag = 100,0 (fik {sidste.pct}) — dagen selv er UDE")
kraev(sidste.n == 399, f"grundlaget er de 399 foregaaende (fik {sidste.n})")

# Faldende serie: spejlvendt. Sidste dag er lavest af alle -> 0.
faldende = vp.beregn(serie_af(range(400, 0, -1)), burnin=10)
kraev(faldende[-1].pct == 0.0,
      f"faldende serie: sidste dag = 0,0 (fik {faldende[-1].pct})")

print("\n3. En LAEKKENDE udgave skal give et ANDET svar — ellers maaler testen intet")


def beregn_med_laek(serie, burnin=10):
    """Samme beregning, men dagen selv ER med i grundlaget. Bevidst defekt."""
    dage = sorted(serie)
    grundlag: list[float] = []
    ud = []
    for d in dage:
        x = serie[d]
        bisect.insort(grundlag, x)              # ⚠ FOER beregningen — laekket
        n = len(grundlag)
        pct = vp.percentil_af(grundlag, x) if n >= burnin else None
        ud.append(vp.Punkt(dag=d, vaerdi=x, pct=pct, n=n))
    return ud


laek = beregn_med_laek(serie_af(range(1, 401)), burnin=10)
kraev(laek[-1].pct != 100.0,
      f"den laekkende giver {laek[-1].pct:.4f}, ikke 100 — forskellen ER laekket")
kraev(abs(laek[-1].pct - punkter[-1].pct) > 0.05,
      f"ren og laekkende adskiller sig maalbart "
      f"({punkter[-1].pct:.4f} mod {laek[-1].pct:.4f})")

print("\n4. Burn-in: ingen tal foer grundlaget baerer")
p = vp.beregn(serie_af(range(1, 21)), burnin=10)
kraev(all(x.pct is None for x in p[:10]),
      "de foerste 10 dage har pct=None (grundlaget var for tyndt)")
kraev(all(x.pct is not None for x in p[10:]), "derefter er der tal")
kraev(len(p) == 20, "alle dage er MED i listen — kun tallet mangler")
kraev(vp.foerste_gyldige(p) == sorted(serie_af(range(1, 21)))[10],
      "foerste_gyldige peger paa dag 11")

print("\n5. De tre referencer")
# 600 dage: 300 lave, saa 300 hoeje. Paa den sidste dag ser en 252-dages
# reference KUN hoeje vaerdier; den ekspanderende ser ogsaa de lave.
vaerdier = [10.0] * 300 + [20.0] * 300
s = serie_af(vaerdier)
eksp = vp.beregn(s, "ekspanderende", burnin=10)[-1]
r252 = vp.beregn(s, "252", burnin=10)[-1]
r504 = vp.beregn(s, "504", burnin=10)[-1]
print(f"     ekspanderende n={eksp.n} pct={eksp.pct:.1f} · "
      f"252 n={r252.n} pct={r252.pct:.1f} · 504 n={r504.n} pct={r504.pct:.1f}")
kraev(r252.n <= 252, f"252-referencen ser hoejst 252 dage (fik {r252.n})")
kraev(r504.n <= 504, f"504-referencen ser hoejst 504 dage (fik {r504.n})")
kraev(eksp.n == 599, f"den ekspanderende ser alt hidtil (fik {eksp.n})")
kraev(eksp.pct > r252.pct,
      "den ekspanderende giver HOEJERE pct — den kender ogsaa den rolige periode")

try:
    vp.beregn(s, "ukendt")
    kraev(False, "ukendt reference skal kaste")
except vp.Percentilfejl:
    kraev(True, "ukendt reference kaster frem for at falde tilbage paa en default")

print("\n6. Rullende vindue glemmer FAKTISK det gamle")
# 300 ekstreme dage, saa 300 normale. Efter 252+ normale dage maa de ekstreme
# vaere ude af 252-vinduet — ellers er vinduet kun kosmetik.
s2 = serie_af([100.0] * 300 + [10.0] * 300)
sidste252 = vp.beregn(s2, "252", burnin=10)[-1]
kraev(sidste252.pct == 50.0,
      f"kun identiske vaerdier tilbage i vinduet -> 50,0 (fik {sidste252.pct}) "
      f"— de ekstreme er glemt")
kraev(vp.beregn(s2, "ekspanderende", burnin=10)[-1].pct < 50.0,
      "den ekspanderende husker dem stadig og giver lavere")

print("\n7. Rigtige data: SPY's percentil er velformet hele vejen")
try:
    import vol_serier as vs
    spy = vs.laes_serie("SPY")
    pkt = vp.beregn(spy)
    gyldige = [p for p in pkt if p.pct is not None]
    kraev(len(pkt) == len(spy), "én post pr. handelsdag")
    kraev(len(gyldige) == len(spy) - vp.BURNIN_LAG1,
          f"{len(gyldige)} gyldige = {len(spy)} - {vp.BURNIN_LAG1} burn-in")
    kraev(all(0.0 <= p.pct <= 100.0 for p in gyldige), "alle percentiler i [0,100]")
    kraev(vp.foerste_gyldige(pkt) is not None,
          f"foerste gyldige dag: {vp.foerste_gyldige(pkt)}")
except FileNotFoundError:
    print("     (vol_cache mangler — springer over)")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")

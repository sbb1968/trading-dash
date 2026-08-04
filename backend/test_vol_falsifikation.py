"""
test_vol_falsifikation.py — virker falsifikationsmaskineriet selv?
═══════════════════════════════════════════════════════════════════════════════════
Der er en morsom faelde i at bygge et vaern mod kontroller der ikke kan fejle: hvis
vaernet selv ikke kan fejle, har man bygget sygdommen ind i kuren. Denne fil er
derfor Revision G anvendt paa Revision G's eget maskineri.

Den viser fire ting, alle koert frem for paastaaet:

  1. Fikstuerne ER hvad de paastaar — maalt, ikke antaget.
  2. Registret AFVISER en kontrol der altid bestaar (den forrige motors fejl).
  3. Registret AFVISER en kontrol der altid dumper (den modsatte fejl, lige saa vaerdiloes).
  4. Den naive benchmark slaar et beviseligt vaerdiloest maal — ellers maaler
     benchmarken heller ikke noget, og saa er V-test 1's sammenligning tom.

    python test_vol_falsifikation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from vol_falsifikation import (Falsifikationskrav, Falsifikationsregister,
                               bootstrap_forskel, fikstur_egenskaber,
                               hvid_stoej_serie, klynget_serie, konstant_serie,
                               random_walk_serie, shufflet, spearman,
                               vaerdiloest_maal)

FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


print("\n[1] Fikstuerne er hvad de paastaar (lag-1 autokorrelation, maalt)")
e = fikstur_egenskaber(1500)
for k, v in e.items():
    print(f"        {k:20s} {v:+.3f}")
paastand(abs(e["hvid_stoej"]) < 0.06,
         f"hvid stoej har ingen tidsstruktur ({e['hvid_stoej']:+.3f})")
paastand(abs(e["shufflet_klynget"]) < 0.06,
         f"shufflet klynget serie har ingen tidsstruktur ({e['shufflet_klynget']:+.3f})")
paastand(e["klynget"] > 0.5,
         f"den kendt-positive HAR klyngning ({e['klynget']:+.3f})")
paastand(e["random_walk"] > 0.9,
         f"random walk er trivielt forudsigelig ({e['random_walk']:+.3f}) "
         f"— derfor forbudt som kendt-negativ for V-test 1")


# ── En realistisk prediktiv kontrol i V-test 1's form ─────────────────────────
def prediktiv_kontrol(serie: np.ndarray) -> bool:
    """Forudsiger gaarsdagens range morgendagens? Bestaar hvis Spearman > 0,10."""
    return spearman(serie[:-1], serie[1:]) > 0.10


print("\n[2] En aegte kontrol dumper paa kendt-negativ og bestaar paa kendt-positiv")
reg = Falsifikationsregister()
p = reg.kraev("prediktiv kontrol (rigtig)", prediktiv_kontrol,
              dumper_paa=shufflet(klynget_serie(1200)),
              bestaar_paa=klynget_serie(1200),
              dumpe_beskrivelse="shufflet klynget serie — samme fordeling, nul tidsstruktur",
              bestaa_beskrivelse="klynget serie med aegte volatilitetsklyngning")
paastand(p.dumpede, "dumper paa den shufflede serie")
paastand(p.bestod, "bestaar paa den klyngede serie")
paastand(p.gyldig, "registreres som BRUGBAR")


print("\n[3] Registret afviser en kontrol der ikke kan fejle")
# Praecis den forrige regime-motors fejl: et kriterium der er opfyldt pr. konstruktion.
def altid_bestaaet(serie) -> bool:
    andel_over_median = float(np.mean(serie > np.median(serie)))
    return andel_over_median > 0.30      # ~0,50 uanset input. Kan ikke fejle.


p = reg.kraev("'≥30 % over median' (kan ikke fejle)", altid_bestaaet,
              dumper_paa=shufflet(klynget_serie(1200)),
              bestaar_paa=klynget_serie(1200),
              dumpe_beskrivelse="shufflet klynget serie",
              bestaa_beskrivelse="klynget serie")
paastand(not p.dumpede, "den bestod paa den kendt-negative — som forudset")
paastand(not p.gyldig, "og registreres derfor som IKKE brugbar")

print("\n[4] Registret afviser ogsaa en kontrol der altid dumper")
p = reg.kraev("'altid falsk' (kan ikke bestaa)", lambda s: False,
              dumper_paa=shufflet(klynget_serie(500)),
              bestaar_paa=klynget_serie(500))
paastand(p.dumpede and not p.bestod, "den dumpede paa begge")
paastand(not p.gyldig, "en kontrol der afviser alt er lige saa vaerdiloes")

print("\n[5] Registret raaber op frem for at rapportere paent")
try:
    reg.rapport(rejs=True)
    paastand(False, "rapport() rejste Falsifikationskrav")
except Falsifikationskrav as ex:
    paastand("kan ikke fejle" in str(ex) and "altid falsk" in str(ex),
             f"rapport() rejste og navngav begge ubrugelige kontroller")

print("\n[6] Den naive benchmark slaar et beviseligt vaerdiloest maal")
# Specens krav: kan benchmarken ikke slaa noget der intet ved, maaler den heller
# ikke noget, og V-test 1's sammenligning er tom.
serie = klynget_serie(1500)
i_gaar, i_dag = serie[:-1], serie[1:]
vaerdiloes = vaerdiloest_maal(len(i_gaar))
r_bench = spearman(i_gaar, i_dag)
r_vaerdiloes = spearman(vaerdiloes, i_dag)
print(f"        benchmark (gaarsdagens range) : {r_bench:+.3f}")
print(f"        vaerdiloest maal              : {r_vaerdiloes:+.3f}")
paastand(r_bench > 0.20, f"benchmarken har reel prediktiv kraft ({r_bench:+.3f})")
paastand(abs(r_vaerdiloes) < 0.08, f"det vaerdiloese maal har ingen ({r_vaerdiloes:+.3f})")

forskel, lav, hoej = bootstrap_forskel(i_gaar, vaerdiloes, i_dag, n_resamples=1000)
print(f"        forskel {forskel:+.3f}  95 %-KI [{lav:+.3f}, {hoej:+.3f}]")
paastand(lav > 0, "bootstrap-KI'et udelukker nul — benchmarken slaar stoej")

print("\n[7] … og et vaerdiloest maal slaar IKKE benchmarken")
# Den anden retning: bootstrap-testen maa ikke sige ja til hvad som helst.
forskel2, lav2, hoej2 = bootstrap_forskel(vaerdiloes, i_gaar, i_dag, n_resamples=1000)
print(f"        forskel {forskel2:+.3f}  95 %-KI [{lav2:+.3f}, {hoej2:+.3f}]")
paastand(lav2 < 0 < hoej2 or hoej2 < 0,
         "KI'et udelukker IKKE nul i den gale retning — testen er ikke bare altid positiv")

print("\n[8] Kantsager: konstante serier giver nan frem for et falsk tal")
r = spearman(konstant_serie(100), konstant_serie(100))
paastand(np.isnan(r), "konstant mod konstant = nan (ingen rangorden findes)")
paastand(np.isnan(spearman([1.0], [2.0])), "for faa punkter = nan")

print("\n[9] Registret godkender naar alt er i orden")
ren = Falsifikationsregister()
ren.kraev("prediktiv kontrol", prediktiv_kontrol,
          dumper_paa=shufflet(klynget_serie(1200)), bestaar_paa=klynget_serie(1200))
try:
    tekst = ren.rapport(rejs=True)
    paastand("| ja |" in tekst, "rapporten markerer kontrollen som brugbar")
except Falsifikationskrav:
    paastand(False, "et rent register maa ikke rejse")

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")

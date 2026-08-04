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
              bestaa_beskrivelse="klynget serie med aegte volatilitetsklyngning",
              egenskab="prediktiv", nul_fikstuurnavn="shufflet_klynget")
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
              bestaa_beskrivelse="klynget serie",
              egenskab="prediktiv", nul_fikstuurnavn="shufflet_klynget")
paastand(not p.dumpede, "den bestod paa den kendt-negative — som forudset")
paastand(not p.gyldig, "og registreres derfor som IKKE brugbar")

print("\n[4] Registret afviser ogsaa en kontrol der altid dumper")
p = reg.kraev("'altid falsk' (kan ikke bestaa)", lambda s: False,
              dumper_paa=shufflet(klynget_serie(500)),
              bestaar_paa=klynget_serie(500),
              egenskab="prediktiv", nul_fikstuurnavn="shufflet_klynget")
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
ren.kraev_egenskab("prediktiv kontrol", prediktiv_kontrol, egenskab="prediktiv")
try:
    tekst = ren.rapport(rejs=True)
    paastand("| ja |" in tekst, "rapporten markerer kontrollen som brugbar")
except Falsifikationskrav:
    paastand(False, "et rent register maa ikke rejse")

print("\n[10] H1 — fiksturet skal vaere nul for DEN EGENSKAB kontrollen maaler")
import vol_falsifikation as vf

paastand(vf.nul_fikstur("prediktiv")[0] == "shufflet_klynget",
         "nul for 'prediktiv' er den shufflede serie — ikke en random walk")
paastand(vf.nul_fikstur("regimeophold")[0] == "random_walk",
         "nul for 'regimeophold' ER random walk — glathed uden regimer")
paastand(vf.positiv_fikstur("prediktiv")[0] == "klynget",
         "kendt-positiv for 'prediktiv' er den klyngede serie")

# DEN DYRE FEJL, gjort umulig: en random walk som nul for en prediktiv test ville
# faa en fuldstaendig korrekt test til at se defekt ud.
try:
    vf.bekraeft_nul("random_walk", "prediktiv")
    paastand(False, "random_walk afvises som nul for 'prediktiv'")
except vf.Fikstuurfejl as ex:
    paastand("+0.995" in str(ex) and "shufflet_klynget" in str(ex),
             "afvist MED det maalte tal og med anvisning paa det rette fikstur")

# Og den modsatte retning: random walk ER et gyldigt nul for regimepaastande.
try:
    vf.bekraeft_nul("random_walk", "regimeophold")
    paastand(True, "random_walk accepteres som nul for 'regimeophold'")
except vf.Fikstuurfejl:
    paastand(False, "random_walk accepteres som nul for 'regimeophold'")

paastand(vf.nul_fikstur("prediktiv")[0] != vf.nul_fikstur("regimeophold")[0],
         "de to egenskaber faar FORSKELLIGE nulserier — der findes ingen universel")

print("\n[10b] Registret vaelger selv, saa kalderen ikke kan gribe forkert")
reg2 = Falsifikationsregister()
p = reg2.kraev_egenskab("V-test 1 (form)", prediktiv_kontrol, egenskab="prediktiv")
paastand(p.gyldig, "kraev_egenskab gav en brugbar registrering uden at kalderen valgte")
paastand("shufflet_klynget" in p.dumpe_beskrivelse,
         "og den valgte det rette nul af sig selv")

try:
    reg2.kraev("uargumenteret", prediktiv_kontrol,
               dumper_paa=random_walk_serie(500), bestaar_paa=klynget_serie(500))
    paastand(False, "et fikstur uden egenskab eller begrundelse afvises")
except vf.Fikstuurfejl as ex:
    paastand("begrundelse" in str(ex), "afvist — et uargumenteret nulfikstur er H1's fejl")


print("\n[11] H2 — shuffl raavaren, ikke den vinduesberegnede serie")
# Pointen der slog det forrige projekt ihjel, vist med tal.
rng = np.random.default_rng(4242)
afkast = rng.normal(0, 1, 1500)                     # iid — INGEN struktur overhovedet

vinduesberegnet = vf.rullende_middel(afkast, 30)
naiv = shufflet(vinduesberegnet)                    # den FORKERTE maade
korrekt = vf.shufflet_via_underliggende(afkast, lambda x: vf.rullende_middel(x, 30))

ac_orig = spearman(vinduesberegnet[:-1], vinduesberegnet[1:])
ac_naiv = spearman(naiv[:-1], naiv[1:])
ac_korrekt = spearman(korrekt[:-1], korrekt[1:])
print(f"        30-dages rullende middel af IID stoej : {ac_orig:+.3f}")
print(f"        naivt shufflet (forkert)              : {ac_naiv:+.3f}")
print(f"        shufflet via raavaren (rigtigt)       : {ac_korrekt:+.3f}")

paastand(ac_orig > 0.9,
         f"et rullende vindue paa REN STOEJ er mekanisk glat ({ac_orig:+.3f}) — "
         f"glatheden er ikke et fund")
paastand(abs(ac_naiv) < 0.1,
         f"naiv shuffling oedelaegger ogsaa udglatningen ({ac_naiv:+.3f})")
paastand(ac_korrekt > 0.9,
         f"shuffling via raavaren BEVARER udglatningen ({ac_korrekt:+.3f})")
paastand(ac_korrekt - ac_naiv > 0.8,
         "de to metoder giver vidt forskellige nul — de er ikke ombyttelige")

print("        -> Sammenlignes en vinduesberegnet praediktor med et naivt shufflet nul,")
print("          maaler man udglatningen og ikke markedet. Det var m7-konklusionen.")

print("\n[12] H3 — de tests der ikke kan falsificeres med en nulserie")
# Realtids- og stabilitetstesten sammenligner MOTOREN MED SIG SELV, ikke signal med
# stoej. En nulserie falsificerer dem ikke: trunkering og genberegning virker lige
# godt paa stoej. Derfor bygges defekte MOTORER som fikstur i stedet.
serie_h3 = klynget_serie(1200)

print("        motor        stabilitet   realtid")
matrix = {}
for navn, motor in [("ren", vf.motor_uden_laek),
                    ("laekkende", vf.motor_med_laek),
                    ("skroebelig", vf.skroebelig_motor)]:
    stab = vf.stabilitetstest(motor, serie_h3)
    real = vf.realtidstest(motor, serie_h3)
    matrix[navn] = (stab, real)
    print(f"        {navn:11s}  {'BESTAAET' if stab else 'DUMPET  '}   "
          f"{'BESTAAET' if real else 'DUMPET'}")

paastand(matrix["ren"] == (True, True),
         "den rene motor bestaar BEGGE — ellers er testene for stramme")
paastand(matrix["laekkende"][1] is False,
         "realtidstesten FANGER en motor der maaler percentil mod hele serien")
paastand(matrix["skroebelig"][0] is False,
         "stabilitetstesten FANGER en motor hvis score afhaenger af vinduets laengde")

# At hver defekt kun fanges af SIN test er ikke pynt: det viser at de to tests er
# uafhaengige og at ingen af dem er en stedfortraeder for den anden.
paastand(matrix["laekkende"][0] is True,
         "den laekkende motor er STABIL — look-ahead ses ikke af stabilitetstesten")
paastand(matrix["skroebelig"][1] is True,
         "den skroebelige motor laeser IKKE fremtiden — ses ikke af realtidstesten")

print("\n[12b] Registret optager motor-fikstuerne — med skreven begrundelse")
reg3 = Falsifikationsregister()
p = reg3.kraev("realtidsgyldighed (look-ahead)",
               lambda motor: vf.realtidstest(motor, serie_h3),
               dumper_paa=vf.motor_med_laek, bestaar_paa=vf.motor_uden_laek,
               dumpe_beskrivelse="motor der maaler percentil mod HELE serien",
               bestaa_beskrivelse="motor der kun ser bagud",
               begrundelse="look-ahead kan ikke falsificeres med en nulserie — "
                           "trunkering virker lige godt paa stoej. Fiksturet er en "
                           "defekt MOTOR, ikke en defekt serie (H3).")
paastand(p.gyldig, "registreret som brugbar via begrundelses-undtagelsen")

print("\n[13] Et fund om V-test 3's bestaa-kriterium, vaerd at kende foer den skrives")
m = vf.stabilitetsmaal(vf.motor_uden_laek, serie_h3)
for v, x in m["varianter"].items():
    print(f"        vindue {v:3d}: score flytter {x['median_score_aendring_pp']:4.1f} pp, "
          f"men {x['klasseskift_andel']:.0%} skifter KLASSE "
          f"({x['andel_af_skift_naer_graense']:.0%} af dem laa <10 pp fra en graense)")
v126 = m["varianter"][126]
paastand(v126["median_score_aendring_pp"] < 6,
         f"en KORREKT rangpercentil er meget score-stabil ({v126['median_score_aendring_pp']:.1f} pp)")
paastand(v126["klasseskift_andel"] > 0.15,
         f"… men dumper et 15 %-klasseskift-kriterium ({v126['klasseskift_andel']:.0%})")
paastand(v126["andel_af_skift_naer_graense"] > 0.7,
         "og hovedparten af skiftene er dage taet paa en klassegraense")
print("        -> Et kriterium paa KLASSESKIFT maaler diskretiseringen, ikke robustheden.")
print("           Laeg bestaa-kriteriet paa scoren; rapportér klasseskiftet som kontekst.")

print("\n[14] I1 — 'regimeophold' maales af INGEN V-test i denne byggeklods")
paastand("MAALES IKKE" in vf.EGENSKABER["regimeophold"],
         "taksonomien siger det selv — egenskaben er arvet fra den forrige motors V4")
paastand("V-test 1 OG V-test 2" in vf.EGENSKABER["prediktiv"],
         "'prediktiv' daekker baade V-test 1 og 2")
paastand(set(vf.EGENSKABSLOESE_TESTS) == {"V-test 3 (stabilitet)",
                                          "V-test 4 (realtidsgyldighed)"},
         "V-test 3 OG 4 staar som egenskabsloese — begge kraever en defekt motor")

print("\n[15] I2 — de to tal diagnosticerer HVER SIN aarsag")
diag_ren, _ = vf.stabilitetsdiagnose(vf.stabilitetsmaal(vf.motor_uden_laek, serie_h3))
diag_skr, gor_skr = vf.stabilitetsdiagnose(vf.stabilitetsmaal(vf.skroebelig_motor, serie_h3))
print(f"        ren       -> {diag_ren}")
print(f"        skroebelig-> {diag_skr}")
paastand(diag_ren == "STABIL SCORE, USTABIL KLASSE",
         "den rene motor: stabil score, ustabil klasse")
paastand("BESLUTNINGSLAGET" in vf.stabilitetsdiagnose(
             vf.stabilitetsmaal(vf.motor_uden_laek, serie_h3))[1],
         "… og diagnosen peger paa beslutningslaget, ikke paa maaleklodsen")
paastand(diag_skr == "USTABIL SCORE" and "MAALET" in gor_skr,
         "den skroebelige motor: ustabil score -> MAALET skal laves om")
paastand(diag_ren != diag_skr,
         "de to tilfaelde giver FORSKELLIG diagnose — ellers var skelnen tom")

print("\n[16] I3 — taersklen er sat empirisk, ikke som et rundt tal")
k = vf.kalibrer_stabilitetstaerskel(n_serier=4)
print(f"        ren, vaerst      : {k['ren_vaerst']:.2f} pp")
print(f"        skroebelig, bedst: {k['skroebelig_bedst']:.2f} pp")
print(f"        gaeldende taerskel: {k['gaeldende_pp']} pp")
paastand(k["ren_vaerst"] < vf.STABILITET_TAERSKEL_PP < k["skroebelig_bedst"],
         "taersklen ligger MELLEM den vaerste rene og den bedste skroebelige")
paastand(vf.STABILITET_TAERSKEL_PP / k["ren_vaerst"] > 2.0,
         f"mindst 2x margin ned til den vaerste rene "
         f"({vf.STABILITET_TAERSKEL_PP / k['ren_vaerst']:.1f}x)")
paastand(k["skroebelig_bedst"] / vf.STABILITET_TAERSKEL_PP > 2.0,
         f"mindst 2x margin op til den bedste skroebelige "
         f"({k['skroebelig_bedst'] / vf.STABILITET_TAERSKEL_PP:.1f}x)")
paastand(vf.STABILITET_TAERSKEL_PP % 5 != 0,
         f"{vf.STABILITET_TAERSKEL_PP} er ikke et rundt tal — den er maalt frem")

print("\n[17] Stabilitetsgitteret daekker ±50 % OG de tre percentilreferencer")
varianter = set(vf.stabilitetsmaal(vf.motor_uden_laek, serie_h3)["varianter"])
paastand({126, 378, 504, vf.EKSPANDERENDE} <= varianter,
         f"varianter: ±50 % (126/378), 504 og ekspanderende")

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")

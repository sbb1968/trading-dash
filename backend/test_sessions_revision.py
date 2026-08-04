"""
test_sessions_revision.py — B2 og B3 skal fange det de er bygget til
═══════════════════════════════════════════════════════════════════════════════════
En kontrol der ikke er afproevet paa et tilfaelde den BURDE fange, er ikke en kontrol.
Testene her bygger derfor syntetiske serier med kendte defekter — et sessionsbrud,
et hul, en tidszonefejl — og verificerer at revisionen faktisk raaber op. Og
omvendt: at en ren serie ikke udloeser falsk alarm.

    python test_sessions_revision.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta

from nyse_kalender import er_halv_dag, handelsdage
from sessions_revision import (SessionsBrud, forventede_barer,
                               fuldstaendighedsrevision, tjek_konstant_barantal)

FEJL: list[str] = []
VINDUE = (time(9, 30), time(16, 0))


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


def byg(start: date, slut: date, aabner=time(9, 30), lukker=time(16, 0),
        udelad: set[date] | None = None) -> list[datetime]:
    """Syntetisk 1-min-serie. Halve dage lukker 13:00 ligesom i virkeligheden.

    At generatoren selv kender halve dage er ikke pynt: goer den det ikke, faar hver
    test en 390-minutters bar-raekke paa en dag der kun kan rumme 210, og saa fanger
    kontrollen den defekt i stedet for den defekt testen handler om.
    """
    udelad = udelad or set()
    ud = []
    for d in handelsdage(start, slut):
        if d in udelad:
            continue
        luk = min(lukker, time(13, 0)) if er_halv_dag(d) else lukker
        t0 = datetime.combine(d, aabner)
        n = (luk.hour * 60 + luk.minute) - (aabner.hour * 60 + aabner.minute)
        ud += [t0 + timedelta(minutes=i) for i in range(max(0, n))]
    return ud


print("\n[1] Ren serie giver ingen alarm")
ren = byg(date(2022, 1, 1), date(2023, 12, 31))
r = fuldstaendighedsrevision(ren, "ren", vindue=VINDUE)
paastand(r.daekning == 1.0, f"daekning 100 % ({r.sessioner_fundet}/{r.sessioner_forventet})")
paastand(not r.manglende and not r.huller, "ingen manglende sessioner")
paastand(not r.uventede, "ingen data paa lukkedage")
paastand(not r.ufuldstaendige, "ingen ufuldstaendige sessioner")
paastand(r.effektiv_start == r.foerste, f"effektiv start = foerste bar ({r.effektiv_start})")
b = tjek_konstant_barantal(ren, VINDUE, "ren", rejs=False)
paastand(b["ok"], "B2: ingen brud")
paastand(b["median"] == 390, f"B2: median 390 barer (fik {b['median']})")

print("\n[2] Halve dage taeller IKKE som brud eller som huller")
halve = [d for d in handelsdage(date(2022, 1, 1), date(2023, 12, 31)) if er_halv_dag(d)]
# 2022: kun 25/11 (3. juli faldt paa en soendag, juleaften paa en loerdag).
# 2023: 24/11 og 3/7.
paastand(len(halve) == 3, f"3 halve dage i perioden: {', '.join(str(d) for d in halve)}")
paastand(all(forventede_barer(d, VINDUE) == 210 for d in halve),
         "alle tre forventes at give 210 barer i 09:30-16:00-vinduet")

print("\n[3] Et langt hul rykker den effektive start")
# Tre ugers manglende data midt i 2022 — praecis det B3 er bygget til at fange.
hul = set(handelsdage(date(2022, 6, 1), date(2022, 6, 21)))
med_hul = byg(date(2022, 1, 1), date(2023, 12, 31), udelad=hul)
r = fuldstaendighedsrevision(med_hul, "hul", vindue=VINDUE)
paastand(len(r.manglende) == len(hul), f"{len(r.manglende)} manglende sessioner fundet")
paastand(len(r.huller) == 1 and r.huller[0][2] == len(hul), "hullet rapporteret som ét hul")
paastand(r.effektiv_start is not None and r.effektiv_start > date(2022, 6, 21),
         f"effektiv start rykket til efter hullet ({r.effektiv_start})")
paastand(r.effektiv_start != r.foerste,
         "effektiv start er IKKE den foerste bar — det er hele pointen i B3")

print("\n[4] Spredte enkeltdage flytter ikke starten")
spredt = {date(2022, 3, 15), date(2022, 8, 9), date(2023, 5, 2)}
r = fuldstaendighedsrevision(byg(date(2022, 1, 1), date(2023, 12, 31), udelad=spredt),
                             "spredt", vindue=VINDUE)
paastand(len(r.manglende) == 3, "tre manglende dage fundet")
paastand(r.effektiv_start == r.foerste,
         "effektiv start uaendret — enkeltdage er ikke et brud i sammenhaengen")

print("\n[5] B2 fanger et skift i sessionsdefinitionen")
# Foerste aar lukker 15:15 (345 barer i vinduet), andet aar 16:00 (390) — praecis
# formen paa VIX' sessionsskift, og et skift der ligger INDE i vinduet.
tidlig = byg(date(2022, 1, 3), date(2022, 12, 30), lukker=time(15, 15))
sen = byg(date(2023, 1, 3), date(2023, 12, 29))
try:
    tjek_konstant_barantal(tidlig + sen, VINDUE, "skift", rejs=True)
    paastand(False, "B2 rejste SessionsBrud ved skiftende sessionsdefinition")
except SessionsBrud as e:
    paastand("aarsmedian" in str(e), f"B2 rejste SessionsBrud: {str(e)[:90]}…")

print("\n[5b] Et skift der ligger HELT uden for vinduet er usynligt — efter hensigten")
# Aabner en time tidligere, men 09:30-16:00 er uroert. Profilen kan ikke forurenes,
# saa kontrollen skal tie. Dokumenteret raekkevidde, ikke en mangel.
udenfor = byg(date(2022, 1, 3), date(2022, 12, 30), aabner=time(8, 30))
b = tjek_konstant_barantal(udenfor + sen, VINDUE, "udenfor", rejs=False)
paastand(b["ok"], "ingen alarm — vinduet indeholder 390 barer i begge aera")

print("\n[6] B2 fanger dubletter (flere barer end sessionen kan rumme)")
# En genoptaget harvest der skriver de samme barer to gange. Markedet kan ikke
# producere 780 minutter i en 390-minutters session.
dub = byg(date(2023, 1, 3), date(2023, 3, 31))
b = tjek_konstant_barantal(sorted(dub + dub), VINDUE, "dub", rejs=False)
paastand(not b["ok"], "B2 melder brud")
paastand(any("flere barer end sessionen kan rumme" in x for x in b["brud"]),
         "brudteksten peger paa dubletter/tidszone, ikke paa markedet")

print("\n[6b] En serie i forkert tidszone rammer slet ikke vinduet")
# Stempler gemt i UTC men laest som ET: RTH ligger da 13:30-20:00, og et
# 09:30-16:00-vindue fanger nul barer i stedet for at fange de forkerte.
utc = [t + timedelta(hours=4) for t in byg(date(2023, 1, 3), date(2023, 3, 31))]
utc = [t for t in utc if t.time() >= time(16, 0)]
try:
    tjek_konstant_barantal(utc, VINDUE, "utc", rejs=True)
    paastand(False, "B2 rejste SessionsBrud ved tom vindue")
except SessionsBrud as e:
    paastand("ingen barer" in str(e), f"B2 rejste SessionsBrud: {str(e)[:70]}…")

print("\n[7] Data paa en lukkedag rapporteres som kalenderfejl, ikke som datahul")
ekstra = byg(date(2023, 1, 3), date(2023, 3, 31))
juledag = datetime(2023, 2, 20, 10, 0)      # Washingtons foedselsdag = lukket
ekstra += [juledag + timedelta(minutes=i) for i in range(390)]
r = fuldstaendighedsrevision(sorted(ekstra), "ekstra", vindue=VINDUE)
paastand(r.uventede == [date(2023, 2, 20)],
         "lukkedagen med data staar i 'uventede' — adskilt fra manglende sessioner")
paastand(date(2023, 2, 20) not in r.manglende, "og ikke i manglende")

print("\n[8] Ufuldstaendig session flages")
delvis = byg(date(2023, 1, 3), date(2023, 3, 31))
delvis = [t for t in delvis if not (t.date() == date(2023, 2, 8) and t.minute % 3 == 0)]
r = fuldstaendighedsrevision(delvis, "delvis", vindue=VINDUE)
paastand(any(d == date(2023, 2, 8) for d, _, _ in r.ufuldstaendige),
         "sessionen med en tredjedel manglende minutter er flaget")
paastand(date(2023, 2, 8) not in r.manglende,
         "men taelles som til stede — den mangler ikke, den er tynd")

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")

# Kalibreringslog — regime-byggeklods 1 (volatilitet)

Oprettet 2026-08-04 (Revision I3).

**Formål.** Hver gang en tærskel eller konstant sættes ud fra målinger, skrives her
hvad der blev målt, hvad der blev valgt, og hvorfor. Loggen læses sammen med
valideringsrapporten, så en tærskel kan efterprøves frem for at blive troet.

## Hvorfor dette ikke er kontaminering

Kontaminationsreglen (spec afsnit 7) forbyder at kalibrere mod **resultatet** — mod
strategiafkast eller mod holdout-data. Kalibreringerne her gør noget andet: de sætter
et **instrument** ud fra en kendt-god og en kendt-dårlig standard, begge syntetiske og
begge konstrueret før måledata er set. Det svarer til at nulstille en vægt med et lod
af kendt masse.

Skellet skal kunne ses på en post: står der en *syntetisk* standard i "målt mod", er
det instrumentkalibrering. Står der markedsdata fra en forseglet periode, hører posten
i stedet i `vol_kontamineringslog.md`.

---

## Post 1 — V-test 3's beståtærskel, 2026-08-04

**Hvad blev sat:** `STABILITET_TAERSKEL_PP` i `vol_falsifikation.py`.

**Målt mod:** to syntetiske motorer over **8 uafhængige serier**
(GARCH-agtig klyngning, seeds 20260806–20260813).
Ingen markedsdata indgår.

| motor | median |Δscore|, værste variant pr. serie |
|---|---|
| ren (rangpercentil, kun bagud) | 4.4, 9.1, 4.8, 4.4, 4.7, 5.2, 6.8, 6.8 → **værst 9.13 pp** |
| skrøbelig (vinduets længde siver ind i scoren) | 54.2, 53.5, 53.6, 53.9, 51.6, 54.1, 54.1, 53.6 → **bedst 51.60 pp** |

**Valgt tærskel: 21.7 pp** — det geometriske midtpunkt, hvilket giver
**2.38×** margin over den værste rene serie og **2.38×** under
den bedste skrøbelige. Symmetrisk i forholdstal frem for i absolutte point, fordi de
to fordelinger ligger på hver sin størrelsesorden.

**Et første gæt på 11,0 pp blev forkastet.** Det lå kun 1,2× over den værste rene
serie (9.13 pp), og en enkelt uheldig seed ville dermed have dumpet
en fuldstændig korrekt motor. Spredningen på tværs af seeds — 4,4 til 9,1 — er
grunden til at tærsklen ikke kan sættes fra én serie.

**Variantgitteret** (spec kræver ±50 % vindue **og** de tre percentilreferencer):

Ren motor:

| variant | median |Δscore| (pp) | klasseskift | heraf < 10 pp fra grænse |
|---|---|---|---|
| 126 | 4.4 | 21% | 83% |
| 378 | 2.5 | 14% | 97% |
| 504 | 3.8 | 16% | 98% |
| ekspanderende | 4.0 | 19% | 96% |

Skrøbelig motor:

| variant | median |Δscore| (pp) | klasseskift | heraf < 10 pp fra grænse |
|---|---|---|---|
| 126 | 21.5 | 78% | 89% |
| 378 | 20.8 | 83% | 89% |
| 504 | 41.3 | 94% | 90% |
| ekspanderende | 54.2 | 94% | 90% |

**Bemærk hvad klasseskiftraten IKKE er.** Den rene motor flytter scoren 2,5–4,4 point
men skifter klasse på 14–21 % af dagene, og 83–97 % af de skift ligger under ti point
fra en klassegrænse. Klasseskiftraten er derfor **ikke** beståkriterium (Revision I2);
den rapporteres som kontekst, og `stabilitetsdiagnose()` oversætter kombinationen:

| score | klasse | konklusion |
|---|---|---|
| ustabil | — | målet er parameterafhængigt → **målet** skal laves om |
| stabil | ustabil | grænserne ligger uheldigt → **beslutningslaget** skal have hysterese; måleklodsen fejler ikke |
| stabil | stabil | ingen handling |


---

## Post 2 — bekræftelse af tærsklen på 50 seeds, 2026-08-04

**Hvad blev efterprøvet:** om den rene motors øvre hale var underestimeret med kun 8
serier. Post 1 satte tærsklen ud fra et observeret maksimum på 9,13 pp, hvor de
øvrige syv lå 4,4–6,8 — altså et enligt yderpunkt, og dermed et usikkert estimat.

**Resultat, 50 uafhængige serier:**

| motor | min | median | p95 | max |
|---|---|---|---|---|
| ren | 3.57 | 6.15 | 8.55 | **9.13** |
| skrøbelig | **51.60** | 53.78 | — | 55.07 |

**Maksimum stabiliserer sig.** Løbende maksimum for den rene motor efter
8/16/25/32/40/50 seeds: 9.13/9.13/9.13/9.13/9.13/9.13 pp.
Værdien blev nået inden for de første otte trækninger og er aldrig overskredet siden.

Med p95 på 8.55 og median 6.15 er 9,13 desuden
ikke et vildt udfald, men toppen af en forholdsvis stram fordeling — spændet er
3.57–9.13 pp over alle 50.

**Tærsklen forbliver 21.7 pp.** Det geometriske
midtpunkt på 50 seeds er identisk med værdien fra 8. Adskillelsen mellem værste rene
og bedste skrøbelige er **5.7×**.

**Hvad øvelsen faktisk viste.** Otte seeds gav det rigtige tal — men det kunne vi
ikke vide uden at køre flere. Forskellen mellem "estimatet var rigtigt" og "estimatet
var efterprøvet" er hele grunden til at posten står her.

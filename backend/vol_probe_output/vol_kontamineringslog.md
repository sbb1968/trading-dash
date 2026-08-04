# Kontamineringslog — regime-byggeklods 1 (volatilitet)

Oprettet på anmodning i Revision A, punkt A7.

**Formål.** Hver gang forseglet data er set — også utilsigtet, også i en form der
virker uskyldig — føjes en post her. Loggen læses sammen med valideringsrapporten,
så det kan bedømmes hvor meget af det endelige resultat der er reelt og hvor meget
der er søgning.

**Hvad tæller som en post.** Enhver beregning der har set data i design-validerings-
eller holdout-perioden, uanset om der blev tilpasset en parameter. Grunden er at
selve *kigget* flytter, hvor meget vi bagefter må læne os på tallet — også når
ingen knap blev drejet.

**Alvorlighed** (indført i Revision B, punkt B4). Hver post bærer ét af to niveauer:

| niveau | betyder | konsekvens for valideringen |
|---|---|---|
| **VÆRDIER SET** | der er regnet på priser, ranges, afkast eller korrelationer — noget der kan indgå i eller pege på et volatilitetsmål | resultatet på den periode skal læses med forbehold; retningen var kendt på forhånd |
| **METADATA SET** | der er kun set på dataenes *eksistens*: bartællinger, tidsstempler, dækning, filstørrelser | ingen konsekvens for signalvurderingen |

Uden gradbøjningen ville "jeg talte barer" læse som "jeg så benchmark-statistikken",
og så drukner signalet i støj. Loggen skal stadig være udtømmende — også
metadata-kig føres ind — men de to må kunne skelnes på et øjeblik.

**Perioder** (spor 1, jf. spec afsnit 5): udvikling → 2023-12-31 · design-validering
2024 · holdout 2025 → i dag. Spor 2 (futures-intradag) begynder 2024-06-21 og
ligger dermed **i sin helhed** uden for udviklingsperioden.

---

## Post 1 — RTH/ETH-måling på MES, 2026-08-04

**Alvorlighed: VÆRDIER SET.**

**Hvem/hvad:** VS Code Claude, under arbejdet med V0.3's første beslutning
(RTH mod ETH som primær range-definition).

**Data set:** `data_harvest/mes_m2k_stitched/MES_1min.csv`, 501 komplette
RTH-sessioner fra **2024-06-21 til 2026-06-30**. Det spænder ~6 måneder af
design-valideringsperioden og hele holdout-perioden til dato.

**Hvad blev beregnet:**

| mål | værdi |
|---|---|
| median RTH-range | 0,90 % |
| median overnight-range | 0,64 % (= 71 % af RTH) |
| Spearman, overnight-range → samme dags RTH-range | **+0,547** |
| Spearman, gårsdagens RTH-range → dagens | **+0,492** |

**Hvorfor det er mere end deskriptivt.** Det andet tal er V-test 1's benchmark, og
det første er i praksis et forhåndskig på hvor godt et kandidatmål til lag 2 klarer
sig mod netop den benchmark. Der blev ikke tilpasset nogen parameter, og ingen
tærskel blev valgt herfra — men en tidlig, forsimplet udgave af hovedkriteriet er
kørt på forseglet data.

**Hvilken beslutning det påvirkede:** valget af RTH som primær range-definition og
overnight-vinduet som input til lag 2 (spec V0.3, godkendt som Revision A punkt A3).

**Afbødning.** Marginen (+0,055) er lille, og begge tal er teoretisk uoverraskende —
volatilitetsklyngning og overnight-informationsindhold er begge veletablerede. Ved
den endelige V-test 1 skal resultatet derfor læses med den viden at retningen var
kendt på forhånd. Den rene måling bliver spor 3 (fremadrettet holdout fra
frysningsdatoen), hvor ingen har set data.

**Status:** registreret, ikke afbødet. Beslutningen står ved magt.

---

## Post 2 — A9-dybdeverifikation, 2026-08-04

**Alvorlighed: METADATA SET.**

**Hvem/hvad:** VS Code Claude, punkt A9 (blokerende verifikation af 1-min-dybde).

**Data set:** faktiske 1-min-hentninger for SPY, IWM og VIX i 2-dages vinduer i
2012, 2015, 2018, 2021, 2023, **2024 og 2025**. De to sidste år ligger i
design-validerings- og holdout-perioden.

**Hvad blev beregnet:** udelukkende *antal barer og deres tidsstempler* — ingen
priser, ingen ranges, ingen afkast, intet der kan indgå i et volatilitetsmål.

**Vurdering:** dette regnes **ikke** som kontaminering af signal-indholdet. Der er
set på dataenes eksistens og fuldstændighed, ikke på deres værdier. Posten står her
alligevel, fordi loggen skal være udtømmende og ikke selektiv — det er den eneste
måde den bevarer sin værdi.

**Sidefund værd at kende:** VIX' sessionslængde er udvidet to gange i perioden
(~09:31–16:14 ET indtil ca. 2015, derefter 03:15–16:14, og fra ca. 2023
03:15–16:59). En tid-på-dagen-profil bygget hen over de brud vil blande tre
forskellige sessionsdefinitioner. Skal håndteres i V2.

**Status:** registreret, ingen afbødning nødvendig.

---

## Post 3 — B3-fuldstændighedsrevision på MES/M2K, 2026-08-04

**Alvorlighed: METADATA SET.**

**Hvem/hvad:** VS Code Claude, punkt B3 (fuldstændighedsrevision som afslutning på
harvesten) og B2 (assert på konstant barantal pr. session).

**Data set:** tidsstemplerne i `data_harvest/mes_m2k_stitched/{MES,M2K}_1min.csv`,
2024-06-21 → 2026-06-30. Hele spor 2, som i sin helhed ligger uden for
udviklingsperioden.

**Hvad blev beregnet:** antal barer pr. handelsdag, sammenholdt med NYSE-kalenderen.
Ingen priser blev læst — `laes_tider()` parser udelukkende `timestamp`-kolonnen og
rører aldrig open/high/low/close.

**Resultat:** 507 af 507 forventede sessioner til stede i begge serier, ingen indre
huller, effektiv udviklingsstart 2024-06-21 = den nominelle.

**Sidefund:** seks dage havde 225 barer hvor NYSE-kalenderen ventede 210. CME's
equity-index-futures lukker 13:15 ET på halve dage, ikke 13:00. Kalenderen blev
rettet; data havde ret.

**Status:** registreret, ingen afbødning nødvendig.

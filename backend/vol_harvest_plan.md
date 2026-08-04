# Harvest-plan for volatilitets-byggeklodsen (fase V1)

Skrevet 2026-08-04. Daekker Revision D1 (redning, arkiv, kvartalsjob), B2, B3 og B4.
Dette er den fil der skal aabnes naar nogen spoerger "hvor ligger arkivet" eller
"hvad mangler at blive hentet".

---

## 1. Redningen — kontrakter der doer inden for uger

IBKR purger kontrakt**definitionen** ca. **24 maaneder efter udloeb**. Derefter kan
man ikke engang spoerge om barerne. Graensen er indrammet af to observationer
(2026-08-04): 202409 svarer ved ~23 mdr., 202406 er vaek ved ~25,5 mdr.

**202409 er den aeldste kontrakt der stadig kan reddes, og den doer inden for uger.**
ES og RTY er aldrig hoestet. MES og M2K har vi frem til 2026-06-30.

### Koer i denne raekkefoelge

**Trin 1 — det korte job foerst, som roegtest af hele kaeden.** Fejler noget i
scriptet, opdages det efter fem minutter frem for efter tre timer:

```
python harvest_futures_1min.py --symbols MES,M2K --start 2026-06-25 --end 2026-08-04 --client-id 75
```

Lukker de fem ugers datagab frem til i dag. Overlappet paa faa dage er med vilje —
scriptet dedupliker paa bar-tidsstempel, saa en overlappende hentning koster ingenting
og fjerner risikoen for et hul i soemmen.

**Trin 2 — 202409 for baade ES og RTY. Den mest udsatte kontrakt, foerst.**

```
python harvest_futures_1min.py --symbols ES  --start 2024-06-21 --end 2024-09-30 --client-id 75
python harvest_futures_1min.py --symbols RTY --start 2024-06-21 --end 2024-09-30 --client-id 75
```

**Startdato 2024-06-21, ikke 1. august.** 202409 blev forreste kontrakt ved
juni-rullet, og den eksisterende `mes_m2k_clean` starter netop dér. Asymmetrien er
entydig: at spoerge for tidligt koster ingenting — kaldet returnerer bare tomt —
mens at spoerge for sent koster data permanent.

**Trin 3 — resten, kronologisk stigende.**

```
python harvest_futures_1min.py --symbols ES  --start 2024-09-01 --end 2026-08-04 --client-id 75
python harvest_futures_1min.py --symbols RTY --start 2024-09-01 --end 2026-08-04 --client-id 75
```

Hvorfor ét symbol ad gangen frem for `--symbols ES,RTY`: scriptet tager symbolerne i
raekkefoelge, saa en samlet koersel ville lade RTY's 202409 — den mest udsatte fil i
hele operationen — vente til allersidst. Delt op lander det udsatte foerst, jf. E3.

**Foerudsaetninger:** TWS/Gateway aabent, clientId 75 (ledig; backend=?, harvest=48,
asian=47). Trin 1 tager minutter, trin 2 en halv time pr. symbol, trin 3 flere timer
pr. symbol. Scriptet er resumerbart pr. fil — en afbrydelse koster ikke arbejdet, koer
bare samme kommando igen.

**Naar en hentning er faerdig**, koer fuldstaendighedsrevisionen (afsnit 4) og
derefter arkiveringen (afsnit 2).

---

## 2. Arkivet — placering og procedure

**Sti: `H:\trading_dash_arkiv`** (ekstern disk "Elements", 1,8 TB fri pr.
2026-08-04). Besluttet af Søren 2026-08-04. Arkivet er ~282 MB i dag og vokser med
ca. 140 MB pr. aar pr. symbolpar.

`data_harvest/mes_m2k_clean/` og `mes_m2k_stitched/` er **den eneste del af
datagrundlaget der ikke kan reproduceres**. De skal behandles som et primaert aktiv
paa niveau med kildekoden.

```
python arkiv_futures.py status          # hvad er nyt siden sidst
python arkiv_futures.py kopier --fuld   # kopiér + skriv manifest med sha256
python arkiv_futures.py gendan-test     # gendan FRA arkivet og verificér
```

**`gendan-test` er ikke valgfri.** En sikkerhedskopi ingen har proevet at gendanne er
en formodning, ikke en sikkerhedskopi. Kommandoen kopierer tilbage til en temp-mappe,
hasher hver fil mod manifestet og tjekker at CSV-hovedet stadig kan laeses.

### Tre begraensninger der skal vaere kendt paa forhaand

**1. `kopier` ser ikke paa arkivfilerne.** Den sammenligner KILDEN med manifestet.
Bliver en arkiveret fil beskadiget paa disken, melder `kopier` derfor "uaendret".
Bitroed i arkivet opdages **kun** af `verificer`, og repareres kun af
`verificer --reparer`. Kvartalsjobbet goer det automatisk.

**2. Uden `--fuld` ser `kopier` heller ikke bitroed i KILDEN.** Hurtigstien stoler
paa stoerrelse + tidsstempel, og aegte bitroed aendrer ikke mtime — disken taber en
bit, filsystemet ved intet. Brug `--fuld` naar det betyder noget; den hasher hver
kildefil og koster faa sekunder paa 282 MB.

**3. Arkivet er ikke selvhelbredende.** `--reparer` henter fra kilden. Er kilden vaek
eller beskadiget, kan et beskadiget arkiv ikke repareres — de to kopier er koblet.
Acceptabelt med én ekstern disk, men oenskes reel uafhaengighed, er svaret en kopi
mere paa en anden disk (`--dest`), ikke en ny mekanisme.

### Foranderlige og uforanderlige filer (Revision F1)

Manifestet markerer hver fil. **Barer fra en udloebet kontrakt aendrer sig aldrig** —
en hash-aendring dér kan kun vaere korruption, aldrig en opdatering. Saadanne filer
overskrives ALDRIG automatisk. Uden det skel kunne en raadden KILDEfil skrives hen
over en intakt arkivkopi, hvorefter manifestet opdateres og verifikationen melder
alt i orden: en selvbekraeftende fejl der oedelaegger data frem for blot at overse
skade.

Pr. 2026-08-04: **80 uforanderlige** (udloebne kontrakter), **22 foranderlige**
(202609-front-maaneden og de stitchede serier). Markeringen genberegnes ved hver
skrivning — en kontrakt der var front-maaned da den blev kopieret, er uforanderlig
et kvartal senere.

Skal en udloebet kontrakts fil alligevel opdateres — fordi en senere hoest fyldte et
hul — kraever det `--accepter-vaekst`, og selv da kun hvis kilden er en **verificeret
ren udvidelse**: hver eneste allerede arkiveret bar skal staa uaendret i kilden.
Korruption slipper ikke igennem den doer.

Verificeret 2026-08-04 med 24 testpaastande i `test_arkiv_futures.py`: begge
raadne-retninger, aegte udvidelse, foranderlig opdatering, gendannelse og reparation.

---

## 3. Det staaende kvartalsjob

```
python vol_kvartalsjob.py               # efter hvert kvartalsudloeb, TWS aabent
```

Kortlaegger hvilke kontrakter der stadig kan kvalificeres (med kontrolfikstur i
begge retninger), peger paa hvad der mangler at blive hoestet, verificerer og
reparerer arkivet, og skriver en post i `vol_probe_output/vol_arkiv_log.md`.

**Maalsaetning: arkivér en udloebet kontrakt inden for TOLV maaneder, ikke tyve.**
Marginen er hele pointen — graensen kan strammes uden varsel, og logposten er det
eneste sted vi ville opdage det.

Vaerdien paa laengere sigt: hoster vi hver kontrakt mens den lever, vokser arkivet med
et aar hvert aar. Om tre aar har vi fem aars futures-intradag og kan udvikle lag 3
direkte paa MES og M2K i stedet for omvejen om SPY og IWM. **Toaarsgraensen binder
kun saa laenge man ikke arkiverer.**

---

## 4. Fuldstaendighedsrevisionen (B3) — afslutning paa enhver harvest

```
python sessions_revision.py --mappe data_harvest/mes_m2k_stitched --moenster "*_1min.csv" \
       --vindue alle --marked futures --streng
python sessions_revision.py --mappe vol_cache --moenster "*_1min.csv" --streng
```

Taeller sessioner fundet mod forventet ud fra NYSE-kalenderen, lister de manglende
datoer, og udleder **effektiv start**: dér hvor sammenhaengen faktisk begynder — ikke
dér hvor den foerste bar ligger. Det er den dato en udviklingsperiode maa regnes fra.

Kontrollen indeholder ogsaa B2's assert paa konstant barantal pr. session, som skal
koeres **foer** enhver tid-paa-dagen-profil bygges.

### Resultat pr. 2026-08-04 (MES/M2K stitched)

| serie | sessioner | daekning | effektiv start | B2 |
|---|---|---|---|---|
| MES 1-min | 507/507 | 100,0 % | **2024-06-21** | OK |
| M2K 1-min | 507/507 | 100,0 % | **2024-06-21** | OK |

Ingen indre huller. Spor 2's effektive udviklingsstart er altsaa identisk med den
nominelle — det var ikke givet, og det er nu maalt frem for antaget (C4, punkt 5).

**Sidefund undervejs.** Foerste koersel meldte brud paa seks dage: 225 barer hvor
NYSE-kalenderen ventede 210. Det viste sig at CME's equity-index-futures lukker
**13:15 ET** paa halve dage, ikke 13:00 som NYSE. Data havde ret, kalenderen tog fejl.
`nyse_kalender.forventede_rth_minutter(d, marked="futures")` kender nu forskellen.
Det er praecis derfor revisionen ogsaa rapporterer "data findes, men kalenderen sagde
lukket" som sin egen kategori: en kalenderfejl maa ikke absorberes som et datahul.

---

## 5. B4 — percentilreferencens startdato for lag 1

**Beslutning: referencen starter 2009-08-17.**

Lag 1's fire serier findes hos IBKR fra hver sin dato:

| serie | foerste dagsbar hos IBKR | aarsag til graensen |
|---|---|---|
| SPY | 1993-02-01 | harvest-parameter (kan hentes dybere) |
| VIX | 2005-10-06 | vendor-onboarding |
| RVX | 2007-11-23 | vendor-onboarding |
| **VIX3M** | **2009-08-17** | **vendor-onboarding — den bindende** |

Terminsstrukturens haeldning kraever VIX og VIX3M samtidigt, og den er lag 1's
enkeltrigeste input. En faelles reference kan derfor ikke begynde foer 2009-08-17.

**Alternativet — at lade hver serie bruge sin egen fulde historik — er fravalgt.**
Det ville give VIX en reference der indeholder 2008 og VIX3M en der ikke goer, og saa
sammenlignes komponenternes percentiler paa tvaers af forskellige aeraer. En
sammensat tilstand bygget af den slags er ikke fortolkelig.

**Prisen skal staa skrevet: finanskrisen 2008 indgaar ikke i referencen.** Til
gengaeld daekker 2009-08 → i dag flash-crash 2010, eurokrisen 2011, august 2015,
februar og december 2018, marts 2020 og hele 2022. Det er en rimelig spredning af
stressregimer, og marts 2020 vil dominere yderkanten uanset startdato — hvilket V0.2
allerede kraever rapporteret som en foelsomhedsanalyse.

**Resterende arbejde:** kvalitetsrevisionen daekker 15 aar (fra 2011-08-09), saa
**2009-08-17 → 2011-08-09 indgaar i referencen uden at vaere revideret.** Det er ca.
to aar. `KVALITET_VARIGHED` i `vol_data_probe.py` staar paa `"15 Y"` fordi `"20 Y"`
timede ud; `"18 Y"` daekker referencen og er ikke afproevet endnu. Koer:

```
python vol_data_probe.py --niveau AB --kvalitet-varighed "18 Y"
```

Bestaar den ikke, er faldback at saette referencestarten til 2011-08-09 og notere at
vi har givet halvandet aars historik for at kunne staa inde for kvaliteten.

---

## 6. Aabne punkter

| # | Punkt | Status |
|---|---|---|
| D1.1 | Redningen (afsnit 1) | **afventer koersel — haster, 202409 doer om uger** |
| D1.2 | Arkiv paa H: | bygget og verificeret; foerste rigtige `kopier` afventer trin 1-3 |
| D1.3 | Kvartalsjob | bygget; koeres efter naeste udloeb (2026-09) |
| B2 | Assert paa barantal | bygget, testet |
| B3 | Fuldstaendighedsrevision | bygget, testet, koert paa futures |
| B4 | Referencestart | besluttet: 2009-08-17; 18-aars kvalitetskoersel udestaar |
| F1 | Uforanderlige filer beskyttes | bygget, testet |
| F2 | Beslutningsprotokoller i git | gjort — se nedenfor |
| G | Falsifikationskrav paa alle kontroller | `vol_falsifikation.py` + kontrollernes fejlveje koert |
| C2/C3 | Outputkontrakt + provenans | hoerer til V2, ikke paabegyndt |
| — | Datagab 2026-06-30 → i dag | lukkes af trin 1 |

---

## 7. Hvad der versionsstyres, og hvorfor (Revision F2)

Skillelinjen er ikke filtype, men funktion: **kan filen bruges til at bedoemme om vi
arbejdede aerligt, skal den i git.** Er den blot et mellemresultat der kan genskabes,
skal den ikke.

Kontamineringsloggens hele vaerdi er at den om seks maaneder kan laeses sammen med
valideringsrapporten og *troes*. En fil der kun findes lokalt kan redigeres eller
forsvinde sporloest — saa er den en paastand, ikke et bevis. En kontamineringslog man
kan rette i stilhed er vaerdiloes.

| i git | ignoreret |
|---|---|
| `vol_kontamineringslog.md` | `vol_data_probe.json` |
| `vol_arkiv_log.md` | `vol_futures_retention.json` |
| `vol_data_probe.md` | `vol_futures_overlevende.json` |
| `vol_futures_retention.md` | `vol_intradag_dybde.json` |
| `vol_intradag_dybde.md` | `kvalitet_vx.log` |

Implementeret med negationsmoenstre i `.gitignore` frem for at aabne hele mappen, saa
nye raa dumps forbliver ignoreret som standard.

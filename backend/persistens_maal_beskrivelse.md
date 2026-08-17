# Persistens — mål, benchmark og forhåndsviden

**Skrevet 2026-08-17, FØR nogen kandidatprædiktor findes i koden.**

Det er hele pointen med dokumentet. Alt herunder er kendt på forhånd, og det
skal stå her, så et fund der bekræfter det ikke kan tælles som et friskt fund.

> Det er ikke kontaminering at kende det — det er kontaminering at glemme at
> man kendte det. *(spec §4)*

---

## 1. Målet

```
ER(d) = |luk − aabning| / Σ |serie(i) − serie(i−1)|

serie = [aabning, luk₁, luk₂, … lukₙ]   for dagens 5-min RTH-barer
```

⚠ **Åbningen er med i nævneren.** Uden den ville bevægelsen fra sessionens
åbning til første bars luk tælle i tælleren men ikke i nævneren, og ER kunne
overstige 1 på en gap-dag. Med den er ER ∈ [0, 1] pr. konstruktion — og et mål
der pr. definition ligger i sit interval, kan kontrolleres.

**Halve dage udelades** (spec §2.1): 32 pr. instrument. Nævneren er en sum over
barer; 42 barer i stedet for 78 giver mekanisk højere ER, og halve dage klumper
sæsonmæssigt om helligdage.

**B2 bekræftet:** 78 barer på 3.638 af 3.639 SPY-sessioner og 3.640 af 3.640
IWM-sessioner.

---

## 2. Benchmarken — målt, ikke antaget

Gårsdagens ER som prædiktor for morgendagens. Udviklingsperiode
2011-12-30 → 2023-12-31, 5-min.

| | rho | KI95 blok 20 | KI95 blok 40 | par |
|---|---|---|---|---|
| **SPY** | **−0,0775** | [−0,1156, −0,0436] | [−0,1152, −0,0461] | 2.965 |
| **IWM** | **−0,0381** | [−0,0691, −0,0043] | [−0,0707, −0,0030] | 2.967 |

Begge udelukker nul — **på den negative side**. En trend-dag efterfølges lidt
oftere af en chop-dag end af en ny trend-dag.

**Før par-rettelsen** stod tallene på −0,0737 og −0,0351. Rettelsen krævede at
de to dage er faktiske nabo-handelsdage; 26 par blev sprunget over. Den blev
foretaget **før** nogen kandidat fandtes, netop fordi en rettelse efter
kandidattallene ikke kan skelnes fra tuning.

---

## 3. Bestå-kriteriet — Revision A, låst

⚠ **Det oprindelige kriterium var defekt, og det blev opdaget af benchmarkens
fortegn.** Det stod som *"kandidatens Spearman skal slå benchmarkens"*. Med en
benchmark på −0,08 slår **alt** den: et møntkast på 0,00 vinder med 0,08, og en
kandidat på +0,05 vinder med 0,13 uden at være værd at handle på.

Det er projektets tilbagevendende fejlklasse i ny forklædning — en kontrol hvis
udfald er strukturelt gunstigt, denne gang fordi benchmarken er negativ.

**Rettet kriterium, låst 2026-08-17:**

1. En stabil negativ sammenhæng er lige så anvendelig som en positiv af samme
   størrelse; man vender fortegnet i aflæsningen. **Benchmarkens brugbare
   styrke er derfor |rho| = 0,0775 (SPY) / 0,0381 (IWM).**
2. **Kandidater bedømmes på |rho| mod |benchmark|** — ellers gives de den
   dobbelte afstand gratis.
3. En kandidat må gerne selv være negativ. rho −0,20 er et udmærket resultat.
4. **Fortegnsstabilitet er et selvstændigt krav:** en prædiktor hvis fortegn
   skifter hen over gitteret 1/5/15-min er ikke brugbar, uanset |rho|.

**Og hvis ingen kandidat slår |0,08| meningsfuldt** (rho −0,08 forklarer under
1 % af rangvariansen — detekterbart og praktisk ubrugeligt på samme tid), er
konklusionen at trend/chop ikke kan afgøres et døgn i forvejen. De tre tilladte
udgange er da:

1. aksen hører til **sessionslaget**, ikke dagslaget — hvilket ville genåbne
   lag 3 med god grund
2. aksen skal komme fra **kalenderen**, som netop blev mere interessant
3. **trend/chop er ikke forudsigeligt for os**

> ⚠ *"Prøv flere prædiktorer" er ikke på listen.*

---

## 4. Forhåndsviden — deklareret før valget

### 4.1 Signal-evalueringen af 15-08

`analyse/SIGNAL_EVAL.md`, commit `1e99cdf`, 25 måneders MES, n = 14.302:

> 15m-z ≥ +2 gør et 0,8 %-fald inden for 20 minutter cirka **halvt** så
> sandsynligt. Løft 0,54, z = −6,0.

Altså: **bevægelser fortsætter oftere end de vender på den horisont.**

Hvad det **ikke** siger: det testede én indikator som selvstændig prædiktor på
én horisont mod ét mål. Det falsificerer ikke mean reversion generelt, og det
siger intet om et system med stops og targets.

### 4.2 Benchmarkens eget fortegn

Anti-persistens på dagsniveau, målt ovenfor. Enhver kandidat der finder samme
fortegn, bekræfter noget der allerede er kendt — den opdager det ikke.

### 4.3 Konvergensen med m7 — og hvad den ikke er

Den gamle motor konkluderede at median(m7) var anti-persistent. Spec §2.3
formoder at det er et artefakt af vinduesudglatning: serien var beregnet med
dagligt skridt over 30-dages vinduer, så nabodage delte 29 af 30 observationer.

Målingen her har **ingen udglatning** — ER beregnes pr. dag af
ikke-overlappende barer — og fortegnet holder alligevel. **Artefakt-hypotesen
er derfor ikke længere nødvendig for at forklare m7**, og formodningen står
ikke uimodsagt.

⚠ **To forbehold, så konvergensen ikke oversælges:**

- H2's metoderegel gælder stadig generelt. Den forklarer blot ikke *dette* fund.
- **De to målinger måler ikke samme størrelse.** m7 var variance ratio på
  ES/RTY, 15-min, 30-dages vinduer; dette er daglig ER på 5-min. Samme fortegn
  fra to forskellige mål er interessant, men det er **konvergens, ikke
  bekræftelse**.

---

## 5. Dataopdeling — håndhævet i kode

| Periode | Navn | Regler |
|---|---|---|
| 2011-12-30 → 2023-12-31 | Udvikling | Fri iteration |
| 2024 | Design-validering | Højst 3 kørsler, hver logget |
| 2025 → i dag | Holdout | Én gang, efter frysning |

`persistens_benchmark.py` filtrerer på `UDVIKLING_SLUT` i koden, ikke i
disciplin.

---

## 6. Kandidatprædiktorer — endnu ingen valgt

Dette afsnit udfyldes når prædiktorerne bygges. Det skal indeholde:

- **antal varianter prøvet pr. kandidat**, ikke kun den valgte
- for hver forkastet variant: hvad den var, og hvorfor den røg
- robusthedsgitteret 1 / 5 / 15-min, rapporteret — ikke valgt imellem
- de tre percentilreferencer (ekspanderende, 252, 504), alle rapporteret

Ingen af delene findes endnu. Dokumentet er skrevet **før** for at kunne bevise
det.

# Spec: trading-simulator (bar-replay)

**Fra:** VS Code Claude · **Til:** desktop Claude / Cowork
**Dato:** 11-08-2026
**Status:** ⚠ **Beslutningsoplæg. Ingen kode skrevet.**

---

## 0. Formål

Øve daytrading mod historiske 1-min barer, hurtigere end live paper trading kan
levere. En handelsdag på ti minutter, femten tusind gange.

⚠ **Seks beslutninger skal træffes før første linje.** De står i §2 med min
anbefaling ved hver — men de er jeres at afgøre, ikke mine.

---

## 1. Datagrundlaget — målt 11-08-2026

Alt ligger under `backend/`. Se `hvor-ligger-markedsdata` i hukommelsen.

| Kilde | Omfang | Egnethed |
|---|---|---|
| **`bar_cache/`** | 225 tickere · **15.485 ticker-dage** 1-min · 2021-08-10 → 2026-06-30 · median 49 dage/ticker | ⚠ **Kernen.** Fulde RTH-sessioner, ~390 barer/dag |
| `data_trendjoin/` | 99 tickere · 5,5 mio. **5-min** barer · 2025-03 → 2026-06 | Large caps. For grov til scalping, fin til swing-øvelse |
| `catalyst_data/bars/` | 15 large caps · 526.500 1-min · 2026-02 → 2026-06 | `news/` ligger ved siden af — nyhedsdrevet øvelse mulig |
| `data_harvest/mes_m2k_stitched/` | MES 1-min: **752.775 barer · 665 dage** · 2024-06 → 2026-08 | Futures. Det instrument vi faktisk kan eksekvere |

**15.485 aktie-sessioner + 665 futures-dage.** Én session pr. øvegang betyder
årevis uden gensyn.

⚠ **MNQ er ikke i høsten.** Kun MES og M2K. Skal MNQ med, er det en
selvstændig høst.

---

## 2. De seks beslutninger

### B1 · ⚠ Look-ahead-porten — alt andet er pynt

Kan ét sted i kæden se en fremtidig bar, træner simulatoren **falsk
selvtillid**, og det er værre end ingen træning.

Lækagerne er ikke oplagte:

| Vej | Hvordan den lækker |
|---|---|
| Charten | tegner hele serien og skjuler højre side i CSS |
| Indikatorer | beregnes på hele serien, skæres til bagefter |
| Universet | tickeren blev valgt **fordi** den bevægede sig (se B4) |
| ⚠ **Datoen** | ser du 5. februar, husker du måske hvad der skete |

**Min anbefaling:** bar-serveren ejer uret. Den kan **kun** returnere barer
≤ T, og frontenden får aldrig serien. Med falsifikationstest der beder om T+1 og
beviser afvisning — samme disciplin som `TilladtInput` i `vol_lag2.py`.

**Segmenter anonymiseres:** intet ticker-symbol, ingen dato på skærmen under
kørsel. Begge afsløres først ved sessionens slutning.

⚠ Det koster noget: du kan ikke slå op i nyheder undervejs, hvilket er en del af
rigtig daytrading. **Er det acceptabelt?**

---

### B2 · ⚠ Intrabar-rækkefølgen kan ikke vides

Bar med high 7800, low 7790. Stop 7792, target 7799. **Begge inde i baren.**
Hvad ramte først? OHLC kan ikke svare, og vi har ingen tick-data.

| Valg | Konsekvens |
|---|---|
| **Pessimistisk** — stoppet først | Undervurderer systematisk. Træner disciplin |
| Optimistisk — target først | Overvurderer. Træner overmod |
| Tilfældigt | Ærligt om usikkerheden, men samme session giver forskelligt resultat to gange |
| Afvis — ingen fill, marker baren | Ingen løgn, men mange handler bliver uafgjorte |

**Min anbefaling: pessimistisk**, og det skal **stå på skærmen**, ikke gemmes i
koden. Det er den ærlige retning at fejle i.

---

### B3 · ⚠ Hvilken grænseflade — og her er en reel konflikt

| | Træner replay det? |
|---|---|
| Mønstergenkendelse | **ja** |
| Eksekvering under tidspres | kun ved realtidshastighed |
| Følelsesmæssig disciplin | **nej** — intet på spil |
| Muskelhukommelse i platformen | **kun hvis grænsefladen er den samme** |

Det sidste peger på at bygge simulatoren **ind i Trading Dashs watchlist** med
samme K og S. Så træner du de hænder du skal bruge.

⚠ **Men det peger den anden vej på sikkerhed.** Watchlisten er koblet til live
kurser og til IBKR-ordrevejen. En "simulator-tilstand" skal bytte både
priskilden og ordrevejen ud — og en fejl dér kan sende en øve-ordre til en
rigtig konto.

Vi har i dag brugt to dage på at bygge værn mod præcis den fejlklasse
(`ordre_forbindelse`, V1–V3, kvitteringen der viser kontoen). At åbne den vej
igen for en simulators skyld er ikke en lille beslutning.

| Valg | Muskelhukommelse | Sprængradius |
|---|---|---|
| I watchlisten | ✓ den rigtige | ⚠ deler kode med den rigtige ordrevej |
| Eget vindue | ✗ andre knapper | ✓ kan ikke nå IBKR |

**Min anbefaling: eget vindue**, men med **identisk tastatur** (K/S/ALT+tal) og
identisk visuel opbygning. Så træner man hænderne uden at dele kode med
ordrevejen. ⚠ Jeg er ikke sikker på at det er rigtigt — det er den beslutning
jeg helst vil have jeres modspil på.

---

### B4 · ⚠ Udvælgelsen af sessioner er ikke en detalje

`bar_cache` er skrevet af `backtest_confluence2`, `cross_sectional_rs_backtest`,
`download_midcap_bars` og `live_scanner` — **det univers scannerne udvalgte**.
Tickerne er der fordi de bevægede sig.

Øver du kun på dem, træner du et marked hvor der **altid sker noget**. Og så
lærer du at handle for ofte, hvilket er den mest almindelige måde at tabe penge
på som daytrader.

**Min anbefaling:**

1. bland kedelige dage ind bevidst — også for de samme tickere
2. vis universet **som scanneren så det den morgen**, ikke som vi ved det blev
3. mål hvor ofte du handler i simulatoren mod hvor ofte du burde

⚠ Punkt 2 kræver point-in-time-univers. `cross_sectional_rs_backtest.py` har
allerede den mekanik — værd at se på før noget bygges.

---

### B5 · Gentagelse er memorering

Samme dag to gange = du lærer **den dag**, ikke markedet.

**Min anbefaling:** tilfældigt valg, log over brugte segmenter, og ⚠ **et sæt
der aldrig øves på** — så vi kan måle om evnen overføres.

---

### B6 · ⚠ Hvordan ved vi om det virker?

Projektets egen standard. Uden et mål er det legetøj.

**Min anbefaling:** hit rate og forventning på **usete** segmenter, målt over
tid. Præregistreret, som `oversalg_forudsigelse.md`.

⚠ **Og den ubehagelige mulighed skal stå eksplicit:** det kan vise sig at
simulatoren ikke forbedrer noget. Replay-træning har blandet ry, og
mønstergenkendelse på historiske barer overføres ikke nødvendigvis til et
levende marked. **Rapportér det hvis det er det tallene siger** — samme regel
som EUMOMENTUM og det asiatiske spor, der begge blev lukket på et rent nej.

---

## 3. Arkitektur

### Det vi allerede ejer

| | |
|---|---|
| Barer | 2 GB, målt ovenfor |
| Journal + forensik | entry/exit, P&L, MFE/MAE, chart-barer — `manuel_forensik.py` |
| Fill-logik | strategierne modellerer det allerede |
| Indikatorer | `indicators_cmf`, strategiernes egne |
| Point-in-time-univers | `cross_sectional_rs_backtest.py` |

### Det der er nyt

| | Omfang |
|---|---|
| **Bar-server med look-ahead-port** | lille, men den vigtigste kode i projektet |
| **Ur og afspilning** | 1×, 5×, spring til næste bar, pause |
| **Chart** | ⚠ **det tunge stykke** |
| Sessionsstyring | valg, log, usete segmenter |

⚠ **Der er intet chart-bibliotek i projektet.** `HandelsChart.tsx` og Studio
tegner håndlavet SVG. Det er en fordel — intet bibliotek der kan komme til at
kigge frem — men en *løbende* chart med 400 lys der opdateres hvert sekund er
reelt arbejde, ikke genbrug.

---

## 4. Byggerækkefølge

1. **Bar-server + look-ahead-port**, med falsifikationstest. Intet UI
2. **Ur og afspilning**, verificeret mod porten
3. **Chart**
4. **Ordreindtastning + fills** efter B2's regel
5. **Sessionsstyring** efter B4 og B5
6. **Måling** efter B6

⚠ Trin 1 og 2 kan afprøves helt uden grænseflade. Bliver de ikke grønne, er der
ingen grund til at tegne noget.

---

## 5. Ikke-mål

- **Ikke en backtest-motor.** Vi har `backtest_confluence2` m.fl.
- **Ikke live-data.** Simulatoren rører ikke IBKR
- **Ikke en ny strategi.** Den træner et menneske, ikke en algoritme
- ⚠ **Ikke aktie-høst.** Data er der allerede; det var min fejl at tro andet

---

## 6. Spørgsmål tilbage

1. **B3** — eget vindue eller i watchlisten? Muskelhukommelse mod sprængradius
2. **B1** — er anonymiserede segmenter acceptabelt, når det koster nyhedslæsning?
3. **B2** — pessimistiske fills: enig?
4. **Aktier eller futures først?** Aktier har 23× flere sessioner; futures er
   det vi kan eksekvere

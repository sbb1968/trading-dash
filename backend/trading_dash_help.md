# Trading Dash – brugerdokumentation (kilde for hjælpe-assistenten)

> **Søren:** Denne fil er det eneste hjælpe-assistenten ved om Trading Dash. Den
> relayerer ordret hvad der står her, så forkert eller manglende indhold giver
> forkerte eller manglende svar. Gennemgå den for korrekthed, og **udvid især
> "Adfærd / hvorfor gør den sådan"** hver gang Iben rammer et spørgsmål den ikke
> kunne svare godt på. Filen genindlæses ved hvert spørgsmål – ingen genstart nødvendig.

## Hvad er Trading Dash
En trading-platform til small-cap momentum-handel. Målet er at Trading Dash på sigt
er det eneste vindue man bruger i handelsdagen, mens IBKR TWS og TradingView kører i
baggrunden.

## Vinduer (åbnes via menuen "Tilføj vindue", ALT+T)
Menuen er delt i grupper. Disse vinduer findes:

**Charts** – 1, 2, 3, 5, 10, 15, 30 min, 1 time, 4 time, daily, weekly. Vælg tidsramme i dropdownen og tryk Tilføj.

**Scannere**
- **Swing trading Top-15** – de 15 bedste swing-kandidater lige nu.
- **Day trading Top-15** – de 15 bedste day trading-kandidater.
- **Halt-scanner** – aktier der er handelsstoppet (halted).

**Markedsdata**
- **Watchlist** – egen overvågningsliste. Klik på en ticker for at åbne firmaets hjemmeside.
- **Level 2** og **Time & Sales** – orderbog og tape.
- **Markedsoverblik** (titellinjen kan vise "MARKETOVERVIEW") – samlet markedsbarometer: aktivitetsscore, VIX, SPY/IWM og en small cap-scanner. Se eget afsnit nedenfor.
- **Sektorer** – overblik over de 11 sektorer og deres nicher. Se eget afsnit nedenfor.

**Analyse**
- **Swing trading-rapport** – scorer ÉN aktie på egnethed til swing-handel; rapport kan åbnes/printes som PDF via knappen.
- **Buy-and-Hold-rapport** – langsigtet trend-rapport for én aktie (PDF).
- **Buy-and-Hold Top-15** – de 15 bedste buy-and-hold-kandidater.
- **Day trading-rapport** – day trading-rapport.

**Konto & ordrer**
- **Konto** – IBKR konto-balance, equity og positioner.
- **Ordrer** – ordre-status/-historik.

**Algo & log**
- **Live Algo** – starter/stopper de automatiske strategier og viser status + log i realtid.
- **Dagens log** – dagens hændelser (events) fra systemet.

**Øvrige**
- **Dokumentation** – PDF-guides (åbnes eksternt).
- **Hjælp (Claude)** – dette hjælpe-vindue.
- **Paper Trading** – øve-handel uden rigtige penge (ALT+K køb / ALT+S sælg).

## Markedsoverblik-vinduet
Et samlet markedsbarometer der hjælper med at vurdere om det er en god handelsdag:
- **Aktivitetsscore (0–100):** beregnes ud fra VIX (30 %), SPY-gap (20 %), antal gap-aktier (25 %) og volumen-aktivitet (25 %). Under 40 = rolig dag (algoritmen handler ikke), 40–60 = moderat (halv position size), over 60 = aktiv (fuld position size).
- **Markedsindikatorer:** VIX, SPY-kurs, SPY-gap og SPY intradag (vs. open + VWAP).
- **IWM** – small cap-regimet (Russell 2000): kurs, gap og intradag.
- **Small Cap Scanner:** antal aktier med gap > 10 %, antal med høj volumen, og dagens top gainers.

**Sådan får du en frisk opdatering:** klik på **↺ Opdater**-knappen øverst i vinduet. Markedsoverblik er **ikke** realtidsopdateret – det henter et nyt øjebliksbillede, hver gang du trykker. Linjen **"Beregnet af backend: HH:MM:SS ET"** viser hvornår tallene sidst blev udregnet; rykker den tid, når du trykker Opdater, er data blevet genberegnet.

**Hvorfor ændrer scoren sig ikke?** Hvis "Beregnet"-tiden rykker, men selve scoren bliver stående (fx på 40), er det fordi markedsforholdene er uændrede – typisk fordi **markedet er lukket** (VIX/SPY/scanner bevæger sig ikke uden for handelstid). Det er normalt. Når markedet er åbent og aktivt, bevæger både tiden og scoren sig.

## Sektorer-vinduet
To-delt overblik over markedets sektorer:
- **De 11 sektorer** (Technology, Health Care, Financials, …) med ydeevne **nu / 1 uge / 1 måned** og hver sektors **andel** (størrelse) af de 11.
- **Klik på en sektor** for at folde dens **nicher** ud (fx Semiconductors, Software under Technology) med samme tal + nichens andel inden for sektoren.
Vinduet opdaterer automatisk ca. hvert minut og kan opdateres manuelt med **↻ Opdatér**. Data kommer fra TradingView og er ca. 15 min forsinket.

## Sådan opdateres data (kort regel)
- **Realtids-vinduer** (Live Algo, Level 2, Time & Sales, Watchlist) opdaterer løbende af sig selv via backenden.
- **Øjebliksbillede-vinduer** (Markedsoverblik, Sektorer, Top-15-lister, rapporter) opdateres med en **opdater-knap i selve vinduet** (↺ / ↻) – de er ikke realtids. Sektorer opdaterer dog også automatisk hvert minut.

## Sådan åbner du en chart
1. Tryk **ALT+T** for at åbne "Tilføj vindue"-menuen.
2. Øverst under **Charts** vælger du tidsrammen (fx "5 minutter") i dropdownen og trykker **Tilføj**. Chart-vinduet åbnes.
3. Skriv tickeren (fx **AAPL**) i **tickerfeltet** i chart-vinduet.

Bemærk: Tidsrammen vælges i dropdownen i menuen, så chartet åbner med den valgte tidsramme. Du kan bagefter ændre tidsrammen inde i selve chart-vinduet.

## Tastatur-genveje
- **ALT+T** – Tilføj vindue.
- **ALT+V** – Værktøjer (Konfigurator, Layout-vælger, Tema, Åbn Skærm 2).
- **ALT+A** – Auto-arrange (rydder op i vinduerne og placerer dem i et fast layout).
- **ALT+K** – Køb i Paper Trading.
- **ALT+S** – Sælg i Paper Trading.
- Når en dropdown er åben: tryk det **understregede bogstav** i et menupunkt for at vælge det (uden ALT).

## Temaer og skrift
12 temaer vælges under **Værktøjer → Tema** (stealth er standard). Skriftstørrelser pr.
vinduestype sættes i **Konfiguratoren** (Værktøjer → Konfigurator) og gemmes lokalt på maskinen.

## Skærm 2
En selvstændig skærm 2 kan åbnes under **Værktøjer → Åbn Skærm 2**. Layouts kan have
egne vinduer på skærm 2.

## Adfærd / hvorfor gør den sådan (typiske spørgsmål)
- **"Hvordan får jeg friske tal i Markedsoverblik / Sektorer / en Top-15-liste?":** Klik
  opdater-knappen (↺ / ↻) øverst i vinduet. De er øjebliksbilleder, ikke realtid. I
  Markedsoverblik viser "Beregnet af backend"-tiden om tallene rent faktisk blev genberegnet.
- **Markedsoverblik-scoren står stille (fx 40):** Hvis "Beregnet"-tiden rykker når du
  trykker Opdater, genberegnes den fint – tallet er bare uændret fordi markedsforholdene er
  det (typisk lukket marked). Det er forventet.
- **Vinduerne flytter sig / bytter rundt:** Det er typisk **Auto-arrange** (⊞-knappen i
  menubaren eller ALT+A), som placerer alle vinduer i et fast layout. Det kan også være
  at et gemt **layout** er blevet anvendt (Værktøjer → Layout-vælger). Sker det helt af
  sig selv uden at man trykker noget, er det værd at sige til Søren.
- **Live Algo / data forsvinder eller genforbinder:** Live-vinduerne forbinder til
  backenden og **genforbinder automatisk hvert 3. sekund** hvis forbindelsen tabes. En
  **gul advarsel** om at forbindelsen lukkede og genforbinder er normal, fx mens backenden
  genstartes – den bliver grøn ("Forbundet") igen af sig selv. Vedvarende udfald (uden at
  nogen har genstartet noget) betyder at backenden eller TWS er nede.
- _(Søren: tilføj flere her efterhånden – fx layout-skift, lyd-knappen, kendte småfejl.)_

# FX-probe mod IBKR — rapport

**Kørt:** 30-08-2026 (søndag, marked lukket) · **Konto:** DUN748991 (paper) ·
**Forbindelse:** TWS :7497 · **Script:** `fx_probe.py` · **Rå data:** `fx_probe_output/`

---

## Svar på de to spørgsmål

**Kan vi handle forex gennem vores IBKR-API?** Ja. Spot FX er aktiveret på
paper-kontoen, alle fjorten par kvalificerer på IDEALPRO, og kurser og
historik kommer ind gennem præcis den forbindelse vi har. Der skal ingen ny
adapter til for at *nå* markedet.

**Hvad er gearingen?** **Ikke målt — men vi ved nu at spørgsmålet måske er
forkert stillet.** Alle 48 whatIf-kald på spot FX blev **aktivt afvist** med
kode 201: *"FX trade would expose account to currency leverage."* Samme kode,
samme tekst, to uafhængige kørsler. Kontrolinstrumentet MES blev i samme
kørsel *tavst* tomt — to forskellige fejltilstande, side om side.

Kontoen er bekræftet **Retail hos IBIE**, hvor ESMA-loftet er 30:1 på majors.
At gearet spot FX alligevel afvises peger på at spot hos os er
*valutaveksling*, og at gearet FX-eksponering ligger i **Forex CFD** — et
andet produkt med egen tilladelse. Det skal bekræftes med åbent marked
(søndag 23:15) før det bogføres.

⚠ **Det vigtigste fund er ikke gearingen.** Det er at `get_positions_reliable()`
lover at en tom liste betyder *"kontoen er FAKTISK flad"* — og at det løfte
bliver usandt i samme øjeblik der ligger en FX-position. Se §P3/§P8.

---

## Om denne rapport

**8 af de 10 dokumenter specen henviser til findes ikke i projektet:**
`beslutning_oevevindue_og_instrument.md`, `arbejdsordre_15aug2026.md`,
`spec_volatilitet.md`, `cockpit_design_spec.md`, `spec_execution_ninjatrader.md`,
`spec_handelstimer_analyse.md`, `screener_scalping_crypto.md`,
`spec_instrument_profiler_crypto.md`. Kun `regime_data_depth_probe.py` og
`run_smoke.py`-mønsteret havde en pendant (`run_smoke.py` findes heller ikke;
`--kun`-mønsteret er lånt fra de eksisterende prober). Kravene står i specen
selv og er fulgt; henvisningerne er ikke.

### Afvigelse fra sikkerhedsreglerne

| Regel | Status |
|---|---|
| S1 paper-konto | ✔ `DU`-præfiks assertes; afbryder med `SystemExit` |
| **S2 kun port 4002** | ⚠ **afveget** — se nedenfor |
| S3 kun `whatIf` | ✔ `assert order.whatIf is True` umiddelbart før hvert kald |
| S4 read-only default | ✔ `--tillad-ordre` defaulter til `False` |
| S5 luk i samme kørsel | ✔ implementeret; ikke afprøvet (P3 ikke kørt) |
| S6 manglende = manglende | ✔ `None` → `—` overalt; ingen `0`-defaults |

**S2:** der er ingen Gateway på 4002 på nogen maskine lige nu. DUQ441063 kører
**TWS på 7497** uden Gateway, og `account.yaml` på denne maskine har
`ordre_forbindelse` (4002) udkommenteret siden 11-08. Reglen som skrevet er
uopfyldelig. Dens **formål** — ram aldrig en live-port — er håndhævet i stedet:

```python
PAPER_PORTE = {4002, 7497}
LIVE_PORTE  = {4001, 7496}
```

**Spærrerne er falsificeret, ikke antaget.** Alle fire adfærd er set:

```
port 7496  -> AFBRUDT: 7496 er en LIVE-port. Proben koerer kun paper.
port 4001  -> AFBRUDT: 4001 er en LIVE-port. Proben koerer kun paper.
port 8000  -> AFBRUDT: 8000 staar ikke paa paper-allowlisten [4002, 7497].
port 7497  -> forbundet · konti ['DUN748991'] · clientId 22
```

`fx_probe.py` er registreret i `ibkr_client_ids.py` med **id 22** — ikke et
tilfældigt tal.

---

## P0 · Konto og klassifikation

| Tag | Værdi |
|---|---|
| Konto | DUN748991 (paper ✔) |
| AccountType | INDIVIDUAL |
| NetLiquidation | 9.673,16 USD |
| AvailableFunds | 9.673,16 USD |
| BuyingPower | 64.487,73 USD |
| Cushion | 1 |
| FullInitMarginReq | 0,00 USD |
| FxCashBalance | 0,00 |
| **Juridisk enhed** | **IBIE — Interactive Brokers Ireland** ✔ |
| **MiFID-kategori** | **Retail Client** ✔ |
| Account Type (live U22652219) | Margin · IBKR Pro · bopæl Danmark |

**Bestå-kriteriet er opfyldt — men ikke via API'et.** `reqAccountSummary`
eksponerer hverken juridisk enhed eller MiFID-kategori; `AccountType` siger
kontoform, ikke regulatorisk status. Begge blev bekræftet manuelt i Client
Portal 30-08-2026: domænet er `interactivebrokers.**ie**`, og
*Settings → MiFID Client Category* siger ordret *"Interactive Brokers currently
treats you as a Retail Client entitled to the highest level of regulatory
protection."*

### ⚠ Det gør P2 til en falsificerbar test i stedet for en aflæsning

Retail under IBIE betyder at ESMA's produktintervention gælder, og dermed at
gearingen har et **loft vi kan forudsige før vi måler**:

| Par | ESMA-kategori | Forventet loft | Forventet initial margin |
|---|---|---|---|
| EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD | major | 30:1 | 3,33 % |
| **AUDUSD, NZDUSD** | **ikke-major** | **20:1** | **5 %** |
| EURDKK | ikke-major | 20:1 | 5 % |

⚠ **ESMA's "major" er snævrere end dagligsprogets:** par sammensat af to af
{USD, EUR, JPY, GBP, CAD, CHF}. **AUD og NZD er ikke med.** AUDUSD og NZDUSD er
altså ikke-majors under reglen, selv om enhver handelsplatform kalder dem
majors. Det er probens skarpeste enkeltforudsigelse, og den er lagt ind i
koden (`esma_forventning()`) så P2 selv sammenligner målt mod forventet.

⚠ **Loftet er et loft, ikke et løfte.** IBKR må kræve *mere* end reglens
minimum, og gør det ofte. Måler vi 3,33 %, er reglen bindende; måler vi mere,
er det IBKR's husmargin. Begge dele er svar. Men **mindre end 3,33 % ville
betyde at en af antagelserne er forkert** — og det er dét der gør det til en
test.

⚠ Bemærk `BuyingPower / NetLiquidation = 6,67` på paper-kontoen. Det er
aktie-købekraft, ikke et FX-tal, og må ikke læses som FX-gearing.

---

## P1 · Tilgængelighed og kontraktfakta

**Alle 14 par er handelbare** på IDEALPRO. Fuld tabel i
`fx_instrument_profil.md`; her kun det der modsiger en antagelse:

⚠ **`minSize` = 0,01 eller 1,0 — ikke 20.000–25.000.** Specen krævede tallet
fra `ContractDetails` frem for websitet. Det blev hentet derfra, og de to
kilder er uenige med en faktor 2,5 million. Hypotesen er at `minSize` beskriver
IDEALFX-rutning og at IDEALPRO-minimum først håndhæves ved ordreafgivelse.
**Uafklaret** — kan kun afgøres med en ægte ordre under minimum (P3).

⚠ **`minTick` = 0,00005 — halve pip.** `ticks_pr_pip = 2,0` for **alle** par,
også JPY-krydserne (0,005 mod pip 0,01). Tick-gitteret er ikke pip-gitteret.

---

## P2 · Gearingsmålingen — ⚠ IKKE MÅLT, MEN IKKE UDEN SVAR

**48 whatIf-kald på spot FX + 3 på Forex CFD.** Første kørsel gav tomme svar
hele vejen igennem.

Det ser ud som et fund — *"kontoen får ingen margin på spot FX"*, begrundet i
48 målinger. Apparat-kontrollen er grunden til at rapporten ikke skrev det:

```
apparat-kontrol: ⚠ NEDE
  kontrolinstrument MES 20260918 · whatIf-status: tomt_uden_fejl
  → Tomme FX-svar siger INTET om FX. P2 skal koeres igen naar FX er aabent.
```

**MES kom også tomt tilbage.** MES har en margin vi kender og en kontrakt vi
handler hver uge. Når den måling fejler, er instrumentet i stykker — og et
tomt FX-svar bærer så ingen information om FX.

Uden kontrolgruppen ville sætningen være røget i rapporten som et fund. Den
ville have været umulig at gennemskue bagefter. Det er projektets
tilbagevendende fejlklasse: **en kontrol hvis fejl behandles som en
beslutning.**

⚠ Men kontrollen var kun det halve arbejde. Den forhindrede en forkert
konklusion — den afdækkede ikke at proben samtidig **kasserede sit eget
vigtigste svar**. Det gjorde næste afsnit.

### Hvorfor er apparatet nede? Tre hypoteser, ét forsøg

"Lukket marked" var først en formodning. Søren rejste den rigtige indvending —
kunne det være **manglende prisdata på kontoen**? Det er en anden forklaring
med helt andre konsekvenser, så den blev afgjort med et diskriminerende forsøg
i stedet for et skøn:

| | H1 lukket marked | H2 manglende prisdata | H3 whatIf generelt i stykker |
|---|---|---|---|
| Forudsigelse | intet virker | virker dér hvor vi HAR abonnement | intet virker |

**AAPL er nøglen:** kontoen har NASDAQ/NYSE-abonnement. Virker whatIf dér mens
futures og FX fejler, er det H2.

```
AAPL     type=LIVE   bid=-1  ask=-1  last=nan  close=314.58
MES      type=LIVE   bid=-1  ask=-1  last=nan  close=7742.5
EURUSD   type=LIVE   bid=-1  ask=-1  last=nan  close=1.1652

AAPL   MKT BUY 10        TOM
AAPL   LMT BUY 10 @200   TOM
MES    MKT BUY 1         TOM
EURUSD MKT BUY 25000     TOM
```

**H2 er udelukket.** whatIf fejler også på AAPL, hvor TWS selv melder
`marketDataType = LIVE`. Kontoens abonnementer er altså i orden, og valget
mellem DUN748991, DUQ441063 og DUO509856 er uden betydning for dette
spørgsmål.

⚠ Og `bid = −1`, `ask = −1`, `last = NaN` på **alle tre** aktivklasser peger
direkte på årsagen: der findes ingen aktuelle kurser, kun sidste `close`.
whatIf kan ikke beregne margin uden en kurs. **H1 er dermed understøttet, ikke
blot formodet** — men den endelige bekræftelse er stadig at målingen lykkes
når markedet åbner.

### ⚠ RETTELSE: der var to slags tomme svar, og proben blandede dem sammen

Ovenstående var kun halvdelen af sandheden, og fejlen var min. `whatIfOrderAsync`
resolverer sin future på `openOrderEnd`, men fejlbeskeden ankommer et øjeblik
**senere** på `errorEvent`. Jeg aflæste fejlopsamleren med det samme — så et
**aktivt afvist** kald blev bogført som `tomt_uden_fejl`.

Det er præcis den skelnen hele P2 hænger på:

| Status | Betydning |
|---|---|
| `tomt_uden_fejl` | apparatet kunne ikke regne — ingen information |
| `afvist` + kode | IBKR sagde aktivt nej, med en begrundelse — **information** |

Med 0,4 sekunders ventetid før aflæsningen ser billedet helt anderledes ud:

```
alle 48 spot-FX-kald   ->  afvist, kode 201, samme tekst hver gang
MES (kontrol)          ->  tomt_uden_fejl, INGEN fejlkode
CFD 25k / 100k         ->  tomt_uden_fejl
CFD 500k               ->  afvist
```

```
Error 201: Order rejected - reason: FX trade would expose account to currency leverage.
```

**48 ud af 48, uden undtagelse, i to uafhængige kørsler.** Det er ikke en
lukket-marked-artefakt: MES bliver tavst tomt i samme kørsel, mens FX bliver
aktivt afvist. De to fejltilstande optræder side om side og skiller sig ad.

⚠ **Hypotesen det peger på:** for en retail-klient hos IBIE er spot FX
begrænset til *valutaveksling* — man må konvertere det man har, men ikke tage
gearet eksponering. Den gearede FX-adgang ligger i så fald i **Forex CFD**,
som er et andet produkt med egen tilladelse. Det ville forklare hvorfor CFD'en
opfører sig anderledes end spot i tabellen ovenfor, og det ville gøre
CFD-sammenligningen til P2's afgørende måling frem for en fodnote.

**Men det er stadig en hypotese.** Den skal bekræftes med åbent marked, før
den bogføres. Fejl 201 kan i princippet også udløses af at margin ikke kan
beregnes uden kurser.

### Pip-regnestykket fra samtalen

Til kalibrering — venstre kolonne er stadig **antaget**, ikke målt:

| | Antaget (30:1) | Målt |
|---|---|---|
| Notional 25.000 EUR ved 1,1652 | ~$29.130 | ✔ kursen er målt |
| Margin | ~$971 | **—** |
| 1 pip | $2,50 | ✔ **$2,50 målt** |
| 100 pip | $250 = ~26 % af margin | **—** |

Pointen fra samtalen står uændret: 1 pip er lidt på notional og meget på
indskud. Men **nævneren mangler stadig**.

⚠ **Og selv når den måles: paper-konti bruger ikke altid samme marginmodel som
live.** En gearing målt her er ikke nødvendigvis den der gælder med penge på.

---

## P3 · Positionsrepræsentationen — ⚠ IKKE KØRT

Kræver `--tillad-ordre` og et åbent marked. Implementeret og klar:
snapshot → køb minimum → **vent på verificeret `Filled`** (ikke `Submitted`) →
snapshot → luk i samme kørsel → snapshot → verificér flad.

**Men spørgsmål 4 kan besvares allerede nu — fra koden.** Og svaret er
alvorligere end specen forudså.

`ibkr_connect.py:568` `get_positions_reliable()` giver en eksplicit garanti:

> `reliable=True` → *"listen er autoritativ (tom liste = kontoen er FAKTISK flad)"*

Den garanti er omhyggeligt bygget: den falder aldrig tilbage til cachen, den
skelner mellem "tom fordi flad" og "tom fordi vi ikke ved det", og den findes
netop for at forhindre at tavshed læses som et svar.

⚠ **Og den bliver usand i samme sekund der ligger en FX-position.** Spot FX er
to valutabalancer, ikke en position. `positions()` returnerer tomt. `reliable`
er `True`, fordi `reqPositions` nåede `positionEnd` — svaret *er* autoritativt.
Konklusionen "kontoen er FAKTISK flad" er så både velbegrundet og forkert.

**Det er ikke en fejl i koden.** Det er en garanti hvis forudsætning —
*`positions()` dækker al eksponering* — holder op med at gælde uden at nogen
linje ændrer sig. Præcis den fejlklasse hele projektet jager, men opstået ved
at aktivklassen skifter i stedet for ved at koden gør.

**Designkravet, ikke en detalje:** kan FX komme ind i den eksisterende
execution-arkitektur, skal reconcile afstemme mod **valutabalancerne**
(`CashBalance` / `FxCashBalance` / `NetLiquidationByCurrency` pr. valuta) —
og `get_positions_reliable()` skal vide at dens garanti er begrænset til
`secType` den faktisk dækker. Indtil da kan reglen *"reconcile-fejl fører til
SPÆRRET"* ikke opfyldes for FX, fordi reconcile ikke kan se en FX-fejl.

---

## P4 · Tick-gitter og pipværdi

Fuld profil i **`fx_instrument_profil.md`**. Hovedpunkter:

- **Halve pip overalt.** `ticks_pr_pip = 2,0` for alle 14 par.
- **Fast pipværdi kun for USD-kvoterede:** EURUSD, GBPUSD, AUDUSD, NZDUSD →
  $2,50 pr. 25.000, $10,00 pr. 100.000, uafhængigt af kursen.
- **Flydende for resten** — inkl. alle skandinaviske og alle JPY-krydser.
  EURDKK: $0,39 pr. 25.000. EURGBP: $3,40. USDCHF: $3,11.
- ⚠ **EURSEK og EURNOK står tomme.** USDSEK og USDNOK blev ikke målt, og
  pipværdien må ikke gættes.

---

## P5 · Datadybde og -kvalitet

### ⚠ Hovedfundet: `TRADES` giver nul barer på FX

| | `TRADES` | `MIDPOINT` |
|---|---|---|
| EURUSD, GBPUSD, USDJPY, EURDKK | **0 barer** (fejl 162) | 10 / 120 / 285 / 570 / 1425 |
| MES (kontrol) | 10 / 115 / 276 / 552 / 1380 | samme |

```
Error 162: No historical market data for EUR/CASH@FXSUBPIP
```

Ethvert harvest-script skrevet til futures henter **tomt** på FX. Det fejler
med en fejlkode her — men 32 steder i kodebasen har `whatToShow="TRADES"`
hardkodet, og flere af dem behandler en tom serie som "ingen data i perioden".

### Datadybden er stor — og verificeret

| | FX | MES |
|---|---|---|
| Headstamp `MIDPOINT` | **2005-03-09** (21 år) | 2025-06-22 |
| Headstamp `TRADES` | **—** | 2025-06-22 |

**Headstamp'en er efterprøvet, ikke bare aflæst** (den overlover normalt):
hentning der slutter 2005-04-01 gav 4 barer, 2004-04-01 gav 0. Påstanden
holder præcist. 1-minutters data findes mindst **24 måneder** tilbage.

⚠ Det er dybere end vores futures-data — MES's headstamp er 14 måneder, fordi
kontrakter udløber. **Der er rigeligt til at bygge en vol-reference på FX.**

### Volumen findes ikke

`volume = −1` i alle FX-barer. Det er bedre end 0: −1 kan skelnes fra "ingen
handel". Men 16 steder i koden regner på volumen og vil få −1 ind som tal.
MES under `TRADES` har ægte volumen (46 forskellige værdier over 46 barer) —
kontrollen bekræfter at −1 er en FX-egenskab, ikke en fejl i hentningen.

### Årsagstaksonomien mangler en femte kategori

Kontraktlevetid bortfalder (ingen expiries på spot). Retention,
harvest-parameter og vendor-onboarding dækker ikke dette tilfælde:
**`whatToShow`-uforenelighed** — data *findes*, parameteren er *gyldig*, og
kombinationen giver alligevel nul. Det er ikke en parameterfejl; det er en
uforenelighed med aktivklassen, og den bør have sin egen kategori.

---

## P6 · Handelstimer

| | Spot FX | MES |
|---|---|---|
| Tidszone | `US/Eastern` | `US/Central` |
| `tradingHours == liquidHours` | **JA** | **NEJ** |
| Dagsgrænse | 17:15 → 17:00 ET | 17:00 → 16:00 CT |
| Ophold | 15 min | 60 min |

**Bekræftet af bardata, ikke kun af strengen:** EURUSD giver 1425 1-min barer
på et døgn (1440 − 15), MES giver 1380 (1440 − 60).

**En FX-handelsdag** løber fra 17:15 ET til 17:00 ET dagen efter — hen over
midnat, uden RTH, med ét 15-minutters ophold. Ugen åbner søndag 21:15 UTC
(målt direkte: en 1-dags hentning der slutter mandag 00:00 UTC gav 165 barer =
21:15→24:00).

⚠ **Ikke kompatibel med projektets sessionstælling.** Den bygger på
NYSE-kalenderen (46 træf i koden) og på en RTH/ETH-opdeling (32 træf). FX har
ingen RTH — opdelingen findes stadig som kode, men begge halvdele bliver ens.
En sessionstæller der spørger `nyse_kalender.er_handelsdag()` svarer forkert
for FX hver eneste søndag.

⚠ **Sommertid:** US og EU skifter på forskellige datoer (1–2 ugers forskydning
i både marts og oktober). Kontrakten er ankret i `US/Eastern`, så de danske
klokkeslæt forskyder sig i det vindue. Reel fejlkilde for enhver scheduler med
faste danske tider.

---

## P7 · Omkostninger — ⚠ IKKE MÅLT

Spread-sampling kræver åbent marked og skal køres i **fem separate vinduer**.
Implementeret som `--kun P7 --vindue <navn> --minutter N`:

| Vindue | Dansk tid |
|---|---|
| `asien` | 01:00–09:00 |
| `london_aabn` | 09:00–10:00 |
| `london_ny` | 15:30–17:30 |
| `ny_eftm` | 18:00–21:00 |
| **`rollover`** | **22:30–23:30** ← vigtigste |

Kommission kommer fra P2's whatIf og mangler derfor også.
Finansiering over natten kan ikke læses via API'et — skal slås op manuelt.

**Uden en omkostningslinje i pip pr. rundtur kan ingen FX-strategi vurderes.**
Det er ikke en formalitet: på EURUSD med 25.000 enheder er 1 pip $2,50, så
forskellen mellem 0,5 og 2,0 pip i spread er forskellen mellem en levedygtig
og en umulig strategi.

---

## P8 · Fejlklassejagt i kodebasen

**Fund, ikke rettelser.** Fuld liste med fil og linje i
`fx_probe_output/P8_kodebase.json`.

| Klasse | Træf | Tungeste sted |
|---|---|---|
| NYSE-kalender som *den* kalender | 46 | `nyse_kalender.py`, `sessions_revision.py:67`, `eco_kalender.py:61` |
| `whatToShow="TRADES"` hardkodet | 32 | `data_source.py:157`, `market_conditions.py:184`, `catalyst_harvest.py:199` |
| RTH/ETH som meningsfuld | 32 | `trade_chart.py:74`, `download_intraday_ibkr.py:12` |
| Volumen antaget > 0 | 16 | `eureversion_backtest.py:376`, `algo_trendjoin.py:971`, `market_conditions.py:233` |
| Tick-afrunding med futures-gitter | 8 | `futures_katalog.py:73`, `asian_data_probe.py:12` |
| `USD_PER_POINT`-konstanter | 4 | `strategies/europa_reversion/config.py:55`, `strategies/us_reversion/config.py:101` |
| **`positions()` som sandhed** | **3** | **`ibkr_connect.py:544`, `:554`, `:1113`** |
| `secType` antaget FUT/STK | 1 | `test_konto_binding.py:44` |

⚠ **De tre `positions()`-træf vejer tungest trods det laveste antal.** Se §P3.
Et træf er ikke i sig selv en fejl — det er et sted hvor en FX-antagelse
brister.

---

## Hører FX hjemme i denne stak?

Det spørgsmål er ikke afgjort, og proben kan endnu ikke afgøre det. Men den
har flyttet regnestykket, og i begge retninger:

**For:** 21 års historik mod futures' 14 måneder. Ingen expiries, ingen roll,
ingen kontinuitetssyning. Ingen ny adapter. Døgnåbent marked som ikke slås
ihjel af en NYSE-helligdag.

**Imod — og det er tungere:** FX rører fire af de fem søjler stakken hviler
på. Ingen volumen (16 steder regner på den). Ingen RTH (32 steder deler efter
den). En anden kalender (46 steder bruger NYSE's). Og `positions()` — som er
det reconcile og execution afstemmer imod — ser ikke FX overhovedet.

⚠ **Den ubehagelige mulighed står åben.** Regimemotoren, volatilitetsarbejdet
og screenerne er ikke skrevet med et volumenløst, RTH-løst, positionsløst
instrument for øje. At tilføje EURUSD er at åbne et rør ved siden af det
eksisterende, ikke at tilføje et symbol. Det kan meget vel koste mere i
særkode end det bidrager.

**Men beslutningen kan ikke træffes endnu**, for de tre tal der afgør den —
gearing, spread og kommission — er præcis dem der mangler. Et velbegrundet nej
kræver også en måling.

---

## Hvad der mangler, og hvornår det kan gøres

FX åbner **søndag 23:15 dansk tid**. Derefter:

| # | Punkt | Kommando | Bemærkning |
|---|---|---|---|
| 1 | **P2 gearing** | `--kun P2` | Bekræft først at apparat-kontrollen siger OK. Genprøv fejl 201. |
| 2 | **P3 position** | `--kun P3 --tillad-ordre` | Ægte paper-ordre. Afgør reconcile-spørgsmålet — og `minSize`-uenigheden. |
| 3 | **P7 spread** | `--kun P7 --vindue rollover --minutter 60` | Fem kørsler, ét vindue ad gangen. Rollover først. |
| 4 | P0 klassifikation | Client Portal, manuelt | Kan ikke læses via API. |

⚠ **P3 skal køres med varsomhed.** Den lægger en ægte ordre på 25.000 EUR ≈
$29.000 notional på en konto med $9.673. Hvis kontoen faktisk har gearing, går
den igennem; hvis ikke, afvises den — og *det* er også et svar. Den lukker i
samme kørsel og verificerer flad bagefter, men jeg vil gerne have din
accept før jeg kører den.

⚠ **Kør ikke på Ibens maskine mens hun handler.** Kun ét login som
`fasteriben2` ad gangen. Denne probe kørte mod Sørens egen paper-konto
(DUN748991) og rørte intet af hendes.

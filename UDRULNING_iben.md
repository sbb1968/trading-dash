# Udrulning til Ibens workstation — og videre til live

To planer. Den første skal køres i dag. Den anden ligger uger ude, men står her
nu, fordi rækkefølgen betyder noget og ét af trinene har en fælde der skal
besluttes **før** den udløses.

---

## ⚠ Svaret på "skal jeg rette account.yaml manuelt?"

**Nej — ikke på Ibens maskine, og det er et bevidst design.**

`account.yaml` er gitignoreret og maskinlokal, så den kommer rigtigt nok ikke med
`git pull`. Men hendes maskine skal **ingen ændring** have:

| account.yaml | Watchlist Futures går til | Hvorfor |
|---|---|---|
| **uden** `nt_forbindelse` | **IBKR** (DUQ441063) | som i dag — intet ændrer sig for Iben |
| **med** `nt_forbindelse` | NT8 | maskinen har en NinjaTrader-vej |

Armeringen **er** ruten. En maskine uden NinjaTrader kan ikke rute til
NinjaTrader, så de to kan ikke komme i utakt.

⚠ Det var ikke sådan for to timer siden. Reglen stod hardkodet som
*"futures → NT8, altid"*, og med den ville Ibens første klik på **KØB MES** have
svaret *"NinjaTrader-ordrevejen er spærret"*. Hun handler MES på IBKR gennem
netop den knap. Rettet i `0749e23`.

---

# Plan 1 — udrulning i dag

## Før du rører hendes maskine

**1. Test exe'en på din egen først.** Din maskine er armet mod Sim101 og har NT8
kørende, så den afdækker mere end hendes gør.

```
cd C:\Projects\trading_dash\backend
venv\Scripts\activate
uvicorn main:app --host 0.0.0.0
```

Banneret skal sige:

```
[Server] NT8:       Sim101 (simulation) — konfiguration, ikke kontrolleret
[Server] Rute:      Watchlist Futures -> NT8 · Watchlist Stocks -> IBKR
```

Start så exe'en og kontrollér:
- Toplinjen viser **DK**…  og **US**… i kraftig gul
- Ordrer-vinduet står på **Aktuel dag (fra midnat)**
- Fanerne **Ordrer** / **Handler** — den aktive er neongul, den inaktive grå
- **Handler**-fanen viser gårsdagens fem Sim101-handler med P&L og −$2,50 i alt
- Et klik på KØB giver en **gul, pulserende** kvittering og spærrer begge knapper

**2. Tag en kopi af hendes nuværende exe.** `git pull` henter den ikke, så den
version du overskriver, er den eneste du har at falde tilbage på.

```
copy "C:\Projects\trading_dash\app.exe" "C:\Projects\trading_dash\app.exe.foer-28-09"
```

## På Ibens workstation

⚠ **Luk ikke hendes TWS.** Du har ikke `fasteriben2`-adgangskoden, og der kan
kun være én session ad gangen.

**3.** `git pull` i `C:\Projects\trading_dash`

**4.** Kopiér den friske `app.exe` ind (fra
`src-tauri\target\release\app.exe` på din maskine).

**5.** Genstart backenden. Kontrollér at der ikke kører strategier først — på en
workstation er auto-start slået fra, men se efter i banneret.

**6. Verificér banneret.** Det skal sige:

```
[Server] IBKR:      DUQ441063 (paper)
[Server] NT8:       ikke armeret (ingen nt_forbindelse i account.yaml) — futures handles paa IBKR
[Server] Rute:      Watchlist Futures -> IBKR · Watchlist Stocks -> IBKR
```

⚠ Står der `Futures -> NT8` på hendes maskine, så **stop** — så er der kommet en
`nt_forbindelse`-blok i hendes `account.yaml`, og hendes MES-handel vil fejle.

**7. Det afgørende tjek** — spørg ruten direkte:

```
curl http://127.0.0.1:8000/ordre/rute
```

Svaret **skal** være:

```json
{"stocks":"IBKR","futures":"IBKR","nt_armeret":false,"nt_konto":""}
```

⚠ Står der `"futures":"NT8"`, så **stop**. Så er der kommet en
`nt_forbindelse`-blok i hendes `account.yaml`, og hendes MES-handel vil fejle.

**8. En rigtig prøvehandel**, mens du stadig sidder der: køb 1 MES i Watchlist
Futures, se den gule kvittering, og se at den fylder på **DUQ441063**. Sælg den
igen. Kig i **Handler**-fanen — der skal stå én linje med begge ben og P&L.

Det koster ~$1 i kurtage og er de penge værd: det er forskellen på at vide at det
virker og at håbe det.

## Hvis noget går galt

Læg den gamle exe tilbage og genstart backenden på forrige commit:

```
git log --oneline -5
git checkout <commit-før-i-dag>
```

Hendes handel afhænger kun af IBKR-stien, som ikke er rørt i denne omgang.

## Hvad Iben vil opleve som nyt

- **Ordrer-vinduet viser kun i dag.** Det er rettelsen på hendes egen melding —
  valget hed *"I dag (24 timer)"* men var et rullende døgn og viste i går.
- **En ny fane, Handler** — én linje pr. transaktion med P&L.
- **To ure** i toplinjen: `DK` og `US`. US-tiden i gul. Markedet åbner når der
  står **09.30** i den gule.
- **Kvittering ved klik.** Knappen siger `SENDER…`, begge knapper spærres, og en
  gul bjælke navngiver ordren indtil svaret kommer. ⚠ Sig til hende at den gule
  bjælke betyder *"jeg har hørt dig"* — ikke at handlen er gennemført.

---

# Plan 2 — paper → live på NinjaTrader

Rækkefølgen er ikke vilkårlig. Hvert trin gør ét nyt forhold virkeligt, så en
fejl kan henføres til det trin der lige blev taget.

## ⚠ Trin 0 — beslut V4, før kontoen finansieres

Som koden er nu, spærrer `klar()` **al** NT8-handel — også Sim101 — i samme
øjeblik en ukendt konto dukker op i ATI-strømmen. Live-kontoen **2080414** vil
dukke op dér den dag den finansieres.

Konsekvens hvis det ikke besluttes først: NT8-handel stopper uden varsel, midt i
en session, med en fejl Iben ikke kan gøre noget ved.

Beskyttelsen er reelt overflødig, fordi V1 skriver kontoen eksplicit i hver
ordre — en live-konto der blot *findes* i platformen kan ikke modtage en ordre
stilet til Sim101.

**Forslag:** gør V4 til en højlydt advarsel (journalhændelse + rød linje i
watchlisten) i stedet for en spærring. Ikke besluttet endnu.

## Trin 1 — DEMO8580770 på DIN maskine, før noget går til Iben

Foreslået af Søren 28-09, og det er den rigtige rækkefølge: virker det hos dig
mod rigtig infrastruktur, ved vi at det også kan virke hos hende.

**Hvad det kræver:**
- En mæglerforbindelse i din NT8. ⚠ Der er **ingen** i dag — `Config.xml` har kun
  `Simulated Data Feed`, `Playback` og `Kinetick End Of Day (Free)`.
- **Markedsdata.** Bevist nødvendigt: ordren 16-09 blev afvist med
  *"Real-time market data required to trade this contract."*

⚠ **Og markedsdata købes ikke — kontoen finansieres.** Aflæst i portalen 28-09
(`td.ninjatrader.com/settings/plans`):

```
Account plan:  Live Trading | Free        Commissions: $0.39/micro
Features (included when you fund your account)
   CME Bundle (Level 1)     ← nedtonet, altså ikke aktiv
   EUREX Bundle (Level 1)   ← nedtonet
   TradingView · Order Flow+ · Market Replay
```

Der er **ingen** aktiv dataabonnement på kontoen, og der er heller ingen separat
$4-post at købe på denne plan. CME Level 1 følger med i det øjeblik kontoen
finansieres. Den tidligere antagelse om "$4/md" stammede fra EU-prislisten og
gælder ikke denne plan.

⚠ **Det flytter finansieringen frem foran DEMO-testen** — og dermed også
V4-beslutningen i trin 0, for live-kontoen dukker op i ATI-strømmen samme dag
pengene går ind.

**Afklaret med supporten 28-09:** finansiering af 2080414 giver også data på
DEMO8580770 — *"the complimentary Level I CME Group and EUREX market data
benefits apply to both the live and Demo environments under the same username…
A separate Demo payment or add-on is not required."*

Hele DEMO-testen kan altså køres uden at handle på live-kontoen. Pengene skal
blot **stå** der.

⚠ Svaret var fra supportchatten og sagde *"should also apply"* — en slutning,
ikke en bekræftet kendsgerning. Efterprøv det i NT8 når kontoen er finansieret:
viser MES DEC26 levende kurser under DEMO8580770, holder det.

**Hvor meget skal der ind?** Supporten 28-09: *"There is no stated minimum
deposit amount. Your NinjaTrader Brokerage account only needs to be funded,
meaning the balance must be above $0."*

⚠ **MEN DET ER EN LØBENDE BETINGELSE, IKKE EN ENGANGS.** Samme svar:

> *If the balance is not positive on the **first of the month**, access to the
> included data may be removed until you add funds.*

Saldoen tjekkes altså den 1. hver måned. Falder kontoen til nul — af gebyrer,
af et tab, eller fordi pengene er hentet hjem — **forsvinder markedsdataen ved
næste månedsskifte.** Og symptomet er ikke "du mangler data": det er en
NT8-ordre der afvises med *"Real-time market data required to trade this
contract"*, midt i en handelsdag, som om der var en fejl i koden.

Derfor: læg et beløb ind der ikke kan nå nul ved et uheld, frem for det
mindstebeløb der teknisk rækker. Og tjek saldoen omkring den 1.

**Hvad det køber, som Sim101 aldrig kan give os:**

| | |
|---|---|
| Rigtige priser | fyldningerne indtil nu var opdigtede |
| Mæglerkæden | NT8 → Tradovate → børs, hele vejen |
| Slippage | en markedsordre mod en rigtig ordrebog |
| Afvisninger fra et rigtigt marked | margin, session, kontrakt |
| **⚠ Instrumentnøglen** | se nedenfor — den er vores mest skrøbelige antagelse |

⚠ **Instrumentnøglen er den vigtigste grund.** Positionsvagten slår op på præcis
`MES DEC26`, og vi har kun set den navngivning under den simulerede feed. Målt
28-09 kl. 10:17, mens kontoen var **flad**:

```
@MES         1  @ 7772.5   ⚠ forældet
MES 12-26    0             ✅
MES DEC26    0             ✅  ← den vi læser
MES Z6       1  @ 7772.5   ⚠ forældet
MESZ26       1  @ 7772.5   ⚠ forældet
```

Tre af fem aliasser påstår en åben position, med en plausibel pris. Skifter
navngivningen på en mæglerforbindelse, læser vagten et forkert tal der ser
rigtigt ud. **Det skal efterprøves her, ikke hos Iben.**

Kør `python nt_klik_test.py` med `konto: DEMO8580770` i `account.yaml`, og
kontrollér bagefter at `MES DEC26` stadig er den nøgle der holder sandheden.

## Trin 2 — NT8 på Ibens maskine (gratis)

⚠ **Her skal der ingen penge og intet dataabonnement bruges.** Det stod forkert
i første udgave af denne plan.

Det vi beviste 27.–28. september på Sørens maskine, kørte mod NT8's **Simulated
Data Feed** — en lokal, syntetisk kurskilde der følger med platformen. Der var
ingen Tradovate-forbindelse overhovedet opsat; de konfigurerede forbindelser var
`Simulated Data Feed`, `Playback Connection` og `Kinetick End Of Day (Free)`.

Det forklarer begge observationer: Sim101 fyldte i handelstiden og afviste uden
for den (*"There is no market data available to drive the simulation engine"*),
fordi den simulerede feed respekterer instrumentets sessionstider.

- Installér NinjaTrader 8
- Forbind med **Simulated Data Feed** (`Connections`-menuen)
- `Tools → Options → Automated trading interface` → slå ATI til
- ⚠ `Tools → Options → Trading` → **fjern** *"Confirm order placement"*. Med den
  slået til venter hver OIF-ordre på et menneskeklik i NT8; loggen skriver
  `processing`, men der oprettes intet. Det kostede os en time 27-09.

⚠ **Og hvad det så IKKE beviser.** Fyldningen på 7772,50, gevinsten på $3,75 og
positionen på −4 var alle regnet på **opdigtede priser**. Mekanikken er bevist
— OIF-skrivning, ATI-aflæsning, parring, forensik, multiplikator — men intet om
rigtige markedsforhold, slippage eller at mæglerkæden virker. Det kommer først
i trin 3.

## Trin 3 — Sim101 på hendes maskine

```yaml
  nt_forbindelse:
    konto: Sim101
```

⚠ Nu flytter hendes Watchlist Futures til NT8. **Hendes IBKR-MES-handel på
DUQ441063 stopper samme øjeblik.** Det er det egentlige skift, og det skal
besluttes bevidst — ikke opdages.

Kør hele vejen igennem: køb → kvittering → fyldning → `Handler`-fanen viser
linjen med P&L. Først når det virker på **hendes** maskine, går vi videre.

## Trin 4 — DEMO på Ibens egen konto

Samme øvelse som trin 1, men på hendes konto og hendes maskine.

- **CME Level 1, $4/md.** Nu er den nødvendig: uden den afviser Tradovate ordren
  med *"Real-time market data required to trade this contract"* — det er den
  præcise fejl vi fik 16-09.
- **Finansiér 2080414.** Dashboard → `TRANSFER FUNDS` → `WIRE`. SEPA/EUR, intet
  gebyr, intet minimum. ⚠ Hent bankoplysningerne fra dit eget dashboard, ikke fra
  et forum — wires bærer en klientspecifik reference. (Kan gøres samtidig; demo
  og live deler dataabonnement.)
- Cypriotisk investorgaranti dækker €20.000. Et argument for ikke at parkere mere
  end nødvendigt.

```yaml
    konto: DEMO8580770
```

Samme kode, rigtig Tradovate-infrastruktur, rigtige priser, legetøjspenge. Lad
det køre nogle dage — det er her vi første gang ser systemet møde et marked.

**Iben har sin egen NinjaTrader-konto** — samme model som IBKR, hver sin. Der
er derfor ingen delt-login-begrænsning at tage hensyn til, sådan som
`fasteriben2` har kostet os tid hos IBKR.

⚠ **Men markedsdata følger kontoen.** Hendes konto skal finansieres for at få
CME Level 1 — det kan ikke deles fra Sørens. To konti, to indskud.

## Trin 5 — live

```yaml
  nt_forbindelse:
    konto: 2080414
    tillad_live: true
```

⚠ **To ændringer, ikke én.** Kontonummeret alene spærres af V2, fordi 2080414
ikke er en kendt simulationskonto. IBKR får den andenlås gratis af portnummeret
(4002 paper / 4001 live); ATI har ingen, så den er bygget i kode.

Banneret vil derefter råbe ved hver opstart:

```
[Server] NT8:       2080414 (⚠ IKKE en kendt simulationskonto)
[Server] ⚠⚠ NT8 LIVE-HANDEL ER TILLADT (tillad_live: true)
```

Start med **1 kontrakt** og et beløb der ikke betyder noget.

---

## Efterskrift: én ting der ikke er besluttet

**V4**, som beskrevet i trin 0. Den skal afgøres før live-kontoen finansieres,
ikke efter.

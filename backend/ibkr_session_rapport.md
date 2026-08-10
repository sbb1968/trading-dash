# IBKR's sessions- og markedsdatakonflikt — kortlægning

**Dato:** 2026-08-10 · **Fase A gennemført · Fase B og C IKKE kørt**

---

## 0. Hvorfor B og C ikke er kørt

Sikkerhedsregel 2 blev tjekket først, og den blokerer:

```
Algoserveren (DUO509856), aflæst kl. ~16:00 DK:
  12 åbne positioner hos IBKR
   8 åbne rækker i journalen — nyeste entry 2026-08-10T09:57 ET
  NLV $9.648,05
```

Markedet er åbent, og strategierne handler netop nu. Enhver forstyrrende test
ville ramme åbne positioner. **Fase B og C udsættes** — se afsnit 6 for hvornår.

⚠ Sidebemærkning der ikke hører til denne spec, men som er målt undervejs: 12
IBKR-positioner mod 8 journalrækker. VELO, WOLF, TE, ALOY, SHAZ og XE har ingen
journalrække, mens NUAI har en række uden IBKR-position. Det er det kendte
ejerløs-problem, ikke et sessionsproblem.

---

## 1. Målt kontra formodet

Specens §1 er hele grunden til at denne rapport findes. Listerne holdes adskilt.

### Målt

| # | Fakta | Kilde |
|---|---|---|
| M1 | Algoserveren kører `account_id=iben`, `instance_role=algoserver`, `ibkr_account=DUO509856`, `paper_trading=True` | `/account` over Tailscale |
| M2 | Sørens workstation kører `soren`/`workstation`/`DUN748991`/`paper=True` | `/account` lokalt |
| M3 | **Algoserverens Gateway logger ind med `IbLoginId` = Ibens IBKR-LOGINBRUGERNAVN, ikke kontonummeret**, med `TradingMode=paper` | `ALGOSERVER_IBC_udrulning.md` §6 + faldgrube-listen |
| M4 | Udrulningen konkluderede allerede **"Ét login pr. konto ad gangen"** — derfor blev TWS-genvejen fjernet fra Startup på algoserveren | samme dokument |
| M5 | TWS på Sørens workstation afviste paper-konto 2 med: *"the real account username associated with this paper-trading username is also running Trader Workstation"* | skærmbillede |
| M6 | IBKR's Paper Trading-side for DUQ441063: bruger `fasteriben2`, deling **Yes**, datakilde-dropdown `fasteriben`, med noten *"only one of these accounts can have an active session at any given time"* | skærmbillede |
| M7 | Backenden vælger `clientId = random.randint(10, 99)` ved **hver** forbindelse | `ibkr_connect.py:180` |
| M8 | Faste client-id'er i samme interval i kodebasen: 10, 11, 12, 15, 16, 28, 47, 48, 51 (+50, 55 i `_archive`) | kildekode |
| M9 | Nyhedsfeeden kørte mens kurserne stod stille og kvotefelterne viste `?` | Sørens observation |

### Formodet — endnu ikke bekræftet

| # | Formodning | Falder eller bekræftes ved |
|---|---|---|
| F1 | At `fasteriben` er LIVE-brugernavnet og `fasteriben2` dets paper-modstykke | Fase A3: aflæs begge kontoers Paper Trading-side |
| F2 | At algoserverens `IbLoginId` er præcis det brugernavn `fasteriben2` er knyttet til | Fase A1: åbn algoserverens `config.ini` og læs `IbLoginId` |
| F3 | At konflikten skyldes **brugernavns-parret**, ikke markedsdata-delingen | Fase C, og evt. et spørgsmål til IBKR |
| F4 | At historiske barer (B4) overlever mens streaming (B3) falder | Fase B |
| F5 | At en client-id-kollision **ikke** er årsagen | Fase A5 — se ⚠ nedenfor |

---

## 2. ⚠ To forskellige begrænsninger er blevet blandet sammen

Det er rapportens vigtigste præcisering, og den ændrer hvad der skal spørges om.

**M5 handler om BRUGERNAVNE.** Beskeden nævner ikke markedsdata med ét ord. Den
siger at det *rigtige* brugernavn knyttet til dette paper-brugernavn allerede
kører TWS. Det er IBKR's regel om at en paper-bruger og dens tilhørende
live-bruger ikke kan være logget ind samtidig.

**M6 handler om MARKEDSDATA.** Noten står med stjerne til spørgsmålet *"Share
real-time market data subscriptions with paper trading account?"* og siger at når
data deles, kan kun én af kontiene have en aktiv session.

Vi har hidtil behandlet de to som samme sag. De kan være det — men de kan lige så
godt være to uafhængige begrænsninger, hvor kun den ene kan slås fra. **Slår man
markedsdata-delingen fra og konflikten består, er det M5 der binder**, og så
hjælper hverken et ekstra abonnement eller forsinkede data.

Det er præcis den slags forskel der afgør hvilken driftsmodel der overhovedet kan
virke, og det er derfor spørgsmålet til IBKR skal stilles skarpt (afsnit 5).

---

## 3. ⚠ Client-id-kollisionen skal udelukkes — og den er reel

Specen kræver at denne udelukkes før noget konkluderes. Den kan **ikke**
udelukkes på det nuværende grundlag, og fundet står uanset sessionsspørgsmålet:

Backenden trækker `clientId` tilfældigt i **10–99** ved hver forbindelse (M7).
Kodebasen bruger faste id'er i netop det interval (M8). Kører en høst samtidig
med at backenden genforbinder, er der ca. **10 % risiko** for at backenden rammer
høstens id — og resultatet er en konflikt der ligner denne, men har en helt anden
årsag.

Konkret i dag: RTY-høsten kørte på id **48** i flere timer, og halt-høsten kører
på **51**. Begge ligger i backendens trækningsinterval.

Kollisionen kan kun ske **inden for samme maskine** (samme TWS), så den forklarer
ikke M5, der går på tværs af to maskiner. Men den skal væk før fase B og C, ellers
kan et svigt dér ikke tilskrives entydigt.

**Rettelsen er lille:** giv backenden et fast, reserveret id uden for det interval
scripts bruger — eller lad den trække fra et interval ingen faste id'er ligger i.
Ikke gennemført her, da §7 forbyder konfigurationsændringer i undersøgelsesfasen.

---

## 4. Foreløbigt svar på hovedspørgsmålet

> **Kan Iben afbryde algoserveren midt i en åben position?**

**Den ene observation vi har, peger på nej — men den er svag, og der findes en
farligere variant af spørgsmålet.**

I M5 var det **den nytilkomne** (Sørens TWS på paper-konto 2) der blev afvist.
Algoserveren kørte videre. Det er den gunstige retning: inkumbenten beholder
sessionen.

Men det er ét tilfælde, det var TWS mod TWS og ikke API mod API, og vi har ikke
prøvet den omvendte rækkefølge. Fase C's to sidste rækker findes netop for det.

### ⚠ Den farligere variant, som ingen har stillet endnu

Algoserveren laver en **daglig genstart med friskt login** (`ALGOSERVER_IBC_udrulning.md`
§1). Risikoen er derfor ikke kun "bliver den smidt af midt i en position" — det er
også:

> **Hvad sker der ved algoserverens daglige auto-login, hvis Iben har TWS åben
> på det tidspunkt?**

Holder hendes session brugernavnet, kan algoserverens auto-login **fejle**, og så
starter den slet ikke. Det ville ikke ligne en sessionskonflikt; det ville ligne
at algoserveren var nede. Det spørgsmål skal med i fase C.

---

## 5. Hvad der ikke kunne afgøres — og hvad der skal spørges IBKR om

### Kræver at et menneske aflæser noget jeg ikke kan nå

| # | Hvad | Hvor |
|---|---|---|
| A1 | Algoserverens præcise `IbLoginId` — **kun brugernavnet, ikke passwordet** | Algoserveren: stien i `set CONFIG=` i `C:\IBC\StartGateway.bat` |
| A2 | Brugernavnet Sørens TWS var logget på med da fejlen kom | Sørens workstation |
| A3 | Paper Trading-siden for **DUO509856** — brugernavn, deling til/fra, og hvilket navn der står i dropdownen | IBKR Client Portal |
| A4 | Hvilke markedsdata-abonnementer der findes, og på hvilket brugernavn | IBKR Client Portal → Market Data Subscriptions |

A3 er den afgørende. **Står der `fasteriben` i dropdownen for begge konti, kan de
pr. konstruktion aldrig køre samtidig**, og fase C bliver en formalitet.

### Spørgsmål til IBKR

De skal stilles adskilt, for de kan have forskellige svar:

1. *Can two paper usernames belonging to the same real account username have
   simultaneous TWS/Gateway sessions?* (Dette er M5.)
2. *If market data sharing is disabled for a paper username, can that username
   then run a session simultaneously with the real username?* (Dette er M6 — og
   svaret afgør om et ekstra abonnement overhovedet hjælper.)
3. *Does the restriction apply to API-only Gateway sessions, or only to full TWS?*

Spørgsmål 2 er det økonomisk afgørende: er svaret nej, er "abonnement nummer to"
ikke en løsning, og driftsmodel 2 falder bort før den koster noget.

---

## 6. Hvornår fase B og C kan køres

Fase C kræver to ting der trækker i hver sin retning: **ingen åbne positioner**
(sikkerhedsregel 2) og **et åbent marked** (ellers kan B3 streaming ikke måles
meningsfuldt).

Forslag: **første dag algoserveren står flad, i vinduet 14:05–15:10 DK** — efter
førmarkedet er begyndt, men før strategiernes auto-start kl. 15:20. Da er der
kvoter at streame, og intet at miste.

Alternativt en weekend, hvor B1, B2, B4, B5, B7 og hele kollisionsmatricen kan
måles; kun B3 og B6 bliver da uafklarede.

**Før fase C køres:** ret client-id-kollisionen (afsnit 3), ellers kan et svigt
ikke tilskrives entydigt.

---

## 7. Driftsmodellerne — endnu ikke vurderet

Specen kræver at ingen anbefaling gives før A–C foreligger, og C mangler. Men to
af dem kan allerede indsnævres af det målte:

- **Model 3 (algoserver uden streaming)** afhænger helt af F4, som fase B afgør.
  M9 — at nyheder kørte mens kurser stod stille — er et svagt tegn på at
  kapabiliteterne fejler uafhængigt, hvilket ville tale for modellen.
- **Model 2 (to abonnementer)** afhænger af IBKR-spørgsmål 2. Binder M5 frem for
  M6, løser et ekstra abonnement ingenting.

Beslutningsreglen fra §5 står ved magt og er værd at gentage, fordi den udelukker
den nemmeste udvej: **en model der kun virker hvis man logger på i den rigtige
rækkefølge, er ikke en løsning.** Algoserveren genstarter automatisk hver dag; en
rækkefølgeafhængig opsætning ville før eller siden ramme netop det tidspunkt.

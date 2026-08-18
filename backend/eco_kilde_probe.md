# Eco-kilde-probe

**Målt 18-08-2026.** Specens §3.2 og §10.2.

Proben skal svare på fem ting pr. kandidat: rækkevidde frem, rækkevidde tilbage,
klokkeslæt eller kun dato, forecast/previous/actual, samt nøgle, ratebegrænsning
og vilkår.

⚠ **Kontrolfikstur i begge retninger, som ved ES-proben (T5).** En probe der kun
prøver det den forventer at finde, måler ingenting. Den kendt-negative står
nederst i hvert afsnit.

---

## 1. ForexFactory — JSON-feed `ff_calendar_thisweek.json`

| | |
|---|---|
| Rækkevidde frem | **Indeværende uge, intet mere** |
| Rækkevidde tilbage | **Ingen** |
| Klokkeslæt | Ja, ISO med ET-offset i `date` |
| Forecast / previous | Ja, begge |
| **Actual** | **Nej — nøglen findes ikke** |
| Nøgle | Ingen |
| Ratebegrænsning | ⚠ **Ja.** Fire kald på et par minutter → HTTP 429 |
| Felter | `title, country, date, impact, forecast, previous` |

`nextweek`, `lastweek` og `thismonth` giver alle **404**. Kun `thisweek` svarer.

**Kendt-positiv:** hentet 18-08, 96 rækker for ugen 17.–23. august, herunder
`FOMC Meeting Minutes` 19-08 kl. 20:00 dansk — et event vi vidste fandtes.

**Kendt-negativ:** de tre andre feed-navne (`nextweek`, `lastweek`, `thismonth`)
svarer 404 og bliver derfor til en `KildeFejl`, ikke til en tom liste. Havde de
svaret 200 med `[]`, ville en naiv kilde have meldt "ingen events" for en hel
uge. Se `test_kilde_sundhed`.

⚠ **Ratebegrænseren er grunden til at høsten er ét kald om dagen.** Kalenderen
skal ikke være nede præcis den morgen den betyder noget.

---

## 2. ForexFactory — uge-HTML `calendar?week=aug9.2026`

| | |
|---|---|
| Rækkevidde frem | **Vilkårlig uge** |
| Rækkevidde tilbage | **Vilkårlig uge** |
| Klokkeslæt | Ja, `dateline` (UNIX-sekunder) |
| Forecast / previous | Ja |
| **Actual** | **Ja** — det feedet ikke har |
| Nøgle | Ingen |
| Ratebegrænsning | Ikke ramt, men 391 KB pr. kald |
| Ekstra | `ebaseId` — stabilt id pr. begivenhedstype |

Kalenderen ligger indlejret som et JS-objekt (`calendarComponentStates[1]`).
`days`-arrayet er gyldig JSON og skæres ud med **klammematchning, ikke regex** —
det omgivende objekt har unoterede nøgler, og et regex-fix af dem brækker strenge
der selv indeholder `:` (URL'er, klokkeslæt).

**Kendt-positiv:** `aug17.2026` gav 96 rækker med Faktisk-værdier.

**Kendt-negativ, to stykker:**
- HTML uden `calendarComponentStates` → `KildeFejl` med besked om at layoutet er
  ændret. ⚠ **Det er den vigtigste kontrol på denne kilde.** HTML-skrabning
  knækker stille hvor et feed knækker højlydt; en parser der returnerer nul
  rækker fordi et klassenavn skiftede, må aldrig ligne en rolig uge.
- `days: []` → `KildeFejl`, ikke tom liste.

**Rev. A1 har ret:** rækkevidde er ikke længere et argument for en anden
leverandør. FF kan selv dække ±45 dage. En anden kilde er nu et **dæknings**-
spørgsmål.

---

## 3. Den ene målte dækningsforskel

Søren sammenlignede FF, Trading Economics og Investing.com på 12. og 14. august.
**I øvevinduet 08:00–15:00 dansk er der præcis én forskel:**

`Retail Sales Control Group` (14-08, 14:30) findes hos TE og Investing, **ikke
hos FF**. Efterprøvet mod den cachede uge 18-08: nul rækker med "ontrol" i navnet
i hele ugen 9.–15. august.

Kontrolgruppen går ind i BNP-beregningen og handles undertiden frem for
overskriften. **Men vurderet mod kalenderens faktiske opgave — at sige *hvornår*
— missede FF ingenting de to dage:** 14:30-slottet var flagget, retail sales var
fanget, UoM var fanget. Kontrolgruppen ville have tilføjet præcision til en
release der allerede var advaret om.

Øvrige forskelle ligger uden for vinduet (Michigan-underkomponenter 16:00,
Baker Hughes 19:00, CFTC 21:30), er delkomponenter der bevæger sig med en
overskrift FF allerede viser, eller er støj (MBA Mortgage, rig counts).

---

## 4. Sammenfletningen — rev. B4's to tal, målt

Der er **ikke** to leverandører endnu, så det tal må ikke opfindes. Men de to
*indgange* til FF dækker den samme uge, og de er den samme prøve af
fletningsmaskineriet: er de uenige om `(dato, titel, tid)`, er mindst én vej
forkert konfigureret — sandsynligvis tidszone.

Målt 18-08 på ugen 17.–23. august:

| | |
|---|---|
| feed | 96 rækker |
| uge-HTML | 96 rækker |
| efter fletning | **96** |
| **dubletter flettet** | **96** |
| **klokkeslæts-uenigheder** | **0** |
| kun i feed | 0 |
| kun i uge-HTML | 0 |

De to indgange er enige om alle 96 events, ned til sekundet. `TITEL_SYNONYMER`s
arbejdsliste er derfor **tom for dette par** — hvilket den skal være, det er
samme udbyder. Den rigtige arbejdsliste kommer først med en anden leverandør, og
tallene skal måles igen dér.

⚠ **En dublet fletningen fandt, som ikke var vores:** `ADP Weekly Employment
Change` står **to gange** 11-08, kl. 08:14 og 08:15. Forskellige `dateline`, samme
titel, samme dag. Det er kildens egen dublet, og fletningen fanger den — cachen
gik fra 76 rå rækker til 75 gemte. Uden fletning ville den have stået to gange på
skærmen.

---

## 5. Ikke probet

`tradingeconomics.com` og `investing.com` er **ikke** probet mod deres API'er.
Sammenligningen ovenfor er Sørens aflæsning af deres websider, ikke en måling af
hvad de udleverer programmatisk. Begge kræver nøgle for seriøs brug, og
vilkårene for automatiseret hentning er ikke læst.

Det er en bevidst udeladelse, ikke en forglemmelse: §3.4 siger at høsten skal i
drift **først**, fordi hver dag uden høst er permanent tabt. En leverandør mere
kan tilføjes bagefter — den er én linje i `KILDER` plus én klasse — og
`droppede_titler.csv` viser da med det samme hvad den bidrager med.

---

## 6. Hvad proben ændrede i designet

| | |
|---|---|
| §3.2's antagelse om at rækkevidde kræver en anden kilde | **Faldt.** FF's HTML dækker ±45 dage selv |
| §9's udskydelse af overraskelses-målet | **Delvist faldt.** `actual` findes, så `actual − forecast` er beregnebar. Felterne gemmes fra dag ét (rev. A3); visningen venter |
| Kun-dato-events | FF markerer dem `All Day`/`Tentative` i HTML og lægger dem 12:00am ET i feedet. Begge veje giver `har_klokkeslet = 0` |

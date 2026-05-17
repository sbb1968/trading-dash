# Momentum ORB — sådan virker algoritmen

Det her er hvordan algoritmen tænker og handler på din vegne. Læs det igennem så du forstår hvad der sker når den kører, og hvornår du skal være ekstra opmærksom.

---

## Aktuelle indstillinger

Sidste opdatering: 17. maj 2026

- **Aktiv variant**: "all_winner" — smal target med trail-mulighed
- **Retning**: Både LONG (køb) og SHORT (sælg-først) — spejlvendt logik
- **+1% gevinst**: Bliver til break-even-beskyttelse (stop flyttes til entry)
- **+1.5% gevinst**: Trailing stop aktiveres
- **Trail-afstand**: 0.5% fra bedste pris (under for long, over for short)
- **Stop loss**: ORB Mid eller maks 1% risiko (typisk -1% til -2%)
- **Target**: +4% / -4% (sikkerhedsnet hvis trail aldrig aktiveres)
- **Volumen-krav**: 3× gennemsnit
- **RSI-filter**: < 80 for long, > 20 for short
- **Entry-vindue**: 09:45-11:00 ET (15:45-17:00 dansk sommer)
- **Force-close**: 15:55 ET (21:55 dansk sommer) — alle positioner lukkes
- **Per handel**: $2.500 (eller $1.250 ved moderate markedsbetingelser)
- **Max positioner**: 3 samtidige (long og short tæller sammen)
- **Daglig tab-grænse**: $300

---

## Den korte version (30 sekunder)

Algoritmen leder efter aktier der bryder UD af den første kvarters handelsrange (US tid 09:30–09:44), kommer **tilbage** for at teste niveauet, og så **fortsætter i samme retning igen**. Når den ser det mønster, slår den til:

- **Bryder op** over ORB High → algoritmen **køber** (long)
- **Bryder ned** under ORB Low → algoritmen **shorter** (sælger først, køber tilbage senere)

Bagefter holder den positionen indtil:

- Stop loss bliver ramt → tab på 1-2%
- +1% i favør nået → stop flyttes til entry (vi kan ikke længere tabe)
- +1.5% i favør nået → trailing stop aktiveres (vi følger prisen)
- 15:55 ET → alle positioner lukkes (markedet lukker snart)

Det er en **forsigtig** version af en momentum-strategi: små sikre gevinster med åbning for store når prisen virkelig løber — uanset om det er op eller ned.

---

## Hvad algoritmen prøver at fange

Forestil dig en aktie der har handlet i sin egen lille range i et kvarter efter børsen åbner — for eksempel mellem $10.00 og $10.30. Det "loft" på $10.30 kalder vi **ORB High** (Opening Range Breakout High), og "gulvet" på $10.00 kalder vi **ORB Low**.

På ægte momentum-dage sker en af to ting:

### Op-scenariet (LONG)

1. **Kursen bryder op** over $10.30 med stor volumen — købere er sultne
2. **Kursen falder lidt tilbage** ned mod $10.30 — sælgere prøver at slå tilbage
3. **Køberne vinder kampen** og kursen springer op igen — DET øjeblik køber vi

### Ned-scenariet (SHORT)

1. **Kursen bryder ned** under $10.00 med stor volumen — sælgere er panikket
2. **Kursen rebounder lidt** op mod $10.00 — købere prøver at fange en bund
3. **Sælgerne vinder igen** og kursen falder under $10.00 igen — DET øjeblik shorter vi

Det her mønster kaldes **break-and-retest**, og det er en af de mest pålidelige måder at bekræfte at et breakout er ægte og ikke bare en kortvarig falsk bevægelse. Det gælder symmetrisk i begge retninger.

Når algoritmen ser hele mønsteret udspille sig, slår den til.

### Hvad er en SHORT?

Når vi shorter en aktie, sælger vi den uden at eje den (vi låner den af mægleren). Vi tjener penge hvis kursen FALDER — vi køber den tilbage billigere og leverer den retur. Det er spejlbilledet af et normalt køb: hvor long tjener på stigninger, tjener short på fald. Risikoen og potentialet er det samme — bare omvendt.

---

## Hvornår handler algoritmen overhovedet?

Først tjekker algoritmen om markedet er værd at handle på i dag. Det kaldes **markedsbetingelser**, og du kan se det i Markedsoverblik-vinduet.

Algoritmen kigger på tre ting:

**VIX** (CBOE Volatilitetsindex) — et tal der måler frygt i markedet:
- Under 15: markedet er for roligt → ingen handel
- 15-40: normal → fuld position
- Over 40: ekstrem → halveret position (panik er farligt for begge sider)

**SPY** (S&P 500 ETF) — barometeret for det generelle marked:
- Hvis SPY åbner mere end 1.5% lavere end gårsdagens luk → ingen handel (alt for risikabelt)
- Ellers OK

**Scanner-resultater** — hvor mange small caps der har stort gap eller højt volumen:
- Mange gainers = momentum i markedet, god dag
- Få = stille dag, dårligt for algoritmen

De her tre tal lægges sammen til en **score fra 0-100**. Scoren bestemmer:

- **Over 60**: aktiv dag → algoritmen handler normalt
- **40-60**: moderat → halveret position size
- **Under 40**: rolig → algoritmen handler IKKE

---

## Tidsplan — hvornår sker hvad

Alle tidspunkter er US Eastern Time (ET). I dansk tid er det 6 timer senere om vinteren / 5 timer om sommeren.

| Tid (ET) | Tid (DK sommer) | Hvad sker der |
|----------|-----------------|---------------|
| 09:30 | 15:30 | Børsen åbner. Algoritmen begynder at observere ORB-vinduet |
| 09:44 | 15:44 | ORB-vinduet er færdigt. Algoritmen kender nu ORB High og ORB Low |
| 09:45 | 15:45 | **Entry-vindue åbner** — algoritmen kan nu købe |
| 11:00 | 17:00 | **Entry-vindue lukker** — ingen NYE køb efter dette tidspunkt |
| 11:00-15:55 | 17:00-21:55 | Eksisterende positioner kører videre med stop/target/trail |
| 15:55 | 21:55 | **Alle positioner lukkes** — markedet er ved at lukke |

Mellem 09:45 og 11:00 kigger algoritmen efter sit mønster på alle de aktier den overvåger. Efter 11:00 holder den op med at åbne nye positioner — men hvis vi allerede har en position der løber, får den lov at fortsætte indtil dens egne exit-regler udløses.

---

## Hvordan algoritmen vælger en handel — 4 krav

Når en aktie skal handles, **skal alle fire krav være opfyldt samtidig**. Kravene gælder symmetrisk for både long og short — kun retningen vendes.

### Krav 1: Pris bryder ORB-niveau

- **Long**: aktien skal handle højere end ORB High (toppen af det første kvarter)
- **Short**: aktien skal handle lavere end ORB Low (bunden af det første kvarter)

Hvis ORB High er $10.30 og ORB Low er $10.00, kigger algoritmen først på aktien når den enten bryder over $10.30 eller under $10.00.

### Krav 2: Volumen-eksplosion

Volumen i breakout-baren skal være mindst **3 gange** højere end den gennemsnitlige volumen den dag — samme krav for både long og short.

Det her er den **vigtigste** filter. Almindelige breakouts har 1.5-2× volumen — men ægte momentum-breakouts har 3×+. Vi venter på de **ægte**.

Det er derfor algoritmen sjældent handler — i live trading forventer vi få handler om ugen, ikke flere om dagen.

### Krav 3: RSI-filter (modsat retning for short)

RSI er en indikator der måler om en aktie er "overophedet" eller "overudsalgt".

- **Long**: RSI skal være **under 80** (vi vil ikke købe noget der allerede topper ud)
- **Short**: RSI skal være **over 20** (vi vil ikke shorte noget der allerede har ramt bunden)

Begge filtre beskytter mod at handle bevægelser der er ved at vende. Hvis RSI er imod os, springer algoritmen over.

### Krav 4: Break-and-retest bekræftet

Mønsteret skal udspilles i tre faser (i den relevante retning):

**Fase A — Breakout/breakdown opdaget**
Prisen bryder ud af ORB-rangen med stor volumen og OK RSI.

**Fase B — Pullback (op til 5 minutter)**
- Long: prisen falder tilbage og **rører** ORB High igen (eller kommer meget tæt på)
- Short: prisen rebounder op og **rører** ORB Low igen (eller kommer meget tæt på)

Hvis pullback'en ikke sker inden 5 minutter, glemmer algoritmen breakout'et og venter på et nyt.

**Fase C — Bounce (entry)**
- Long: prisen springer tilbage **over** ORB High → algoritmen **køber**
- Short: prisen falder tilbage **under** ORB Low → algoritmen **shorter**

Hele mønsteret bekræfter at breakout'et er ægte: vinderne (købere eller sælgere) var stærke nok til at presse prisen igennem niveauet, og stærke nok igen til at forsvare det da modparten prøvede at slå tilbage.

---

## Når en handel åbnes

Algoritmen handler aktier for cirka **$2.500 per position** (eller halvdelen hvis markedet er moderat, eller nul hvis markedet er roligt).

Maksimalt **3 positioner samtidig** — long og short tæller i samme bunke. Algoritmen vil aldrig være over-eksponeret.

Når en position er åbnet, sættes to ting med det samme:

**Stop loss** — prisniveau hvor algoritmen lukker positionen med tab:
- **Long**: stop ligger UNDER entry, typisk 1-2% lavere (ved ORB Mid eller maks 1% under entry)
- **Short**: stop ligger OVER entry, typisk 1-2% højere (spejlvendt)

I begge tilfælde er den maksimale risiko per handel **1% af entry-prisen** — det er det vigtige tal at huske.

**Target** — prisniveau hvor algoritmen tager gevinst:
- **Long**: +4% over entry (sikkerhedsnet)
- **Short**: -4% under entry (sikkerhedsnet)

I praksis tager break-even og trailing stop over først — target udløses sjældent.

---

## Hvordan en position lukkes — 3 stadier

Det her er den smarte del. Algoritmen styrer ikke positioner med ét stop og ét target — den ændrer regler undervejs som du tjener mere. Logikken er **identisk for long og short** — kun retningen vendes.

Når jeg skriver "i favør" herunder mener jeg: pris stiger for long, pris falder for short.

### Stadie 1: Initial (entry til +1% i favør)

Vi har lige åbnet position. Algoritmen overvåger:

- **Hvis prisen rammer stop** → luk position med tab (1-2%)
- **Hvis prisen bevæger sig 1% i favør** → gå til Stadie 2

Det er det farlige stadie — vi kan stadig tabe.

### Stadie 2: Break-even (+1% til +1.5% i favør)

Vi har 1% i favør nu. Algoritmen flytter **stop op (long) / ned (short) til entry-prisen**. Det betyder:

- Hvis kursen retracerer tilbage til entry → vi lukker ved 0% (ingen gevinst men heller ingen tab)
- Hvis kursen fortsætter til 1.5% i favør → gå til Stadie 3
- Hvis kursen springer direkte til 4% i favør (sikkerhedsnettet) → luk med gevinst

Det er det "sikre" stadie — vi kan ikke længere tabe på handlen.

### Stadie 3: Trailing (1.5% i favør og videre)

Nu kører vi for alvor. Algoritmen:

- **Fjerner target** (4%-grænsen) — vi vil lade prisen løbe
- **Følger bedste pris med trailing stop**:
  - **Long**: stop sættes 0.5% under den HØJESTE pris vi har set
  - **Short**: stop sættes 0.5% over den LAVESTE pris vi har set

**Long-eksempel**: Hvis prisen stiger til +3%, flytter stop sig op til +2.5%. Stiger den til +5%, flytter stop sig op til +4.5%. Falder den så 0.5% fra sit højeste → SÆLG.

**Short-eksempel**: Hvis prisen falder til -3% (i favør), flytter stop sig ned til -2.5%. Falder den til -5%, flytter stop sig ned til -4.5%. Stiger den så 0.5% fra sit laveste → COVER (køb tilbage).

Det er "lad-vindere-løbe"-stadiet. Vi forlader aldrig mere end 0.5% af gevinsten tilbage på bordet.

### Sikkerhedsnet: 15:55 ET

Hvis hverken stop eller trail har lukket en position kl. 15:55 ET → algoritmen lukker uanset hvad. Det er fordi markedet lukker kl. 16:00 og vi vil ikke have positioner natten over — hverken long eller short.

---

## Risikostyring — hvad beskytter dig mod store tab

Udover stop loss per handel har vi to globale beskyttelser:

**Daglig tab-grænse**: $300 samlet for hele dagen. Hvis algoritmen har tabt $300 i alt på tværs af alle handler, STOPPER den automatisk — også selvom klokken kun er 10:00. Du kan starte den igen næste dag.

**Total eksponering**: max $20.000 i markedet samtidig. Selv hvis algoritmen kunne se 10 perfekte setups, ville den ikke købe alle 10. Den begrænser sig selv.

Disse to grænser kan du altid se øverst i Live Algo-vinduet (den grønne/gule/røde stribe).

---

## Hvad du bør holde øje med

Når algoritmen kører, kigger du på:

**Strategi-liste** (øverst i Live Algo): viser om Momentum ORB kører eller er stoppet, og dagens P&L.

**Risiko-stribe**: bør være grøn det meste af tiden. Bliver den rød = noget er galt.

**Live log** (højre side): viser alt hvad algoritmen tænker og gør i realtid.

**Markedsoverblik-vinduet**: tjek score om morgenen. Hvis den er under 40, ved du at algoritmen ikke vil handle i dag — og det er meningen.

---

## Det du IKKE skal gøre

- **Stop ikke algoritmen mid-trade** med mindre noget er rigtigt galt. Den er bygget til at klare sig selv.
- **Tag ikke positioner manuelt** på samme aktier som algoritmen handler. Det forstyrrer dens regnskab.
- **Sammenlign ikke dagens resultat** med backtest-tal. Backtest er gennemsnit over hundredevis af dage. Én dag betyder ingenting.

## Særligt om shorts

Shorts kræver at aktien kan **lånes** af mægleren. Ikke alle aktier kan shortes — især small caps under $5 er ofte "hard-to-borrow" eller helt utilgængelige. Algoritmen forsøger at sende SHORT-ordren til IBKR uanset, og hvis aktien ikke kan shortes vil ordren fejle stille i loggen. Det er ikke en bug — det er bare en konsekvens af at vi handler small caps.

På paper trading har vi ofte adgang til at shorte tickers som normalt ikke ville være tilgængelige live. Vær opmærksom på det hvis vi senere går live: short-flowet kan blive markant mindre på en rigtig konto.

---

## Når du undrer dig

Hvis algoritmen IKKE handler en dag hvor du synes der var fine setups: det er sandsynligvis fordi volumen ikke nåede 3×, eller fordi RSI var for høj, eller fordi markedsscoren var under 40. Det er ikke en fejl — det er disciplin.

Hvis algoritmen handler et setup der **taber** penge: det er den naturlige pris for at deltage. Selv en god strategi taber omkring halvdelen af sine handler. Det er om de samlede gevinster er større end de samlede tab.

Hvis algoritmen handler noget der ser **mærkeligt** ud: notér det i din Trade Journal og tag det op med Søren. Mønstre der dukker op flere gange er værd at undersøge.
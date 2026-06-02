# Konfluens — Generel fortælling

*Den statiske, kronologiske beskrivelse af hvad der sker, og hvad der gælder, fra det øjeblik Konfluens-algoritmen sættes i gang, til handelsdagen er afsluttet. Inklusive fejlbehandling.*

*Version 1.1 — udkast, gennemtjekket mod frisk codebase. Baseret på `algo_confluence.py`, `strategies/confluence/{config,entry,exit,indicators}.py`.*

---

## 1. Indledning — forudsætninger, vilkår og princip

Konfluens er en **long-only** strategi for amerikanske aktier, der arbejder på **færdige 5-minutters bars** gennem hele handelsdagen. Grundprincippet er konfluens i ordets egentlige betydning: vi handler ikke på ét enkelt signal, men venter på at **flere uafhængige tegn på styrke optræder samtidig på den samme bar**. Først når mindst 4 ud af 6 bullish betingelser er opfyldt, åbnes en position; og positionen lukkes igen via et tre-lags exit-system.

**Forudsætninger der skal være på plads, før algoritmen overhovedet kan starte:**

- IBKR TWS skal køre og være logget ind på paper-kontoen, med API aktiveret på port 7497 (Read-Only API slået fra).
- Backenden (`uvicorn main:app`) skal køre, og LiveAlgo-vinduet skal være forbundet via WebSocket.
- Algoritmen forventer at blive startet i god tid før eller omkring markedsåbning (09:30 ET / 15:30 dansk tid), da opbygningen af universe og indikator-historik tager 1–3 minutter.

**Vilkår der gælder under hele afviklingen:**

- **Handelsvindue:** Algoritmen er aktiv fra 09:30 ET, og dagen afsluttes (alle positioner lukkes) kl. **15:45 ET**. Nye positioner åbnes dog kun frem til **entry-cutoff kl. 14:00 ET** (se afsnit 5). Eksisterende positioner får lov at løbe efter 14:00, men senest til 15:45.
- **Tidszone:** Alt internt regnes i `America/New_York` (ET). Dansk tid er ET + 6 timer (sommertid).
- **Bar-opløsning:** 5-minutters bars. Algoritmen poller hvert 30. sekund, men reagerer kun, når en **ny 5-min bar** er kommet til for en ticker — hver bar behandles **præcis én gang** (se afsnit 4a for den præcise mekanik og en vigtig nuance om delvise vs. færdige bars).
- **Kun long:** Strategien køber aldrig short. Al exit-logik antager en long-position.
- **Variant:** Live kører variant `baseline`, som matcher Pine Script-defaults 1:1. Alle parameterværdier i denne fortælling er baseline-værdier.

Det skal understreges: `algo_confluence.py` indeholder **ingen** strategi-beslutninger. Den står for IBKR-forbindelse, bar-streaming, universe-scanning, ordrer, fills, kapital, status-broadcast og fejlhåndtering. Selve beslutningen om at gå ind og ud sker udelukkende i `strategies/confluence/entry.py` og `exit.py`.

---

## 2. Preflight

Når algoritmen startes, kører fire tjek i rækkefølge. Hvert tjek broadcaster en status til LiveAlgo-vinduet, og fejl undervejs stopper enten starten eller markeres som ikke-fatal (se nedenfor).

**1) IBKR-forbindelse.** Det tjekkes at `conn.connected` er sand. Er der ingen forbindelse, afbrydes preflight med beskeden *"IBKR ikke forbundet"*, og algoritmen starter ikke.

**2) Konto-data.** Konto-summary hentes, og det kontrolleres at **net liquidation (NLV) er større end 0**. Er den 0 eller mangler, afbrydes med *"Ingen konto-data"*. Ved succes opdateres risikostyringens NLV-grundlag med den aktuelle saldo.

**3) Datafeed.** Der hentes test-bars for **AAPL** (5-min, `TRADES`). Lykkes det ikke, prøves `MIDPOINT` som fallback (relevant uden for handelstid, hvor `TRADES` kan returnere tomt). Kommer der stadig ingen bars tilbage, afbrydes med *"Kan ikke hente markedsdata"*.

**4) Markedsforhold.** `MarketConditionChecker` vurderer dagens overordnede betingelser og udregner en **position-størrelses-faktor** (`position_size_pct`) mellem 0 og 1. Denne faktor skalerer al positionsstørrelse resten af dagen.
- Hvis markedsforholdene siger at vi **ikke skal handle** (`skal_handle = False`), gennemføres preflight som OK, men dagen markeres med *"Ingen handel i dag"*, og faktoren sættes reelt til 0.
- Dette tjek er **ikke-fatalt**: hvis selve `MarketConditionChecker` fejler, fanges fejlen, logges, og algoritmen kører videre med beskeden *"Markedsforhold ikke vurderet (fejl ignoreret)"*.

Når alle relevante tjek er passeret, sættes status til `orb_ready`, og en samlet opsummering broadcastes (fx *"Pre-flight OK: ✅ IBKR forbundet | ✅ Konto aktiv — NLV: \$… | ✅ Datafeed virker …"*).

---

## 3. Universe-opbygning

Dette er den tunge del af opstarten og kan tage 1–3 minutter. Den sker én gang, lige efter preflight.

**Scanning.** Algoritmen henter dagens top-gainers via TradingView/IBKR-scanneren med indbyggede filtre: **pris $5–$50**, **dagsvolumen > 500.000**, og den tager **top 25**. (Den almindelige IBKR-wrapper bypasses, fordi den ikke understøtter pris/volumen-filtre og ellers ville være domineret af penny-stocks.)
- Returnerer scanneren færre end 3 tickers, prøves igen én gang efter 3 sekunder.
- Er der stadig for få, og der ikke er defineret en fallback-liste, sættes status til `error` med *"Scanner returnerede 0 tickers — afbryder"*, og dagen stopper her.

**Prisfilter.** For hver ticker hentes en reference-pris: snapshot hvis markedet er åbent, ellers seneste daglige close. Hvert opslag har en **timeout på 5 sekunder**. Tickere uden for $5–$50 frasorteres. Fejler prisopslaget for *alle* tickere (typisk lukket marked eller IBKR-problem), sættes status til `error`, og dagen stopper.

**Warmup-historik.** For hver overlevende ticker hentes **20 handelsdages** 5-min bars. Indikatorerne (HTF EMA, VWAP, RSI, ATR, pivots/swings, volume-MA) kræver lang historik for at være korrekt "warmed up". En ticker med færre end **50 bars** springes over. For hver ticker bygges en *session-context*, hvor alle indikatorer er pre-computet for hele serien — det er denne pre-compute der sikrer at live giver samme resultat som backtest.

**Trade Forensics.** Til sidst startes en `TapeBuffer`, der abonnerer på tape (time & sales) og Level 2-dybde for hver ticker. L2 fejler typisk for de fleste pga. IBKR's grænse på 3 samtidige dybde-abonnementer — det er accepteret, og algoritmen kører videre uden L2 for dem. Skulle hele forensics-opsætningen fejle, er det **ikke-fatalt**: handelsflowet fortsætter uden forensics.

Når universe er klar, broadcastes *"✅ Universe klar — N tickers med fuld indikator-historie"*, og handels-loopet startes.

---

## 4. Handels-loopet — den løbende cyklus

Loopet kører, så længe status er `RUNNING`, og laver én iteration ca. **hvert 30. sekund**. På hver iteration gælder følgende rækkefølge:

1. **Tidskontrol.**
   - Før 09:30 ET: loopet venter (*"Venter på handelsvindue"*) og gør ellers ingenting.
   - Kl. 15:45 ET eller senere: alle åbne positioner lukkes, dagens diagnostik skrives, og dagen afsluttes (status `done`, derefter `STOPPED`).
   - Hvis markedsforholdene sagde "ingen handel" (`position_size_pct = 0`): loopet idler 60 sekunder ad gangen og åbner aldrig en position.

2. **Heartbeat.** Hvert 5. minut skrives et heartbeat med dagens diagnostik (antal evalueringer, scorede bars, entries, åbne positioner, universe-størrelse), så data overlever selv et crash.

3. **Per-ticker-tjek.** For hver ticker i universe kaldes ticker-tjekket (afsnit 4a). Fejl her tælles som fortløbende fejl.

4. **Pause.** Loopet sover 30 sekunder og gentager.

### 4a. Hvad sker der pr. ticker

For hver ticker, på hver iteration:

- Den **seneste 5-min bar** hentes fra IBKR (de seneste ~60 minutters bars, hvoraf den nyeste tages). Har vi allerede behandlet en bar med samme tidsstempel, springes tickeren over — derfor sker der kun noget, **første gang en ny bars tidsstempel ses**.
- Den nye bar tilføjes historikken, og **alle indikatorer genberegnes for hele serien** (bevidst valg: det er ikke det hurtigste, men det giver bit-for-bit samme resultat som backtest).
- **Har vi allerede en åben position i tickeren:** stop/trail-state opdateres, exit-betingelserne tjekkes på baren, og MFE/MAE (max favorable/adverse excursion) opdateres til forensics. Udløses en exit, lukkes positionen (afsnit 6).
- **Har vi ingen position:** entry-betingelserne evalueres (afsnit 5). Hver evaluering logges (Lag B+-diagnostik), så vi bagefter præcist kan se hvilke af de 6 betingelser der manglede. Giver evalueringen et signal, åbnes en position.

> **Vigtig nuance — delvis vs. færdig bar.** Koden er *skrevet med den hensigt* at behandle den seneste **færdige** 5-min bar. Men fordi der polles hvert 30. sekund, og hver ny bars tidsstempel udløser behandling **første gang det ses**, afhænger det af IBKR's adfærd, om baren reelt er færdig eller stadig er ved at danne sig: returnerer IBKR den igangværende bar som den nyeste, bliver entry/exit vurderet på en **delvis bar** kort efter dens åbning (i praksis tæt på bar-open), ikke på dens close. Dette er sandsynligvis kernen i den kendte forskel mellem backtest (der altid bruger færdige bar-closes) og live. **Dette punkt skal bekræftes mod din faktiske live-observation** (se mine spørgsmål nederst), da det afgør, om afsnit 5 og 6 skal formuleres om "bar-close" eller "bar-open/delvis bar".

---

## 5. Entry

Entry vurderes på hver **ny, færdig 5-min bar**, men kun når **alle** disse rammevilkår er opfyldt:

- Baren ligger inden for sessionen (09:30–16:00 ET).
- Klokken er **før entry-cutoff 14:00 ET**. Efter 14:00 åbnes ingen nye positioner. *(Baggrund: backtest viste at strategien topper i P&L omkring kl. 14:00 ET og derefter taber konsekvent — win rate falder fra ~71 % før 14:00 til ~27–31 % senere.)*
- Der er en gyldig ATR for baren (ellers kan stoppet ikke beregnes).
- Vi har ikke allerede ramt maks. antal samtidige positioner.

Når rammevilkårene er opfyldt, beregnes en **entry-score** ved at tælle, hvor mange af de følgende 6 betingelser der er sande. **Entry udløses, når mindst 4 af de 6 er opfyldt** (`entry_threshold = 4`). Hver betingelse har et bogstav, så scoren kan skrives kompakt som fx `T·R·CL` (et punktum betyder "ikke opfyldt").

**1) HTF-trend (T) — prisen er over den højere tidsrammes trend.**
Barens close skal ligge over **EMA(50) beregnet på 15-minutters bars**. Det er trendfilteret: vi køber kun, når den overordnede 15-min trend stadig peger op. Er EMA'en endnu ikke beregnet (for lidt historik), tæller betingelsen som ikke opfyldt.

**2) VWAP-styrke (V) — køberne har kontrol omkring dagens volumevægtede gennemsnit.**
Betingelsen er sand hvis **enten** close ligger over **VWAP** (dagens volumevægtede gennemsnitspris, ankret ved dagsstart) — **eller** baren har lavet et "pullback-køb": dens low rørte eller brød det **nedre VWAP-bånd** (VWAP − 1,5 std.afvigelser) og lukkede alligevel bullish (close > open). Mangler VWAP-værdien, er betingelsen ikke opfyldt. *(Hvis VWAP-konfluens var slået fra i konfigurationen, ville betingelsen altid tælle som opfyldt — men i baseline er den slået til.)*

**3) RSI-reset (R) — aktien har været oversolgt og vender nu opad.**
To ting skal gælde samtidig: RSI(14) skal have været **under 35** på mindst én af de seneste **5 bars** (oversold-vindue), **og** RSI skal på denne bar **krydse op gennem 40** (forrige bar ≤ 40, denne bar > 40). Det fanger det øjeblik, hvor en aktie kommer ud af oversolgt-tilstand og begynder at vise fornyet styrke.

**4) Higher Low (H) — strukturen bygger højere bunde.**
Det seneste bekræftede **svingende lavpunkt** skal ligge **højere end det forrige** svingende lavpunkt (begge skal eksistere). Svinglavpunkter findes via pivots med 3 bars til venstre og 3 til højre — dvs. et lavpunkt bekræftes først 3 bars efter, det er sat. Stigende bunde er et klassisk tegn på, at køberne stopper salget på højere niveauer end før.

**5) Reversal-candle (C) — selve baren bekræfter et vendepunkt.**
Mindst ét af tre bullish candle-mønstre skal være til stede:
- **Bullish engulfing:** denne bar er grøn (close > open), forrige bar var rød, og denne bars krop omslutter den forrige (close > forrige open, open < forrige close).
- **Hammer:** lang nedre veke (mere end 2× kroppen), kort øvre veke (mindre end kroppen), og grøn close.
- **Strong close:** grøn bar, der lukker i den øverste tredjedel af sit range (close ligger mere end 66 % oppe i bar-rangen), og hvis low er lavere end forrige bars low.

**6) Volume-spike (L) — der er reel handel bag bevægelsen.**
Volumen på baren skal være mindst **1,2× det 20-bar gennemsnit**, **og** baren skal lukke bullish (close > open). Det sikrer, at bevægelsen er understøttet af volumen og ikke bare er støj.

**Når score ≥ 4:** der lægges en **markeds-købsordre**. Antal aktier = `(max_position_size × position_size_pct) / entry-pris`, rundet ned. `max_position_size` er som standard **$2.500** (samme som ORB), og `position_size_pct` kommer fra preflightens markedsvurdering. Entry-prisen registreres som **den udløsende bars close**. Ordren skal desuden godkendes af risikostyringen, før den sendes; afvises den, åbnes ingen position.

Ved åbning gemmes alt til forensics (indikatorer, tape, L2, MFE/MAE-tracking initialiseres), en trade-row åbnes i journalen, og et `algo_trade`-event broadcastes til LiveAlgo-vinduet med score og brick-streng.

---

## 6. Exit

Hver åben position styres af et **tre-lags hybrid-exit**. På hver ny 5-min bar tjekkes lagene i en fast rækkefølge, og **det første lag der udløses, lukker positionen**.

Først opdateres dog stop- og trail-state (se "Stop-mekanik" nedenfor). Derefter tjekkes i denne rækkefølge:

**Lag 0 — Force-close (session-luk).**
Er klokken **15:45 ET** eller senere, lukkes positionen ubetinget. Grund: `session_close`. Det er her dagens "ryd bordet"-regel ligger; vi vil ikke holde positioner ind mod lukketid. I praksis er det handels-loopet selv, der lukker alle positioner kl. 15:45 (afsnit 4) — exit-motoren har den samme 15:45-grænse som backstop (og som den vej, backtesten lukker på).

**Lag 1 — Hard stop / Trailing stop.**
Hvis barens **low ≤ det aktuelle stop**, lukkes positionen til **stop-prisen**. Grund: `trail` hvis trailing-stoppet var aktivt, ellers `stop`. Dette lag har forrang over signal-exit (se konflikt-reglen).

**Lag 2 — Signal-exit (bearish konfluens).**
Hvis mindst **3 af 5 bearish betingelser** (`exit_threshold = 3`) er opfyldt på baren, lukkes positionen til barens close. Grund: `signal_exit`. De fem betingelser er:
- **(O) RSI overbought-reversal:** RSI har været **over 65** på en af de seneste 5 bars **og** krydser nu **ned gennem 60**.
- **(L) Lower High:** seneste svingende højpunkt ligger lavere end det forrige (strukturen begynder at lave lavere toppe).
- **(C) Bearish candle:** bearish engulfing, shooting star, eller en "weak close" (rød bar der lukker i bunden af sit range med et nyt højere high end forrige bar).
- **(E) EMA-crossunder:** close krydser **ned under EMA(9)** (forrige bar var over, denne er under).
- **(V) Bearish volumen/divergens:** enten en volume-spike på en rød bar (volumen > 1,2× snit og close < open), eller en bearish RSI-divergens (højere pris-top, men lavere RSI på toppen).

**Konflikt-regel:** Hvis både stop (Lag 1) og signal-exit (Lag 2) rammes på samme bar, **vinder stoppet**. Det er en bevidst konservativ konvention: ved tvetydighed inden for én bar antager vi worst case (stop-prisen frem for close-prisen).

### Stop-mekanik

- **Initial stop** beregnes ved entry: `entry-pris − 1,2 × ATR(14)` (ATR målt på entry-baren). Forskellen mellem entry og initial stop kaldes **R** (risiko pr. aktie).
- Stoppet kan **kun bevæge sig opad** (det ratcheter), aldrig nedad.
- **Trailing aktiveres**, når barens close når **+1R over entry** (`trail_activ_r = 1,0`). Når det sker, sættes trail-stoppet i første omgang lig initial-stoppet.
- **Trail-type i baseline er "Swing Low":** trail-stoppet løftes op til det seneste svingende lavpunkt, hver gang det er højere end det nuværende trail-stop. (Tre andre trail-typer findes til sammenligning — "EMA Fast", "ATR" og "Percent" (12 % fra højeste close) — men de bruges ikke i live-varianten baseline.)
- Når trailing er aktiv, er det gældende stop = `max(initial stop, trail stop)`.

Ved lukning registreres exit-prisen og grunden, P&L beregnes, en SELL-ordre sendes, trade-row'en lukkes i journalen, exit-forensics gemmes, og et `algo_trade`-event (action `sell`) broadcastes.

---

## 7. Fejlbehandling og edge cases

Algoritmen er bygget, så enkeltstående fejl ikke vælter handelsdagen, men gentagne eller fundamentale fejl stopper den sikkert.

**Fortløbende loop-fejl → genforbinding.** Fejler per-ticker-tjekkene, tælles fortløbende fejl. Ved **3 fejl i træk** forsøges en genforbinding til IBKR: forbindelsen lukkes, der ventes, og der reconnectes med op til **3 forsøg** med 10 sekunders mellemrum. Lykkes det, nulstilles fejltælleren, og handlen fortsætter. Mislykkes alle forsøg, sættes status til `error`, og algoritmen stopper.

**Ikke-fatale fejl (algoritmen kører videre).**
- `MarketConditionChecker` fejler → markedsforhold markeres som ikke-vurderet, handlen fortsætter.
- Trade Forensics / TapeBuffer fejler → handlen fortsætter uden forensics-data.
- L2-dybde fejler for en ticker (IBKR's 3-linjers grænse) → tape bruges alligevel, L2 udelades for den ticker.
- Warmup-fejl eller for få bars (< 50) for en enkelt ticker → den ticker springes over, resten fortsætter.
- Prisopslag-timeout (> 5 s) for en enkelt ticker → den ticker springes over.

**Fatale fejl (dagen stopper).**
- IBKR ikke forbundet ved preflight.
- NLV = 0 / ingen konto-data ved preflight.
- Ingen markedsdata overhovedet ved preflight (hverken TRADES eller MIDPOINT).
- Scanneren returnerer 0 tickers (efter retry, uden fallback).
- Prisopslag fejler for *alle* tickere.
- Genforbinding mislykkes efter gentagne loop-fejl.

**Crash-sikring.** Uanset om loopet afsluttes pænt, annulleres eller crasher, skrives dagens diagnostik altid i en `finally`-blok (idempotent — sidste skrivning vinder), så vi aldrig mister dagens statistik. Diagnostikken indeholder bl.a. nedlukningsårsag, antal evalueringer, peak-score pr. aktie, og hvilken af de 6 entry-betingelser der oftest manglede.

---

## 8. Dagsafslutning

Når klokken passerer 15:45 ET, lukkes alle åbne positioner (`market_close 15:45`), og der broadcastes en opsummering: *"✅ Handelsdagen afsluttet | P&L: \$… | N handler (W/L)"*. Dagens diagnostik skrives, og status sættes til `STOPPED`. Hvis algoritmen stoppes manuelt undervejs, lukkes eventuelle åbne positioner med grunden *"strategi stoppet"*.

---

*Slut på generel fortælling, version 1.1.*

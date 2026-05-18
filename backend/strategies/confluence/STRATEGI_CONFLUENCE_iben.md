# Konfluens-strategien — forklaring til Iben

## Hvad er Konfluens?

Konfluens er en handelsstrategi der køber aktier når **flere positive signaler**
peger samme vej på samme tid. Tanken er enkel: i stedet for at handle på ét
enkelt signal (fx "RSI er oversolgt"), venter vi på at flere uafhængige
tegn på styrke kommer sammen — så er sandsynligheden for en stigning højere.

Den handler **kun long** (køb, aldrig short) og kører på 5-minutters bars
fra markedsåbning (15:30 dansk tid) til lukning (22:00 dansk tid).

## De seks entry-betingelser (mindst 4 skal være opfyldt)

Algoritmen tjekker hver 5-min bar om disse er sande:

1. **HTF trend (T)** — Prisen står over 15-minutters EMA(50). Dvs. trenden på
   højere tidsramme er stadig oppe. Hvis vi er under den, handler vi ikke.

2. **VWAP-styrke (V)** — Prisen står over VWAP (dagens volumevægtede
   gennemsnitspris), ELLER vi har lige haft et dyk under nedre VWAP-bånd
   som lukkede bullish. Dvs. køberne er stadig i kontrol.

3. **RSI-reset (R)** — RSI(14) har været under 35 i de seneste 5 bars OG
   krydser nu op gennem 40. Dvs. aktien er ude af oversolgt-tilstand og
   begynder at vise styrke igen.

4. **Higher Low (H)** — Det seneste svingende lavpunkt ligger over det forrige.
   Dvs. køberne stopper salget på højere niveauer end før — klassisk
   uptrend-tegn.

5. **Reversal candle (C)** — Vi har lige set en bullish engulfing, en hammer,
   eller en stærk bullish close (luk i top af range). Visuel bekræftelse på
   at køberne tog over.

6. **Volume spike (L)** — Volumen er mindst 1.2 × det 20-bar gennemsnit, og
   baren lukkede bullish. Dvs. der er reel handel bag bevægelsen, ikke bare
   støj.

Når mindst 4 af disse 6 er Y → algoritmen køber.

## Sådan håndteres exit (sælg)

Hver position har **tre lag** af exit, og det første der trigger vinder:

1. **Hard stop loss** — Ved køb beregner vi ATR (gennemsnitlig prisbevægelse)
   og lægger en stop 1.2× ATR under entry-prisen. Hvis prisen falder dertil
   sælger vi automatisk.

2. **Trailing stop** — Når prisen er steget 1R (1 risiko-enhed) over entry,
   begynder en trailing stop at følge prisen op. Default følger den seneste
   svingende lavpunkt. Det betyder vi låser gevinst ind efterhånden som
   trenden bevæger sig.

3. **Signal exit** — Hver bar tjekker vi om 3 af 5 *bearish* konfluens-signaler
   er Y (RSI overbought reversal, lower high, bearish candle, EMA cross down,
   volume spike med rød lukning eller divergens). Hvis ja → sælg.

Plus: **session close kl. 21:55 dansk tid** — alle åbne positioner lukkes
før markedet lukker.

## Hvordan er det forskelligt fra ORB?

| | ORB | Konfluens |
|---|---|---|
| Handelsvindue | Kun 09:45-10:30 ET | Hele dagen 09:30-15:55 ET |
| Side | Long + Short | Kun long |
| Antal kriterier | 1-2 (breakout + volume) | 4 af 6 |
| Entry-signal | Hurtigt, reaktivt | Langsomt, bekræftet |
| Forventet antal handler/dag | 1-3 | 3-8 |

Kort sagt: ORB sigter efter det første breakout efter åbningen, og handler
hurtigt. Konfluens er mere tålmodig og venter på at flere signaler er enige
før den handler.

## Hvad kan du forvente i praksis?

- **Win rate ~35-50%** — vi vinder mindre end halvdelen, men vinderne er
  typisk større end taberne (profitabel risk:reward)
- **Få store vindere bærer dagen** — typisk er 1-2 trades pr. dag dem der
  giver hovedparten af profit; resten er små vindere og tabere
- **Strategien er mere aktiv end ORB** — flere handler pr. dag, mere
  skærm-tid til at følge med

## Risikostyring

- Max **$150 tab pr. handel** før algoritmen automatisk sælger
- Max **$250 samlet dagligt tab** — derefter pauses strategien resten af dagen
- Max **3 samtidige positioner** så vi ikke får ALT-eller-INTET dage
- **$2500 kapital pr. handel** (samme som ORB) — typisk 50-200 aktier pr. position
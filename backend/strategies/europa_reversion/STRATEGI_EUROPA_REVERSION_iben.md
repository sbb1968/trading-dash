# Europa-reversion — sådan virker strategien

Europa-reversion er en helt anden type strategi end Konfluens og ORB. Hvor de
jagter momentum (køber styrke og håber på mere), gør Europa-reversion det
modsatte: den satser på at en pris der er løbet for langt væk fra sit normale
niveau, kommer tilbage igen. Det kaldes "mean reversion" — tilbagevenden til
gennemsnittet.

Den handler ikke aktier, men **futures** — små index-kontrakter — og den kører
om morgenen i den **europæiske handelssession**, fra kl. 08:00 til 14:00 dansk
tid. Det er før den amerikanske session, hvor de andre strategier arbejder, så
de to ligger ikke oven i hinanden.

## Idéen i én sætning

Når prisen pludselig er strakt usædvanligt langt væk fra sit eget gennemsnit —
enten meget højt eller meget lavt — så er den ofte "overstrukket", og chancen
for at den vender tilbage mod midten er god. Strategien satser imod udsvinget
og tjener på tilbagevenden.

## Hvad den handler

To micro-futures på amerikanske aktieindeks:

- **MES** — micro S&P 500
- **M2K** — micro Russell 2000

Den handler bevidst **ikke** den tredje oplagte kandidat (micro Nasdaq, MNQ),
fordi den ikke vender tilbage mod midten lige så pålideligt — den har en tendens
til at fortsætte sine bevægelser i stedet.

## Hvornår åbner den en handel

Strategien måler hvor langt prisen er fra sit eget gennemsnit over de seneste 30
kvarters-bjælker, målt i "standardafvigelser" (et statistisk mål for hvor
usædvanlig en afstand er). Det tal kaldes z.

- Er prisen strakt langt **op** (z er +2 eller mere) → den **shorter** (satser
  på et fald tilbage).
- Er prisen strakt langt **ned** (z er −2 eller mindre) → den går **long**
  (satser på en stigning tilbage).

Den har højst én handel åben i hvert instrument ad gangen.

## Hvornår lukker den igen

Der er to udgange, og den første der indtræffer vinder:

- **Tilbagevenden (gevinst):** når prisen er kommet tæt nok på gennemsnittet
  igen (z er tilbage inden for ±0,5), lukker den og tager gevinsten. Det var
  hele pointen.
- **Stop (tab):** hvis prisen i stedet bliver ved med at strække sig endnu
  længere væk (z når ±3,5), erkender strategien at den tog fejl, og lukker med
  et tab før det bliver større.

Derudover lukkes alt automatisk kl. 13:55 dansk tid, lige inden sessionen
slutter, så ingen handel bæres videre.

## Hvor mange penge per handel

Her sizer strategien efter **risiko**, ikke et fast beløb. Den regner ud hvor
langt der er fra indgang til stop, og køber så mange kontrakter at et stop koster
omkring 1 % af kontoen — dog aldrig mere end en fast grænse på ca. 170 dollar per
handel. Så en handel med kort vej til stop får flere kontrakter end en med lang
vej, men risikoen i kroner er nogenlunde den samme hver gang. Hele strategien
sætter sig på pause resten af dagen hvis det samlede tab når 300 dollar.

## Hvorfor den passer godt sammen med de andre

- Den kører på et **andet tidspunkt** (europæisk morgen, ikke amerikansk
  eftermiddag).
- Den handler en **anden aktivklasse** (futures, ikke aktier).
- Den har en **modsat logik** (satser på tilbagevenden, ikke på momentum).

Det betyder at den ofte tjener penge når de andre står stille, og omvendt — så
den samlede portefølje bliver mere stabil.

## Kort sagt

Europa-reversion satser på at overstrukne priser vender tilbage mod deres
gennemsnit, på to index-micro-futures i den europæiske morgensession. Den åbner
når prisen er strakt mindst 2 standardafvigelser væk, tager gevinst når den
vender tilbage, og stopper tabet hvis udsvinget fortsætter. Den risikostyrer hver
handel til omkring 1 % af kontoen og kører paper trading.

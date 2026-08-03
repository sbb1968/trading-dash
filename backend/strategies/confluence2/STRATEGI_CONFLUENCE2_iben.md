# Konfluens 2 — sådan virker strategien

Konfluens 2 er en momentum-strategi der handler på 1-minuts candles på likvide
amerikanske aktier der bevæger sig meget i løbet af dagen. Den blev bygget efter
erfaringerne med den oprindelige Konfluens-strategi, som ventede på flere
bagudskuende bekræftelser og derfor ofte handlede *efter* en bevægelse allerede
var sket. Konfluens 2 reagerer i stedet på selve impulsen i realtid.

## Idéen i én sætning

Når en aktie pludselig viser et kraftigt købspres — stor volumen, stor
prisbevægelse og en stærk grøn candle — på samme minut, så er der ofte mere
bevægelse på vej de næste minutter. Konfluens 2 fanger den impuls og holder
positionen indtil bevægelsen fejler eller dagen slutter.

## Hvilke aktier kigger den på

Konfluens 2 handler ikke længere de billigste small-caps (de gav for dårlige
kandidater). I stedet bruger den en screener — den samme idé som Sørens
TradingView "Intraday Volatility"-liste: solide mellem- og store selskaber
(markedsværdi fra 5 milliarder og op) i prisintervallet $5–50, som handles i
store mængder og alligevel svinger meget intraday. Det er likvide navne der
bevæger sig nok til at impuls-setuppet giver mening. De op til 25 mest
volatile navne udgør dagens jagtmark.

## Hvornår køber den

En handel kræver to ting på samme afsluttede 1-minuts candle:

Først to obligatoriske impuls-tegn: usædvanligt høj volumen i forhold til både
forrige candle og det seneste gennemsnit, og en stor, stærk grøn candle (stor
krop, lukker højt i sit eget interval). Det er selve impulsen.

Dernæst mindst to af fire kontekst-betingelser, der bekræfter at impulsen sker
i et fornuftigt miljø: at prisen ikke allerede er løbet alt for langt fra sit
gennemsnit, at den bryder forrige candles top, at momentum (RSI) er i et sundt
interval, og at den brede trend ikke er imod.

Når impulsen og nok kontekst er til stede, køber strategien ved candlens
lukkekurs. Der åbnes ikke nye handler efter kl. 15:00 amerikansk tid.

## Hvornår sælger den

Konfluens 2 sælger i to tilfælde: hvis prisen falder ned til strategiens stop
(bevægelsen fejlede), eller ved dagens lukning kl. 15:30 amerikansk tid, hvor
alle positioner lukkes så intet bæres natten over.

Stoppet lægges som udgangspunkt lige under bunden af den candle der udløste
købet. Men hvis den bund ligger usædvanligt tæt på købskursen — så tæt at helt
almindelige kursudsving ville ramme den med det samme — flyttes stoppet lidt
længere væk, til en afstand der passer til hvor meget aktien normalt svinger.
På den måde skrabes handlen ikke ud af ren støj før bevægelsen har fået lov at
folde sig ud.

Det betyder at strategien tager mange små, kontrollerede tab på de impulser der
ikke fortsætter, og til gengæld lader de få vindere løbe gennem dagen. Det er
dér gevinsten kommer fra: et mindre antal handler der løber langt, som mere end
opvejer de mange små stop-tab.

## Hvor mange penge per handel

I den live-version vi kører nu, sætter strategien et fast beløb i arbejde per
handel — omkring 1.000 dollar — og køber så mange aktier det rækker til ved
købskursen. Den holder højst tre positioner åbne samtidig. Oven på det ligger
to sikkerhedsbremser: en handel lukkes hvis den taber mere end 150 dollar, og
hele strategien sætter sig selv på pause resten af dagen hvis det samlede tab
når 300 dollar.

## Hvad vi ved om den

Konfluens 2 er testet grundigt på historiske data over to måneder (april og maj
2026). Den viste en ægte fordel der overlevede realistiske handelsomkostninger,
positionsbegrænsninger og test på data den aldrig var tilpasset. Resultaterne
var stabile på tværs af begge måneder med begrænsede kursudsving undervejs.

Men: backtest er ikke det samme som live handel. Strategien er endnu ikke bevist
under rigtige markedsforhold med en live scanner, og begge testmåneder var
relativt gunstige for momentum. Derfor kører Konfluens 2 udelukkende paper
trading indtil den har vist sig konsistent over en længere periode med blandede
markedsforhold. Først derefter overvejes rigtige penge.

## Kort sagt

Konfluens 2 fanger pludselige købsimpulser på 1-minuts candles i likvide,
volatile amerikanske aktier, sætter et fast beløb i arbejde per handel, tager
hurtige små tab når impulsen fejler, og lader vinderne løbe til dagens slutning.
Den er valideret på historiske data men er stadig i paper-test-fasen.

# BuyTheDip — sådan virker strategien

BuyTheDip er en long-only intradag-strategi der køber "dykket" efter en impuls og
rider bouncen op igen. Den er bygget som Konfluens 2's komplement: hvor K2 køber
selve impuls-**toppen**, køber BuyTheDip det korte tilbagefald bagefter — **bouncen**.
I valideringen tabte den 0 ud af 3 af de dage hvor K2 tabte, så de to strategier
supplerer hinanden i stedet for at tabe samtidig.

## Idéen i én sætning

Når en aktie først har vist et kraftigt løft (en impuls) og derefter falder et lille
stykke tilbage, køber BuyTheDip når faldet vender og begynder at bounce — og sælger
igen enten ved et lille mål-overskud eller hvis bouncen fejler.

## Hvilke aktier kigger den på

Samme jagtmark-idé som K2: et TradingView "Intraday Volatility"-scan — likvide
mellem- og store amerikanske aktier der svinger meget i løbet af dagen. BuyTheDip
kører sit **eget** scan, adskilt fra K2, så de to strategier aldrig deler kandidater
eller kommer i vejen for hinanden. Det er de mest volatile navne fra dagens liste
der udgør jagtmarken.

## Hvornår køber den

Setuppet aflæses på færdige 1-minuts candles i tre trin:

1. **Impuls** — over de seneste ca. 20 minutter er aktien løbet mindst 3 % op (fra
   bunden til toppen i vinduet). Det er beviset på at der er købspres til stede.
2. **Dip** — prisen falder mindst 1,5 % tilbage fra vinduets top. Det er tilbagefaldet
   vi vil købe.
3. **Bounce** — når faldet vender, køber strategien ved bounce-candlens lukkekurs.

Nye handler åbnes **kun i åbningstimen** — mellem 09:30 og 10:30 amerikansk tid, hvor
bevægelserne er størst og renest.

## Hvornår sælger den

BuyTheDip har tre exits:

- **Stop** — hvis prisen falder ned til dip-bunden igen (så fejlede bouncen).
- **Target** — et lille, defineret mål på +2 % over købskursen.
- **Force-close** — alle positioner lukkes senest kl. 15:55 amerikansk tid, så intet
  bæres natten over.

Det giver mange små, kontrollerede tab når bouncen ikke holder, mod til gengæld hurtige
+2 %-gevinster når den gør.

## Hvor mange penge per handel

Sizingen er **risiko-baseret**: strategien risikerer omkring 100 dollar per handel
(afstanden fra købskurs ned til stoppet), men lægger aldrig mere end 1.000 dollar
notionel i én position — et haleværn mod de værste udfald. Den holder højst **3**
positioner åbne samtidig, og hvis der er flere kandidater på én gang, prioriteres det
**dybeste dyk** først. Oven på ligger det fælles daglige tabs-stop, som deles med de
andre strategier på kontoen.

## Hvad vi ved om den

BuyTheDip blev valideret på historiske data som K2's komplement — den ramte ikke de
samme tabsdage. Sizing-tallene (100 dollar risiko / 1.000 dollar notionel) er
deploy-valg der finjusteres på paper, ikke tal fra backtesten (som var procent-baseret).

Strategien kører **udelukkende paper trading** (Ibens konto). På handelsserveren
starter den **automatisk** kort før den amerikanske børsåbning; på Sørens egen maskine
startes den manuelt. Den skal vise sig konsistent over en længere periode før rigtige
penge overhovedet overvejes.

## Kort sagt

BuyTheDip køber det korte tilbagefald efter en impuls i likvide, volatile amerikanske
aktier i åbningstimen, risikerer et lille fast beløb per handel, tager stop ved
dip-bunden og mål ved +2 %, og lukker alt inden lukketid. Den er Konfluens 2's
komplement og er stadig i paper-test-fasen.

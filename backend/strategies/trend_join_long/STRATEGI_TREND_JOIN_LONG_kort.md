# Trend Join Long — kort fortalt

"Gap-and-go": joiner momentum i aktier der åbner kraftigt højere på en **frisk, positiv
nyhed**, når de bryder til ny dagshøjde. Long-only, 5-minutters candles. Paper, manuel
start.

## Ved start

- **Pre-flight:** tjekker IBKR-forbindelse, markedsdata **og** at IBKR-nyheder svarer.
- **Markeds-vurdering:** scorer markedet og kan skrue positionsstørrelsen ned (**halv**)
  eller helt fra på ugunstige dage.
- **Oprydning (reconcile):** lukker evt. forældede positioner fra en tidligere kørsel.
- **Univers:** dagens **top-gappere** fra TradingView (re-scannes hvert **30. minut**):
  aktier fra $3, mindst 500.000 i volumen, i optrend på 1 dag/uge/måned — top 25.
- **Nyhedsfilter:** kun aktier med en frisk positiv nyhed i dag (hentet **direkte fra
  IBKR/TWS** — Dow Jones/Briefing.com) kommer i puljen. Kernen i strategien.

## Køber når (alle skal være opfyldt)

- **Gap** ≥ **3 %** over gårsdagens luk.
- Gårsdagens luk **over 200-dages gennemsnit** (langsigtet optrend).
- **Relativ volumen** ≥ 2× det normale.
- **Frisk positiv nyhedskatalysator** (via IBKR).
- **Trigger:** prisen bryder til **ny dagshøjde over premarket-toppen**.

Kun i vinduet **10:05–15:00** amerikansk tid.

## Sælger når (flertrins — låser gevinst undervejs)

- **Initial stop:** 1 % under dagens laveste.
- **Delvis gevinst:** sælger 1/3 ved 0,75R.
- **Breakeven:** flytter stoppet op til købskursen ved 1,0R (kan herefter ikke tabe).
- **Trailing:** trailer stoppet under 5-minutters swing-lavpunkter.
- **Force-close kl. 15:30** amerikansk tid: alt lukkes, intet natten over.

## Penge pr. handel

Risikerer højst **1 %** af kontoen pr. handel og højst **10 %** af porteføljen i én
position. Højst **5** positioner samtidig (største gap prioriteres).

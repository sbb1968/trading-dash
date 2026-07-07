# BuyTheDip — kort fortalt

Køber det korte tilbagefald ("dykket") efter en impuls og rider bouncen op igen.
Konfluens 2's komplement (K2 køber toppen, BuyTheDip køber dykket). Long-only,
1-minuts candles. Paper, Ibens konto.

## Ved start

- **Pre-flight:** tjekker IBKR-forbindelse og markedsdata.
- **Oprydning (reconcile):** lukker evt. forældede positioner fra en tidligere kørsel.
- **Univers:** eget **TradingView** "Intraday Volatility"-scan (samme motor som K2, men
  adskilt) — likvide, volatile US-aktier.
- **Opvarmning:** læser candles tilbage så impuls/dip kan måles fra start.

## Køber når (aflæst på afsluttede 1-minuts candles)

Tre trin i rækkefølge:

- **Impuls** — aktien er løbet mindst **3 %** op over de seneste ~20 minutter.
- **Dip** — prisen falder mindst **1,5 %** tilbage fra toppen.
- **Bounce** — når dykket vender, købes ved bounce-candlens **lukkekurs**.

Kun i **åbningstimen (09:30–10:30** amerikansk tid). Ved flere kandidater prioriteres
det **dybeste dyk** først.

## Sælger når

- **Target:** +2 % over købskursen.
- **Stop:** prisen falder tilbage til dip-bunden (bouncen fejlede).
- **Force-close kl. 15:55** amerikansk tid: alt lukkes, intet natten over.

## Penge pr. handel

Risiko-baseret: ~**$100** risiko (købskurs → stop) pr. handel, men aldrig mere end
**$1.000** i én position. Højst **3** positioner samtidig. Deler det daglige −$300
tabs-stop med de andre strategier.

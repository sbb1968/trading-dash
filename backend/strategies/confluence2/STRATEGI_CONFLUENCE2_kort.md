# Konfluens 2 — kort fortalt

Momentum-strategi der køber pludselige købsimpulser på 1-minuts candles i likvide,
volatile amerikanske aktier. Paper, Ibens konto.

## Ved start

- **Pre-flight:** tjekker at IBKR er forbundet og at der er markedsdata.
- **Markeds-vurdering:** scorer markedet (VIX/bredde) og beslutter **fuld, halv eller
  ingen** positionsstørrelse for hele dagen.
- **Oprydning (reconcile):** lukker evt. forældede positioner fra en tidligere kørsel.
- **Univers:** henter dagens aktier fra **TradingView** ("Intraday Volatility"): likvide
  mellem-/store US-aktier ($5–50) der svinger meget intraday — de **25 mest volatile**.
- **Opvarmning:** læser ~25 candles tilbage for at fylde indikatorerne.

## Køber når (alt på samme afsluttede 1-minuts candle)

To **obligatoriske** impuls-tegn:

- **Volumen-spike** — usædvanlig høj volumen ift. både forrige candle og det seneste snit.
- **Stor, stærk grøn candle** — stor krop der lukker højt i sit eget interval.

…**og mindst 2 af disse 4** bekræftelser:

- **Ikke løbet for langt** — prisen er ikke allerede strakt for langt fra sit gennemsnit.
- **Bryder toppen** — candlen bryder forrige candles top.
- **Sund RSI** — momentum i et fornuftigt leje (RSI mellem 50 og 78).
- **Trend ikke imod** — prisen er over sit længere gennemsnit.

Køb sker ved candlens **lukkekurs**. Ingen nye handler efter **kl. 15:00** amerikansk tid.

## Sælger når

- **Stop:** prisen falder til bunden af impuls-candlen (bevægelsen fejlede) — stoppet har
  et **gulv**, så det ikke ligger urimeligt tæt på købskursen.
- **Dagens lukning kl. 15:30** amerikansk tid: alt lukkes, intet bæres natten over.

## Penge pr. handel

Fast beløb (~$1.000) pr. handel; højst **3** positioner samtidig. Sikkerhedsbremser:
en handel lukkes ved −$150 tab, og hele strategien pauser resten af dagen ved −$300.

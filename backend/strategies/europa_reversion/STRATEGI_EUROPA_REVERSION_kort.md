# Europa-reversion — kort fortalt

Mean-reversion på **futures** (MES + M2K micro) i den europæiske morgensession. Vædder
på at en pris der er strukket usædvanligt langt fra sit gennemsnit vender tilbage.
Handler på 15-minutters candles. Paper, Ibens konto.

## Ved start

- **Pre-flight:** tjekker IBKR-forbindelse og markedsdata.
- **Kontrakt-valg:** kvalificerer front-måneden for MES og M2K (den aktuelle
  futures-kontrakt).
- **Oprydning (reconcile):** lukker evt. forældede futures-positioner fra en tidligere
  kørsel (rører kun MES/M2K).
- **Opvarmning:** læser ~30 candles tilbage for at beregne gennemsnit og spredning
  (grundlaget for afstands-målingen).

## Køber / sælger (short) når

Strategien måler hvor langt prisen er fra sit gennemsnit i standardafvigelser (z):

- **Går LONG** når prisen er strakt usædvanligt langt **ned** (z ≤ −2) — forventer den
  vender op.
- **Går SHORT** når prisen er strakt usædvanligt langt **op** (z ≥ +2) — forventer den
  vender ned.

Handles kun i den europæiske morgensession (ca. **08:00–14:00 dansk tid**).

## Lukker når

- **Målet nås:** prisen er vendt det meste af vejen tilbage mod gennemsnittet (z omkring ±0,5).
- **Stop:** prisen strækkes endnu længere væk i stedet for at vende (z ud til ±3,5).
- **Session-slut:** alt lukkes senest **ca. 13:55 dansk tid** — intet bæres videre.

## Penge pr. handel

Risikerer ca. **1 %** af kontoen pr. handel; antal kontrakter beregnes ud fra
stop-afstanden og futures-multiplikatoren ($5 pr. point). Højst **2** positioner samtidig
(MES + M2K).

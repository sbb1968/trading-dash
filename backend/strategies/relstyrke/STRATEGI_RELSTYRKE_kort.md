# Relativ Styrke — kort fortalt

Rangerer hver morgen dagens liste af aktier efter hvem der er stærkest lige nu, og
køber de **3 stærkeste**. Én beslutning om dagen — ingen jagt resten af dagen. Long-only,
1-minuts candles. Paper, Ibens konto.

## Ved start

- **Pre-flight:** tjekker IBKR-forbindelse og markedsdata.
- **Oprydning (reconcile):** lukker evt. forældede positioner fra en tidligere kørsel.
- **Univers:** eget **TradingView** top-gainer-scan — likvide US-aktier mellem $3 og $500.
- **Opvarmning:** venter på de første candles så morgen-styrken kan måles.

## Køber når (én gang om dagen)

- **Klokken 09:45** amerikansk tid (15:45 dansk) måles hver akties **morgen-styrke**:
  hvor meget den er steget fra åbningen (09:30) til nu, i procent.
- Alle dagens aktier **rangeres** mod hinanden.
- Der købes **long i de 3 stærkeste** (lige stor position i hver).

Beslutningen fyrer først kl. 09:46, når 09:45-candlen er helt færdig — så live rammer
præcis samme kurs som i backtesten.

## Sælger når

- **Force-close kl. 15:51** amerikansk tid: alle positioner lukkes. Intet natten over.
- Ingen target eller stop undervejs — positionerne holdes hele dagen og lukkes samlet.

## Penge pr. handel

Notional-baseret: ca. **3 % af kontoen** fordeles ligeligt på de 3 navne (~1 % pr. navn),
dog aldrig mere end **$1.000** i én position. Højst **3** positioner — kun de tre stærkeste.

## Det særlige ved den

Fordelen ligger i at **vælge de rigtige** navne, ikke i markedsretningen. Vi måler derfor
hver dag om de 3 valgte slår **gennemsnittet af hele listen** — det er strategiens
egentlige dygtighed (kaldet "selection alpha"), og den følges separat efter lukketid.

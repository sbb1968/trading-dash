# vol_data_probe — FASE V0: hvad kan vi faktisk faa fra IBKR?
Koert: 2026-08-04T10:43:47  ·  niveauer: AB  ·  barstoerrelser: 1 day, 1 hour, 30 mins, 5 mins, 1 min
Regime-byggeklods 1 (volatilitet), fase V0. Ingen volatilitetsmaal beregnes her.

## Sammenfatning
- 10 instrumenter gav data, 0 gjorde ikke.

## V0.1 — raekkevidde pr. instrument pr. barstoerrelse

### VIX  ·  niveau A  ·  ind
*S&P 30-dages implicit vol — eneste FREMADSKUENDE kilde*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2005-10-03 | 2005-10-06 | 2026-08-03 | 20.8 | nej | VENDOR-ONBOARDING — instrumentet blev lanceret 2003, men IBKR's serie starter 2 aar senere. Hverken retention eller instrumentets levetid; IBKR begyndte foerst at foere symbolet da. Kan ikke skaffes. |
| 1 hour | 2005-10-03 | 2005-10-04 | 2026-08-03 | 20.8 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 30 mins | 2005-10-03 | 2005-10-04 | 2026-08-03 | 20.8 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 5 mins | 2005-10-03 | 2005-10-05 | 2026-08-03 | 20.8 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 1 min | 2005-10-03 | 2005-10-05 | 2026-08-03 | 20.8 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |

### VIX3M  ·  niveau A  ·  ind
*3-maaneders implicit — sammen med VIX giver den terminsstrukturen*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2009-08-12 | 2009-08-17 | 2026-08-03 | 17.0 | nej | VENDOR-ONBOARDING — instrumentet blev lanceret 2002, men IBKR's serie starter 7 aar senere. Hverken retention eller instrumentets levetid; IBKR begyndte foerst at foere symbolet da. Kan ikke skaffes. |
| 1 hour | 2009-08-12 | 2009-08-13 | 2026-08-03 | 17.0 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 30 mins | 2009-08-12 | 2009-08-13 | 2026-08-03 | 17.0 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 5 mins | 2009-08-12 | 2009-08-14 | 2026-08-03 | 17.0 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 1 min | 2009-08-12 | 2009-08-14 | 2026-08-03 | 17.0 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |

### ES  ·  niveau A  ·  contfut
*Realiseret vol paa det vi faktisk handler (via MES)*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2022-06-19 | 2022-06-21 | 2026-08-03 | 4.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 hour | 2022-06-19 | 2026-01-29 | 2026-08-03 | 0.5 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 30 mins | 2022-06-19 | 2026-07-02 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 5 mins | 2022-06-19 | 2026-07-02 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 min | 2022-06-19 | 2026-07-27 | 2026-08-03 | 0.0 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |

### RTY  ·  niveau A  ·  contfut
*Realiseret vol paa Russell — M2K-benet*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2023-06-18 | 2023-06-19 | 2026-08-03 | 3.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 hour | 2023-06-18 | 2024-08-04 | 2026-08-03 | 2.0 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 30 mins | 2023-06-18 | 2026-07-02 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 5 mins | 2023-06-18 | 2026-07-02 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 min | 2023-06-18 | 2026-07-27 | 2026-08-03 | 0.0 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |

### SPY  ·  niveau A  ·  stk
*KONTROLGRUPPE: lang ubrudt dagshistorik. Afgoer om en graense er retention*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 1993-01-29 | 1993-02-01 | 2026-08-03 | 33.5 | nej | HARVEST-PARAMETER — dyb dagshistorik findes, vi skal bare bede om den |
| 1 hour | 1993-01-29 | 2011-08-04 | 2026-08-03 | 15.0 | **JA** | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 30 mins | 1993-01-29 | 2011-08-04 | 2026-08-03 | 15.0 | **JA** | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 5 mins | 1993-01-29 | 2011-08-05 | 2026-08-03 | 15.0 | **JA** | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 1 min | 1993-01-29 | 2011-08-05 | 2026-08-03 | 15.0 | **JA** | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |

### VIX9D  ·  niveau B  ·  ind
*9-dages implicit — fanger begivenhedsrisiko (FOMC/CPI)*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2018-06-22 | 2018-06-25 | 2026-08-03 | 8.1 | nej | VENDOR-ONBOARDING — instrumentet blev lanceret 2011, men IBKR's serie starter 7 aar senere. Hverken retention eller instrumentets levetid; IBKR begyndte foerst at foere symbolet da. Kan ikke skaffes. |
| 1 hour | 2018-06-22 | 2018-06-22 | 2026-08-03 | 8.1 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 30 mins | 2018-06-22 | 2018-06-22 | 2026-08-03 | 8.1 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 5 mins | 2018-06-22 | 2018-06-22 | 2026-08-03 | 8.1 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 1 min | 2018-06-22 | 2018-06-22 | 2026-08-03 | 8.1 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |

### RVX  ·  niveau B  ·  ind
*Russell 2000-vol — small-cap-vol afviger systematisk fra large-cap*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2007-11-20 | 2007-11-23 | 2026-08-03 | 18.7 | nej | VENDOR-ONBOARDING — instrumentet blev lanceret 2004, men IBKR's serie starter 4 aar senere. Hverken retention eller instrumentets levetid; IBKR begyndte foerst at foere symbolet da. Kan ikke skaffes. |
| 1 hour | 2007-11-20 | 2007-11-20 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 30 mins | 2007-11-20 | 2007-11-20 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 5 mins | 2007-11-20 | 2007-11-21 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 1 min | 2007-11-20 | 2007-11-21 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |

### VXN  ·  niveau B  ·  ind
*Nasdaq 100-vol — tech-dimensionen*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2007-11-20 | 2007-11-23 | 2026-08-03 | 18.7 | nej | VENDOR-ONBOARDING — instrumentet blev lanceret 2001, men IBKR's serie starter 7 aar senere. Hverken retention eller instrumentets levetid; IBKR begyndte foerst at foere symbolet da. Kan ikke skaffes. |
| 1 hour | 2007-11-20 | 2007-11-20 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 30 mins | 2007-11-20 | 2007-11-20 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 5 mins | 2007-11-20 | 2007-11-21 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |
| 1 min | 2007-11-20 | 2007-11-21 | 2026-08-03 | 18.7 | nej | HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente |

### NQ  ·  niveau B  ·  contfut
*Realiseret vol, tech*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2023-06-18 | 2023-06-19 | 2026-08-03 | 3.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 hour | 2023-06-18 | 2026-01-29 | 2026-08-03 | 0.5 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 30 mins | 2023-06-18 | 2026-07-02 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 5 mins | 2023-06-18 | 2026-07-02 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 min | 2023-06-18 | 2026-07-27 | 2026-08-03 | 0.0 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |

### VX  ·  niveau B  ·  contfut
*VIX-FUTURE: terminsstruktur paa tradeable kontrakter. Kritisk for lag 2 — spot-VIX opdaterer KUN i RTH, futures handler naesten doegnet rundt*

whatToShow der virkede: `TRADES`

| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |
|---|---|---|---|---|---|---|
| 1 day | 2023-11-24 | 2023-11-24 | 2026-08-03 | 2.7 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 hour | 2023-11-24 | 2026-07-05 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 30 mins | 2023-11-24 | 2026-07-05 | 2026-08-03 | 0.1 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 5 mins | 2023-11-24 | 2026-07-27 | 2026-08-03 | 0.0 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |
| 1 min | 2023-11-24 | 2026-07-27 | 2026-08-03 | 0.0 | ? | CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; dybere historik kraever PR-EXPIRY-kontrakter + stitching |

## V0.2 — kvalitet paa dagsserien
| instrument | bedt om | dage | foerste | sidste | stille-loeb | absurde | halve dage | range% p50 | p90 | p99 | p99 u. marts-2020 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| VIX | ? | 3767 | 2011-08-09 | 2026-08-04 | 0 | 0 | 33 | 8.578 | 17.234 | 34.574 | 32.786 |
| VIX3M | ? | 3767 | 2011-08-08 | 2026-08-03 | 0 | 0 | 33 | 4.434 | 9.938 | 20.55 | 19.135 |
| ES | ? | 1055 | 2022-06-21 | 2026-08-04 | 1 | 0 | 9 | 0.725 | 1.868 | 3.672 | 3.672 |
| RTY | ? | 799 | 2023-06-19 | 2026-08-04 | 0 | 0 | 8 | 1.499 | 3.034 | 6.313 | 6.313 |
| SPY | ? | 3768 | 2011-08-09 | 2026-08-04 | 0 | 0 | 33 | 1.07 | 2.408 | 5.29 | 4.337 |
| VIX9D | ? | 2038 | 2018-06-22 | 2026-08-03 | 0 | 0 | 18 | 12.648 | 25.784 | 47.761 | 45.879 |
| RVX | ? | 3767 | 2011-08-08 | 2026-08-03 | 0 | 0 | 33 | 7.186 | 13.315 | 24.374 | 23.225 |
| VXN | ? | 3767 | 2011-08-08 | 2026-08-03 | 1 | 0 | 33 | 7.011 | 13.867 | 29.074 | 28.065 |
| NQ | ? | 799 | 2023-06-19 | 2026-08-04 | 0 | 0 | 8 | 1.298 | 2.765 | 5.566 | 5.566 |
| VX | ? | 677 | 2023-11-24 | 2026-08-04 | 4 | 0 | 6 | 4.167 | 10.795 | 25.952 | 25.952 |

**Kolonnen 'bedt om'** er den varighed der faktisk lykkedes. Timede den oenskede ud, gik proben stigen ned — og saa daekker revisionen et kortere vindue end der blev bedt om. `foerste` viser hvor langt den naaede.

**Marts-2020-foelsomhed:** forskellen mellem `p99` og `p99 u. marts-2020` viser hvor meget én periode dominerer percentilreferencen. Er forskellen stor, skal referencevinduet i V2 vaelges med det for oeje.

## Anbefaling
*(udfyldes efter gennemlaesning — se statusnotatet)*

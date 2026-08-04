# Futures-retention — testet med UDLOEBNE kontrakter

Koert: 2026-08-04T11:06:44  ·  1 min, 1 D pr. kontrakt, useRTH=False

Punkt B1 i Revision B. Kontraktlevetid er elimineret som forklaring: hver kontrakt eksisterede beviseligt paa den dato der spoerges om. Kommer der barer, er retention ikke bindende dér.

| symbol | kontrakt | spurgt om | barer | periode | note |
|---|---|---|---|---|---|
| M2K | 202509 | 2025-08-20 | 1320 | 2025-08-19T22:00 .. 2025-08-20T19:59 |  |
| M2K | 202506 | 2025-05-20 | 1320 | 2025-05-19T22:00 .. 2025-05-20T19:59 |  |
| M2K | 202412 | 2024-11-20 | 1260 | 2024-11-19T23:00 .. 2024-11-20T19:59 |  |
| M2K | 202406 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 202312 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 202306 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 202212 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 202206 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 202112 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 202106 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 202006 | — | 0 | — | kontrakt findes ikke hos IBKR |
| M2K | 201906 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202509 | 2025-08-20 | 1320 | 2025-08-19T22:00 .. 2025-08-20T19:59 |  |
| ES | 202506 | 2025-05-20 | 1320 | 2025-05-19T22:00 .. 2025-05-20T19:59 |  |
| ES | 202412 | 2024-11-20 | 1260 | 2024-11-19T23:00 .. 2024-11-20T19:59 |  |
| ES | 202406 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202312 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202306 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202212 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202206 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202112 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202106 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 202006 | — | 0 | — | kontrakt findes ikke hos IBKR |
| ES | 201906 | — | 0 | — | kontrakt findes ikke hos IBKR |

## Dom

- **M2K:** data helt tilbage til kontrakt **202412**. 3 af 12 probede kontrakter svarede.
  Foerste kontrakt UDEN data: 202406. Retention-graensen ligger mellem 202406 og 202412.
- **ES:** data helt tilbage til kontrakt **202412**. 3 af 12 probede kontrakter svarede.
  Foerste kontrakt UDEN data: 202406. Retention-graensen ligger mellem 202406 og 202412.

**Konsekvens for spor 2:** raekker futures-intradag laengere tilbage end de ~2 aar ContFuture kunne levere, faar spor 2 en aegte udviklingsperiode, og proxy-omvejen over SPY/IWM bliver valgfri frem for noedvendig.

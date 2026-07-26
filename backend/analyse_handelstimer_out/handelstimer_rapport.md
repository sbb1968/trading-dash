# MES — handelstimer i dansk tid
*Ren beskrivende bevaegelses-statistik pr. time. Ingen strategi, ingen P&L.*
- **Data:** 715,755 1-min bars, 2024-06-21 06:00 → 2026-07-01 01:59 (532 handelsdage)
- **Enhed:** MES, 1 point = $5 (1 tick = 0.25 pt)
- **Tidszone:** alt i Europe/Copenhagen (dansk vaegur). Kilde er ET (tz-bevidst); konverteret med rigtig tz — dansk sommertid haandteret automatisk.

> **Note om sommertid:** USA og EU skifter sommertid paa forskellige datoer. I 2-3 uger om aaret daekker en 'dansk time' derfor et lidt andet markedsoejeblik end resten af aaret (fx US-aabningen rykker en dansk time i de uger). Det er korrekt: du handler efter dit eget vaegur, og det er praecis hvad tz-konverteringen giver.

## Bedste 3-5 timer at handle (dansk tid)
- **16:00-17:00** — median range 25.8 pt ($129), bias LONG — 🟢 staerk, konsistent
- **15:00-16:00** — median range 24.1 pt ($121), bias LONG — 🟢 staerk, konsistent
- **17:00-18:00** — median range 19.5 pt ($98), bias LONG — 🟢 staerk, konsistent
- **21:00-22:00** — median range 17.8 pt ($89), bias neutral — 🟢 staerk, konsistent
- **18:00-19:00** — median range 17.5 pt ($88), bias neutral — 🟢 staerk, konsistent

## Undgaa typisk (mindst bevaegelse)
- 03:00-04:00 — median range 7.5 pt — 🔴 for stille
- 07:00-08:00 — median range 7.0 pt — 🔴 for stille
- 04:00-05:00 — median range 6.5 pt — 🔴 for stille
- 06:00-07:00 — median range 5.8 pt — 🔴 for stille
- 05:00-06:00 — median range 5.8 pt — 🔴 for stille

## Alle 24 timer

| Time (DK)   |   n (dage) |   Median range (pt) |   Range ($) |   Median |beveg.| (pt) |   Gns. afkast (pt,±) |   Median max-op (pt) |   Median max-ned (pt) |   Churn (pt) |   Range p90 (pt) |   Andel store | Bias    | Vurdering                  |
|:------------|-----------:|--------------------:|------------:|-----------------------:|---------------------:|---------------------:|----------------------:|-------------:|-----------------:|--------------:|:--------|:---------------------------|
| 00:00-01:00 |        522 |               10.5  |          52 |                   3.75 |                 0.45 |                 4.5  |                  4.5  |        35    |            26.23 |            45 | neutral | 🟡 moderat                  |
| 01:00-02:00 |        522 |                8.5  |          42 |                   3    |                 0.06 |                 3.5  |                  3.25 |        30.75 |            21    |            26 | neutral | 🔴 for stille               |
| 02:00-03:00 |        522 |                9.25 |          46 |                   4    |                 0.5  |                 4    |                  3.62 |        33.5  |            22.73 |            34 | neutral | 🔴 for stille, inkonsistent |
| 03:00-04:00 |        522 |                7.5  |          38 |                   3    |                 0.47 |                 3.25 |                  3.5  |        30.25 |            18.23 |            18 | neutral | 🔴 for stille               |
| 04:00-05:00 |        522 |                6.5  |          32 |                   2.75 |                -0.46 |                 2.5  |                  2.88 |        25.38 |            14.75 |            11 | neutral | 🔴 for stille               |
| 05:00-06:00 |        522 |                5.75 |          29 |                   2.25 |                 0.06 |                 2.75 |                  2.25 |        22.75 |            14.23 |             5 | neutral | 🔴 for stille               |
| 06:00-07:00 |        523 |                5.75 |          29 |                   2.5  |                -0.41 |                 2.5  |                  2.5  |        22.25 |            14.45 |             6 | neutral | 🔴 for stille               |
| 07:00-08:00 |        523 |                7    |          35 |                   3    |                -0.01 |                 3    |                  3    |        27.5  |            17    |            15 | neutral | 🔴 for stille               |
| 08:00-09:00 |        523 |                8.75 |          44 |                   3.75 |                 0.4  |                 3.75 |                  3.5  |        33.75 |            19.95 |            23 | neutral | 🔴 for stille               |
| 09:00-10:00 |        523 |               11.75 |          59 |                   5    |                 0.72 |                 5.25 |                  4.75 |        44.75 |            25.25 |            55 | LONG    | 🟡 moderat                  |
| 10:00-11:00 |        523 |               10.25 |          51 |                   4.5  |                 0.04 |                 4.5  |                  4.25 |        40.5  |            23    |            43 | neutral | 🟡 moderat                  |
| 11:00-12:00 |        523 |                8.75 |          44 |                   3.25 |                 0.22 |                 3.75 |                  3.75 |        34.5  |            18.5  |            22 | neutral | 🔴 for stille               |
| 12:00-13:00 |        523 |                9    |          45 |                   3.5  |                 0.25 |                 4    |                  3.75 |        34    |            21.5  |            30 | neutral | 🔴 for stille, inkonsistent |
| 13:00-14:00 |        523 |               10.25 |          51 |                   4.25 |                -0.34 |                 4.5  |                  4.5  |        39.25 |            27    |            43 | neutral | 🟡 moderat                  |
| 14:00-15:00 |        523 |               14.5  |          72 |                   6    |                 0.47 |                 6    |                  5.75 |        51.25 |            38.5  |            65 | LONG    | 🟡 moderat                  |
| 15:00-16:00 |        522 |               24.12 |         121 |                  10.5  |                 0.3  |                10.12 |                  9.75 |        82.88 |            47.73 |            98 | LONG    | 🟢 staerk, konsistent       |
| 16:00-17:00 |        521 |               25.75 |         129 |                  11.25 |                -0.2  |                10.25 |                 10.5  |        93.75 |            52    |            96 | LONG    | 🟢 staerk, konsistent       |
| 17:00-18:00 |        521 |               19.5  |          98 |                   8    |                -0.07 |                 8.5  |                  8.25 |        77.25 |            40.75 |            93 | LONG    | 🟢 staerk, konsistent       |
| 18:00-19:00 |        521 |               17.5  |          88 |                   6.75 |                -0.45 |                 7    |                  7.5  |        65.25 |            37    |            85 | neutral | 🟢 staerk, konsistent       |
| 19:00-20:00 |        501 |               16.5  |          82 |                   6.75 |                 2.21 |                 7.75 |                  6.5  |        65.75 |            37.5  |            82 | LONG    | 🟢 staerk, konsistent       |
| 20:00-21:00 |        501 |               17    |          85 |                   6.5  |                -1.16 |                 6.5  |                  7    |        63.75 |            38    |            75 | neutral | 🟢 staerk, konsistent       |
| 21:00-22:00 |        501 |               17.75 |          89 |                   7.25 |                 0.25 |                 7.75 |                  7.25 |        66.5  |            41.75 |            83 | neutral | 🟢 staerk, konsistent       |
| 22:00-23:00 |        461 |                9.25 |          46 |                   3.5  |                -0.29 |                 4    |                  4.5  |        32.25 |            26    |            34 | neutral | 🔴 for stille, inkonsistent |
| 23:00-00:00 |         40 |               15.5  |          78 |                   6.62 |                -0.7  |                 5    |                  5.75 |        47.5  |            32.08 |            15 | SHORT   | 🟡 moderat, inkonsistent    |

## Ugedag x time — median range (pt) + trafiklys

| Time (DK) | Mandag | Tirsdag | Onsdag | Torsdag | Fredag | Soendag |
|---|---|---|---|---|---|---|
| 00:00-01:00 | 🟢 17.9 (n106) | 🟡 9.5 (n106) | 🔴 8.5 (n104) | 🟡 10.8 (n103) | 🟡 9.5 (n103) | — |
| 01:00-02:00 | 🟡 10.1 (n106) | 🔴 7.5 (n106) | 🔴 8.2 (n104) | 🔴 8.8 (n103) | 🔴 8.2 (n103) | — |
| 02:00-03:00 | 🟡 9.5 (n106) | 🟡 9.2 (n106) | 🔴 8.0 (n103) | 🟡 10.2 (n103) | 🟡 9.0 (n104) | — |
| 03:00-04:00 | 🔴 8.1 (n106) | 🔴 8.2 (n106) | 🔴 7.0 (n103) | 🔴 7.5 (n103) | 🔴 7.6 (n104) | — |
| 04:00-05:00 | 🔴 6.4 (n106) | 🔴 7.0 (n106) | 🔴 5.8 (n103) | 🔴 7.2 (n103) | 🔴 6.5 (n104) | — |
| 05:00-06:00 | 🔴 5.5 (n106) | 🔴 6.0 (n106) | 🔴 5.5 (n103) | 🔴 6.0 (n103) | 🔴 5.9 (n104) | — |
| 06:00-07:00 | 🔴 5.9 (n106) | 🔴 5.8 (n106) | 🔴 5.2 (n103) | 🔴 6.2 (n103) | 🔴 5.8 (n105) | — |
| 07:00-08:00 | 🔴 6.4 (n106) | 🔴 7.0 (n106) | 🔴 6.8 (n103) | 🔴 7.8 (n103) | 🔴 7.5 (n105) | — |
| 08:00-09:00 | 🟡 9.1 (n106) | 🔴 8.5 (n106) | 🔴 7.8 (n103) | 🟡 9.5 (n103) | 🟡 9.0 (n105) | — |
| 09:00-10:00 | 🟡 12.8 (n106) | 🟡 12.9 (n106) | 🟡 9.8 (n103) | 🟡 12.2 (n103) | 🟡 11.0 (n105) | — |
| 10:00-11:00 | 🟡 10.6 (n106) | 🟡 10.2 (n106) | 🟡 10.0 (n103) | 🟡 11.8 (n103) | 🟡 9.8 (n105) | — |
| 11:00-12:00 | 🔴 8.5 (n106) | 🟡 9.0 (n106) | 🟡 9.0 (n103) | 🔴 8.2 (n103) | 🔴 8.8 (n105) | — |
| 12:00-13:00 | 🔴 7.8 (n106) | 🟡 9.0 (n106) | 🟡 9.5 (n103) | 🟡 9.5 (n103) | 🟡 9.0 (n105) | — |
| 13:00-14:00 | 🟡 9.9 (n106) | 🟡 10.6 (n106) | 🟡 9.8 (n103) | 🟡 10.8 (n103) | 🟡 10.0 (n105) | — |
| 14:00-15:00 | 🟡 11.5 (n106) | 🟡 13.6 (n106) | 🟢 15.2 (n103) | 🟢 18.0 (n103) | 🟢 18.0 (n105) | — |
| 15:00-16:00 | 🟢 25.4 (n106) | 🟢 22.0 (n106) | 🟢 22.8 (n103) | 🟢 27.8 (n103) | 🟢 24.2 (n104) | — |
| 16:00-17:00 | 🟢 23.0 (n106) | 🟢 25.8 (n106) | 🟢 23.2 (n103) | 🟢 29.4 (n102) | 🟢 27.9 (n104) | — |
| 17:00-18:00 | 🟢 16.9 (n106) | 🟢 18.2 (n106) | 🟢 19.5 (n103) | 🟢 22.4 (n102) | 🟢 20.4 (n104) | — |
| 18:00-19:00 | 🟢 15.4 (n106) | 🟢 17.6 (n106) | 🟢 15.8 (n103) | 🟢 19.8 (n102) | 🟢 17.2 (n104) | — |
| 19:00-20:00 | 🟢 14.8 (n98) | 🟢 16.2 (n105) | 🟢 16.5 (n101) | 🟢 20.5 (n97) | 🟢 17.5 (n100) | — |
| 20:00-21:00 | 🟡 14.0 (n98) | 🟢 15.5 (n105) | 🟢 18.5 (n101) | 🟢 20.5 (n97) | 🟢 16.6 (n100) | — |
| 21:00-22:00 | 🟢 16.8 (n98) | 🟢 17.0 (n105) | 🟢 20.0 (n101) | 🟢 20.0 (n97) | 🟢 16.8 (n100) | — |
| 22:00-23:00 | 🔴 8.6 (n90) | 🔴 8.8 (n97) | 🟡 11.2 (n93) | 🟡 10.8 (n89) | 🔴 8.0 (n92) | — |
| 23:00-00:00 | ⚪ 9.8 (n8) | ⚪ 11.8 (n8) | ⚪ 16.6 (n8) | ⚪ 12.1 (n8) | — | ⚪ 31.5 (n8) |

## Split-half robusthed (foer/efter 2025-06-27)

Samme vurdering koert paa hver halvdel. En times 'groen' skal helst findes i begge halvdele for at vaere robust.

| Time (DK) | 1. halvdel | 2. halvdel | Robust? | Flag |
|---|---|---|---|---|
| 00:00-01:00 | 🟡 | 🟡 | ja |  |
| 01:00-02:00 | 🔴 | 🔴 | ja |  |
| 02:00-03:00 | 🔴 | 🔴 | ja |  |
| 03:00-04:00 | 🔴 | 🔴 | ja |  |
| 04:00-05:00 | 🔴 | 🔴 | ja |  |
| 05:00-06:00 | 🔴 | 🔴 | ja |  |
| 06:00-07:00 | 🔴 | 🔴 | ja |  |
| 07:00-08:00 | 🔴 | 🔴 | ja |  |
| 08:00-09:00 | 🔴 | 🔴 | ja |  |
| 09:00-10:00 | 🟡 | 🟡 | ja |  |
| 10:00-11:00 | 🔴 | 🟡 | NEJ | skift |
| 11:00-12:00 | 🔴 | 🔴 | ja |  |
| 12:00-13:00 | 🔴 | 🔴 | ja |  |
| 13:00-14:00 | 🟡 | 🟡 | ja |  |
| 14:00-15:00 | 🟢 | 🟢 | ja |  |
| 15:00-16:00 | 🟢 | 🟢 | ja |  |
| 16:00-17:00 | 🟢 | 🟢 | ja |  |
| 17:00-18:00 | 🟢 | 🟢 | ja |  |
| 18:00-19:00 | 🟢 | 🟢 | ja |  |
| 19:00-20:00 | 🟢 | 🟢 | ja |  |
| 20:00-21:00 | 🟢 | 🟢 | ja |  |
| 21:00-22:00 | 🟢 | 🟢 | ja |  |
| 22:00-23:00 | 🔴 | 🔴 | ja |  |
| 23:00-00:00 | ⚪ | ⚪ | ja |  |

## Datakvalitet
- Tynde time-barer (<30 min, typisk CME-vedligeholdelse ~23:00 DK + weekend-kanter): 7 styk — holdt UDE af gennemsnittene.
- Timer med n < 30 dage vurderes ⚪ (for lidt data).
- CME: handel soendag aften → fredag; daglig vedligeholdelsespause 17:00-18:00 ET (~23:00 DK). Weekend = lukket, ikke 'stille'.

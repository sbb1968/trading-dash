# Regime-motor v2 — cache-dækningskort (fase 0.2)

Genereret af `regime_daekning.py`. `DESIGN_END = 2026-04-30`.
Kun markedsdata; ingen strategi-afkast læst.

---

## 1. Kilder — rå dækning

| Kilde | Handelsdage | Første | Sidste | Heraf ≤ DESIGN_END |
|---|---|---|---|---|
| ES_1day | 1052 | 2022-03-22 | 2026-06-05 | 1027 |
| NQ_1day | 805 | 2023-03-20 | 2026-06-05 | 780 |
| RTY_1day | 802 | 2023-03-20 | 2026-06-05 | 777 |
| MES_15min | 633 | 2024-06-21 | 2026-06-30 | 581 |
| M2K_15min | 633 | 2024-06-21 | 2026-06-30 | 581 |
| aktier_bar_cache | 1267 | 2021-08-11 | 2026-06-30 | 1225 |
| aktie_univers | 43 | 2026-03-30 | 2026-05-29 | 23 |

## 2. Huller (kalendergab > 5 dage)

| Kilde | Fra | Til | Dage |
|---|---|---|---|
| ES_1day | 2023-02-03 | 2023-02-10 | 7 |
| RTY_1day | 2023-10-04 | 2023-10-11 | 7 |

---

## 3. Reel backfill-rækkevidde

Et vindue kræver 30 handelsdage med mindst 80% dækning (spec fase 1.3). Tabellen viser hvor mange
dage der derfor kan få en **gyldig** metrik — ikke hvor mange barer der findes.

| Kilde | Gyldige dage (≤ DESIGN_END) | Første | Sidste |
|---|---|---|---|
| ES_1day | 998 | 2022-05-03 | 2026-04-30 |
| NQ_1day | 758 | 2023-04-21 | 2026-04-30 |
| RTY_1day | 758 | 2023-04-21 | 2026-04-30 |
| MES_15min | 443 | 2024-07-25 | 2026-04-30 |
| M2K_15min | 443 | 2024-07-25 | 2026-04-30 |
| aktier_bar_cache | 998 | 2022-05-03 | 2026-04-30 |
| aktie_univers | 0 | — | — |
| aktie_effektiv | 0 | — | — |

---

## 4. Aktie-siden — hvorfor den er den bindende begrænsning

- `bar_cache` indeholder **225 tickere** over **1267 handelsdage** (2021-08-11 .. 2026-06-30).
- Men aktie-metrikkerne løber over **universlisten**, ikke over cachen (`smallcap_metrics`: `days_in_win` hentes fra `uni`).
- Universlisten dækker kun **43 datoer** (2026-03-30 .. 2026-05-29).
- Effektive aktie-dage (univers ∩ cache): **43**, heraf **23** ≤ DESIGN_END.
- Med 30-dages vindue og 80%-krav giver det **0 gyldige dage** i design-perioden.

**Cache-bredde pr. år** (afgør om universet kan rekonstrueres bagud):

| År | Handelsdage | Tickere med data |
|---|---|---|
| 2021 | 103 | 1 |
| 2022 | 260 | 1 |
| 2023 | 258 | 1 |
| 2024 | 260 | 2 |
| 2025 | 259 | 2 |
| 2026 | 127 | 225 |

---

## 5. Konsekvens for akserne (spec fase 2.2)

| Akse | Komponenter (spec) | Beregnelige i design | Dækning |
|---|---|---|---|
| A_retning | m7_daily_autocorr, m2_intraday_autocorr, m1_gap_follow_through | m7_daily_autocorr | 1/3 |
| A_dispersion | m5_dispersion | **INGEN** | 0/1 |
| A_vol | m3_atr_ekspansion, m9_term_ratio | m9_term_ratio | 1/2 |

---

## 6. Metrik → kilde

| Metrik | Kilde |
|---|---|
| m1_gap_follow_through | aktier |
| m2_intraday_autocorr | aktier |
| m3_atr_ekspansion | aktier |
| m4_hod_morgen | aktier |
| m5_dispersion | aktier |
| m6_halt | (mangler helt) |
| m7_daily_autocorr | futures |
| m8_overnight_ratio | futures |
| m9_term_ratio | futures |
| m10_spread | futures |

---

## 7. Vagt-status

```
Design-mode-vagt: mode=design, DESIGN_END=2026-04-30
  Ingen datoer skaaret fra (alle kilder ligger inden for snittet).
```

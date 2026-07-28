# IS/OOS-validering af long/short-signaturerne

Genereret af `valider_signaturer.py`. Ren signal-validering — ingen
P&L, ingen entry/exit-simulering, ingen taerskel-optimering.

**Signaturer (praeregistreret, uaendret gennem hele koerslen):**

```
LONG  = (z_15m_start <= -2.0) & (rvol_15m_start >= 1.5) & (dot_type_3m_start == "kraftig_groen")   # positiv = retning "up"
SHORT = (z_15m_start >= 2.0) & (rvol_15m_start >= 1.5) & (dot_type_3m_start == "kraftig_roed")    # positiv = retning "down"
Rul-filter: bars_since_roll > 100 (paa BAADE events og baseline)
```

---

## 0. Dom

Kriterierne blev defineret foer koerslen. Ingen af dem er justeret bagefter.

### LONG — **DUMPET** (3/4 kriterier opfyldt)

| Kriterium |  | Tal |
|---|---|---|
| Enrichment >= 3x (OOS) | ✔ | OOS 5.04x [4.0–6.4] (IS 4.36x [3.5–5.5]) |
| Retningstraef >= 95 % (OOS) | ✔ | OOS 99.1 % [95.1–99.8] (IS 98.2 %) |
| Praecision inden for IS +/-25 % | ✔ | OOS 39.8 % ligger inden for IS-intervallet 24.6–41.0 % (IS 32.8 %) |
| RVOL -> stoerrelse monotont stigende (OOS) | ✘ | lift pr. taerskel: 6.8x → 9.0x → 8.1x → 8.7x → 12.0x |

**Hvordan fejlede den?** (aendrer ikke dommen — kriterierne staar uroert)

- **Praecision OOS mod IS:** 39.8 % mod 32.8 % (+21 %). Inden for tolerancen (svag stigning).
- **Hale-lift, samlet tendens:** 6.8x → 12.0x (x1.8), rang-korrelation +0.70, 1 nedadgaaende trin ud af 4. Kriteriet kraever STRENG monotoni og fejler paa de enkelte trin, men den samlede retning er opad. Med en haandfuld kontrol-hits bag hvert punkt er et enkelt dyk fuldt foreneligt med ren stoej.
- **Kan IS og OOS skelnes?** Enrichment IS 4.36x [3.5–5.5] mod OOS 5.04x [4.0–6.4]. Intervallerne overlapper kraftigt — forskellen mellem de to halvdele er ikke statistisk paaviselig. Det udelukker ikke en aegte svaekkelse, men data kan ikke vise en.

### SHORT — **BESTAAET** (4/4 kriterier opfyldt)

| Kriterium |  | Tal |
|---|---|---|
| Enrichment >= 3x (OOS) | ✔ | OOS 4.51x [3.4–5.9] (IS 3.82x [2.9–5.0]) |
| Retningstraef >= 95 % (OOS) | ✔ | OOS 98.7 % [93.2–99.8] (IS 100.0 %) |
| Praecision inden for IS +/-25 % | ✔ | OOS 32.8 % ligger inden for IS-intervallet 20.8–34.7 % (IS 27.7 %) |
| RVOL -> stoerrelse monotont stigende (OOS) | ✔ | lift pr. taerskel: 5.0x → 6.8x → 7.7x → 8.1x → 8.7x |

**Hvordan fejlede den?** (aendrer ikke dommen — kriterierne staar uroert)

- **Praecision OOS mod IS:** 32.8 % mod 27.7 % (+18 %). Inden for tolerancen (svag stigning).
- **Hale-lift, samlet tendens:** 5.0x → 8.7x (x1.7), rang-korrelation +1.00, 0 nedadgaaende trin ud af 4. Monotont.
- **Kan IS og OOS skelnes?** Enrichment IS 3.82x [2.9–5.0] mod OOS 4.51x [3.4–5.9]. Intervallerne overlapper kraftigt — forskellen mellem de to halvdele er ikke statistisk paaviselig. Det udelukker ikke en aegte svaekkelse, men data kan ikke vise en.

---

## 0b. Hvad aendrede sig fra IS til OOS?

Hver metrik, ikke kun de fire kriterier. Flag: fald paa 30 %+ =
⚠ SVAEKKET, stigning paa 43 %+ = ↑ styrket.

### LONG — 4 svaekket, 0 styrket af 17 metrikker

| Metrik | IS | OOS | OOS/IS | Flag |
|---|---|---|---|---|
| Enrichment (fuld signatur) | 4.36 | 5.04 | 1.15 | stabil |
| Retningstraef | 0.98 | 0.99 | 1.01 | stabil |
| Praecision | 0.33 | 0.40 | 1.21 | stabil |
| Median size_atr | 3.99 | 3.33 | 0.84 | stabil |
| Median size_pt | 28.38 | 23.75 | 0.84 | stabil |
| Median varighed (min) | 135.00 | 90.00 | 0.67 | ⚠ SVAEKKET |
| Andel overshoot til modsat baand | 0.15 | 0.05 | 0.36 | ⚠ SVAEKKET |
| Lift ved ≥ 1.5 ATR | 4.36 | 5.04 | 1.15 | stabil |
| Lift ved ≥ 3.0 ATR | 4.92 | 5.41 | 1.10 | stabil |
| Lift ved ≥ 4.0 ATR | 5.72 | 4.23 | 0.74 | stabil |
| Lift ved ≥ 6.0 ATR | 7.55 | 3.98 | 0.53 | ⚠ SVAEKKET |
| Lift ved ≥ 8.0 ATR | 6.16 | 4.38 | 0.71 | stabil |
| Lift m. RVOL≥4 ved ≥ 1.5 ATR | 5.82 | 6.77 | 1.16 | stabil |
| Lift m. RVOL≥4 ved ≥ 3.0 ATR | 7.58 | 8.99 | 1.19 | stabil |
| Lift m. RVOL≥4 ved ≥ 4.0 ATR | 9.58 | 8.12 | 0.85 | stabil |
| Lift m. RVOL≥4 ved ≥ 6.0 ATR | 16.10 | 8.73 | 0.54 | ⚠ SVAEKKET |
| Lift m. RVOL≥4 ved ≥ 8.0 ATR | 15.01 | 12.00 | 0.80 | stabil |

### SHORT — 0 svaekket, 5 styrket af 17 metrikker

| Metrik | IS | OOS | OOS/IS | Flag |
|---|---|---|---|---|
| Enrichment (fuld signatur) | 3.82 | 4.51 | 1.18 | stabil |
| Retningstraef | 1.00 | 0.99 | 0.99 | stabil |
| Praecision | 0.28 | 0.33 | 1.18 | stabil |
| Median size_atr | 3.22 | 4.24 | 1.32 | stabil |
| Median size_pt | 21.00 | 32.12 | 1.53 | ↑ styrket |
| Median varighed (min) | 90.00 | 75.00 | 0.83 | stabil |
| Andel overshoot til modsat baand | 0.10 | 0.12 | 1.11 | stabil |
| Lift ved ≥ 1.5 ATR | 3.82 | 4.51 | 1.18 | stabil |
| Lift ved ≥ 3.0 ATR | 3.57 | 5.13 | 1.43 | ↑ styrket |
| Lift ved ≥ 4.0 ATR | 3.74 | 5.89 | 1.57 | ↑ styrket |
| Lift ved ≥ 6.0 ATR | 3.89 | 5.16 | 1.33 | stabil |
| Lift ved ≥ 8.0 ATR | 2.43 | 4.65 | 1.92 | ↑ styrket |
| Lift m. RVOL≥4 ved ≥ 1.5 ATR | 5.33 | 4.98 | 0.93 | stabil |
| Lift m. RVOL≥4 ved ≥ 3.0 ATR | 6.47 | 6.77 | 1.05 | stabil |
| Lift m. RVOL≥4 ved ≥ 4.0 ATR | 7.25 | 7.72 | 1.06 | stabil |
| Lift m. RVOL≥4 ved ≥ 6.0 ATR | 7.17 | 8.13 | 1.13 | stabil |
| Lift m. RVOL≥4 ved ≥ 8.0 ATR | 3.62 | 8.70 | 2.40 | ↑ styrket |

---

## 0c. Syntese: begge sider bevaeger sig samme vej

Hovedtallet gaar **samme vej for begge sider**: long op (x1.15), short op (x1.18). (11 af 17 mindre metrikker peger hver sin vej, men det er stoej i enkeltmaal, ikke i hovedtallet.)

**Det er en anden konklusion end med det lille kontrol-sample.** Der
faldt long og steg short, hvilket lignede et regimeskift hvor den
ene side tog over for den anden. Det moenster overlever ikke et
stoerre kontrol-sample — det var stoej i naevneren, ikke et signal i
markedet.

Konsekvens: der er ikke belaeg for at behandle long og short som
regime-modsaetninger. Til sammenligning det samlede tal:

| Metrik | IS | OOS |
|---|---|---|
| Fyrer (K) / retnings-events (E) | 189/3,549 | 189/3,658 |
| Kontrol-hits (retnings-vejet) | 189.8/14,709 | 159.0/14,791 |
| **Enrichment (long+short samlet)** | 4.12x [3.4–5.0] | 4.81x [3.9–5.9] |

Samlet: 4.12x → 4.81x (x1.17). Long alene x1.15, short alene x1.18. Poolingen stabiliserer altsaa ikke noget her: alle tre flytter sig omtrent lige meget og samme vej. Det er hvad man forventer naar udsvingene er stikproeve-stoej og ikke modsatrettede regimer.

---

## 0d. Krympede konfidensintervallerne?

Samme events, samme kriterier — kun kontrol-samplet er skiftet fra 3,932 til 29,500 barer. Det er naevneren i alle lift-tal, saa det er her praecisionen
af hele analysen bestemmes.

### LONG

| Metrik | Lille: hits | Lille: estimat | Stor: hits | Stor: estimat | CI-bredde stor/lille |
|---|---|---|---|---|---|
| IS enrichment | 26 | 4.75x [3.1–7.2] | 209 | 4.36x [3.5–5.5] | 0.48 |
| IS hale-lift ≥ 8.0 ATR | 7 | 14.21x [5.2–38.7] | 49 | 15.01x [7.2–31.2] | 0.72 |
| OOS enrichment | 29 | 3.90x [2.6–5.8] | 171 | 5.04x [4.0–6.4] | 0.74 |
| OOS hale-lift ≥ 8.0 ATR | 6 | 10.24x [3.2–33.2] | 39 | 12.00x [4.8–30.0] | 0.84 |

### SHORT

| Metrik | Lille: hits | Lille: estimat | Stor: hits | Stor: estimat | CI-bredde stor/lille |
|---|---|---|---|---|---|
| IS enrichment | 26 | 3.38x [2.2–5.2] | 170 | 3.82x [2.9–5.0] | 0.67 |
| IS hale-lift ≥ 8.0 ATR | 5 | 3.72x [0.7–19.1] | 38 | 3.62x [0.9–14.9] | 0.76 |
| OOS enrichment | 16 | 5.40x [3.2–9.2] | 146 | 4.51x [3.4–5.9] | 0.41 |
| OOS hale-lift ≥ 8.0 ATR | 8 | 5.57x [1.8–16.9] | 39 | 8.70x [3.5–21.9] | 1.22 |

### Er IS/OOS-forskellen nu statistisk paaviselig?

Dette er spoergsmaalet Fase A blev sat i vaerk for at afgoere.
Kriteriet er enkelt: overlapper IS- og OOS-intervallet stadig?

| Metrik | IS | OOS | Paaviselig forskel? |
|---|---|---|---|
| long · Enrichment | 4.36x [3.5–5.5] | 5.04x [4.0–6.4] | NEJ — overlapper |
| long · Hale-lift ≥ 8 ATR | 15.01x [7.2–31.2] | 12.00x [4.8–30.0] | NEJ — overlapper |
| short · Enrichment | 3.82x [2.9–5.0] | 4.51x [3.4–5.9] | NEJ — overlapper |
| short · Hale-lift ≥ 8 ATR | 3.62x [0.9–14.9] | 8.70x [3.5–21.9] | NEJ — overlapper |

---

## 1. Splittet

**IS/OOS** — graense 2025-06-29 20:52

| Halvdel | Fra | Til | Hverdage | Events op | Events ned | Kontrol-barer |
|---|---|---|---|---|---|---|
| IS | 2024-06-28 | 2025-06-29 | 261 | 1,806 | 1,743 | 14,709 |
| OOS | 2025-06-29 | 2026-07-01 | 262 | 1,905 | 1,753 | 14,791 |

**aar** — graense 2025-06-28 16:00

| Halvdel | Fra | Til | Hverdage | Events op | Events ned | Kontrol-barer |
|---|---|---|---|---|---|---|
| aar-1 | 2024-06-28 | 2025-06-28 | 261 | 1,806 | 1,743 | 14,709 |
| aar-2 | 2025-06-28 | 2026-07-01 | 262 | 1,905 | 1,753 | 14,791 |

**Krydstjek af prevalens-naevneren.** Specen estimerer antal 15m-barer
som hverdage x 23 t x 4. Jeg har ogsaa talt de faktiske barer i
1-min-filen:

| Halvdel | Estimat (spec) | Faktisk | Afvigelse |
|---|---|---|---|
| IS | 24,012 | 23,485 | +2.2 % |
| OOS | 24,104 | 23,732 | +1.6 % |

Estimatet rammer inden for et par procent. Og som noteret i koden gaar antallet
af barer alligevel ud af lift-beregningen — det paavirker kun
praecisionen, ikke liftet.

---

## 2. IS vs. OOS, side om side

### LONG (forventet retning: up)

| Metrik | IS | OOS |
|---|---|---|
| Retnings-event-starter (E) | 1,806 | 1,905 |
| Heraf fyrer signaturen (K) | 112 | 111 |
| Signaler i alt (begge retn.) | 114 | 112 |
| **Retningstraef** | 98.2 % | 99.1 % |
| Event-rate (K/E) | 6.20 % | 5.83 % |
| Kontrol-rate | 1.42 % (209/14709) | 1.16 % (171/14791) |
| **Enrichment** | 4.36x [3.5–5.5] | 5.04x [4.0–6.4] |
| Praecision | 32.8 % | 39.8 % |
| Praecision ±25 % barer | 26.3 % – 43.8 % | 31.9 % – 53.1 % |
| Basisrate | 7.5 % | 7.9 % |
| Lift (= enrichment) | 4.36x | 5.04x |

**Exit-profil** (kun de fyrende events i forventet retning):

| Metrik | IS | OOS |
|---|---|---|
| Median size_atr | 3.99 | 3.33 |
| Median size_pt | 28.4 | 23.8 |
| Median varighed (min) | 135 | 90 |
| Median z ved START | -2.65 | -2.70 |
| Median z ved SLUT | 0.36 | -0.27 |
| Andel der overshooter til modsat baand | 15.2 % | 5.4 % |
| Andel swing-metode (resten fwd) | 57.1 % | 55.0 % |

**Stoerrelses-taerskler — Fuld signatur**

| Taerskel | IS K/E | IS praec. | IS lift | OOS K/E | OOS praec. | OOS lift |
|---|---|---|---|---|---|---|
| ≥ 1.5 ATR | 112/1806 | 32.8 % | 4.36x [3.5–5.5] | 111/1905 | 39.8 % | 5.04x [4.0–6.4] |
| ≥ 3.0 ATR | 72/1030 | 21.1 % | 4.92x [3.8–6.4] | 66/1055 | 23.7 % | 5.41x [4.1–7.1] |
| ≥ 4.0 ATR | 56/689 | 16.4 % | 5.72x [4.3–7.6] | 32/654 | 11.5 % | 4.23x [2.9–6.1] |
| ≥ 6.0 ATR | 34/317 | 10.0 % | 7.55x [5.3–10.7] | 14/304 | 5.0 % | 3.98x [2.3–6.8] |
| ≥ 8.0 ATR | 14/160 | 4.1 % | 6.16x [3.7–10.3] | 8/158 | 2.9 % | 4.38x [2.2–8.7] |

*Kontrol-hits bag naevneren: IS 209, OOS 171 af hhv. 14709 og 14791 kontrol-barer.*

**Stoerrelses-taerskler — Signatur + RVOL >= 4.0 (hale-filter)**

| Taerskel | IS K/E | IS praec. | IS lift | OOS K/E | OOS praec. | OOS lift |
|---|---|---|---|---|---|---|
| ≥ 1.5 ATR | 35/1806 | 43.8 % | 5.82x [3.8–9.0] | 34/1905 | 53.5 % | 6.77x [4.3–10.7] |
| ≥ 3.0 ATR | 26/1030 | 32.5 % | 7.58x [4.7–12.1] | 25/1055 | 39.3 % | 8.99x [5.5–14.8] |
| ≥ 4.0 ATR | 22/689 | 27.5 % | 9.58x [5.8–15.8] | 14/654 | 22.0 % | 8.12x [4.4–14.9] |
| ≥ 6.0 ATR | 17/317 | 21.3 % | 16.10x [9.4–27.6] | 7/304 | 11.0 % | 8.73x [3.9–19.4] |
| ≥ 8.0 ATR | 8/160 | 10.0 % | 15.01x [7.2–31.2] | 5/158 | 7.9 % | 12.00x [4.8–30.0] |

*Kontrol-hits bag naevneren: IS 49, OOS 39 af hhv. 14709 og 14791 kontrol-barer.*

### SHORT (forventet retning: down)

| Metrik | IS | OOS |
|---|---|---|
| Retnings-event-starter (E) | 1,743 | 1,753 |
| Heraf fyrer signaturen (K) | 77 | 78 |
| Signaler i alt (begge retn.) | 77 | 79 |
| **Retningstraef** | 100.0 % | 98.7 % |
| Event-rate (K/E) | 4.42 % | 4.45 % |
| Kontrol-rate | 1.16 % (170/14709) | 0.99 % (146/14791) |
| **Enrichment** | 3.82x [2.9–5.0] | 4.51x [3.4–5.9] |
| Praecision | 27.7 % | 32.8 % |
| Praecision ±25 % barer | 22.2 % – 37.0 % | 26.2 % – 43.7 % |
| Basisrate | 7.3 % | 7.3 % |
| Lift (= enrichment) | 3.82x | 4.51x |

**Exit-profil** (kun de fyrende events i forventet retning):

| Metrik | IS | OOS |
|---|---|---|
| Median size_atr | 3.22 | 4.24 |
| Median size_pt | 21.0 | 32.1 |
| Median varighed (min) | 90 | 75 |
| Median z ved START | 2.63 | 2.52 |
| Median z ved SLUT | 0.60 | 0.21 |
| Andel der overshooter til modsat baand | 10.4 % | 11.5 % |
| Andel swing-metode (resten fwd) | 67.5 % | 55.1 % |

**Stoerrelses-taerskler — Fuld signatur**

| Taerskel | IS K/E | IS praec. | IS lift | OOS K/E | OOS praec. | OOS lift |
|---|---|---|---|---|---|---|
| ≥ 1.5 ATR | 77/1743 | 27.7 % | 3.82x [2.9–5.0] | 78/1753 | 32.8 % | 4.51x [3.4–5.9] |
| ≥ 3.0 ATR | 42/1017 | 15.1 % | 3.57x [2.6–5.0] | 51/1008 | 21.4 % | 5.13x [3.8–7.0] |
| ≥ 4.0 ATR | 30/694 | 10.8 % | 3.74x [2.6–5.5] | 40/688 | 16.8 % | 5.89x [4.2–8.3] |
| ≥ 6.0 ATR | 17/378 | 6.1 % | 3.89x [2.4–6.3] | 19/373 | 8.0 % | 5.16x [3.2–8.2] |
| ≥ 8.0 ATR | 6/214 | 2.2 % | 2.43x [1.1–5.4] | 10/218 | 4.2 % | 4.65x [2.5–8.7] |

*Kontrol-hits bag naevneren: IS 170, OOS 146 af hhv. 14709 og 14791 kontrol-barer.*

**Stoerrelses-taerskler — Signatur + RVOL >= 4.0 (hale-filter)**

| Taerskel | IS K/E | IS praec. | IS lift | OOS K/E | OOS praec. | OOS lift |
|---|---|---|---|---|---|---|
| ≥ 1.5 ATR | 24/1743 | 38.7 % | 5.33x [3.2–8.9] | 23/1753 | 36.2 % | 4.98x [3.0–8.3] |
| ≥ 3.0 ATR | 17/1017 | 27.4 % | 6.47x [3.7–11.4] | 18/1008 | 28.3 % | 6.77x [3.9–11.8] |
| ≥ 4.0 ATR | 13/694 | 21.0 % | 7.25x [3.9–13.5] | 14/688 | 22.0 % | 7.72x [4.2–14.1] |
| ≥ 6.0 ATR | 7/378 | 11.3 % | 7.17x [3.2–15.9] | 8/373 | 12.6 % | 8.13x [3.8–17.3] |
| ≥ 8.0 ATR | 2/214 | 3.2 % | 3.62x [0.9–14.9] | 5/218 | 7.9 % | 8.70x [3.5–21.9] |

*Kontrol-hits bag naevneren: IS 38, OOS 39 af hhv. 14709 og 14791 kontrol-barer.*

---

## 3. Kontrol-split: aar-1 vs. aar-2

⚠ **Dette split er degenereret paa dette datasaet.** Serien spaender
praecis to aar (2024-06-28 → 2026-07-01), saa graensen mellem aar 1 og
aar 2 falder inden for ét doegn af 50/50-kalendergraensen. De to splits
er i praksis det SAMME split — se de identiske n i afsnit 1. Det kan
derfor ikke bekraefte IS/OOS-resultatet; det gentager det.
Det uafhaengige robusthedstjek ligger i stedet i afsnit 3b (pr. kontrakt).

### LONG

| Metrik | Aar 1 | Aar 2 |
|---|---|---|
| Retnings-events (E) | 1,806 | 1,905 |
| Fyrer (K) | 112 | 111 |
| Retningstraef | 98.2 % | 99.1 % |
| Kontrol-rate | 1.42 % (209/14709) | 1.16 % (171/14791) |
| **Enrichment** | 4.36x [3.5–5.5] | 5.04x [4.0–6.4] |
| Praecision | 32.8 % | 39.8 % |
| Median z START → SLUT | -2.65 → 0.36 | -2.70 → -0.27 |

### SHORT

| Metrik | Aar 1 | Aar 2 |
|---|---|---|
| Retnings-events (E) | 1,743 | 1,753 |
| Fyrer (K) | 77 | 78 |
| Retningstraef | 100.0 % | 98.7 % |
| Kontrol-rate | 1.16 % (170/14709) | 0.99 % (146/14791) |
| **Enrichment** | 3.82x [2.9–5.0] | 4.51x [3.4–5.9] |
| Praecision | 27.7 % | 32.8 % |
| Median z START → SLUT | 2.63 → 0.60 | 2.52 → 0.21 |

---

## 3b. Robusthed pr. futures-kontrakt

Otte fulde kvartals-kontrakter (+ en 12-dages rest til sidst, som er
for kort til at laese noget ud af). Her ses om edgen findes hele vejen
igennem, eller kun i enkelte regimer. **Dette er det egentlige
uafhaengige robusthedstjek**, nu hvor aar-splittet viste sig at vaere
det samme som IS/OOS-splittet.

Enkelt-vaerdierne er stoejende (2–12 kontrol-hits bag hver). Det der
taeller er om fortegnet holder hele vejen — ikke niveauet i den
enkelte periode.

### LONG

| Kontrakt-periode | E | K | Kontrol-hits | Enrichment | Retningstraef |
|---|---|---|---|---|---|
| 2024-06→2024-09 | 438 | 29 | 43/3493 | 5.38x | 100 % |
| 2024-09→2024-12 | 437 | 33 | 56/3782 | 5.10x | 100 % |
| 2024-12→2025-03 | 440 | 27 | 61/3584 | 3.61x | 93 % |
| 2025-03→2025-06 | 459 | 23 | 49/3626 | 3.71x | 100 % |
| 2025-06→2025-09 | 454 | 23 | 42/3741 | 4.51x | 100 % |
| 2025-09→2025-12 | 451 | 26 | 46/3740 | 4.69x | 100 % |
| 2025-12→2026-03 | 462 | 36 | 51/3561 | 5.44x | 97 % |
| 2026-03→2026-06 | 513 | 25 | 31/3609 | 5.67x | 100 % |
| 2026-06→2026-07 *(rest)* | 57 | 1 | 1/364 | 6.39x | 100 % |

**Over de 8 fulde kontrakter:** enrichment fra 3.61x til 5.67x, median 4.89x. 8/8 over 1x, 8/8 over 3x.

### SHORT

| Kontrakt-periode | E | K | Kontrol-hits | Enrichment | Retningstraef |
|---|---|---|---|---|---|
| 2024-06→2024-09 | 405 | 19 | 40/3493 | 4.10x | 100 % |
| 2024-09→2024-12 | 427 | 19 | 39/3782 | 4.32x | 100 % |
| 2024-12→2025-03 | 420 | 16 | 32/3584 | 4.27x | 100 % |
| 2025-03→2025-06 | 467 | 23 | 52/3626 | 3.43x | 100 % |
| 2025-06→2025-09 | 417 | 10 | 48/3741 | 1.87x | 100 % |
| 2025-09→2025-12 | 437 | 20 | 24/3740 | 7.13x | 100 % |
| 2025-12→2026-03 | 418 | 20 | 33/3561 | 5.16x | 100 % |
| 2026-03→2026-06 | 452 | 27 | 43/3609 | 5.01x | 96 % |
| 2026-06→2026-07 *(rest)* | 53 | 1 | 5/364 | 1.37x | 100 % |

**Over de 8 fulde kontrakter:** enrichment fra 1.87x til 7.13x, median 4.29x. 8/8 over 1x, 7/8 over 3x.

---

## 4. Robusthedsgitter

Enrichment (og retningstraef) for nabo-vaerdier. **Dette er ikke en
soegning efter den bedste kombination** — det ville vaere kurve-
tilpasning. Det er et tjek af om edgen overlever smaa rykninger i
taersklerne. Kig efter et plateau, ikke efter et maksimum.

### LONG

| z | RVOL | Prik | IS K | IS enrich. | IS retn. | OOS K | OOS enrich. | OOS retn. |
|---|---|---|---|---|---|---|---|---|
| -1.5 | 1.25 | kun kraftig | 170 | 4.25x | 98 % | 165 | 4.42x | 98 % |
| -1.5 | 1.25 | kraftig + alm | 203 | 3.93x | 99 % | 206 | 4.20x | 99 % |
| -1.5 | 1.5 | kun kraftig | 152 | 4.33x | 98 % | 144 | 4.62x | 99 % |
| -1.5 | 1.5 | kraftig + alm | 180 | 4.06x | 98 % | 180 | 4.55x | 99 % |
| -1.5 | 2.0 | kun kraftig | 118 | 5.03x | 98 % | 101 | 4.84x | 99 % |
| -1.5 | 2.0 | kraftig + alm | 136 | 4.60x | 98 % | 125 | 4.69x | 99 % |
| -2.0 | 1.25 | kun kraftig | 122 | 4.38x | 98 % | 121 | 4.84x | 99 % |
| -2.0 | 1.25 | kraftig + alm | 139 | 3.85x | 99 % | 145 | 4.61x | 99 % |
| -2.0 ← | 1.5 | kun kraftig | 112 | 4.36x | 98 % | 111 | 5.04x | 99 % |
| -2.0 | 1.5 | kraftig + alm | 128 | 3.98x | 98 % | 134 | 4.93x | 99 % |
| -2.0 | 2.0 | kun kraftig | 94 | 5.28x | 98 % | 85 | 5.28x | 99 % |
| -2.0 | 2.0 | kraftig + alm | 106 | 4.77x | 98 % | 102 | 5.21x | 99 % |
| -2.5 | 1.25 | kun kraftig | 73 | 4.64x | 100 % | 68 | 4.98x | 100 % |
| -2.5 | 1.25 | kraftig + alm | 83 | 4.12x | 100 % | 81 | 5.28x | 100 % |
| -2.5 | 1.5 | kun kraftig | 70 | 4.71x | 100 % | 66 | 5.07x | 100 % |
| -2.5 | 1.5 | kraftig + alm | 79 | 4.23x | 100 % | 79 | 5.43x | 100 % |
| -2.5 | 2.0 | kun kraftig | 62 | 5.00x | 100 % | 55 | 5.21x | 100 % |
| -2.5 | 2.0 | kraftig + alm | 70 | 4.45x | 100 % | 65 | 5.49x | 100 % |

← = den praeregistrerede signatur.

### SHORT

| z | RVOL | Prik | IS K | IS enrich. | IS retn. | OOS K | OOS enrich. | OOS retn. |
|---|---|---|---|---|---|---|---|---|
| +1.5 | 1.25 | kun kraftig | 134 | 3.70x | 99 % | 132 | 3.88x | 99 % |
| +1.5 | 1.25 | kraftig + alm | 162 | 3.38x | 97 % | 161 | 3.71x | 96 % |
| +1.5 | 1.5 | kun kraftig | 109 | 4.09x | 98 % | 114 | 4.47x | 98 % |
| +1.5 | 1.5 | kraftig + alm | 131 | 3.61x | 97 % | 141 | 4.31x | 96 % |
| +1.5 | 2.0 | kun kraftig | 76 | 4.03x | 97 % | 83 | 4.73x | 99 % |
| +1.5 | 2.0 | kraftig + alm | 94 | 3.87x | 96 % | 101 | 4.61x | 95 % |
| +2.0 | 1.25 | kun kraftig | 91 | 3.56x | 100 % | 88 | 4.01x | 99 % |
| +2.0 | 1.25 | kraftig + alm | 108 | 3.40x | 100 % | 106 | 3.92x | 98 % |
| +2.0 ← | 1.5 | kun kraftig | 77 | 3.82x | 100 % | 78 | 4.51x | 99 % |
| +2.0 | 1.5 | kraftig + alm | 92 | 3.70x | 100 % | 95 | 4.43x | 98 % |
| +2.0 | 2.0 | kun kraftig | 55 | 3.71x | 100 % | 60 | 4.48x | 98 % |
| +2.0 | 2.0 | kraftig + alm | 67 | 3.77x | 100 % | 73 | 4.50x | 97 % |
| +2.5 | 1.25 | kun kraftig | 47 | 3.48x | 100 % | 45 | 3.87x | 100 % |
| +2.5 | 1.25 | kraftig + alm | 52 | 3.30x | 100 % | 52 | 3.82x | 98 % |
| +2.5 | 1.5 | kun kraftig | 43 | 3.63x | 100 % | 42 | 4.03x | 100 % |
| +2.5 | 1.5 | kraftig + alm | 48 | 3.52x | 100 % | 49 | 3.94x | 98 % |
| +2.5 | 2.0 | kun kraftig | 34 | 3.46x | 100 % | 34 | 3.93x | 100 % |
| +2.5 | 2.0 | kraftig + alm | 38 | 3.49x | 100 % | 39 | 3.74x | 98 % |

← = den praeregistrerede signatur.

---

## 5. Forbehold

1. **Kontrol-raten er den svageste led.** Enrichment er et forhold
   mellem to rater, og naevneren bygger paa et sample af
   kontrol-barer. For hale-filteret (RVOL ≥ 4) er der kun en
   haandfuld kontrol-hits pr. halvdel. Derfor staar der
   konfidensintervaller paa alle lift-tal — laes bredden, ikke kun
   punktestimatet. Et "12x" med interval 4–35 er ikke en maaling af
   12, det er "et sted mellem beskedent og enormt".
2. **Baseline er 4.000 barer, ikke alle barer.** Kontrol-raten er et
   stikproeve-estimat. Vil man snaevre intervallerne ind, er det
   baseline-samplet der skal vokse — flere events hjaelper ikke.
3. **Events er ikke uafhaengige.** Swing- og fwd-metoden finder tit
   den samme bevaegelse; vi deduplikerer paa (start-bar, retning), men
   naboliggende events overlapper stadig i tid. De effektive
   frihedsgrader er derfor lavere end n antyder, og intervallerne
   herover er i den forstand optimistiske.
4. **Retningstraeffet er delvist indbygget.** Et event ER en
   bevaegelse; signaturen maales paa event-starter. At ~99 % gaar den
   forventede vej siger at signaturen skelner retning godt — ikke at
   99 % af alle fyringer i markedet foelges af en bevaegelse. Det tal
   er praecisionen (~30 %), ikke retningstraeffet.
5. **Ingen omkostninger, intet slip.** Dette er signal-validering.
   Om edgen overlever kurtage og slippage er et P&L-spoergsmaal, og
   det er eksplicit ikke stillet her.


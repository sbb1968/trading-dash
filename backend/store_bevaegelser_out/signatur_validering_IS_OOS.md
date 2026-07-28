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
| Enrichment >= 3x (OOS) | ✔ | OOS 3.90x [2.6–5.8] (IS 4.75x [3.1–7.2]) |
| Retningstraef >= 95 % (OOS) | ✔ | OOS 99.1 % [95.1–99.8] (IS 98.2 %) |
| Praecision inden for IS +/-25 % | ✔ | OOS 30.8 % ligger inden for IS-intervallet 26.8–44.6 % (IS 35.7 %) |
| RVOL -> stoerrelse monotont stigende (OOS) | ✘ | lift pr. taerskel: 5.8x → 7.7x → 6.9x → 7.5x → 10.2x |

**Hvordan fejlede den?** (aendrer ikke dommen — kriterierne staar uroert)

- **Praecision OOS mod IS:** 30.8 % mod 35.7 % (-14 %). Inden for tolerancen (svagt fald).
- **Hale-lift, samlet tendens:** 5.8x → 10.2x (x1.8), rang-korrelation +0.70, 1 nedadgaaende trin ud af 4. Kriteriet kraever STRENG monotoni og fejler paa de enkelte trin, men den samlede retning er opad. Med en haandfuld kontrol-hits bag hvert punkt er et enkelt dyk fuldt foreneligt med ren stoej.
- **Kan IS og OOS skelnes?** Enrichment IS 4.75x [3.1–7.2] mod OOS 3.90x [2.6–5.8]. Intervallerne overlapper kraftigt — forskellen mellem de to halvdele er ikke statistisk paaviselig. Det udelukker ikke en aegte svaekkelse, men data kan ikke vise en.

### SHORT — **DUMPET** (3/4 kriterier opfyldt)

| Kriterium |  | Tal |
|---|---|---|
| Enrichment >= 3x (OOS) | ✔ | OOS 5.40x [3.2–9.2] (IS 3.38x [2.2–5.2]) |
| Retningstraef >= 95 % (OOS) | ✔ | OOS 98.7 % [93.2–99.8] (IS 100.0 %) |
| Praecision inden for IS +/-25 % | ✘ | OOS 39.3 % ligger over IS-intervallet 18.4–30.7 % (IS 24.5 %) |
| RVOL -> stoerrelse monotont stigende (OOS) | ✔ | lift pr. taerskel: 3.2x → 4.3x → 4.9x → 5.2x → 5.6x |

**Hvordan fejlede den?** (aendrer ikke dommen — kriterierne staar uroert)

- **Praecision OOS mod IS:** 39.3 % mod 24.5 % (+60 %). Bemaerk retningen: praecisionen er **hoejere** ud af proeve. Kriteriet er tosidet som praeregistreret og fejler derfor, men specens egen parentes siger "dvs. ikke et kollaps" — og et kollaps er det modsatte af det der er sket.
- **Hale-lift, samlet tendens:** 3.2x → 5.6x (x1.7), rang-korrelation +1.00, 0 nedadgaaende trin ud af 4. Monotont.
- **Kan IS og OOS skelnes?** Enrichment IS 3.38x [2.2–5.2] mod OOS 5.40x [3.2–9.2]. Intervallerne overlapper kraftigt — forskellen mellem de to halvdele er ikke statistisk paaviselig. Det udelukker ikke en aegte svaekkelse, men data kan ikke vise en.

---

## 0b. Hvad aendrede sig fra IS til OOS?

Hver metrik, ikke kun de fire kriterier. Flag: fald paa 30 %+ =
⚠ SVAEKKET, stigning paa 43 %+ = ↑ styrket.

### LONG — 6 svaekket, 0 styrket af 17 metrikker

| Metrik | IS | OOS | OOS/IS | Flag |
|---|---|---|---|---|
| Enrichment (fuld signatur) | 4.75 | 3.90 | 0.82 | stabil |
| Retningstraef | 0.98 | 0.99 | 1.01 | stabil |
| Praecision | 0.36 | 0.31 | 0.86 | stabil |
| Median size_atr | 3.99 | 3.33 | 0.84 | stabil |
| Median size_pt | 28.38 | 23.75 | 0.84 | stabil |
| Median varighed (min) | 135.00 | 90.00 | 0.67 | ⚠ SVAEKKET |
| Andel overshoot til modsat baand | 0.15 | 0.05 | 0.36 | ⚠ SVAEKKET |
| Lift ved ≥ 1.5 ATR | 4.75 | 3.90 | 0.82 | stabil |
| Lift ved ≥ 3.0 ATR | 5.35 | 4.19 | 0.78 | stabil |
| Lift ved ≥ 4.0 ATR | 6.22 | 3.28 | 0.53 | ⚠ SVAEKKET |
| Lift ved ≥ 6.0 ATR | 8.21 | 3.08 | 0.38 | ⚠ SVAEKKET |
| Lift ved ≥ 8.0 ATR | 6.70 | 3.39 | 0.51 | ⚠ SVAEKKET |
| Lift m. RVOL≥4 ved ≥ 1.5 ATR | 5.51 | 5.78 | 1.05 | stabil |
| Lift m. RVOL≥4 ved ≥ 3.0 ATR | 7.18 | 7.67 | 1.07 | stabil |
| Lift m. RVOL≥4 ved ≥ 4.0 ATR | 9.08 | 6.93 | 0.76 | stabil |
| Lift m. RVOL≥4 ved ≥ 6.0 ATR | 15.25 | 7.45 | 0.49 | ⚠ SVAEKKET |
| Lift m. RVOL≥4 ved ≥ 8.0 ATR | 14.21 | 10.24 | 0.72 | stabil |

### SHORT — 3 svaekket, 9 styrket af 17 metrikker

| Metrik | IS | OOS | OOS/IS | Flag |
|---|---|---|---|---|
| Enrichment (fuld signatur) | 3.38 | 5.40 | 1.60 | ↑ styrket |
| Retningstraef | 1.00 | 0.99 | 0.99 | stabil |
| Praecision | 0.25 | 0.39 | 1.60 | ↑ styrket |
| Median size_atr | 3.22 | 4.24 | 1.32 | stabil |
| Median size_pt | 21.00 | 32.12 | 1.53 | ↑ styrket |
| Median varighed (min) | 90.00 | 75.00 | 0.83 | stabil |
| Andel overshoot til modsat baand | 0.10 | 0.12 | 1.11 | stabil |
| Lift ved ≥ 1.5 ATR | 3.38 | 5.40 | 1.60 | ↑ styrket |
| Lift ved ≥ 3.0 ATR | 3.16 | 6.14 | 1.94 | ↑ styrket |
| Lift ved ≥ 4.0 ATR | 3.31 | 7.06 | 2.13 | ↑ styrket |
| Lift ved ≥ 6.0 ATR | 3.44 | 6.18 | 1.80 | ↑ styrket |
| Lift ved ≥ 8.0 ATR | 2.15 | 5.57 | 2.59 | ↑ styrket |
| Lift m. RVOL≥4 ved ≥ 1.5 ATR | 5.48 | 3.18 | 0.58 | ⚠ SVAEKKET |
| Lift m. RVOL≥4 ved ≥ 3.0 ATR | 6.65 | 4.33 | 0.65 | ⚠ SVAEKKET |
| Lift m. RVOL≥4 ved ≥ 4.0 ATR | 7.46 | 4.94 | 0.66 | ⚠ SVAEKKET |
| Lift m. RVOL≥4 ved ≥ 6.0 ATR | 7.37 | 5.21 | 0.71 | stabil |
| Lift m. RVOL≥4 ved ≥ 8.0 ATR | 3.72 | 5.57 | 1.50 | ↑ styrket |

---

## 0c. Syntese: long og short bevaeger sig modsat

Af de 17 metrikker der kan sammenlignes, gaar **14** den ENE vej for long og den ANDEN vej for short.

Se moensteret i tabellerne ovenfor: hver eneste stoerrelses-lift
falder for long og stiger for short. To uafhaengige signaler ville
ikke svinge i modfase saa systematisk. Den enkle forklaring er at det
ikke er signalernes kvalitet der aendrer sig, men **markedet**: den
foerste halvdel gav de bedste op-bevaegelser, den anden de bedste
ned-bevaegelser.

Konsekvensen er vigtig for hvordan resultatet skal bruges: *ingen af
siderne er vist at vaere regime-uafhaengig*. Det der ser stabilt ud, er
de to tilsammen:

| Metrik | IS | OOS |
|---|---|---|
| Fyrer (K) / retnings-events (E) | 189/3,549 | 189/3,658 |
| Kontrol-hits (retnings-vejet) | 26.0/1,990 | 22.8/1,942 |
| **Enrichment (long+short samlet)** | 4.08x [2.7–6.1] | 4.36x [2.8–6.7] |

Samlet flytter enrichment sig kun fra 4.08x til 4.36x — mod 4.75x→3.90x for long alene og 3.38x→5.40x for short alene. Det samlede signal er altsaa markant mere stabilt end hver af halvdelene. Det er konsistent med at edgen er aegte, men at fordelingen mellem long og short svinger med regimet.

---

## 1. Splittet

**IS/OOS** — graense 2025-06-29 20:30

| Halvdel | Fra | Til | Hverdage | Events op | Events ned | Kontrol-barer |
|---|---|---|---|---|---|---|
| IS | 2024-06-28 | 2025-06-29 | 261 | 1,806 | 1,743 | 1,990 |
| OOS | 2025-06-29 | 2026-07-01 | 262 | 1,905 | 1,753 | 1,942 |

**aar** — graense 2025-06-28 16:00

| Halvdel | Fra | Til | Hverdage | Events op | Events ned | Kontrol-barer |
|---|---|---|---|---|---|---|
| aar-1 | 2024-06-28 | 2025-06-28 | 261 | 1,806 | 1,743 | 1,990 |
| aar-2 | 2025-06-28 | 2026-07-01 | 262 | 1,905 | 1,753 | 1,942 |

**Krydstjek af prevalens-naevneren.** Specen estimerer antal 15m-barer
som hverdage x 23 t x 4. Jeg har ogsaa talt de faktiske barer i
1-min-filen:

| Halvdel | Estimat (spec) | Faktisk | Afvigelse |
|---|---|---|---|
| IS | 24,012 | 23,485 | +2.2 % |
| OOS | 24,104 | 23,729 | +1.6 % |

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
| Kontrol-rate | 1.31 % (26/1990) | 1.49 % (29/1942) |
| **Enrichment** | 4.75x [3.1–7.2] | 3.90x [2.6–5.8] |
| Praecision | 35.7 % | 30.8 % |
| Praecision ±25 % barer | 28.6 % – 47.6 % | 24.7 % – 41.1 % |
| Basisrate | 7.5 % | 7.9 % |
| Lift (= enrichment) | 4.75x | 3.90x |

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
| ≥ 1.5 ATR | 112/1806 | 35.7 % | 4.75x [3.1–7.2] | 111/1905 | 30.8 % | 3.90x [2.6–5.8] |
| ≥ 3.0 ATR | 72/1030 | 23.0 % | 5.35x [3.4–8.3] | 66/1055 | 18.3 % | 4.19x [2.7–6.4] |
| ≥ 4.0 ATR | 56/689 | 17.9 % | 6.22x [3.9–9.8] | 32/654 | 8.9 % | 3.28x [2.0–5.4] |
| ≥ 6.0 ATR | 34/317 | 10.8 % | 8.21x [5.0–13.5] | 14/304 | 3.9 % | 3.08x [1.6–5.8] |
| ≥ 8.0 ATR | 14/160 | 4.5 % | 6.70x [3.6–12.6] | 8/158 | 2.2 % | 3.39x [1.6–7.3] |

*Kontrol-hits bag naevneren: IS 26, OOS 29 af hhv. 1990 og 1942 kontrol-barer.*

**Stoerrelses-taerskler — Signatur + RVOL >= 4.0 (hale-filter)**

| Taerskel | IS K/E | IS praec. | IS lift | OOS K/E | OOS praec. | OOS lift |
|---|---|---|---|---|---|---|
| ≥ 1.5 ATR | 35/1806 | 41.4 % | 5.51x [2.5–12.4] | 34/1905 | 45.7 % | 5.78x [2.4–13.7] |
| ≥ 3.0 ATR | 26/1030 | 30.8 % | 7.18x [3.1–16.5] | 25/1055 | 33.6 % | 7.67x [3.2–18.6] |
| ≥ 4.0 ATR | 22/689 | 26.0 % | 9.08x [3.9–21.2] | 14/654 | 18.8 % | 6.93x [2.7–18.0] |
| ≥ 6.0 ATR | 17/317 | 20.1 % | 15.25x [6.4–36.5] | 7/304 | 9.4 % | 7.45x [2.5–22.0] |
| ≥ 8.0 ATR | 8/160 | 9.5 % | 14.21x [5.2–38.7] | 5/158 | 6.7 % | 10.24x [3.2–33.2] |

*Kontrol-hits bag naevneren: IS 7, OOS 6 af hhv. 1990 og 1942 kontrol-barer.*

### SHORT (forventet retning: down)

| Metrik | IS | OOS |
|---|---|---|
| Retnings-event-starter (E) | 1,743 | 1,753 |
| Heraf fyrer signaturen (K) | 77 | 78 |
| Signaler i alt (begge retn.) | 77 | 79 |
| **Retningstraef** | 100.0 % | 98.7 % |
| Event-rate (K/E) | 4.42 % | 4.45 % |
| Kontrol-rate | 1.31 % (26/1990) | 0.82 % (16/1942) |
| **Enrichment** | 3.38x [2.2–5.2] | 5.40x [3.2–9.2] |
| Praecision | 24.5 % | 39.3 % |
| Praecision ±25 % barer | 19.6 % – 32.7 % | 31.4 % – 52.4 % |
| Basisrate | 7.3 % | 7.3 % |
| Lift (= enrichment) | 3.38x | 5.40x |

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
| ≥ 1.5 ATR | 77/1743 | 24.5 % | 3.38x [2.2–5.2] | 78/1753 | 39.3 % | 5.40x [3.2–9.2] |
| ≥ 3.0 ATR | 42/1017 | 13.4 % | 3.16x [1.9–5.1] | 51/1008 | 25.7 % | 6.14x [3.5–10.7] |
| ≥ 4.0 ATR | 30/694 | 9.6 % | 3.31x [2.0–5.6] | 40/688 | 20.1 % | 7.06x [4.0–12.5] |
| ≥ 6.0 ATR | 17/378 | 5.4 % | 3.44x [1.9–6.3] | 19/373 | 9.6 % | 6.18x [3.2–11.9] |
| ≥ 8.0 ATR | 6/214 | 1.9 % | 2.15x [0.9–5.2] | 10/218 | 5.0 % | 5.57x [2.6–12.1] |

*Kontrol-hits bag naevneren: IS 26, OOS 16 af hhv. 1990 og 1942 kontrol-barer.*

**Stoerrelses-taerskler — Signatur + RVOL >= 4.0 (hale-filter)**

| Taerskel | IS K/E | IS praec. | IS lift | OOS K/E | OOS praec. | OOS lift |
|---|---|---|---|---|---|---|
| ≥ 1.5 ATR | 24/1743 | 39.8 % | 5.48x [2.1–14.3] | 23/1753 | 23.2 % | 3.18x [1.4–7.1] |
| ≥ 3.0 ATR | 17/1017 | 28.2 % | 6.65x [2.5–18.0] | 18/1008 | 18.1 % | 4.33x [1.9–9.9] |
| ≥ 4.0 ATR | 13/694 | 21.5 % | 7.46x [2.7–20.8] | 14/688 | 14.1 % | 4.94x [2.1–11.7] |
| ≥ 6.0 ATR | 7/378 | 11.6 % | 7.37x [2.4–23.1] | 8/373 | 8.1 % | 5.21x [2.0–13.8] |
| ≥ 8.0 ATR | 2/214 | 3.3 % | 3.72x [0.7–19.1] | 5/218 | 5.0 % | 5.57x [1.8–16.9] |

*Kontrol-hits bag naevneren: IS 5, OOS 8 af hhv. 1990 og 1942 kontrol-barer.*

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
| Kontrol-rate | 1.31 % (26/1990) | 1.49 % (29/1942) |
| **Enrichment** | 4.75x [3.1–7.2] | 3.90x [2.6–5.8] |
| Praecision | 35.7 % | 30.8 % |
| Median z START → SLUT | -2.65 → 0.36 | -2.70 → -0.27 |

### SHORT

| Metrik | Aar 1 | Aar 2 |
|---|---|---|
| Retnings-events (E) | 1,743 | 1,753 |
| Fyrer (K) | 77 | 78 |
| Retningstraef | 100.0 % | 98.7 % |
| Kontrol-rate | 1.31 % (26/1990) | 0.82 % (16/1942) |
| **Enrichment** | 3.38x [2.2–5.2] | 5.40x [3.2–9.2] |
| Praecision | 24.5 % | 39.3 % |
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
| 2024-06→2024-09 | 438 | 29 | 6/450 | 4.97x | 100 % |
| 2024-09→2024-12 | 437 | 33 | 5/475 | 7.17x | 100 % |
| 2024-12→2025-03 | 440 | 27 | 7/518 | 4.54x | 93 % |
| 2025-03→2025-06 | 459 | 23 | 8/511 | 3.20x | 100 % |
| 2025-06→2025-09 | 454 | 23 | 5/527 | 5.34x | 100 % |
| 2025-09→2025-12 | 451 | 26 | 12/487 | 2.34x | 100 % |
| 2025-12→2026-03 | 462 | 36 | 7/448 | 4.99x | 97 % |
| 2026-03→2026-06 | 513 | 25 | 5/457 | 4.45x | 100 % |
| 2026-06→2026-07 *(rest)* | 57 | 1 | 0/59 | —x | 100 % |

**Over de 8 fulde kontrakter:** enrichment fra 2.34x til 7.17x, median 4.75x. 8/8 over 1x, 7/8 over 3x.

### SHORT

| Kontrakt-periode | E | K | Kontrol-hits | Enrichment | Retningstraef |
|---|---|---|---|---|---|
| 2024-06→2024-09 | 405 | 19 | 6/450 | 3.52x | 100 % |
| 2024-09→2024-12 | 427 | 19 | 5/475 | 4.23x | 100 % |
| 2024-12→2025-03 | 420 | 16 | 5/518 | 3.95x | 100 % |
| 2025-03→2025-06 | 467 | 23 | 9/511 | 2.80x | 100 % |
| 2025-06→2025-09 | 417 | 10 | 4/527 | 3.16x | 100 % |
| 2025-09→2025-12 | 437 | 20 | 4/487 | 5.57x | 100 % |
| 2025-12→2026-03 | 418 | 20 | 2/448 | 10.72x | 100 % |
| 2026-03→2026-06 | 452 | 27 | 6/457 | 4.55x | 96 % |
| 2026-06→2026-07 *(rest)* | 53 | 1 | 1/59 | 1.11x | 100 % |

**Over de 8 fulde kontrakter:** enrichment fra 2.80x til 10.72x, median 4.09x. 8/8 over 1x, 7/8 over 3x.

---

## 4. Robusthedsgitter

Enrichment (og retningstraef) for nabo-vaerdier. **Dette er ikke en
soegning efter den bedste kombination** — det ville vaere kurve-
tilpasning. Det er et tjek af om edgen overlever smaa rykninger i
taersklerne. Kig efter et plateau, ikke efter et maksimum.

### LONG

| z | RVOL | Prik | IS K | IS enrich. | IS retn. | OOS K | OOS enrich. | OOS retn. |
|---|---|---|---|---|---|---|---|---|
| -1.5 | 1.25 | kun kraftig | 170 | 4.57x | 98 % | 165 | 3.30x | 98 % |
| -1.5 | 1.25 | kraftig + alm | 203 | 4.22x | 99 % | 206 | 3.39x | 99 % |
| -1.5 | 1.5 | kun kraftig | 152 | 4.53x | 98 % | 144 | 3.34x | 99 % |
| -1.5 | 1.5 | kraftig + alm | 180 | 4.22x | 98 % | 180 | 3.74x | 99 % |
| -1.5 | 2.0 | kun kraftig | 118 | 6.84x | 98 % | 101 | 3.32x | 99 % |
| -1.5 | 2.0 | kraftig + alm | 136 | 5.76x | 98 % | 125 | 3.64x | 99 % |
| -2.0 | 1.25 | kun kraftig | 122 | 4.98x | 98 % | 121 | 3.74x | 99 % |
| -2.0 | 1.25 | kraftig + alm | 139 | 4.25x | 99 % | 145 | 4.00x | 99 % |
| -2.0 ← | 1.5 | kun kraftig | 112 | 4.75x | 98 % | 111 | 3.90x | 99 % |
| -2.0 | 1.5 | kraftig + alm | 128 | 4.15x | 98 % | 134 | 4.27x | 99 % |
| -2.0 | 2.0 | kun kraftig | 94 | 6.91x | 98 % | 85 | 3.94x | 99 % |
| -2.0 | 2.0 | kraftig + alm | 106 | 5.56x | 98 % | 102 | 4.33x | 99 % |
| -2.5 | 1.25 | kun kraftig | 73 | 4.73x | 100 % | 68 | 3.47x | 100 % |
| -2.5 | 1.25 | kraftig + alm | 83 | 3.98x | 100 % | 81 | 3.75x | 100 % |
| -2.5 | 1.5 | kun kraftig | 70 | 4.54x | 100 % | 66 | 3.54x | 100 % |
| -2.5 | 1.5 | kraftig + alm | 79 | 3.78x | 100 % | 79 | 3.83x | 100 % |
| -2.5 | 2.0 | kun kraftig | 62 | 5.69x | 100 % | 55 | 3.74x | 100 % |
| -2.5 | 2.0 | kraftig + alm | 70 | 4.29x | 100 % | 65 | 4.14x | 100 % |

← = den praeregistrerede signatur.

### SHORT

| z | RVOL | Prik | IS K | IS enrich. | IS retn. | OOS K | OOS enrich. | OOS retn. |
|---|---|---|---|---|---|---|---|---|
| +1.5 | 1.25 | kun kraftig | 134 | 4.37x | 99 % | 132 | 5.22x | 99 % |
| +1.5 | 1.25 | kraftig + alm | 162 | 3.70x | 97 % | 161 | 5.25x | 96 % |
| +1.5 | 1.5 | kun kraftig | 109 | 4.15x | 98 % | 114 | 6.01x | 98 % |
| +1.5 | 1.5 | kraftig + alm | 131 | 3.56x | 97 % | 141 | 5.79x | 96 % |
| +1.5 | 2.0 | kun kraftig | 76 | 3.77x | 97 % | 83 | 6.13x | 99 % |
| +1.5 | 2.0 | kraftig + alm | 94 | 3.70x | 96 % | 101 | 5.89x | 95 % |
| +2.0 | 1.25 | kun kraftig | 91 | 3.46x | 100 % | 88 | 4.87x | 99 % |
| +2.0 | 1.25 | kraftig + alm | 108 | 3.24x | 100 % | 106 | 5.11x | 98 % |
| +2.0 ← | 1.5 | kun kraftig | 77 | 3.38x | 100 % | 78 | 5.40x | 99 % |
| +2.0 | 1.5 | kraftig + alm | 92 | 3.28x | 100 % | 95 | 5.54x | 98 % |
| +2.0 | 2.0 | kun kraftig | 55 | 2.99x | 100 % | 60 | 5.11x | 98 % |
| +2.0 | 2.0 | kraftig + alm | 67 | 3.33x | 100 % | 73 | 5.05x | 97 % |
| +2.5 | 1.25 | kun kraftig | 47 | 2.98x | 100 % | 45 | 4.15x | 100 % |
| +2.5 | 1.25 | kraftig + alm | 52 | 2.97x | 100 % | 52 | 4.11x | 98 % |
| +2.5 | 1.5 | kun kraftig | 43 | 2.89x | 100 % | 42 | 4.65x | 100 % |
| +2.5 | 1.5 | kraftig + alm | 48 | 2.88x | 100 % | 49 | 4.52x | 98 % |
| +2.5 | 2.0 | kun kraftig | 34 | 2.59x | 100 % | 34 | 4.19x | 100 % |
| +2.5 | 2.0 | kraftig + alm | 38 | 2.89x | 100 % | 39 | 3.93x | 98 % |

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


# Store MES-bevaegelser — metode-sammenligning

Genereret af `analyse_store_bevaegelser.py`. Rent beskrivende datasaet —
ingen strategi, ingen backtest, ingen forudsigelse.

**Kilde:** `data_harvest\mes_m2k_stitched\MES_1min.csv`
**Detekterings-timeframe:** 15m (47,217 barer efter 500 barers opvarmning)
**Periode (dansk tid):** 2024-06-28 19:00:00+02:00 → 2026-06-30 22:45:00+02:00
**Snapshot-timeframes:** 1h, 15m, 5m, 3m, 2m
**Kontrakt-rul:** 8 rul · 16 rul-krydsende events fjernet

---

## 1. Antal events

| | Metode A (swing) | Metode B (fwd) |
|---|---|---|
| Parametre | pivots L=R=3, ben >= 1.5 x ATR | 20 barer frem, >= 2.0 x ATR |
| Events i alt | 5,138 | 3,087 |
| — op | 2,666 | 1,579 |
| — ned | 2,472 | 1,508 |
| Median size_atr | 3.12 | 3.86 |
| Median varighed (barer) | 6.0 | 12.0 |

Baseline-sample (almindelige, ikke-event barer): **4,000**.

---

## 2. Overlap

Match = samme retning OG start inden for +/-3 barer. Matchet er
ikke 1:1 (én swing kan daekke flere fwd-events), derfor taelles begge veje.

| | matchet | kun denne metode | andel matchet |
|---|---|---|---|
| Metode A (swing) | 2,172 | 2,966 | 42.3 % |
| Metode B (fwd)   | 2,167 | 920 | 70.2 % |

**Hvorfor de er uenige — de to definitioner spoerger om forskellige ting:**

- *Swing* skaerer prisen i skiftende ben og spoerger "hvor stort blev dette
  ben?". Starten er en bekraeftet pivot — altsaa et vendepunkt.
- *Fwd* spoerger for HVER bar "kom der 2 x ATR inden for 20 barer?". Starten er
  en vilkaarlig bar, ikke noedvendigvis et vendepunkt. Den fanger derfor ogsaa
  fortsaettelser midt i et ben, og misser lange, langsomme ben der bruger mere
  end 20 barer paa at levere bevaegelsen.

**Kun fundet af Metode A (swing)** (de 10 stoerste af 2966):

| start (dansk tid) | retning | size_pt | size_atr | varighed_bars |
|---|---|---|---|---|
| 2025-10-10 15:45 | down | 266.25 | 45.01 | 28 |
| 2024-12-18 18:15 | down | 234.25 | 43.10 | 15 |
| 2025-01-15 09:00 | up | 100.50 | 35.78 | 23 |
| 2026-02-03 13:45 | down | 137.00 | 29.84 | 25 |
| 2024-10-23 14:15 | down | 80.50 | 24.85 | 24 |
| 2024-12-11 08:30 | up | 57.00 | 24.69 | 42 |
| 2026-06-09 15:45 | down | 244.00 | 24.23 | 11 |
| 2026-02-12 15:30 | down | 133.00 | 22.26 | 15 |
| 2025-04-30 13:00 | down | 124.75 | 21.84 | 11 |
| 2024-12-30 11:15 | down | 97.00 | 21.62 | 20 |

**Kun fundet af Metode B (fwd)** (de 10 stoerste af 920):

| start (dansk tid) | retning | size_pt | size_atr | varighed_bars |
|---|---|---|---|---|
| 2025-11-28 12:15 | up | 20.75 | 80.61 | 20 |
| 2024-07-11 14:00 | down | 49.50 | 32.86 | 20 |
| 2025-01-15 12:15 | up | 99.25 | 32.00 | 16 |
| 2026-06-09 14:15 | down | 196.50 | 28.02 | 17 |
| 2026-02-03 15:00 | down | 125.75 | 27.14 | 20 |
| 2024-12-30 12:30 | down | 96.25 | 25.94 | 15 |
| 2026-04-17 13:30 | up | 86.00 | 23.89 | 20 |
| 2024-12-11 13:00 | up | 48.75 | 23.80 | 20 |
| 2025-04-30 12:00 | down | 120.50 | 22.51 | 15 |
| 2025-05-12 08:00 | up | 102.25 | 20.26 | 20 |

---

## 3. Fordeling paa dansk time-paa-doegnet

| dansk_time | fwd | swing |
|---|---|---|
| 0 | 65 | 205 |
| 1 | 104 | 135 |
| 2 | 83 | 180 |
| 3 | 72 | 161 |
| 4 | 73 | 165 |
| 5 | 85 | 139 |
| 6 | 151 | 186 |
| 7 | 197 | 223 |
| 8 | 201 | 236 |
| 9 | 168 | 306 |
| 10 | 107 | 250 |
| 11 | 114 | 237 |
| 12 | 160 | 239 |
| 13 | 184 | 259 |
| 14 | 236 | 264 |
| 15 | 243 | 316 |
| 16 | 161 | 284 |
| 17 | 103 | 225 |
| 18 | 143 | 249 |
| 19 | 120 | 254 |
| 20 | 141 | 259 |
| 21 | 113 | 271 |
| 22 | 60 | 86 |
| 23 | 3 | 9 |

---

## 4. Fordeling paa ugedag

| ugedag | fwd | swing |
|---|---|---|
| Mandag | 587 | 1029 |
| Tirsdag | 652 | 1094 |
| Onsdag | 617 | 1050 |
| Torsdag | 611 | 976 |
| Fredag | 618 | 986 |
| Soendag | 2 | 3 |

---

## 5. Stoerrelse

| metode | retning | n | median size_pt | median size_atr | 90%-fraktil size_atr |
|---|---|---|---|---|---|
| swing | up | 2,666 | 22.25 | 3.10 | 6.72 |
| swing | down | 2,472 | 22.00 | 3.13 | 7.54 |
| fwd | up | 1,579 | 25.75 | 3.78 | 8.55 |
| fwd | down | 1,508 | 26.00 | 3.95 | 10.51 |

---

## 6. Indikator-parametre brugt

z=30 (population-std) · ADX(14,14) · ATR(14, Wilder) ·
CMF(20) · RVOL(20, foregaaende barer) · RSI(14) ·
MACD(12,26,9) · StochRSI(14/14/3/3, log-skala) ·
WaveTrend(9,12,3) · VWAP ankret 18:00 ET (CME-doegnet).

---

## 7. Forbehold der skal med til analysen

1. **Prik-kategorierne er den eneste antagelse.** Cipher B's .pine-fil ligger
   ikke i repoet; formlerne er taget fra `docs_src/market_cipher_b_teknisk.md`
   (skrevet direkte ud af Pine-scriptet). Mapningen af de tre groen/roed-styrker
   (`udvandet` = almindeligt WT-kryds, `alm` = divergens-prik, `kraftig` = stor
   cirkel i overkoebt/oversolgt) er udledt af dokumentets egen styrke-beskrivelse
   og skal bekraeftes visuelt i TradingView-tjekket. Aendres den, aendres kun
   `_DOT_PRIORITY` i `store_bevaegelser_lib.py`.
2. **`bars_to_dot` taeller til der hvor prikken TEGNES**, men prikken tages kun
   med hvis den var BEKRAEFTET paa eller foer start-baren. Divergens-prikker
   har 2 barers bekraeftelses-forsinkelse (5-bars fraktal); krydsprikker har
   ingen. Ingen future leak, men tallet passer med det man taeller paa charten.
3. **START-snapshottet bruger kun data til og med start-barens lukning** — paa
   alle fem timeframes, via "previous"-konventionen. Bevaegelsens stoerrelse og
   retning er fremadrettet, men det er labelen, ikke en feature.
4. **Metode A's startpunkt er en pivot**, som foerst bekraeftes 3 barer
   senere. Selve *definitionen* af hvor benet begynder er dermed fremadskuende.
   Det er i orden for moenster-beskrivelse, men et handelssystem kan ikke
   handle paa den bar i realtid.
5. **Baseline har ingen `_end`-kolonner.** En tilfaeldig bar har ingen naturlig
   udmattelse; et vilkaarligt slut-punkt ville vaere et skjult valg. Baseline
   er alle detekterings-barer der IKKE er en event-start (ikke "alle barer
   uden for et event" — swing-benene ligger i forlaengelse af hinanden og
   daekker naesten hele serien).
6. **Kontrakt-rul — laes denne foer du analyserer.** Den kontinuerlige
   MES-serie er raw-stitched, ikke back-adjusted: prisen springer med
   carry-spreadet ved hvert kvartals-rul. Events der SPAENDER over et rul er
   fjernet (8 rul · 16 rul-krydsende events fjernet). Men indikatorerne er ogsaa forurenede i barerne
   EFTER et rul, hvor de rullende vinduer stadig indeholder den gamle
   kontrakts priser. Det kan ses direkte i data: 8 events har
   |`z_15m_start`| = √29 = 5.385 — den stoerst MULIGE z naar de 29
   foregaaende closes er identiske, hvilket kun sker i den doede aabning af
   en ny kontrakt. 7 af dem ligger inden for 100
   barer efter et rul.
   **Anbefalet filter:** `bars_since_roll > 100` — fjerner
   132 af 8225 events (1.6 %).
7. **Degenereret volatilitet.** I doede nattetimer kan ATR falde under ét
   tick (0.25 pt). Alt der divideres med ATR eksploderer saa. `vwap_dist_atr`
   saettes til `na` under den graense, og events med start-ATR under ét tick
   detekteres slet ikke.
8. **Overnight- og weekend-huller er IKKE filtreret.** Et ben kan spaende hen
   over en session-pause. `varighed_min` er vaegur-tid, ikke handelstid.

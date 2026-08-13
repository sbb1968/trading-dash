# MNQ 1-min høst — kørselsvejledning

Målt 12-08-2026. **Læs afsnit 0 først** — ønsket "samme mængde som MES" kan
ikke opfyldes, og grunden er ikke noget vi kan kode os ud af.

---

## 0. Hvad der er muligt — og hvorfor ikke mere

MES rækker tilbage til **2024-06-21**. De gamle MES-kontrakter blev høstet
dengang de var aktuelle; IBKR gemmer ikke udløbne kontrakter for evigt.

Målt for MNQ med `probe_mnq_daekning.py` (12 kvartalsmåneder bagud):

| kontrakt | findes hos IBKR | ældste 1-min bar |
|---|---|---|
| MNQ 202609 | ✅ | 2025-06-22 |
| MNQ 202606 | ✅ | 2025-03-23 |
| MNQ 202603 | ✅ | 2024-12-22 |
| MNQ 202512 | ✅ | 2024-09-22 |
| MNQ 202509 og ældre | ❌ | — kontrakten findes ikke længere |

⚠ **De fire kontrakter rækker på papiret tilbage til 2024-09**, men de tidlige
bars er *back-month*: kontrakten var noteret, men handlede knap nok. Målt på
MNQ 202512:

```
2025-03-17     0 barer          (handlede ikke)
2025-06-16   720 barer, volumen         95     ← ubrugeligt
2025-10-06   720 barer, volumen    180.803     ← front-måned
```

En dag med samlet volumen 95 kan man hverken øve eller backteste på, og det er
netop den slags `curate_futures_data.py` trimmer væk (front-month-trim).

**Konklusion:** MNQ kan realistisk dække **fra ca. 2025-09-22 og frem** — ca.
11 måneder mod MES' 26. Resten er væk hos IBKR og kan ikke genskabes.

---

## 1. Høst 1-min pr. kontrakt

TWS/Gateway skal være åben på 7497. Fra `backend/`:

```bash
python harvest_futures_1min.py --symbols MNQ --start 2025-09-20 --end 2026-08-12 --client-id 48
```

- **Forventet tid: 10–25 minutter.** ~230 handelsdage, 2 dage pr. request.
- Scriptet printer løbende fremdrift og er **resumerbart** — afbrydes den,
  så kør samme kommando igen; den dedupliker på bar-tidsstempel.
- Read-only. Sender ingen ordrer. client-id 48 er adskilt fra backend'en.

Output: `data_harvest/MNQ_202512_1min.csv`, `MNQ_202603_1min.csv`,
`MNQ_202606_1min.csv`, `MNQ_202609_1min.csv`.

⚠ Starter man tidligere end 2025-09-20, leder høsteren efter kontrakt 202509,
som ikke findes — de dage bliver tomme.

---

## 2. Rens, lav de andre tidsrammer, og stitch

```bash
python curate_futures_data.py --symbols MNQ
```

Det ene kald gør alle tre ting:

1. **trimmer** back-month-junk væk pr. kontrakt (daglig volumen < 5 % af
   filens 90-percentil-dag)
2. **resampler** til 3, 5, 10 og 15 min → `data_harvest/mes_m2k_clean/`
3. **stitcher** til én kontinuerlig serie pr. tidsramme →
   `data_harvest/mes_m2k_stitched/MNQ_1min.csv` osv.

⚠ Mappen hedder stadig `mes_m2k_*`. Navnet er nu misvisende, men stien er den
`trading_practice` læser fra, og et skifte ville kræve ændringer begge steder.

⚠ Stitchingen er **rå**, ikke back-adjusted: der er et prisspring ved hver
kontrakt-overgang. Til intradag-brug (én session ad gangen) er det
uproblematisk — til flerdags-serier skal der backadjusteres.

---

## 3. Kontrollér

```bash
python -c "import csv; r=list(csv.reader(open('data_harvest/mes_m2k_stitched/MNQ_1min.csv',encoding='utf-8')))[1:]; print(f'{len(r):,} barer  {r[0][0][:16]} -> {r[-1][0][:16]}')"
```

Forventet: ca. 300.000 barer fra ca. 2025-09-22.

Sammenlign med MES (752.775 barer fra 2024-06-21) — forskellen er den
manglende historik fra afsnit 0, ikke en fejl i høsten.

---

## 4. MNQ i simulatoren

Allerede klargjort i `c:\projects\trading_practice`:

- `sim/data.py` indekserer nu `MES`, `M2K` **og** `MNQ`
- `MULTIPLIKATOR["MNQ"] = 2.0` ⚠ **ikke 5** — MNQ er den eneste af de tre
  mikroer med en anden multiplikator, og med 5 ville P&L være regnet 2,5 gange
  for stort uden at noget så forkert ud
- vælgeren og chart-titlen kender "MNQ (Nasdaq micro)"

Efter afsnit 2 skal indekset bygges om, så de nye sessioner kommer med:

```bash
cd c:\projects\trading_practice
python -m sim.data          # ~15 sek
```

Derefter dukker MNQ op i drop-down'en med sit sessionsantal.

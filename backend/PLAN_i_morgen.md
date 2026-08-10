# Plan for 2026-08-11 — flad bog og ny kode, i den rigtige rækkefølge

**Skrevet 10/8 kl. 19:50, da beslutningen blev at udsætte.**

⚠ **I aften skal du ikke gøre noget.** K2 force-lukker 15:45 ET (21:45 dansk),
så IOVA-eksperimentet kører af sig selv. Algoserveren har kørt på den gamle kode
i dagevis; ét døgn mere tilføjer ingen ny risiko.

---

## Hvorfor det ikke kunne gøres i aften

Flatten kræver et **åbent marked**. De fjorten positioner er small-caps, og
amerikanske aktier lukker 22:00 dansk. Efter det er der kun extended hours med
tynd likviditet — `flatten_alt` ville korrekt returnere exit 1 på det der hang,
og **et halvt gennemført flatten efterfulgt af en genstart er værre end ingen af
delene.**

---

## Hvorfor auto-start ikke forhindres

Auto-start ligger hardkodet i `scheduler.py` med en instance-guard, ikke som et
flag i `account.yaml`. At slå den fra kræver en kodeændring — og den ville
skulle deployeres med netop den genstart vi er ved at sekvensere.

Det er enklere at lade dem starte og **stoppe dem bagefter**. De ti minutters
handel på gammel kode er det samme som er sket hver dag i ugevis, og `flatten_alt`
dækker alligevel hvad de måtte have åbnet.

---

## Rækkefølgen — 15:30 dansk, når markedet åbner

Kør fra `backend/` **på algoserveren**, hvor dens Gateway er.

### 1 · Mål IOVA-forudsigelsen — FØR noget lukkes

⚠ Dette skal ske først. `flatten_alt` skelner ikke mellem beviset og resten, så
lukkes bogen inden, er eksperimentet væk.

```bash
curl -s "http://localhost:8000/journal/trades?symbol=IOVA&date_from=2026-08-10"
curl -s http://localhost:8000/account/dash-snapshot     # står der MES/IOVA?
```

Sammenhold med `oversalg_forudsigelse.md` §4 og skriv udfaldet ind i **§6** i
samme fil — uden at redigere det der står over.

| Udfald | Betyder |
|---|---|
| **IOVA −39** | Stærk bekræftelse |
| **0 (fladt)** | Udløseren fyrede ikke. Hverken/eller |
| **−78** | ⚠ Falsificerer mekanismen |
| Andet tal | ⚠ Mekanismen er ufuldstændig |

### 2 · Stop strategierne — behold forbindelsen

```bash
for S in "Konfluens 2" "BuyTheDip" "Trend Join Long" "Relativ Styrke" \
         "Europa-reversion" "US-reversion"; do
  curl -s -X POST http://localhost:8000/algo/stop \
       -H "Content-Type: application/json" -d "{\"strategy\":\"$S\"}"
done
```

Ellers åbner de nyt mens du lukker.

### 3 · Flatten — preview først

```bash
python flatten_alt.py --konto DUO509856
python flatten_alt.py --konto DUO509856 --udfoer
```

### 4 · ⚠ Verificér flad HOS IBKR — ikke i journalen

`flatten_alt` gør det selv og returnerer **exit 1** hvis noget hænger.

Journalen har vist sig uenig med kontoen i **seks af tolv** tilfælde, så det er
IBKR der skal sige fladt.

**Hænger noget: gen-afgiv ikke.** Find ud af hvorfor (halt, tynd likviditet,
lukket kontrakt) før du gør noget.

### 5 · Pull og genstart

```bash
git pull
# genstart backenden
```

Henter fire ting: `538d465` fast client-id · `c47465b` paper/live i journalen ·
`19b55c8` migrationen · `d42cd75` over-sell-rettelsen.

### 6 · Verificér den nye tilstand

```bash
python port_tjek.py                    # ⚠ præcis ÉN lytter
curl -s http://localhost:8000/health   # forbundet
python flatten_alt.py --konto DUO509856    # preview: "allerede flad"
```

Tom hukommelse er nu **korrekt** frem for farlig, fordi kontoen også er flad.

### 7 · Start strategierne manuelt

Fra Studio, eller `POST /algo/start`. Nu med ny kode og et fladt bogholderi.

---

## Hvis noget går galt undervejs

**Efter trin 3 men før trin 5** er den ubehagelige tilstand: måske halvt flad, gammel
kode. Kom du dertil og må stoppe, så **lad være med at genstarte**. Lad
algoserveren køre videre som den er, og tag resten næste dag. En genstart oven på
en halvt flad bog er præcis det vi undgår.

**Kan en position ikke lukkes**, er den ikke i vejen for pull og genstart — men
notér den, og husk at hukommelsen så ikke er tom-og-korrekt, men tom-og-uenig
med kontoen for netop den ticker.

---

## Det der venter bagefter

- Ibens maskine efter `konto2_opsaetning.md`
- Lag 3's formler fra desktop Claude (spec v2.3)
- O3's stående afstemning — `position_ledger.reconcile_against_ibkr()` findes
  allerede og har ingen kaldere

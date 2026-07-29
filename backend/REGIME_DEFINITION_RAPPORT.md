# Regime-determinering — statusrapport til spec-arbejdet

**Til:** desktop Claude (spec-forfatter)
**Fra:** VS Code Claude, efter gennemlæsning af den faktiske kode og det ene output der findes
**Dato for gennemlæsning:** 2026-07-29
**Formål:** grundlag for at afgøre om regime-definitionen er præcis nok til at drive en
regime-switch meta-strategi, eller skal finpudses først.

Alt herunder er læst ud af koden og det faktiske output — ikke fra hukommelse.
Filreferencer er verificerede.

---

## 0. Hovedfund — læs dette først

**Regime-detektoren har aldrig skelnet mellem noget.** Den har kørt én gang, over fire
vinduer, og alle fire vinduer får den samme etiket: *Stock-picking (relativ værdi)*.

| Vindue | Span | Dispersion | Ø index-trend-persistens | Etiket |
|---|---|---|---|---|
| recent | 2026-04-24 .. 2026-06-05 | 3,88 | −0,086 | Stock-picking |
| prior | 2026-03-12 .. 2026-04-23 | 3,61 | +0,024 | Stock-picking |
| apr | 2026-04-01 .. 2026-04-30 | 3,59 | −0,114 | Stock-picking |
| maj | 2026-05-01 .. 2026-05-29 | 3,97 | −0,118 | Stock-picking |

Tærsklen er `dispersion > 3,0 OG Ø-persistens < 0,05`. Alle fire vinduer opfylder begge
betingelser med god margin — dispersionen har aldrig været i nærheden af 3,0, og
persistensen har aldrig været i nærheden af 0,05.

Konsekvensen for meta-strategien er direkte: **et signal der kun antager én værdi kan ikke
styre et skift.** Før noget som helst switch-design giver mening, skal specen afgøre om
detektoren faktisk kan producere mere end én tilstand på rigtige data — eller om
tærsklerne/metrikkerne skal laves om.

Bemærk at de *underliggende* tal godt bevæger sig (morgen-follow +0,45 → +0,60,
index-trend +0,02 → −0,09 mellem prior og recent). Det er diskretiseringen der kollapser
variationen, ikke markedet der står stille.

---

## 1. Hvad der findes i dag

### Hovedkomponent
| Fil | Rolle |
|---|---|
| `backend/regime_fingerprint.py` (49 KB) | Hele målingen + klassifikationen. Ren stdlib, offline, læser kun cache — ingen IBKR, intet netværk. |
| `backend/regime_fingerprint_output/summary.txt` | Fuld talrapport pr. vindue |
| `backend/regime_fingerprint_output/fingerprint_2026-07-15.json` | Maskinlæsbart output (alle metrikker, alle vinduer) |
| `backend/regime_fingerprint_output/regime_briefing.txt` | Menneskelig oversættelse — ren præsentation, ingen ny måling |
| `backend/regime_fingerprint_output/regime_history.csv` | Tidsserie af kørsler. **Indeholder præcis én række.** |
| `backend/docs_src/regime_fingeraftryk.md` + `docs/regime_01_forstaa_dit_marked.pdf` | Brugerdokumentation |

### Automatisering
`backend/scheduler.py` kører `generate_regime_fingerprint` ugentligt, kun på algoserveren
(`instance_role`-gated), som subprocess med timeout. Historikken på én række tyder på at
den enten lige er sat op eller ikke har kørt siden 15/7 — det bør verificeres på
algoserveren.

### Beslægtet, men noget ANDET
| Fil | Hvad den gør |
|---|---|
| `backend/meanrev_regime.py` | Regime-*split af EUREVERSIONs afkast* — "findes der et regime hvor den bløder?" |
| `backend/washout_regime.py` | Samme for washout-reclaim (mange-aktier) |

Disse to måler strategi-performance pr. regime. Det er præcis den information der
kontaminerer et meta-strategi-design (se afsnit 6). De er nævnt for fuldstændighedens
skyld; jeg har kun læst deres formålsbeskrivelse, ikke deres resultater.

---

## 2. Hvordan regimet bestemmes i dag — hele kæden

```
cache på disk                    metrikker              klassifikation
─────────────                    ─────────              ──────────────
bar_cache/ (1-min small-cap) ──► m1..m6  (small-cap) ──┐
data_harvest/{ES,NQ,RTY}_1day ─► m7..m9  (index)     ──┼──► kaskade ──► én af 4 etiketter
data_harvest/*_1day (par)     ──► m10    (dagligt spread)│
mes_m2k_stitched/ 15-min      ──► spor-A-regel          ─┘
historical_universe_midcap_*.json ► navneliste
```

### Måleuniverset
- **Small-cap-aktier:** 34–39 navne pr. vindue, fra `historical_universe_midcap_*.json`
- **Index-futures:** ES, NQ, RTY (daglige barer)
- **Spread:** ES-RTY, ES-NQ, NQ-RTY (dagligt) + MES-M2K (15-min, kun til spor A)

### Vinduer
`make_windows()` bruger **de sidste 30 tilgængelige handelsdage i cachen**, ikke de sidste
30 dage fra i dag:

```python
win["recent"] = (ds[-30], ds[-1])
win["prior"]  = (ds[-60], ds[-31])
win["apr"], win["maj"] = APR, MAJ      # faste kalenderspænd
```

### Metrikkerne (alle beskrivende, ingen edge-påstand)
| # | Metrik | Kilde |
|---|---|---|
| m1 | gap follow-through-rate + median fade-dybde | small-cap |
| m2 | intraday 5-min autokorrelation (+ = trend, − = chop) | small-cap |
| m3 | median 5-min ATR%, daglig range%, ATR-ekspansionsratio | small-cap |
| m4 | HOD-fordeling over 4 tidsbånd + `morgen_domineret` (bool) | small-cap |
| m5 | breadth (% grønne) + **navne-dispersion%** | small-cap |
| m6 | halt-frekvens | **altid `None` — ingen halt-log på disk** |
| m7 | **daglig autokorrelation** (trend-persistens) + continuation-rate | ES/NQ/RTY |
| m8 | overnight- vs intraday-afkast + ratio | ES/NQ/RTY |
| m9 | realiseret vol 10d/40d + term-ratio | ES/NQ/RTY |
| m10 | spread-autokorr, VR2/VR5/VR10, half-life | daglige spreads |

De **fed**markerede er de eneste to der faktisk afgør etiketten i dag.

### Klassifikationen (`_primary_regime`, linje 619)
En prioriteret kaskade — første match vinder, ingen vægtning, ingen konfidens:

```python
if dispersion > 3.0 and mean(m7 over ES,NQ,RTY) < 0.05:
    return "Stock-picking (relativ vaerdi)"
if follow_through > 0.55 and autocorr_5min > 0.05 and morgen_domineret:
    return "Momentum-fortsaettelse"
if autocorr_5min < -0.05 and follow_through < 0.45:
    return "Intraday mean-reversion"
return "Blandet / uklart"
```

### Strategi-mapping
| Etiket | Peger på |
|---|---|
| Stock-picking (relativ værdi) | Relativ Styrke (tværsnitlig rangering, spor D) |
| Momentum-fortsættelse | K2 / TrendJoin |
| Intraday mean-reversion | BuyTheDip / washout |
| Blandet / uklart | ingen |

### Spor A (separat præregistreret regel)
Fem betingelser på 15-min MES-M2K-spread, alle skal holde. Resultat i den ene kørsel:
**LUKKET** — fejler på VR5 (0,9991 mod krav <0,9), half-life (1013,6 barer mod krav <10)
og gentagelse i uafhængigt vindue. Half-life på 1013 barer er ~253 timer; spreadet
reverterer altså slet ikke inden for en session. Det er et substantielt afslag, ikke et
teknisk.

---

## 3. Hvad der er godt ved det nuværende design

Værd at bevare, ikke rive ned:

1. **Præregistrering er allerede indbygget.** Mapping tal→familie skrives i `summary.txt`
   *før* outputtet fortolkes, og spor-A-reglen er låst før 15-min-tallene beregnes.
   Samme disciplin der reddede signatur-valideringen.
2. **Look-ahead-assert pr. metrik** og coverage rapporteres altid.
3. **Degraderer pænt** ved manglende kilde frem for at fejle eller gætte.
4. **Ren offline, kun stdlib** — ingen TWS-afhængighed, kan køre hvor som helst.
5. **Beskrivende af design** — "ingen handler, ingen edge-påstand" står eksplicit.
   Meta-strategien bliver det første sted hvor outputtet skal *bruges* til noget, og det
   skifte skal håndteres bevidst.

---

## 4. Problemer jeg kan se — med belæg

### 4.1 Detektoren har kun produceret én tilstand (afsnit 0)
Alle fire vinduer → samme etiket. Uden variation er der intet at skifte på.

### 4.2 Målingen halter ~6 uger bagud
Kørsel dateret **2026-07-15**, men `recent`-vinduet slutter **2026-06-05**. Coverage-noten
forklarer hvorfor: *"futures ES|15min: nyeste bar 2026-06-08 (står ~5+ uger)"*. Fordi
`make_windows()` tager de sidste 30 dage der findes i cachen — ikke de sidste 30 dage fra
i dag — flytter vinduet sig ikke når cachen står stille.

For en beskrivende rapport er det acceptabelt. For en meta-strategi der allokerer kapital
er det ikke: man ville skifte strategi på baggrund af et marked der er seks uger gammelt.
**Specen bør kræve en eksplicit friskheds-grænse** (fx: nægt at afgive en etiket hvis
nyeste bar er ældre end N dage) frem for stiltiende at rapportere på gamle data.

### 4.3 Hårde tærskler uden hysterese
`dispersion > 3.0` er en knivsæg. Ligger dispersionen og vibrerer omkring 3,0, skifter
etiketten frem og tilbage fra uge til uge — og en meta-strategi ville rotere kapital hver
gang. Der er ingen minimum-opholdstid, ingen konfidensgrad, ingen "hvor kraftigt".

Til reference ligger de fire observerede værdier på 3,59–3,97 — over tærsklen, men ikke
med voldsom margin.

### 4.4 Gennemsnittet skjuler uenighed mellem indeks
`mean(m7)` over ES/NQ/RTY var i recent-vinduet: ES −0,166, NQ **+0,099**, RTY −0,192.
De peger ikke samme vej. Gennemsnittet −0,086 fortæller ikke at NQ trendede mens ES og
RTY reverterede. For en meta-strategi der vælger mellem strategier på forskellige
instrumenter er det et reelt informationstab.

### 4.5 Måleuniverset matcher ikke nødvendigvis det strategierne handler
Regimet måles på ~39 small-cap-navne + ES/NQ/RTY. Strategierne handler:
Relativ Styrke (tværsnit aktier), K2/confluence2, EU-reversion (MES/M2K futures),
BuyTheDip, TrendJoin (micro-cap gappers). **Specen bør tage stilling til om regimet skal
måles på det univers hver strategi faktisk handler** — et small-cap-dispersionstal siger
ikke nødvendigvis noget om MES-futures.

### 4.6 Én af fire klassifikationsgrene kan aldrig fyre
m6 (halt-frekvens) er hårdkodet `None` fordi der ikke findes en halt-log på disk. Linjen
om spor B i verdict-tabellen er derfor dekorativ. (Jf. projektnotatet er halt-opsamling
i gang som selvstændigt spor.)

### 4.7 Inkonsistente vindueslængder mellem metrik-familier
Samme nominelle vindue giver forskellig dækning: recent = 25 small-cap-dage men 30
futures-dage; prior = 18 mod 30. Metrikker sammenlignes altså på tværs af vinduer med
forskellig effektiv stikprøve. `m4_morgen_domineret` skifter faktisk fra `True` (recent)
til `False` (maj) — og den bool indgår direkte i momentum-grenen.

### 4.8 Ingen historik at validere imod
Én række i `regime_history.csv`. Der findes ingen tidsserie af regimeskift, og dermed
intet at teste en switch-regel på. **Det er den bindende begrænsning på hele projektet**
og bør stå øverst i specen: enten skal fingeraftrykket køres bagud over historikken for
at generere en regime-tidsserie, eller også skal meta-strategien vente på at historikken
akkumulerer.

---

## 5. Spørgsmål specen bør besvare

Prioriteret. De første tre er blokerende.

1. **Kan detektoren overhovedet skelne?** Kør fingeraftrykket bagud over de 2 års
   historik der findes, og se hvor mange distinkte etiketter der faktisk opstår. Giver
   det stadig kun én, skal metrikkerne eller tærsklerne laves om før noget switch-design.
2. **Hvordan genereres regime-tidsserien?** Rullende vinduer bagud over historikken —
   med hvilken skridtlængde, og med hvilken look-ahead-garanti? Etiketten for uge *t* må
   kun bruge data til og med uge *t*.
3. **Hvad er friskheds-kravet?** Hvor gamle data må en etiket bygge på før den afvises?
4. **Skal etiketten være hård eller blød?** Fire diskrete kasser, eller en kontinuert
   score pr. familie som meta-strategien kan vægte efter? Det sidste fjerner
   knivsægs-problemet, men er sværere at præregistrere.
5. **Hysterese og opholdstid?** Minimum antal uger i et regime før skift tillades?
6. **Måleunivers pr. strategi?** Ét globalt regime, eller ét pr. instrument-familie?
7. **Låses rosteren?** Hvilke strategier er med — og bekræftelse på at ingen må fjernes
   bagefter fordi de klarede sig dårligt.
8. **Hvad er baseline?** Meta-strategien skal slå "kør alle strategier hele tiden".
   Gør den ikke det, er regime-switchet ren kompleksitet. Kriteriet bør præregistreres
   med samme stringens som i signatur-valideringen (commit bb92296).

---

## 6. Kontaminations-grænsen — hvad jeg bevidst ikke har gjort

Jeg har læst **regime-maskineriet**: målekode, tærskler, klassifikationskaskade,
outputformat og den ene kørsels tal.

Jeg har **ikke** set på strategi-afkast fordelt på regime. `meanrev_regime.py` og
`washout_regime.py` producerer præcis den slags, og deres output har jeg ikke åbnet.
Grunden er, at hvis den der designer switch-reglen på forhånd ved hvilke strategier der
tjente i hvilke perioder, er reglen kurvetilpasset fra fødslen — uanset hvor pænt den
præregistreres bagefter.

**Anbefaling til Søren:** hold de seneste måneders data tilbage fra begge Claude-instanser
under design. Det er det eneste ægte out-of-sample-tjek, og det er den eneste del af
proceduren hverken desktop Claude eller jeg kan levere.

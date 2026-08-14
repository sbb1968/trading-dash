# Signal-evaluering — har indikatorerne prædiktiv værdi ved fald i MES?

**Kort svar: nej.** Ingen af de fire indikatorer slår basisraten. Det ene der
gør, viser sig ved kontrol at måle volatilitet og ikke retning.

---

## Hvad der blev målt

| | |
|---|---|
| Instrument | MES, 2-minutters barer (aggregeret fra 1-min) |
| Periode | 2024-06-21 → 2026-08-06 · **25 måneder** (du bad om mindst 3) |
| Grundlag | 752.775 1-min barer → 376.391 2m-barer |
| Bedømt | 128.166 barer i 08:00–16:00 ET |
| Prøvet | 144 kombinationer: 16 signaler × N ∈ {5,10,20} × X ∈ {0,3/0,5/0,8 %} |

Kør selv:

```bash
node --max-old-space-size=8192 analyse/signal_eval.cjs
```

Output i `analyse/signal_eval_ud/`:

| fil | indhold |
|---|---|
| `bar_log_2m.csv` | Del 2 — én række pr. bar: z på 2/5/15/60m, ADX pr. TF, WT, MACD, RVOL, minutter siden åbning, alle signalflag, hændelsesflag |
| `signal_statistik.csv` | Del 3+4 — precision, recall, basisrate, løft, z, median forspring, med/uden nyhedsvinduer, begge datahalvdele |
| `nyhedsvinduer.csv` | Del 4 — bevægelse pr. dag i og uden for vinduerne |
| `konklusion.txt` | Del 5 — den skrevne konklusion |

---

## Metode — og de fælder der blev undgået

**⚠ Basisraten måles på SAMME vindue som precision.** Første udgave talte
"begynder en hændelse på denne bar" (1 chance) mod "begynder en inden for de
næste 11 barer" (11 chancer). Det gav *alt* — også ADX > 25 — et løft omkring
5. Det var ikke et signal; det var en brøk med to forskellige nævnere.

**⚠ Ingen bagklogskab på højere tidsrammer.** En 2m-bar kl. 09:32 må kun kende
den 15m-bar der er *lukket*. Bruger man den ufærdige, kender man fremtiden.

**⚠ Indikatorerne er ikke skrevet om.** Analysen læser
`trading_practice/web/indikatorer.js` — den ene oversættelse af Sørens Pine,
som allerede har sin egen prøve. En kopi mere ville drive fra de andre; det er
sket her før, hvor MACD fandtes i to udgaver og den man *så* ikke var den man
*prøvede*.

**⚠ Mange sammenligninger er også kurvetilpasning.** 144 kombinationer ⇒ ~7
ville bestå ved ren tilfældighed på en ukorrigeret 5 %-tærskel. Dommen bruger
derfor en Bonferroni-korrigeret tærskel (z ≥ 3,58) **og** kræver at løftet
holder i begge halvdele af data, delt efter dato ved 2025-07-16.

**⚠ Overlappende hændelser er ikke uafhængige.** Nabobarer deler næsten hele
deres fremtidsvindue, så z-værdien overvurderer. Den står som en grov
sortering, ikke som en p-værdi. Den rigtige kontrol er ud-af-prøve-halvdelen.

**⚠ To signalnavne var samme betingelse.** `macd_hist_neg` ≡ `macd_kryds_ned`
(histogrammet *er* macd − signal), og Cipher B's røde prik ≡ "WT-kryds ned med
wt2 ≥ 53". Talt som fire ville de fylde dobbelt i korrektionen og ligne to
uafhængige bekræftelser. De findes automatisk og slås sammen.

---

## Resultat (Del 3)

Bedste (N, X) pr. signal, sorteret efter løft:

| signal | N | X% | n | præc. | basis | **løft** | z | 1. halv | 2. halv | dom |
|---|---|---|---|---|---|---|---|---|---|---|
| roed_prik_og_rvol | 5 | 0,8 | 461 | 1,3 % | 0,6 % | 2,31 | 2,1 | 2,51 | 0,00 | nej |
| **rvol_over_15_roed** | 5 | 0,3 | 11.505 | 14,5 % | 10,4 % | **1,39** | 14,4 | 1,28 | 1,58 | JA |
| *KONTROL_rvol_groen* | 5 | 0,5 | 10.601 | 3,0 % | 2,3 % | *1,29* | 4,6 | 1,22 | 1,48 | *JA* |
| z5_over_2 | 5 | 0,8 | 8.993 | 0,7 % | 0,6 % | 1,28 | 2,0 | 1,39 | 0,00 | nej |
| adx_over_25 | 5 | 0,8 | 53.468 | 0,7 % | 0,6 % | 1,20 | 3,5 | 1,21 | 1,02 | nej |
| z15_over_25 | 5 | 0,8 | 5.856 | 0,7 % | 0,6 % | 1,18 | 1,0 | 1,34 | 0,00 | nej |
| *KONTROL_tilfaeldig* | 5 | 0,8 | 11.885 | 0,7 % | 0,6 % | *1,18* | 1,5 | 1,20 | 0,94 | nej |
| macd_hist_neg | 5 | 0,5 | 5.224 | 2,8 % | 2,3 % | 1,17 | 2,0 | 1,15 | 1,28 | nej |
| z2_over_2 | 5 | 0,5 | 8.474 | 2,7 % | 2,3 % | 1,14 | 2,0 | 1,07 | 1,41 | nej |
| z15_retur_fra_2 | 20 | 0,8 | 682 | 2,9 % | 2,6 % | 1,12 | 0,5 | 0,93 | 1,67 | nej |
| wt_kryds_ned | 5 | 0,8 | 12.862 | 0,6 % | 0,6 % | 1,08 | 0,7 | 1,08 | 1,01 | nej |
| wt_kryds_ned_ob2 | 5 | 0,8 | 2.509 | 0,6 % | 0,6 % | 1,06 | 0,2 | 1,15 | 0,00 | nej |
| z15_2_og_wt_ned | 10 | 0,5 | 1.567 | 5,1 % | 5,0 % | 1,01 | 0,1 | 1,08 | 0,91 | nej |
| wt_kryds_ned_ob (= rød prik) | 5 | 0,8 | 3.566 | 0,5 % | 0,6 % | 0,95 | −0,2 | 1,02 | 0,00 | nej |
| z15_over_2 | 10 | 0,5 | 14.302 | 4,5 % | 5,0 % | 0,88 | −3,2 | 0,95 | 0,77 | nej |
| wt_bear_div | 10 | 0,5 | 1.282 | 4,4 % | 5,0 % | 0,88 | −1,0 | 0,88 | 0,89 | nej |

### De to kontroller afgør sagen

**Tilfældige barer** (samme mængde, deterministisk pseudo-tilfældig) når løft
**1,18** når man vælger den bedste af 9 (N,X)-kombinationer. Det er støjgulvet:
et signal hvis *bedste* opsætning ligger under ~1,2 kan ikke skelnes fra
tilfældighed.

**Høj volumen på en GRØN bar** giver løft **1,29** mod rødbarens **1,39**.
Spejlet virker altså næsten lige så godt — så RVOL måler **volatilitet, ikke
retning**. Høj volumen varsler bevægelse, ikke bevægelse *nedad*. Den ene
"vinder" i tabellen er dermed også faldet.

### Signaler der er DÅRLIGERE end basisraten

Lige så værdifuldt at vide:

| signal | N | X% | præcision | basis | løft | z | n |
|---|---|---|---|---|---|---|---|
| **z15_over_2** | 10 | 0,8 | 0,6 % | 1,2 % | **0,54** | **−6,0** | 14.302 |
| z15_2_og_wt_ned | 10 | 0,8 | 0,7 % | 1,2 % | 0,59 | −1,8 | 1.567 |
| wt_bear_div | 5 | 0,5 | 1,6 % | 2,3 % | 0,66 | −1,9 | 1.282 |
| z15_over_25 | 20 | 0,8 | 1,8 % | 2,6 % | 0,69 | −3,9 | 5.856 |
| wt_kryds_ned_ob2 | 20 | 0,8 | 1,9 % | 2,6 % | 0,73 | −2,2 | 2.509 |

Den øverste er stærk og går den forkerte vej: **når 15m-z ligger over +2, er et
0,8 %-fald inden for de næste 20 minutter cirka halvt så sandsynligt som på en
tilfældig bar** (z = −6,0 på 14.302 observationer). Mean-reversion-præmissen —
"strakt op ⇒ falder tilbage" — holder ikke på denne tidshorisont. Prisen
fortsætter oftere end den vender.

Cipher B's bearish divergens: 0,88. Den røde prik: 0,95. Begge under 1.

---

## Del 4 — nyhedsvinduerne

±15 min om **08:30, 09:30, 10:00, 14:00, 16:00** ET:

> **28,0 % af barerne → 31,3 % af bevægelsen** (koncentration 1,12×)

Bevægelsen er altså kun svagt koncentreret. Men **edgen** er det:

| signal | N | X% | løft med | løft uden | ændring |
|---|---|---|---|---|---|
| roed_prik_og_rvol | 5 | 0,5 | 1,66 | 1,06 | **−36 %** |
| roed_prik_og_rvol | 10 | 0,5 | 1,55 | 0,88 | **−43 %** |
| rvol_over_15_roed | 5 | 0,3 | 1,39 | 1,10 | −21 % |
| rvol_over_15_roed | 10 | 0,5 | 1,38 | 1,01 | −27 % |
| roed_prik_og_rvol | 20 | 0,8 | 1,33 | 0,72 | **−46 %** |

Målt på de 29 kombinationer med løft ≥ 1,1: gennemsnit **1,274 → 1,129**, og
16 af 29 taber over 10 %.

> **HYPOTESEN ER BEKRÆFTET.** Værdien ligger overvejende i nyhedsvinduerne.
> Uden dem falder de fleste signaler ned mod 1,0 — altså ned til basisraten.

---

## Konklusion

1. **Ingen af de fire indikatorer forudsiger fald i MES.** Cockpit-z,
   WaveTrend/Cipher B, MACD og ADX ligger alle mellem 0,88 og 1,20 i løft —
   inden for eller under støjgulvet på 1,18 som tilfældige barer satte.
2. **Det ene der bestod, var volumen — og kontrollen væltede det.** Grøn bar
   virker næsten lige så godt som rød, så signalet er volatilitet, ikke retning.
3. **Cockpittets z ≥ +2 på 15m er aktivt vildledende** på 20-minutters horisont:
   løft 0,54, z = −6,0. Prisen fortsætter oftere end den vender.
4. **Det der er tilbage af edge, ligger i nyhedsvinduerne** — og der handler man
   mod nyhedsflow, ikke mod et chart.

Det betyder ikke at indikatorerne er ubrugelige til *andet* — de kan udmærket
være gode til at strukturere en beslutning, til at holde disciplin, eller til at
sige noget om regimet. Men som **forudsigelse af et fald på 0,3–0,8 % inden for
5–20 barer** har ingen af dem påviselig værdi i 25 måneders MES-data.

---

## Pine-scriptet

`pine/signal_eval.pine` — til at holde øje med **én** opsætning live på chartet.
Tabel med basisrate, precision, løft, recall, median forspring og en dom.

**⚠ Tre ting kan Pine ikke,** og de er derfor kun i Node-værktøjet: skrive CSV,
sweepe 144 kombinationer, og dele data i to halvdele til ud-af-prøve.
Rå-loggen (Del 2) plottes til `display.data_window`, så den kan hentes via
**Chart → ⋮ → Export chart data**.

**⚠ Tallene i tabellen halter (N + Forspring) barer bagefter.** En hændelse kan
først bedømmes når de N barer er faldet. Det er prisen for ikke at kigge fremad.

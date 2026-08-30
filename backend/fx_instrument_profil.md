# FX-instrumentprofil — spot FX på IDEALPRO

**Målt:** 30-08-2026 · DUN748991 (paper) · TWS :7497 · `fx_probe.py --kun P1,P4`
**Rå data:** `fx_probe_output/P1_kontrakter.json`, `fx_probe_output/P4_tick_pip.json`

> **Reglen fra MES/MNQ-profilen gælder uændret: ingen instrumentafhængige tal
> uden for profilen.** Skal kode kende en pipværdi, et tick-gitter eller en
> minimumsstørrelse, læses det herfra — ikke fra et website og ikke fra en
> konstant i et script.

---

## 1. Tick-gitter og pip

⚠ **Tick-gitteret er IKKE pip-gitteret.** Alle par på IDEALPRO kvoteres i
**halve pip** — `ticks_pr_pip = 2,0` uden undtagelse, også JPY-parrene.
Enhver afrunding skrevet til et futures-gitter rammer forkert her.

| Par | `minTick` | pip | ticks/pip | `minSize` | `sizeIncrement` |
|---|---|---|---|---|---|
| EURUSD | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |
| GBPUSD | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |
| USDJPY | 0,005 | 0,01 | 2,0 | 1,0 | 1,0 |
| AUDUSD | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |
| USDCHF | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |
| USDCAD | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |
| NZDUSD | 0,00005 | 0,0001 | 2,0 | 1,0 | 1,0 |
| EURGBP | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |
| EURJPY | 0,005 | 0,01 | 2,0 | 1,0 | 1,0 |
| GBPJPY | 0,005 | 0,01 | 2,0 | 1,0 | 1,0 |
| EURDKK | 0,00005 | 0,0001 | 2,0 | 1,0 | 1,0 |
| USDDKK | 0,00005 | 0,0001 | 2,0 | 1,0 | 1,0 |
| EURSEK | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |
| EURNOK | 0,00005 | 0,0001 | 2,0 | 0,01 | 0,01 |

⚠ **`minSize` modsiger IBKR's website.** Websitet angiver et IDEALPRO-minimum
på 20.000–25.000 enheder; `ContractDetails` melder 0,01 eller 1,0. Specen
krævede at tallet kom fra API'et — det gjorde det, og de to kilder er uenige.
**Uafklaret.** Hypotesen er at `minSize` beskriver IDEALFX-rutning, og at
IDEALPRO-minimum først håndhæves ved ordreafgivelse. Det kan kun afgøres med
en ægte ordre under minimum (P3, kræver åbent marked).

---

## 2. Pipværdi

**Fast** betyder uafhængig af kursen. **Flydende** betyder at USD-værdien af et
pip ændrer sig med markedet og skal hentes samtidig med signalet — ikke
cachet, ikke antaget.

| Par | Kvot.val. | Pipværdi | 25.000 enh. | 100.000 enh. | Omregning |
|---|---|---|---|---|---|
| EURUSD | USD | **fast** | $2,50 | $10,00 | — |
| GBPUSD | USD | **fast** | $2,50 | $10,00 | — |
| AUDUSD | USD | **fast** | $2,50 | $10,00 | — |
| NZDUSD | USD | **fast** | $2,50 | $10,00 | — |
| USDJPY | JPY | flydende | $1,57 | $6,27 | ÷ USDJPY 159,40 |
| USDCHF | CHF | flydende | $3,11 | $12,43 | ÷ USDCHF 0,8042 |
| USDCAD | CAD | flydende | $1,80 | $7,22 | ÷ USDCAD 1,3855 |
| EURGBP | GBP | flydende | $3,40 | $13,59 | × GBPUSD 1,3593 |
| EURJPY | JPY | flydende | $1,57 | $6,27 | ÷ USDJPY 159,40 |
| GBPJPY | JPY | flydende | $1,57 | $6,27 | ÷ USDJPY 159,40 |
| EURDKK | DKK | flydende | $0,39 | $1,56 | ÷ USDDKK 6,4147 |
| USDDKK | DKK | flydende | $0,39 | $1,56 | ÷ USDDKK 6,4147 |
| EURSEK | SEK | flydende | **—** | **—** | ⚠ kræver USDSEK |
| EURNOK | NOK | flydende | **—** | **—** | ⚠ kræver USDNOK |

⚠ **EURSEK og EURNOK står tomme med vilje.** USDSEK og USDNOK blev ikke målt,
og en pipværdi må ikke gættes. S6: manglende data rapporteres som manglende.
Skal de bruges, tilføj de to kryds til `P2_PAR`/`FX_ALLE` og kør P4 igen.

**Kurser aflæst 30-08-2026 (forsinket feed, marked lukket).** De er
øjebliksbilleder til omregningen ovenfor — ikke faste tal:
EURUSD 1,1652 · GBPUSD 1,3593 · USDJPY 159,40 · AUDUSD 0,71945 ·
USDCHF 0,8042 · USDCAD 1,3855 · NZDUSD 0,5951 · EURGBP 0,8572 ·
EURJPY 185,74 · GBPJPY 216,67 · EURDKK 7,4745 · USDDKK 6,4147 ·
EURSEK 11,089 · EURNOK 10,862

---

## 3. Handelstimer

| | Spot FX | MES (kontrol) |
|---|---|---|
| Tidszone | `US/Eastern` | `US/Central` |
| `tradingHours` == `liquidHours` | **JA** | **NEJ** |
| Dagsgrænse | 17:15 ET → 17:00 ET | 17:00 CT → 16:00 CT |
| Ophold | 15 min dagligt | 60 min dagligt |
| Uge | søn 17:15 ET → fre 17:00 ET | søn 17:00 CT → fre 16:00 CT |

⚠ **FX har ingen RTH.** `tradingHours` og `liquidHours` er identiske strenge.
Al kode der deler døgnet i RTH/ETH producerer på FX en opdeling uden indhold —
ikke en fejl der kaster, men en opdeling hvor begge halvdele er ens.

**Bekræftet af bardata:** EURUSD 1-min over `1 D` giver **1425 barer**
(1440 − 15 minutters ophold). MES giver **1380** (1440 − 60). Tallene
bekræfter opholdenes længde uafhængigt af `tradingHours`-strengen.

**Ugestrukturen målt direkte:** en 1-dags hentning der slutter mandag
00:00 UTC gav **165 barer** = 21:15→24:00 UTC. Søndagsåbningen ligger altså
21:15 UTC (sommertid), og der er intet før den. Det er ikke et datahul.

---

## 4. Data — hvilken `whatToShow` virker

| | `TRADES` | `MIDPOINT` | `BID_ASK` |
|---|---|---|---|
| Spot FX | ⚠ **0 barer** (fejl 162) | ✔ virker | ✔ virker |
| MES (kontrol) | ✔ virker | ✔ virker | ✔ virker |

| | FX | MES |
|---|---|---|
| Headstamp `TRADES` | **—** (intet) | 2025-06-22 |
| Headstamp `MIDPOINT` | **2005-03-09** | 2025-06-22 |
| Volumen | −1 under MIDPOINT (eneste der virker) | ægte tal under TRADES |

**Headstamp'en er verificeret, ikke bare aflæst:** hentning der slutter
2005-04-01 gav 4 barer; 2004-04-01 gav 0. Påstanden holder præcist.
1-minutters data findes mindst 24 måneder tilbage.

⚠ **Volumen findes ikke på FX.** Den eneste `whatToShow` der returnerer data
bærer `volume = −1`. Det er bedre end 0 — −1 kan skelnes fra "ingen handel" —
men enhver beregning får −1 ind som et tal.

---

## 5. Konstanter til kode

```python
# FX — hentet fra denne profil, ikke fra et website.
FX_MIN_TICK      = 0.00005      # alle par undtagen JPY-krydser
FX_MIN_TICK_JPY  = 0.005        # USDJPY, EURJPY, GBPJPY
FX_TICKS_PR_PIP  = 2            # ⚠ halve pip — gælder ALLE par
FX_WHAT_TO_SHOW  = "MIDPOINT"   # ⚠ TRADES giver 0 barer
FX_HAR_VOLUMEN   = False        # volume == -1
FX_HAR_RTH       = False        # tradingHours == liquidHours
FX_UGEAABNING_UTC = "21:15"     # søndag, sommertid
```

⚠ **Ikke i profilen endnu:** margin/gearing (P2), kommission (P2), spread (P7)
og den faktiske IDEALPRO-minimumsstørrelse (P3). Alle fire kræver åbent
marked. Indtil de er målt, må ingen kode antage et tal for dem.

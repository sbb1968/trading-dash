# Arbejdsordre 15. august — indhold og fremdrift

Sammendrag af `arbejdsordre_15aug2026.md` med status. Én linje pr. lukket punkt,
med hvor beviset ligger (§5's procesrettelse).

Sidst opdateret: 2026-08-17

---

## NIVEAU 0 — systemet lyver om sin egen tilstand

### T0 · Journal-500 — værktøjet før arbejdet ✅ `f65ef07`

`GET /journal/events` svarede 500 for ethvert vindue med et `universe_selected`.
Studios Log-fane var tom for hele dage.

| | krav | status |
|---|---|---|
| a | Rodårsagen navngivet med **det konkrete felt** | ✅ `payload.meta.rows[*]` fra TradingViews screener, via `algo_buythedip.py:616`. Kæden: `json.dumps(..., allow_nan=True)` skriver NaN som bart token → `json.loads` accepterer → Starlette (`allow_nan=False`) kaster → 500 |
| b | 200 for et vindue der indeholder `universe_selected` | ✅ |
| c | Sprængradius: ét event må ikke tage vinduet | ✅ **strammere end bedt om** — renses pr. FELT, ikke pr. event. Værdien bliver `None`, stien står i `_ikke_endelige_felter` |
| d | Test der kan fejle | ✅ `test_journal_ikke_endelige.py`, 4 falsifikationer røde |

Begge sider rettet: skrivesiden stopper nye, læsesiden redder dem der allerede
ligger på algoserveren.

### T1 · De fem positioner fra 12-08 — målt, ikke udført

| | krav | status |
|---|---|---|
| a | Afstem mod broker på **begge** konti | ⏳ DUO509856 målt; DUQ441063 ikke (hører til T3) |
| b | REPL 16 stk: luk eller adoptér bevidst | ⏳ afventer K2's reconcile 09:20 ET |
| c | De fire fantomer fjernes + **hvad de var** dokumenteres | ✅ ryddet af reconcile 14-08 09:20/09:22 ET. De var entry-ordrer der aldrig blev til en IBKR-position (`not_findable`), bogført lukket til entry, nul P&L |
| d | Nyt opslag EFTER oprydningen: 1:1 | ⏳ |

**Hvorfor REPL ikke blev lukket 14-08:** reconcile placerede en lukke-ordre der
aldrig blev fyldt — `status=PreSubmitted, filled=0/16`. Rækken forblev åben
med vilje. Samme fejlklasse som `4b9c92f`/`ee98f6c`: en ordre ingen følger op på.

**Status 17-08:** IBKR er flad i REPL, feedet er pålideligt (`data_live=True`,
NLV 9.635). Rækken falder nu i fantom-grenen og bogføres lukket ved K2's
opstart. Der findes **ingen** API-vej udefra — `/journal/manual-trade/close`
afviser algo-rækker med vilje.

### T2 · Reconcile — timeout må ikke betyde "handl alligevel" ✅ `3556f55` + `9ef3bf6`

| | krav | status |
|---|---|---|
| a | Timeout → **SPÆRRET**, eksisterende beskyttelse urørt | ✅ og det viste sig at være **fire udgange, ikke én** |
| b | Genforsøg med backoff | ✅ 3 forsøg a 30 s, 5 s × forsøgsnummer |
| c | Reconcile-**job** i scheduleren efter luk | ✅ `reconcile_efter_luk`, 16:15 ET, kun algoserveren |
| d | Mål budgettet, sæt det derefter | ✅ målt — se nedenfor |
| e | Test: fremtving timeout, kræv INGEN position | ✅ `test_reconcile_spaerrer.py` |

**De fire udgange** (18 kaldesteder × 6 strategier) — alle betød *"reconcile
verificerede intet, handl videre"*:

```
timeout                      "fortsætter til handel"
undtagelse                   "sprang fejlet over"
IBKR ikke forbundet          "sprunget over"
positions-feed upålideligt   "sprunget over"
```

**Målingen (d), 70 opstarter 3.–15. august:** median 0,0 s · p90 0,4 s · max
uden for timeouts 0,5 s. Alt over 0,5 s er fra 13-08 og er *præcis 30,0 s* —
timeout'en selv. Fordelingen er todelt: enten under et sekund, eller også
hænger den. **Et større budget ville ikke have hjulpet.** 30 s bliver stående.

K2 ramte timeout'en **2 af 11 dage** — ikke et engangstilfælde.

### T3 · Ordrestatus V3 + V6 — ikke begyndt

- `4b9c92f` og `ee98f6c` ligger på **DUQ441063** (Ibens workstation, Gateway :4002)
- **V3:** slå ordrernes faktiske tilstand op hos broker dér
- **V6:** mønstersøgning efter samme fejlklasse — *en kontrol hvis fejl behandles som en beståelse*

⚠ V6 er nu vigtigere end da den blev skrevet: fire nye forekomster er fundet
siden (reconcile ×4-udgange, journal-500, nævneren i signal-evalueringen).

---

## NIVEAU 1 — billigt, lukkende, timer ikke dage

### T4 · Stitch MES/M2K frem til 10-08 — ikke begyndt
Rådata er friske til 10-08; de sammensyede filer står på 06-08.
**Bestå:** stitched sidste bar = rådataenes sidste bar, og sessionstællingen matcher.

### T5 · ES — probe før beslutning — ikke begyndt
Samme udløbne-kontrakt-probe som 13-08, på ES 202409 → 202506.
Kontrolfikstur **begge veje**: kendt-positiv ES 202609, kendt-negativ ES 201503
(kvalificerer den, kasseres hele resultatet).

- **Svarer ingen** → sagen er lukket af virkeligheden. Noter i harvest-planen, luk E3/J4.
- **Svarer nogen** → hent kronologisk, ældste først. Derefter J4's præregistrerede
  test: Spearman ≥ 0,98 mellem ES/RTY- og MES/M2K-percentilserierne = "udskiftelige".

⚠ Skriv **hvorfor RTY overlevede men MNQ ikke gjorde** — blev RTY høstet før
grænsen passerede, eller opfører retention sig forskelligt pr. produkt? Det
afgør kvartalsjobbets margin.

### T6 · MNQ ind i arkiveringen — nu, ikke ved næste kvartal — ikke begyndt
MNQ er øveinstrumentet, og dets historik kan **aldrig genskabes bagud** (målt:
ti udløbne kontrakter, nul svar). Hver dag uden løbende høst er permanent tabt.

a) MNQ ind i den løbende daglige harvest · b) ind i kvartalsjobbet ·
c) ind i arkivet på ekstern disk med foranderlig/uforanderlig-markering.
**Bestå:** `MNQ_202609` opdateres dagligt uden manuel indgriben.

### T7 · Idempotens — det sidste V1-lukkekrav — ikke begyndt
Kør harvesten to gange, mål identisk resultat. Vis **også** det input der *ville*
få kontrollen til at fejle (fx en harvest der skriver hentetidspunkt i datafilen).
Består den på begge, måler den ingenting.
**Bestå:** V1 kan erklæres lukket i `spec_volatilitet.md`.

### T8 · Fingeraftryks-jobbet — ikke begyndt

a) **Hvorfor producerede 3/8 og 10/8 intet, uden at nogen fik besked?**
Er `is_first_trading_day_of_week` forkert, fejler jobbet tavst, eller fyrede
scheduleren aldrig? ⚠ Svaret gælder **alle** jobs på den scheduler.

b) Derefter: **sluk jobbet** med en note om hvorfor og hvad der skal til for at
genåbne det.

⚠ Søren korrigerede min begrundelse: lag 1 bygges på `vol_cache` (VIX/VIX3M/RVX/SPY
tilbage til 2009), **ikke** på `regime_history.csv`. Den fil hører til den
parkerede v1-motor.

---

## NIVEAU 2

### T9 · V2 — volatilitetsformlerne — blokeret af T4 + T7
Egen spec (`spec_volatilitet.md` §4/V2 + udbygningen af 15-08). Begynder ikke
før V1 er erklæret lukket.

---

## Rapportering (§6)

- ✅ efter T0–T2 samlet
- ⏳ efter T5 og T8 — begge kan ændre planen
- ⏳ to §7-målinger til desktop Claude: er V-test-maskineriet nogensinde afviklet
  på ægte data, og VX-kvalitetstallene fra V0.5 punkt 5

## Hvad der udtrykkeligt IKKE skal gøres (§4)

- Genopliv ikke v1-fingeraftrykket — diagnosticér, sluk så
- Hæv ikke bare `RECONCILE_TIMEOUT_SEC` og kald T2 løst
- Læs ikke signal-evalueringen som "mean reversion er dødt"
- Byg ikke videre på ES før proben har svaret

---

## Udestående drift

**Algoserveren mangler `git pull` + genstart** for at T0 og T2 virker der.
Genstart skal ske før 15:20 dansk tid — ellers er scheduler-vinduerne lukkede
(K2 09:20–09:40 ET) og dagens strategier kommer ikke tilbage.

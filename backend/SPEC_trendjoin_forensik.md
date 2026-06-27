# SPEC: Trend Join Long — forensik til fortolkelig paper-koersel

**Type:** To observe-only tilfoejelser. Goer den live paper-koersel FORTOLKELIG uden
at roere handelslogikken. Loeser praecis de to ting fra reviewet:

- **(1)** Fang katalysator-tidsstemplet -> katalysator-ALDER pr. trade. Saa kan du
  bagefter se om friskhed forudsiger join-kvalitet.
- **(3)** Fang de nyheds-afviste gappers som en MATCHED kontrolgruppe. Saa kan du
  A/B-teste om nyheds-gaten overhovedet tilfoejer vaerdi.

**Hard garanti:** Del A aendrer INGEN entry/exit-beslutning — samme admits, samme
rejects, samme entries, samme exits. Del B koerer EFTER luk, separat proces, egen
client-id, nul kontention med live.

**Tidskritisk:** Kun Del A behoever vaere deployet foer entry-vinduet aabner
16:05 dansk (10:05 ET), og kun hvis dagens session skal taelle. Del B kan skrives
og koeres naar som helst senere — den laeser bare journal-events Del A skrev live.

---

## 1. Afgraensning (garantier)

- Ingen ordrer. Ingen aendring af nogen entry/exit/reject-beslutning.
- **Del A:** kun (a) en ADDITIV tredje returvaerdi paa en funktion, og (b)
  append-only journal-events paa en EKSISTERENDE afvisnings-gren. Kontrolfloejen
  er uroert.
- **Del B:** separat script, egen client-id (45), skriver INTET til handels-state
  (kun sin egen summary + evt. eet aggregat-event). Pull af historiske bars sker
  EFTER luk, saa det aldrig konkurrerer med live-strategiens datafeed.

---

## 2. Del A — live (handels-neutral)

### A1 — katalysator-tidsstempel i entry-forensik

I `algo_trendjoin.py`, `check_positive_catalyst(...)` beregner allerede `best_ts`
(epoch for den valgte bullish overskrift) men SMIDER den vaek. Ret to ting:

1. Funktionen returnerer nu ogsaa `best_ts` som tredje element:
   `return (True, best, best_ts)` paa succes; `(False, "<aarsag>", 0.0)` ellers.
   **De foerste to returvaerdier er UAENDREDE** — verdikt-booleanen og detail-
   strengen er praecis som foer. Kun en tredje vaerdi er tilfoejet.
2. Opdater det ENE kaldested i `_rescan_watchlist`:
   ```
   has_cat, detail, catalyst_ts = await check_positive_catalyst(session, sym)
   ```
   og gem den paa konteksten naar aktien optages: `ctx["catalyst_ts"] = catalyst_ts`.

I entry-forensikken (blokken `snap["trendjoin"] = {...}`) tilfoej to felter:
```
"catalyst_ts": round(ctx.get("catalyst_ts", 0.0), 0),
"catalyst_age_min": round((entry_time.timestamp() - ctx.get("catalyst_ts", 0.0)) / 60.0, 1),
```
**Tz-faelde:** brug UTC-epoch paa begge sider. `entry_time` er tz-aware (ET);
`entry_time.timestamp()` giver korrekt UTC-epoch. Finnhubs `datetime` er allerede
UTC-epoch. Ingen manuel tz-konvertering — saa undgaas mismatch.

Log-linje ved entry (tilfoej til den eksisterende BUY-log):
`... katalysator-alder {catalyst_age_min:.0f} min`.

Bemaerk: i entry-stien ER katalysatoren altid til stede (aktien bestod gaten), saa
`catalyst_ts > 0` her altid.

### A2 — skygge-kandidat-event paa nyheds-afvisning

I `_rescan_watchlist`, paa den eksisterende gren hvor en kandidat afvises som
`ingen_katalysator` (dvs. den bestod `change >= 3%` OG `pris >= $3`, men fejlede
nyheds-gaten), TILFOEJ — efter den eksisterende `log_rejection_change(...)` — en
observation. Afvisningen selv er uroert; vi skriver kun et event mere:

```python
await self._journal.log_event(
    source=self.name, event_type="trendjoin_shadow_candidate", symbol=sym,
    payload={
        "symbol": sym,
        "scan_ts_utc": datetime.now(pytz.UTC).timestamp(),
        "scan_et": now_et.strftime("%H:%M:%S"),
        "change_pct": round(change, 2),
        "price": round(price, 2),
        "news_detail": detail[:120],   # skelner 'ingen frisk' vs 'blandet/negativ'
    })
```
Log-linje: `👻 shadow-kandidat {sym}: change {change:+.1f}%, {detail[:50]}`.

Hvorfor `news_detail` med: det lader Del B stratificere kontrollen i "ingen nyhed"
vs "blandet/negativ nyhed" — sidstnaevnte er en saerligt interessant kontrol
(gappede, HAVDE nyhed, men netto-negativ).

**Vigtig matching-note (for aerlighedens skyld):** nyheds-gaten ligger FOER D2
(SMA200) og foer premarket-high i gate-raekkefoelgen. En `ingen_katalysator`-afvist
kandidat er derfor IKKE endnu D2-tjekket. Del B retter det offline ved at koere D2
+ resten af mekanikken paa kontrollen selv — saa treatment og control bliver
aebler-til-aebler. Vi reorganiserer IKKE de live gates (det ville roere
handelsstien). Matchingen sker i Del B, ikke i live-koden.

---

## 3. Del B — offline (efter luk, naar som helst)

### `trendjoin_shadow_eval.py` — Placering: `C:\Projects\trading_dash\backend\trendjoin_shadow_eval.py`

Bygger den matchede kontrolgruppe og sammenligner mod dagens faktiske trades.
Read-only mod handel. Egen `CLIENT_ID = 45` (maa ikke kollidere med backend —
stop backend eller skift id). Python 3.14 event-loop-fix oeverst; KUN `*Async`-kald.

**Input:** dato (default = i dag, dansk).

**Trin 1 — laes grupperne fra journal (`trading_dash.db`):**
- Control = dagens `trendjoin_shadow_candidate`-events (source="Trend Join Long").
- Treatment = dagens faktiske entries (`trade_forensics`-events for samme source).

**Trin 2 — rekonstruér mekanikken for HVER control-kandidat** (samme gates som live):
- Daily bars -> `prior_close`, `prior_high`, `sma200`. Anvend **D2**: kraev
  `prior_close > sma200` — ellers EKSKLUDÉR (treatment kraever ogsaa D2, saa
  kontrollen skal det for at vaere matched).
- Premarket bars (foer 09:30 ET, `useRTH=False`) -> `premarket_high` (I1-ref).
- 5-min RTH bars for dagen fra `scan_et` og frem (discovery-realisme: ingen shadow-
  entry foer kandidaten ville vaere opdaget; og aldrig foer 10:05 ET).
- Gaa bar-for-bar; shadow-entry paa foerste bar hvor ALLE rammer:
  - D3: pris `>= prior_close * 1.03` (>= 3% netop her)
  - D1: pris `> prior_high`
  - I1: pris `> premarket_high`
  - I2: bar laver ny intradag-HOD
  - I3: RVOL `>= 2` (kumulativ RTH-vol til denne bar / 14-dages snit-dagsvol)

**Trin 3 — maal udfald (let proxy, IKKE den fulde exit-stige):**
- `R = entry - (LOD_at_entry * (1 - 0.01))` (samme stop-regel: LOD minus 1%).
- `MFE_R = (max(high efter entry) - entry) / R`
- `MAE_R = (entry - min(low efter entry)) / R`
- `hit_partial_first` = naaede `+0.75R` FOER `-1R` (partial-vs-stop-race).
- Den fulde partial/breakeven/swing-low-trail-P&L er BEVIDST udeladt i v1 — disse
  tre metrikker fanger om gapperen fortsaetter, uden at reimplementere stigen.

**Trin 4 — samme tre metrikker for treatment-gruppen** (genberegn paa de faktiske
entries med samme formler, saa sammenligningen er aebler-til-aebler).

**Trin 5 — output `trendjoin_shadow_eval_output\summary.txt`:**
- Per kandidat (begge grupper): symbol, entry/ingen-entry, MFE_R, MAE_R, hit_partial_first.
- Aggregat pr. gruppe: antal, andel der overhovedet trigger entry, median MFE_R,
  median MAE_R, `P(hit +0.75R foer -1R)`.
- Control stratificeret: "ingen nyhed" vs "blandet/negativ".

**Forudregistreret laesning (saet foer du akkumulerer dage):** nyheds-gaten
tilfoejer vaerdi KUN hvis treatment over tid separerer fra control paa disse
metrikker — hoejere `P(hit +0.75R foer stop)` og/eller hoejere median MFE_R. Hvis
de to grupper ligger oven i hinanden efter tilstraekkelige dage, goer gaten (eller
dens keyword-heuristik) ingen forskel -> genovervej den. Husk power: enkelte dage
er for faa events; dette er en akkumuleret dom over mange sessioner.

### Kendt vedligeholdelses-risiko (flag aerligt)
Del B reimplementerer entry-gates og exit-proxyen og kan derfor DRIFTE fra
`algo_trendjoin.py`. v1 accepterer det. Foelg op med en separat refaktor-spec der
faktoriserer de RENE beslutnings-funktioner (gate-evaluering, R-beregning) ud af
live-klassen, saa live og offline deler praecis samme kode. Goer IKKE den refaktor
i denne spec — den roerer handelsstien og maa ikke ske foer en session.

---

## 4. Implementeringsnoter

- Del A: ingen nye imports noedvendige ud over `pytz`/`datetime` som filen allerede
  bruger. Skriv via den eksisterende `self._journal.log_event(...)`-sti.
- Del B: Python 3.14 event-loop-fix oeverst foer `ib_async`-import; KUN `*Async`;
  `CLIENT_ID = 45`; genbrug fetch-moenstre fra eksisterende probes; lav output-
  mappen hvis den mangler.
- `--verify`-flag paa Del B: laeser BARE dagens `trendjoin_shadow_candidate` +
  `trade_forensics` (catalyst_ts/catalyst_age_min) tilbage og printer dem — uden
  noget IBKR-pull. Bruges til at verificere at Del A faktisk fangede data.
- Tider logges dansk foerst, ET i parentes.

---

## 5. Verifikation

### Del A (live, efter deploy — Soeren i LiveAlgo-vinduet)
Kode-gennemgang foerst: bekraeft at de FOERSTE TO returvaerdier fra
`check_positive_catalyst` er uaendrede (kun en tredje tilfoejet), og at A2 kun
TILFOEJER et event paa den eksisterende `ingen_katalysator`-gren uden at aendre
kontrolfloej. Derefter, ved naeste live-scan:
- Verdikt-fordelingen i loggen ser normal ud (samme slags admits/rejects som foer).
- Nye linjer `👻 shadow-kandidat ...` vises for nyheds-afviste kandidater.
- Ved en evt. entry vises `katalysator-alder ... min` i BUY-linjen.

### Del A (data fanget — efter mindst eet scan)
```powershell
cd C:\Projects\trading_dash\backend
python trendjoin_shadow_eval.py --verify
```
Forvent: en liste af dagens shadow-kandidater (symbol + news_detail) og, hvis der
var entries, deres `catalyst_ts` / `catalyst_age_min`. Tomt foer foerste scan.

### Del B (efter luk)
Stop backend foerst (client-id-konflikt). Saa:
```powershell
cd C:\Projects\trading_dash\backend
python trendjoin_shadow_eval.py
Get-Content .\trendjoin_shadow_eval_output\summary.txt
```
Bekraeft at journalen er uroert af Del B (uaendret stoerrelse vs foer):
```powershell
(Get-Item .\trading_dash.db).Length
```

---

## 6. Commit (to separate commits — Del A foerst, saa Del A kan deployes alene)

Del A (live forensik):
```bash
git add backend/algo_trendjoin.py
git commit -m "TrendJoin forensik: log catalyst_ts/age + shadow-candidate events (observe-only, trading-neutral)"
git push origin main
```

Del B (offline eval):
```bash
git add backend/trendjoin_shadow_eval.py
git commit -m "Add TrendJoin shadow-eval: matched control vs treatment A/B (offline, read-only)"
git push origin main
```

---

## 7. Naeste skridt

- Deploy Del A foer 16:05 dansk hvis dagens session skal taelle. Ellers i morgen —
  ingen hast, og selve handlen er upaavirket uanset.
- Efter hver session: koer Del B, akkumulér control vs treatment. Doem nyheds-gaten
  foerst naar event-antallet baerer det (din ~400-taerskel), ikke paa enkelte dage.
- Naar gaten er vurderet: hvis den holder, skriv refaktor-spec'en der deler de rene
  entry/exit-funktioner mellem live og offline, saa de ikke kan drifte.
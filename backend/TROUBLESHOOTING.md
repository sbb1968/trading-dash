# TROUBLESHOOTING — Trading Dash

> Praktisk fejlfindingsguide til Trading Dash. Skrevet ud fra erfaringer
> fra de første live-handelsdage (maj 2026). Opdateres når vi lærer nyt.

## Hvornår skal du bruge denne fil?

Når noget ikke virker som forventet under en handelsdag, eller når du
vil forberede dig på problemer før kørsel. Filen guider dig til det
rigtige diagnose-værktøj for den type problem du oplever.

**Hvis du er midt i en handelsdag med live positioner — læs altid før du
handler.** Forhastet kode-ændring eller backend-genstart med åbne
positioner kan koste penge. Diagnose først, fix bagefter, helst efter
markedsluk.

---

## De fire mistanke-kategorier

Når noget går galt, er årsagen næsten altid i én af disse fire kategorier.
Lær at genkende dem — det sparer timer.

### 1. IBKR-laget (konto, subscriptions, gateway)

**Symptomer:**
- Pre-flight fejler på "Kan ikke hente markedsdata"
- `0/N aktier` fik kontekst bygget (ingen historiske bars)
- Error 162 i logs ("Historical Market Data Service error")
- Error 10189 ("no tick-by-tick permission")
- Error 2152 ("no L2 depth")

**Værktøjer:**
- `python diagnose_feed.py` — TRADES vs MIDPOINT-test på AAPL, GME, SNDL
- `python diagnose_feed2.py` — market-data-type test (Live/Frozen/Delayed)

**Fortolkning:**
| Resultat | Konklusion |
|---|---|
| TRADES virker, MIDPOINT virker | Datafeed OK — problemet er andetsteds |
| TRADES tom, MIDPOINT virker | Bar-type-problem (brug MIDPOINT uden for handelstid) |
| Delayed (3) virker, Live (1) fejler | Kontoen mangler realtime-subscription |
| Alt fejler | Bredere problem: forbindelse, konto-status, eller IBKR-side outage |

**Konkret eksempel — dag 1 (28. maj 2026):**
ORB fik 0/25 aktier kontekst bygget. Iben's konto var midt i en
billable-account-transition (fra U23100448 til U25790444), og market
data-subscriptions var sandsynligvis ikke fuldt aktive ved markedsåbning.
Konfluens på Søren's konto (DUN748991) virkede fint samtidig — det
identificerede problemet som konto-specifikt, ikke generelt. Næste dag
test viste at samme tickers (SPRC, NCT, ATPC) returnerede 78 bars hver
på Søren's konto, hvilket bekræftede at IBKR/symbol-niveauet er fint.

Errors 10189 og 2152 er normale på de fleste tickers (kun IEX har L2
depth subscription) og fail-soft i koden — algoritmen kører videre uden.

### 2. TWS-laget (klient-side, port, client-ID)

**Symptomer:**
- "TWS sessionen er forbundet fra en anden IP"
- "Read-Only API" fejl ved ordreoperationer
- Backend kan ikke connecte til port 7497
- Client-ID-konflikt (to processer prøver samme client-ID)

**Værktøjer:**
- Tjek om TWS kører: `Get-Process | Where-Object { $_.ProcessName -like "*tws*" -or $_.ProcessName -like "*java*" }`
- Tjek port 7497: `Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq 7497 }`

**Fix-tjekliste:**
1. Er TWS åben og logget ind på Paper Trading?
2. Edit → Global Configuration → API → Settings:
   - Enable ActiveX and Socket Clients: ✓
   - Read-Only API: ✗ (skal være SLÅET FRA)
   - Port: 7497
   - Allow connections from localhost only: ✓
3. Hvis "forbundet fra anden IP": luk IBKR Mobile-app og Client Portal i
   browser, genstart TWS

### 3. OS-laget (Windows, dvale, netværk)

**Symptomer:**
- Forbindelseshikke der kommer og går
- Backend hænger uventet og kommer i sig selv tilbage
- "3 fejl — genforbinding" gentager sig
- Maskinen virker tabt fra netværket i perioder

**Værktøjer:**
- `python net_stability_test.py --minutes 10` — netværkstest (kør med TWS LUKKET)
- `powercfg /sleepstudy /output sleep_report.xml /xml` — genererer sleep-rapport
- `powercfg /a` — viser tilgængelige strømtilstande
- `powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE` — viser sleep-timer

**Modern Standby (S0 Low Power Idle) er en stille morder.**
Windows 11 på laptops aktiverer Modern Standby som default. Maskinen
ser ud til at køre, men sniger sig i let dvale når den ikke "bruges".
Backend-processer overlever det normalt, men netværk og IBKR-forbindelse
kan blive uregelmæssige.

**Fix:**
```powershell
# Som administrator — deaktiver Modern Standby permanent
reg add HKLM\System\CurrentControlSet\Control\Power /v PlatformAoAcOverride /t REG_DWORD /d 0 /f

# Bagefter: sleep-timere til 0 (intet sleep)
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
```
Genstart maskinen. Verificér med sleep-rapport at `<ConnectedStandby>0</ConnectedStandby>`.

**Netværk:**
Hvis net_stability_test viser <99% succes eller std.afv. >20ms, har du et
faktisk netværksproblem. Hvis det viser 100% succes og lav varians, er
netværket sandsynligvis ikke skyldig — kig på de andre tre kategorier.

### 4. Kode-laget (bugs i strategi-koden)

**Symptomer:**
- Specifikke tickers droppes silent fra universe (uden fejlmeddelelse)
- Diagnose-events mangler i databasen (Lag A/B/C)
- Sjældne fejl der kommer i live men ikke i backtest
- "Truth value of Series is ambiguous" — pandas Series brugt boolean-vis

**Værktøjer:**
- `python verify_orb_session.py --date YYYY-MM-DD --source "Momentum ORB"` — gør stille fejl synlige
- `python dagens_log.py "Konfluens" YYYY-MM-DD` — viser hele dagens journal
- `python test_diagnostics_live.py` — verificerer diagnostik-logging
- `python test_b1_warmup_in_code.py` — verificerer RSI-warmup virker i kode

**Konkret eksempel — dag 1 candle_features-bug:**
Konfluens droppede 9/25 tickers silent. Stack-trace afslørede:
```python
upper_wick = high - close.combine(open_, max)
```
`Series.combine(other, max)` fejlede sporadisk med "truth value of Series
is ambiguous" når der var NaN i data. Fix: brug `np.maximum(close, open_)`
i stedet — element-vis, robust mod NaN. Test-dækning blev tilføjet
(test_b1_warmup_in_code.py) for at fange lignende silent-fail fremover.

---

## Workflows

### "Algoritmen handlede ikke i dag — hvad gør jeg?"

Først: undgå at konkludere "markedet havde ingen setups" uden at tjekke.

1. **Kør verify_orb_session.py for at se om diagnostik blev logget:**
   ```powershell
   python verify_orb_session.py --date YYYY-MM-DD --source "Momentum ORB"
   ```

2. **Hvis "fejlet-stille":** strategien kørte men loggede ikke. Tjek
   backend-log:
   ```powershell
   Select-String -Path .\logs\*.log -Pattern "fejlede|log_universe|log_rejection|log_daily"
   ```

3. **Hvis "ok":** se daily_diagnostics-stikprøven. Tjek
   `max_state_distribution` (for ORB) eller `peak_score` (for Konfluens).
   - Hvis ORB: hvor mange aktier nåede til breakout_detected eller højere?
   - Hvis Konfluens: hvad var oftest manglende betingelse?

4. **Hvis universe-size = 0 i diagnostik men universe_selected viser
   25 tickers:** så fik ingen tickers kontekst bygget. Det matcher
   dag 1's ORB-problem. Næste skridt: kør diagnose_feed.py for at teste
   datafeed.

### "Backend kører men positioner opdateres ikke"

1. Tjek heartbeat — kører diagnostik-heartbeats hvert 5. minut?
   ```powershell
   python -c "import sqlite3; c = sqlite3.connect('trading_dash.db'); print(c.execute(\"SELECT ts_local FROM events WHERE event_type='diagnostics_heartbeat' ORDER BY id DESC LIMIT 3\").fetchall())"
   ```

2. Hvis heartbeats stopper: backend er gået i dvale eller hængt. Tjek
   Modern Standby (afsnit 3).

3. Hvis heartbeats kører men `evaluations: 0`: `_check_ticker` kører ikke.
   Tjek om vi er inden i handelsvinduet (TRADE_START til ENTRY_END/MARKET_CLOSE).

### "Pre-flight fejler"

Typiske årsager i prioriteret rækkefølge:

1. **TWS ikke åben eller forkert konfigureret** (afsnit 2)
2. **Markedsdata-subscription** (afsnit 1) — kør diagnose_feed.py
3. **Forbindelseshikke** (afsnit 3) — kør net_stability_test.py

Pre-flight fejler ofte ved markedsåbning kl. 15:30 DK fordi:
- IBKR's gateway er under høj belastning
- Hvis Iben's konto er i en transition: subscriptions er ikke aktive endnu
- TWS kan have brug for ~30 sekunder efter åbning for at få frisk data

**Hvis pre-flight fejler kl. 15:30 og lykkes kl. 15:35-15:40 — det er
normalt.** Vent 5 minutter og prøv igen før du dykker dybere.

### "Forbindelseshikke under handel"

1. Tjek først om TWS er den, der hænger — kører den stadig? Kan du klikke
   i interfacet?
2. Tjek backend-loggen for "Genforbindelsesforsøg"-beskeder
3. Hvis flere strategier rammer det samtidig på samme maskine: det er
   netværk eller OS, ikke en enkelt strategi
4. Hvis kun én strategi rammer det: kig på strategiens kode først

---

## Reference: tilgængelige værktøjer

| Værktøj | Hvad det gør |
|---|---|
| `diagnose_feed.py` | TRADES vs MIDPOINT-test på AAPL, GME, SNDL |
| `diagnose_feed2.py` | Market-data-type test (Live/Frozen/Delayed) |
| `net_stability_test.py` | Netværkstest mod IBKR + baseline (10 min med TWS lukket) |
| `verify_orb_session.py` | Verificerer at diagnostik-logging virkede for en dag |
| `dagens_log.py` | Komplet rapport over dagens journal-events |
| `test_b1_rsi_warmup.py` | Validerer RSI-warmup princip |
| `test_b1_warmup_in_code.py` | Regression-test af warmup-fixet i ORB-koden |
| `test_diagnostics_live.py` | Unit-tests af BaseStrategy diagnostik-logging |
| `test_orb_diagnostics.py` | Konsistens-test mellem ORB state-rank og entry-states |

Alle ligger i `backend/`. Aktivér venv først:
```powershell
cd C:\projects\trading_dash\backend
venv\Scripts\activate
```

---

## Hvor data ligger

**Database:** `backend/trading_dash.db` (SQLite). Vigtige tabeller:
- `events` — alle logged events (status, diagnostik, log, trade_forensics)
- `trades` — åbne og lukkede positioner med entry/exit-detaljer

**Backend-log:** `backend/logs/*.log` — direkte fra Python's logging-modul

**Config:** `backend/account.yaml` — IBKR konto-mapping

---

## Beslutningstræ ved hikke under handel

```
Oplever du hikke?
│
├─ Sker det FØR markedsåbning?
│  └─ Vent 5-10 min efter 15:30. Hvis stadig: diagnose_feed.py
│
├─ Sker det MIDT i handelsdag?
│  ├─ Er positioner åbne?
│  │  ├─ Ja → Diagnose først. Rør IKKE koden. Note hvad du ser.
│  │  └─ Nej → Du kan eksperimentere mere frit.
│  │
│  └─ Tjek heartbeats. Tjek backend-log. Tjek TWS lever.
│
└─ Sker det EFTER markedsluk?
   └─ Verify_orb_session.py. Dagens_log.py. Plej tid til ordentlig diagnose.
```

---

## Hvad denne fil IKKE dækker (endnu)

- Frontend-fejl (Tauri/React-laget)
- Specifikke variant-konfigurationer for ORB/Konfluens
- Backtest vs live-konsistens (åben opgave: sammenligning af baseline_foer.txt vs dag 1's live trades)
- Force-stop / nødprocedurer ved kritiske fejl

Tilføj sektioner her når der dukker nye lærings-eksempler op.

---

*Sidst opdateret: 29. maj 2026, efter dag 2 oprydning og diagnose-arbejde.*

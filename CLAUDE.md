\# Ibens Trading Dash — Projekt Vidensbase



\## Projektbeskrivelse

En professionel trading platform bygget med \*\*Tauri v2 + React/TypeScript\*\* (frontend) og \*\*Python FastAPI\*\* (backend). Inspireret af Ross Cameron / Warrior Trading. Målet er at Trading Dash på sigt er det eneste vindue der bruges under handelsdagen — IBKR TWS og TradingView kører i baggrunden.



\---



\## Teknisk stack

| Komponent | Teknologi |

|---|---|

| Frontend | Tauri v2, React, TypeScript |

| Backend | Python FastAPI, WebSocket |

| IBKR API | `ib\_async` (efterfølger til `ib\_insync`) |

| Python | 3.14 (vigtigt: kræver async-only API kald) |

| OS | Windows 11 Pro 64-bit, 4K skærm 150% skalering |

| Placering | `C:\\Projects\\trading-dash\\` (frontend) + `C:\\Projects\\trading-dash\\backend\\` |



\---



\## Filstruktur



\### Frontend (`src/`)

| Fil | Beskrivelse |

|---|---|

| `App.tsx` | Hoved-app, window rendering, font-konfigurator |

| `layouts.ts` | WindowId typer og WINDOW\_LABELS |

| `Menubar.tsx` | Menubar med ALT-shortcuts, grouped windows |

| `FloatingWindow.tsx` | Floating window med auto-arrange sync |

| `Screen2.tsx` | Selvstændig skærm 2 |

| `TradeJournal.tsx` | Trade journal med statistik |

| `mockJournalData.ts` | 20 fiktive handler med entry/exit tidspunkter |

| `AlgoDemo.tsx` | Pædagogisk algotrading demo med animeret backtest |

| `LiveAlgo.tsx` | Live algo trading vindue med WebSocket til backend |

| `PaperTrading.tsx` | Paper trading panel med ALT+K/ALT+S |

| `brokerruter.ts` | FASTLAAST broker-rute: Watchlist Futures -> NT8, Watchlist Stocks -> IBKR. Ingen default, hverken her eller i backenden |
| `SwingReport.tsx` | Swing-rapport vindue (POST /swing/analyze, PDF via print, skrift-skyder) |

| `DocsWindow.tsx` | Dokumentation-vindue — lister backend/docs/ PDF'er, aabner dem eksternt |



\### Backend (`backend/`)

| Fil | Beskrivelse |

|---|---|

| `main.py` | FastAPI server: `/ws`, `/ws/algo`, `/swing/analyze`, `/docs/list` + `/docs/file` |

| `ibkr\_connect.py` | IBKR forbindelsesmanager (ib\_async, Python 3.14 kompatibel) |

| `algo\_momentum.py` | Live momentum breakout algoritme |

| `mock\_data.py` | Mock markedsdata generator |

| `alert\_engine.py` | Alert-motor |

| `nt\_forbindelse.py` | NinjaTrader-ordrevej (ATI/OIF): V1-V4-vagter, `klar()`, `send_ordre()`, `afvent_ordre()` |

| `paper\_trading.py` | Mock paper trading logik |

| `backtest\_momentum.py` | Backtest motor med parameter sweep |

| `download\_data.py` | Downloader historiske data fra Yahoo Finance |

| `data/` | 14+ CSV-filer med historiske kursdata |

| `eco_kalender.py` | Oekonomisk kalender for MES: hoest fra ForexFactory, tier-filter, `/eco/*`, eksport til Trading Practice |
| `kalender_tier.py` | Hvilke event-titler vises (tier 1/2) og hvad de hedder hos andre kilder |
| `eco_kilde_probe.md` | Hvad kilde-proben viste — raekkevidde, actual, rate-graenser, daekningshuller |
| `docs/` | PDF-guides serveret af /docs (auto-listende; `NN\_`-praefiks styrer raekkefoelge) |



\---



\## WindowId typer (komplet liste)

```typescript

"scanner1" | "scanner2" | "watchlist" | "newsroom" | "papertrading"

"chart1min" | "chart2min" | "chart3min" | "chart5min" | "chart10min"

"chart15min" | "chart30min" | "chart1time" | "chart4time"

"chartdaily" | "chartweekly"

"level2" | "timesales" | "journal" | "algodemo" | "livealgo"

```



\---



\## Menubar struktur

\- \*\*Tilføj vindue\*\* (ALT+T) — Charts dropdown+knap, Scannere (S/G), Markedsdata (L/M), Øvrige (A/N/P/J/O/V)

\- \*\*Værktøjer\*\* (ALT+V) — Konfigurator (K), Layout-vælger, Tema (foldes ud inline), Åbn Skærm 2 (2)

\- \*\*⊞ Auto-arrange\*\* (ALT+A) — direkte knap

\- Lyd-knap



\### Keyboard shortcuts

\- Dropdown-items har understregede bogstaver som shortcuts (ingen ALT — bare bogstavet når dropdown er åben)

\- ALT+K = Køb i Paper Trading

\- ALT+S = Sælg i Paper Trading



\---



\## Font-system

CSS-variabler per vinduestype: `--fs-title-{type}`, `--fs-header-{type}`, `--fs-content-{type}`



Vinduestyper: `scanner`, `watchlist`, `newsroom`, `chart`, `level2`, `timesales`, `paper`, `journal`, `algodemo`, `menubar`, `livealgo`



Styres fra Konfiguratoren → gemmes i localStorage.



\---



\## Temaer (12 styk)

`original`, `stealth` (default), `bloomberg`, `amber`, `midnight`, `crimson`, `matrix`, `dracula`, `sunset`, `rosegold`, `solarized`, `monochrome`, `arctic`



Tema-valg ligger under \*\*Værktøjer → Tema\*\* (ikke som separat knap i menubar).



\---



\## IBKR Forbindelsessetup



\### Konto

\- \*\*Paper Trading konto:\*\* `DUNXXXXXXX`

\- \*\*Åben position:\*\* -100 QCLS short (paper)

\- \*\*Net Liquidation:\*\* \~$16.728

\- \*\*TWS port:\*\* 7497 (paper trading)



\### TWS konfiguration

1\. `Edit → Global Configuration → API → Settings`

2\. Enable ActiveX and Socket Clients: ✓

3\. Read-Only API: ✗ (VIGTIGT — skal være slået fra)

4\. Port: 7497

5\. Allow connections from localhost only: ✓



\### Market data abonnementer (aktive)

\- NYSE (Network A/CTA) — $1,50/md

\- NYSE American/BATS/ARCA (Network B) — $1,50/md

\- NASDAQ (Network C/UTP) — $1,50/md



\### Python 3.14 kompatibilitet — KRITISK

`ib\_async` (ikke `ib\_insync`) skal bruges. Alle API-kald \*\*skal\*\* bruge `\*Async` versioner:

```python

await ib.qualifyContractsAsync(contract)      # ikke qualifyContracts()

await ib.reqHistoricalDataAsync(...)           # ikke reqHistoricalData()

await ib.connectAsync(...)                     # ikke connect()

```



Fix for Python 3.14 event loop (øverst i filer der importerer ib\_async):

```python

import asyncio

try:

&#x20;   asyncio.get\_event\_loop()

except RuntimeError:

&#x20;   asyncio.set\_event\_loop(asyncio.new\_event\_loop())

```



\---



\## Momentum Breakout Strategi



\### Backtestresultater (bedste konfiguration)

\- \*\*Stop:\*\* -2% · \*\*Target:\*\* +4% · \*\*Vol:\*\* 1.5x · \*\*Exit:\*\* 10:30 ET

\- 67 handler · Win Rate 50.7% · P\&L +$1.538 · Profit Factor 1.62 · MaxDD -$542

\- Gemt: `backend/data/backtest\_results\_best.csv`



\### Live algoritme (`algo\_momentum.py`)

```

Entry-kriterier:

&#x20; 1. Pris bryder ORB High (første 15 min: 09:30–09:44 ET)

&#x20; 2. Volumen ≥ 1.5x gennemsnitlig volumen

&#x20; 3. RSI(14) < 80

&#x20; 4. Handelsvindue: 09:45–10:30 ET



Exit (hvad end kommer først):

&#x20; - Stop loss: -2% fra entry

&#x20; - Take profit: +4% fra entry

&#x20; - Tidsbaseret: lukker alle positioner kl. 10:30 ET



Kapital: $10.000 per handel · Max 3 samtidige positioner

Universe: IBKR scanner (STK.US.MAJOR, pris $1-20, vol >500k)

Fallback: GME, AMC, CLOV, SKLZ, MVIS, OCGN, TLRY, SNDL, BBIG, SPRT

```



\### Pre-flight tjek (kører automatisk ved start)

1\. Er IBKR forbundet?

2\. Er konto-data tilgængelig (net liquidation > 0)?

3\. Kan vi hente historiske bars for AAPL?



\---



\## LiveAlgo vindue — Daglig procedure



\### Tidsplan (dansk tid)

| Tid | Handling |

|---|---|

| 15:15 | Åbn TWS, log ind på Paper Trading |

| 15:20 | `cd C:\\Projects\\trading-dash\\backend \&\& uvicorn main:app --reload` |

| 15:25 | Åbn Trading Dash → Live Algo vindue |

| 15:28 | Klik \*\*▶ Start Algoritme\*\* (grøn knap) |

| 15:45 | Algoritmen begynder automatisk at handle (09:45 ET) |

| 16:30 | Algoritmen lukker alle positioner automatisk (10:30 ET) |



\### WebSocket flow

\- Frontend (`LiveAlgo.tsx`) forbinder til `ws://127.0.0.1:8000/ws/algo`

\- Genforbinder automatisk hvert 3. sekund hvis forbindelsen tabes

\- `{ command: "start" }` starter algoritmen

\- `{ command: "stop" }` stopper algoritmen

\- Backend broadcaster `algo\_status` og `algo\_trade` beskeder i realtid



\---



\## Trade Journal



\### JournalTrade interface

```typescript

interface JournalTrade {

&#x20; id, date, entry\_time, exit\_time, ticker, setup, side,

&#x20; entry\_price, exit\_price, shares, pnl, pnl\_pct, duration\_min,

&#x20; scanner, timeframe, emotion, followed\_plan, notes, tags

}

```



\### Setup-typer

`ORB` | `Momentum` | `News Play` | `VWAP Bounce` | `Bull Flag` | `Reversal` | `Anden`



\### Emotions

`Rolig` | `Nervøs` | `Overmodig` | `Disciplineret` | `FOMO`



\### Storage

localStorage key: `td\_journal\_trades`



\---



\## Backtest motor (`backtest\_momentum.py`)



\### Data-download

```bash

cd C:\\Projects\\trading-dash\\backend

python download\_data.py    # downloader 5m og 1m data fra Yahoo Finance

python backtest\_momentum.py  # kører parameter sweep over 8 konfigurationer

```



\### Tilgængelige data

14 CSV-filer i `backend/data/` — 5-minutters og daglige candles for:

NVAX, CLOV, SPRT, BBIG, ATER, SKLZ, MVIS, RIDE, OCGN, TLRY, AMC, GME, BBBY, SNDL + SPY, QQQ, IWM



\---



\## AlgoDemo vindue

Pædagogisk demo til at vise algotrading til ikke-tekniske brugere.

\- Viser 12 fiktive handler baseret på rigtige backtestresultater

\- Animeret log der simulerer at algoritmen kører

\- Resultatvisning med equity-kurve, win rate, profit factor

\- Data: `src/AlgoDemo.tsx` (ingen ekstern afhængighed)



\---



\## Kendte problemer og løsninger



| Problem | Løsning |

|---|---|

| `ib\_insync` virker ikke med Python 3.14 | Brug `ib\_async` i stedet: `pip install ib\_async` |

| `RuntimeError: There is no current event loop` | Tilføj event loop fix øverst i filen (se ovenfor) |

| `Trading TWS session is connected from a different IP` | Luk IBKR Mobile app og Client Portal i browser, genstart TWS |

| `Read-Only API` fejl | TWS → Edit → Global Configuration → API → fjern hak ved Read-Only API |

| Historiske bars returnerer tomt uden for handelstid med `TRADES` | Brug `MIDPOINT` i stedet uden for handelstid |

| Market data abonnement equity-krav | Abonner på live-kontoen — deles automatisk med paper trading |
| ⚠ Test fejler mod kode der ER rettet | Python godkender en `.pyc` paa (mtime-sekund, kildestoerrelse). Gendanner man en fil i samme sekund som en tidligere import, og aendringen var en ombytning af linjer (samme stoerrelse), koerer den GAMLE bytecode videre. Kostede en time 06-10. `rm -rf __pycache__` eller koer `python -B` |



\---



\## Næste skridt (backlog)



\### Høj prioritet

\- \[ ] Teste algoritmen live på første handelsdag (kl. 15:28 dansk tid)

\- \[ ] Observere pre-flight tjek og scanner output

\- \[ ] Evaluere første dags handler og justere parametre



\### Mellemfrist

\- \[ ] Konto-vindue der viser IBKR konto-balance, equity og positioner live

\- \[ ] Scanner-vindue med IBKR live scanner i stedet for mock-data

\- \[ ] Automatisk journalisering af algoritmens handler

\- \[ ] Ordrehistorik der henter alle ordrer fra IBKR direkte



\### Langsigt

\- \[ ] Live trading (rigtige penge) når paper trading er konsistent profitabel

\- \[ ] Flere scanner-definitioner (Running Up, HOD Momentum, Halt scanner)

\- \[ ] Konfigurerbar scanner-UI i frontend

\- \[ ] Notifikationer (lyd/popup) ved nye handler



\---



\## Dokumentation-vindue (`docs`)

PDF-guides vist som knapper; klik aabner PDF'en EKSTERNT (systemets browser/PDF-laeser via Tauri `openUrl`).

\- \*\*Filer:\*\* `backend/docs/*.pdf` — committet til git (`.gitattributes` markerer `*.pdf` som binary).

\- \*\*Auto-listende:\*\* `/docs/list` laeser mappen ved hvert kald. Ny PDF = laeg fil i mappen, commit, faerdig. Ingen kodeaendring.

\- \*\*Raekkefoelge:\*\* numerisk praefiks `NN_` i filnavnet (fx `01_start_med_at_laese_mig.pdf`); `docs_list` stripper praefikset fra den viste titel.

\- \*\*Sikkerhed:\*\* `/docs/file/{name}` serverer kun `.pdf` i mappen (sti-traversal afvist), inline (ingen Content-Disposition).

\- Genvej i Menubar: Oevrige → Dokumentation (ALT+T, K).



\## Deployment (exe + launcher)

Tauri-exe'en er KUN frontend; backenden (uvicorn :8000) skal koere separat.

\- \*\*Build:\*\* `npm run tauri build` → `src-tauri/target/release/app.exe` + NSIS/MSI-installere. (`build` = `vite build`; tsc er separat `npm run typecheck`.)

\- \*\*Launcher:\*\* `start_trading_dash.bat` (repo-rod) starter backenden hvis :8000 ikke svarer, venter paa HTTP, aabner exe'en. `install_shortcut.ps1` laver skrivebordsgenvej m. ikon.

\- \*\*Distribuer:\*\* `git pull` henter alt UNDTAGEN `app.exe` (build-artefakt) — kopiér exe'en manuelt. Genstart backenden efter pull (koerer uden `--reload`).



\---



\## Installation og opstart



\### Frontend

```bash

cd C:\\Projects\\trading-dash

npm install

npm run tauri dev

```



\### Backend

```bash

cd C:\\Projects\\trading-dash\\backend

venv\\Scripts\\activate

pip install fastapi uvicorn ib\_async pytz pandas numpy yfinance

uvicorn main:app --reload

```



\### IBKR forbindelsestest

```bash

python ibkr\_connect.py    # tester forbindelse, konto og bars

python test\_feed.py        # tester at market data abonnementer virker

```


---

## Manuel handel: to brokere, én kodesti

Watchlist Stocks sender **altid** til IBKR. Watchlist Futures følger **maskinens
opsætning**: har `account.yaml` en `nt_forbindelse`-blok, går futures til NT8;
har den ikke, går de til IBKR. Backenden svarer på `GET /ordre/rute`, og
`src/brokerruter.ts` spørger den.

⚠ **Armeringen ER ruten.** De to kan ikke komme i utakt, fordi det er samme
faktum. Det blev lavet om 28-09: reglen stod hardkodet som "futures → NT8,
altid", hvilket er rigtigt som slutmål og forkert som overgang — Ibens maskine
har ingen `nt_forbindelse`, så hendes første klik ville have svaret
*"NinjaTrader-ordrevejen er spærret"*, og hun handler MES på IBKR gennem netop
den knap.

⚠ **Der er stadig ingen default.** Kan ruten ikke hentes, spærres knapperne med
besked. MES kan handles hos begge, så en fejlrutet ordre er ikke en
fejlmeddelelse men en rigtig position på den forkerte konto ($2.863 initial
margin hos IBKR mod $50 intraday hos NT8).

| | IBKR | NinjaTrader (ATI) |
|---|---|---|
| Ordrevej | `ordre_forbindelse` → TWS/Gateway | OIF-fil i `Documents\NinjaTrader 8\incoming\` |
| Konto bestemmes af | port (4002 paper / 4001 live) **+** `order.account` | **kun** kontonavnet i kommandoen |
| Positionskontrol før salg | spørger brokeren (`kontroller_ordre`) | spørger brokeren (`kontroller_ordre_nt8`, ATI's `MarketPosition`); journal-vagt kun når positionen er UKENDT |
| Fyldpris | `place_paper_order` | `afvent_ordre()` læser `Filled\|` + `AvgFillPrice\|` |
| Opfølgning på uafklaret ordre | `ib.trades()` ved hvert kig | ⚠ **ingen** — et menneske skal se i NT8's Orders-fane |

⚠ **Kontraktmåneden hentes fra IBKR**, også for NT8-ordrer. NT8 vil have
`MES 12-26`; der findes ingen lokal rullekalender, og `nt_instrument()` nægter at
gætte (MESU6 udløb 18-09-2026 — en konstant fra august ville have virket i seks
uger og derefter været tavst forkert). NT8-handel kræver derfor IBKR oppe, men
bars til forensikken kommer derfra alligevel.

Forensikken er **fælles**: `broker` er et argument til `registrer_entry`/
`registrer_exit`, ikke en kodesti. Samme `trades`-række, samme snapshot, samme
indikatorer. ⚠ `find_aaben()` **skal** have `konto` med — ellers lukker et
NT8-salg en IBKR-entry.

### Armering og paper → live
Ordrevejen findes kun hvis `account.yaml` har en `nt_forbindelse`-blok under
`instance:` (maskinlokal, ikke i git). Uden den svarer et klik "spærret".

```yaml
  nt_forbindelse:
    konto: Sim101          # NT8's egen simulator — lokal, gratis
```

| Konto | Hvad | Markedsdata |
|---|---|---|
| `Sim101` | NT8's indbyggede simulator, ruter ingen steder | ⚠ til ORDRER: nej · til FYLDNING: ja |
| `DEMO8580770` | Tradovate-demo, rigtig infrastruktur | ⚠ $4/md |
| `2080414` | **live**, Payward Europe (CY), CySEC 342/17 | ⚠ $4/md |

⚠ **Sim101 kan modtage ordrer uden markedsdata, men ikke fylde dem.** Målt
27-09-2026 kl. 21:47: ordren blev oprettet (`New state='Submitted'`) og derefter
afvist med *"Sim101, There is no market data available to drive the simulation
engine."* Sim101 er altså ikke det gratis trin det ligner — den beviser
skrivevejen, ikke fyldningen. Og uden fyldning ingen `trades`-række.

⚠ **NT8's ordrebekræftelse skal være slået fra** (Tools → Options → Trading →
"Confirm order placement"). Med den slået til venter hver OIF-ordre på et
menneskeklik i NT8: loggen skriver `processing`, men der oprettes intet, ATI
melder ingenting, og OIF-filen bliver liggende låst. Signaturen er
**`processing` i loggen uden en `Order=`-linje**. Trading Dash har sin egen
bekræftelse, så NT8's er en dublet der ligger det forkerte sted.

⚠ **ATI RAPPORTERER POSITIONER** — men nøglen er ikke den vi sender.
Strømmen pusher `MarketPosition|`, `Position|`, `AvgEntryPrice|` og
`RealizedPnL|` pr. instrument og konto. Vi sender `MES 12-26` i OIF; strømmens
nøgle er `MES DEC26` (`nt_forbindelse.ati_noegle()` oversætter). Ved siden af
den ligger **forældede ekkoer** under `@MES`, `MESZ26` og `MES Z6`, som 28-09
alle stod til `1` mens den rigtige stod til `-4` — slår man forkert op, får man
et forkert svar der ser rigtigt ud. Derfor slås præcis én nøgle op, og
manglende nøgle er **UKENDT**, aldrig "flad".

⚠ **V4 advarer, den spærrer ikke** (ændret 28-09). Dukker der en ukendt konto
op i ATI-strømmen — hvilket live-kontoen gør i samme øjeblik den finansieres —
råber `klar()` op via `advarsler` i profilen, men sender stadig ordren. Før
kastede den, hvilket ville have standset *al* NT8-handel, også Sim101, som
følge af en handling (finansiering) der er nødvendig for at få markedsdata.
Beskyttelsen var overflødig: V1 skriver kontoen eksplicit i hver kommando, så
en fremmed konto i NT8 kan ikke modtage vores ordre. Advarslen journaliseres
som `nt_ukendt_konto_i_stroemmen` og følger med ordresvaret.

⚠ **Live kræver TO ændringer**, ikke én: `konto: 2080414` **og**
`tillad_live: true`. Kontonummeret alene spærres af V2, fordi det ikke er en
kendt simulationskonto. IBKR får den andenlås gratis af portnummeret; ATI har
ingen, så den er bygget i `nt_forbindelse.py`.

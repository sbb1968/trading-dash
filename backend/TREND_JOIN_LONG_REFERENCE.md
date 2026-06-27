# Trend Join Long — sådan virker den (HumbledTrader vs. vores implementering)

> Reference til `algo_trendjoin.py` (live, paper) + `rules.json` (parametre) + `trendjoin_forensik.py` (indsigt).
> Skrevet for at afklare *præcis* hvordan listen ("top % gainers") dannes og opdateres.

---

## 0. Kort svar på det typiske spørgsmål

> "Først finder vi top gappers (steget fra sidste luk til åbningen), det er universet vi bruger resten af dagen, og hver 30. min tjekker vi om nogen i universet er steget >3%?"

**Næsten — men to ting er anderledes:**

1. **Der måles fra sidste luk til AKTUEL pris (live), ikke til åbningskursen.** En aktie der står +X% over gårsdagens luk *lige nu* er en top-gainer — uanset om bevægelsen skete ved åbning eller intradag kl. 13:00. Så intradag-movere fanges også.
2. **Universet er IKKE låst ved åbning.** Hver 30. min laves et **friskt scan af markedet**, og universet **vokser** efterhånden som nye aktier krydser tærsklen. Det er ikke "lås ved åbning → filtrér en fast liste".
3. ✅ Rigtigt: 3%-tærsklen anvendes hver runde — men på de **friske** scan-resultater (nye kandidater), og 3% tjekkes **igen live** lige før entry.

### Terminologi: "top % gainers", ikke "gappers"

Listen vi laver er strengt taget en **top % gainers**-liste (TradingViews egen betegnelse: "US Top Gainers") — aktier sorteret efter **% ændring fra forrige luk, målt live hele dagen**. Et **"gap"** er per definition et *åbnings*-fænomen (springet fra forrige luk til dagens åbning, målt én gang kl. 09:30). Tidligt på dagen er de to næsten identiske (top-gainerne *er* morgen-gapperne), men senere på dagen indeholder listen også **intradag-runnere** der ikke gappede ved åbningen.

I dette dokument bruger vi derfor **"top % gainers"** om listen, og reserverer **"gapper"** til den delmængde der faktisk gappede ved åbningen. (`rules.json` bruger ordet "gap" løst i `D3_min_gap_pct_from_prior_close` — det betyder "% over forrige luk", ikke nødvendigvis et åbnings-gap. Strategien hedder **Trend Join Long**, fordi den *joiner* en igangværende optrend, ikke specifikt et gap.)

Resten af dokumentet forklarer det i detaljer.

---

## 1. HumbledTraders pipeline (kilden)

Fra humbledtrader.com/blog/ai-trading-bot-claude-ibkr. Hendes bot er en **loop der gentager sig hvert 30. minut** (fra hendes egen grafik "The Full Trading Pipeline"):

1. **Scan the market** — gennemgå hele markedet, find potentielle movers
2. **Pull live quotes** — hent aktuelle kurser (% ændring vises live)
3. **Filter through gappers** — behold dem der er steget nok
4. **Execute orders** — placér entry, stop & target
5. **Repeat autonomously — hver 30. minut** → tilbage til trin 1

**Kernen i hendes tese:** kun aktier hvis bevægelse er **udløst af en positiv nyhedskatalysator** har høj sandsynlighed for at *fortsætte* opad. Et gap uden katalysator fader typisk tilbage. Derfor tjekkes nyheder, før en aktie kommer i puljen. Det er den del der **ikke kan backtestes billigt** (kræver dyr historisk, tidsstemplet nyhedsdatabase) — derfor tester vi den **live på paper**.

Vigtigt: hendes "gappers" = aktier op meget **fra forrige luk**, målt på det **aktuelle** tidspunkt i loopet. Fordi loopet kører hele dagen, fanger det også aktier der først krydser tærsklen senere på dagen.

---

## 2. Vores implementering (`algo_trendjoin.py`) — trin for trin

### 2a. 30-min RESCAN (pipeline-loopet)
Hver 30. minut (`RESCAN_INTERVAL_SEC = 1800`) kalder `_rescan_watchlist()`:

- **Scan:** `fetch_tv_top_gainers()` (TradingViews screener) → top 25 aktier sorteret efter **dagsændring faldende**. TV's `change`-felt = `(aktuel pris − forrige luk) / forrige luk` og **opdateres løbende hele dagen**. Det er derfor intradag-movere fanges — listen er ikke fra åbningen, men fra "lige nu".
  - Filtre i screeneren: pris $3–500, børser NYSE/NASDAQ/AMEX, type=stock, volumen >500k.
  - ⚠ **~15 min forsinket** data (TV uden login). Discovery lagger derfor ~15 min; selve entry bruger live IBKR-data (realtid).

- **Pr. ny kandidat** (én der ikke allerede er i puljen eller afvist i dag) løber den gennem **gates** i rækkefølge:
  1. `change ≥ 3%` (MIN_GAP_PCT) — ellers `under_gap`
  2. `pris ≥ $3` (MIN_PRICE_USD) — ellers `under_pris`
  3. **NYHEDSKATALYSATOR (kernen):** `check_positive_catalyst()` — Finnhub company-news i dag, keyword-sentiment. Kræver mindst én **frisk** (≤20t) **positiv** overskrift, netto bullish. Ellers `ingen_katalysator`.
  4. **D2:** forrige dags luk > SMA200 (daglig). Ellers `d2_fejl_under_sma200`.
  5. Hent **premarket-high** (bars før 09:30, `use_rth=False`) som I1-reference.
  - Består alle → aktien **OPTAGES** i puljen (`self.universe`), og dens kontekst gemmes (prior_close, prior_high, SMA200, premarket_high, gap%, katalysator-overskrift).

- **Puljen AKKUMULERER:** en optaget aktie bliver i puljen resten af dagen. En afvist aktie huskes som afvist (`_vetted[sym]=False`) så vi ikke nyheds-tjekker den igen hver runde (Finnhub rate-limit). Næste rescan **tilføjer** kun nye navne.

### 2b. Kontinuerlig overvågning (hvert 15. sek) — entry-trigger
Mellem rescans overvåges **alle** aktier i puljen (`_check_ticker`). For hver henter den seneste færdige **5-min bar** (live IBKR), opdaterer dagens HOD/LOD, og evaluerer entry (`_evaluate_entry`) i vinduet **10:05–15:30 ET**. ALLE skal ramme:

| Filter | Betingelse |
|---|---|
| D3 (live) | pris ≥ 3% over forrige luk **netop nu** |
| D1 | pris > forrige dags high |
| I1 | pris > premarket-high |
| I2 | bar laver **ny intradag-top** (HOD) |
| I3 | RVOL ≥ 2 (kumulativ RTH-vol vs 14-dages snit, live-proxy) |

→ rammer alle: **køb på bar-luk**. Største gap først, op til 5 samtidige positioner.

**Bemærk: 3% tjekkes TO steder** — (a) ved scan-vetting (er den overhovedet en top-gainer?) og (b) live ved hver entry-evaluering (er den ≥3% over forrige luk i selve entry-øjeblikket?). En aktie kan altså være optaget i puljen, men vente på at entry-betingelserne falder på plads — eller falde ud af ≥3% igen og dermed ikke trigge.

### 2c. Exit (rules.json)
Stop = dagens RTH-low × 0.99 ved entry. R = entry − stop.
- **Stop-first** (pessimistisk): rammer bar.low ≤ stop → ud.
- **Partial:** +0.75R → sælg 1/3.
- **Breakeven:** +1.0R → flyt stop til entry.
- **Trail:** efter breakeven trailes til seneste 5m swing-low (2 bars hver side).
- **Force-close 15:51 ET** — holder ALDRIG over natten.

### 2d. Risiko
1% af NLV i risiko pr. handel, notional-loft 10% af NLV, **max 5 samtidige** positioner (`rules.json risk`).

---

## 3. Hvor vi AFVIGER fra HumbledTrader (og hvorfor)

| Emne | HumbledTrader | Vores | Hvorfor |
|---|---|---|---|
| Scan-bredde | "hele markedet" | TV top-gainers (NYSE/NASDAQ/AMEX, pris $3–500, vol >500k, stock) | Genbruger jeres validerede, robuste screener-helper; undgår at bygge en ny markeds-scanner |
| Data-friskhed | live quotes | TV ~15 min forsinket (discovery) + live IBKR (entry) | TV-screeneren har ikke realtid uden betalt login; entry-beslutningen er dog realtid |
| Univers | re-scannes hver runde | re-scannes hver runde + **akkumulerer** + husker afviste | Færre gentagne Finnhub-kald (rate-limit), samme netto-effekt: nye gappers optages løbende |
| Gap-reference | fra forrige luk | fra forrige luk (D3) | Matcher `rules.json` (`D3_min_gap_pct_from_prior_close`) |
| Nyhedskilde | (hendes opsætning) | Finnhub company-news + keyword-sentiment | Det I allerede har i platformen |

**Ingen af afvigelserne ændrer tesen:** top % gainers med positiv nyhedskatalysator, join momentum, flertrins-exit. De er pragmatiske valg givet hvilke data vi har.

---

## 4. Sådan ser du hvad pipelinen gør (forensik)

- **Live i Live Algo-vinduet:** loglinjer som
  `🔁 30-min scan: 22 gappers · max-change +18.3% (ABCD) · 1 ny optaget · pulje 3`
  og `📰➕ ABCD optaget: gap +18.3%, katalysator «...»`.
- **Efter dagen** — kør forensikken (read-only, sikker ved siden af den kørende strategi):
  ```
  cd C:\Projects\trading_dash\backend
  python trendjoin_forensik.py            # i dag
  python trendjoin_forensik.py --day 2026-06-27
  python trendjoin_forensik.py --full     # ALLE gainers pr. scan
  ```
  Viser pr. 30-min-runde: alle fundne top % gainers, **max-change + top-gainer**, og verdikt pr. aktie (optaget / ingen_katalysator / d2_fejl / under_gap / ...), plus max-change-kurven over dagen, gainer-frekvens, og dagens handler.

---

## 5. Parametre (alle i `rules.json` / toppen af `algo_trendjoin.py`)

| Parameter | Værdi | Betydning |
|---|---|---|
| `RESCAN_INTERVAL_SEC` | 1800 | nyt top-% gainer-scan hvert 30. min |
| `MIN_GAP_PCT` | 3.0 | gainer-tærskel (% over forrige luk) |
| `MIN_PRICE_USD` | 3.0 | min aktiekurs |
| entry-vindue | 10:05–15:30 ET | ingen nye entries udenfor |
| `FORCE_CLOSE_ET` | 15:51 | tvangsluk (ingen overnight) |
| `RVOL_MIN` | 2.0 | relativ volumen-krav |
| stop | LOD − 1% | initial stop |
| partial / BE | 1/3 @0.75R / @1.0R | profittagning + breakeven |
| max positioner | 5 | samtidige |
| risiko / notional | 1% / 10% af NLV | sizing |

---

*Strategien er paper-only, manuel start (auto-starter aldrig), og helt isoleret fra K2/BuyTheDip/Europa-reversion. Den live-testes nu, fordi nyhedsfilteret — kernen — ikke kan backtestes billigt historisk.*

# Momentum ORB — Teknisk reference

Komplet specifikation af MomentumORB-strategien. Sammenfletter logik fra `entry.py`, `exit.py`, `config.py`, `strategy.py`, `algo_momentum.py`, `risk_manager.py` og `market_conditions.py`.

Læses sammen med koden — alle filreferencer er i `backend/strategies/momentum_orb/` med mindre andet er angivet.

Sidste opdatering: 16. maj 2026.

---

## 1. Arkitektur

Strategien består af tre komponenter der kan testes uafhængigt:

```
MomentumORBStrategy (strategy.py)
├── MomentumORBEntry (entry.py)   — finder entry-signaler
└── MomentumORBExit  (exit.py)    — styrer stop, target, trail per position
```

Begge engines er **variant-aware**: parametre kommer fra `VariantConfig` der vælges via `LIVE_VARIANT_KEY` (config.py). Aktiv variant pt: **`all_winner`** med trail aktiveret.

Strategien bruges af to consumers med samme API:
- `algo_momentum.py` (live trading via IBKR snapshots)
- `backtest_momentum.py` (CSV-bars, parameter sweep)

Det er det væsentligste designvalg: backtest og live deler entry/exit-kode 1:1. Hvis backtest passer overens med live, er det fordi det er **samme kode**.

---

## 2. Aktiv variant: `all_winner` (med trail)

Defineret i `config.py`:

```python
"all_winner": VariantConfig(
    name="All-winner: +1% BE, vol 3x, ORB Mid stop, trail 0.5%",
    stop_mode="orb_mid",
    target_pct=0.04,                 # +4% — bliver fjernet når trail aktiveres
    vol_mult=3.0,                    # 3× snit-volumen
    breakeven_enabled=True,
    breakeven_trigger_pct=0.01,      # +1% → flyt stop til entry
    trail_enabled=True,
    trail_activate_pct=0.015,        # +1.5% → aktiver trail
    trail_distance_pct=0.005,        # 0.5% under highest_high
)
```

Default-værdier arvet fra `VariantConfig`:
- `rsi_max = 80.0`
- `orb_end_minutes = 14` (ORB-vindue 09:30-09:44)
- `retest_timeout_sec = 300` (5 min)

### Backtest-resultat (16. maj 2026)

På 11 small-cap tickers (AMC, ATER, BBBY, CLOV, GME, MVIS, NVAX, OCGN, SKLZ, SNDL, TLRY) med 5-min historiske bars:

- 46 trades, Win 37.0%, P&L +$42.54, PF 1.18, MaxDD -$87.38, Sharpe +0.49
- Exit-mix: 5 stop / 0 target / 1 trail / 40 force-close

Bemærk at backtest-resultatet ovenfor er fra perioden hvor `TRADE_END_TIME = 10:30`. Efter ændringen til 15:55 vil resultaterne sandsynligvis se anderledes ud (flere trails udløses, færre force-closes) — der bør køres ny backtest når data tilgængelig.

---

## 3. Tidskonstanter — VIGTIG ÆNDRING

Der er nu **to forskellige tider** der adskiller "stop med nye entries" fra "luk alle positioner":

| Konstant | Værdi | Defineret i | Effekt |
|----------|-------|-------------|--------|
| `ORB_START` | 09:30 ET | strategy.py | ORB-vindue starter |
| ORB-slut | 09:30 + `orb_end_minutes` (= 09:44) | strategy.py via `_orb_end_time()` | ORB-vindue slutter, ORB High/Low/Mid kendes |
| `TRADE_START` | 09:45 ET | strategy.py | Entry-vindue åbner |
| `ENTRY_END` | 11:00 ET | algo_momentum.py | **Stop med nye entries** — eksisterende positioner fortsætter |
| `MARKET_CLOSE` | 15:55 ET | algo_momentum.py | Loop force-closes alle positioner |
| `TRADE_END_TIME` | 15:55 ET | exit.py | Exit-engines force-close (backup) |
| `RETEST_TOLERANCE` | 1.001 | config.py | Pullback rammer ved `bar.low ≤ orb_high × 1.001` |

`MARKET_CLOSE` og `TRADE_END_TIME` er samme værdi men i forskellige scopes — det første styrer trading-loopets force-close, det andet styrer exit-engines force-close. Begge findes som backup mod hinanden.

**Bag-konsekvens:** Force-close har ændret rolle. Før var det den primære exit-mekanisme (de fleste positioner lukkede ved 10:30). Nu er det et sikkerhedsnet — vi forventer at stop/target/trail håndterer langt de fleste exits.

---

## 4. Entry-logik (entry.py)

### State machine per (ticker, dag)

```
              [WAITING]
                 │
                 │ breakout-betingelser opfyldt
                 ▼
       [BREAKOUT_DETECTED] ──── timeout 300s ───▶ [WAITING]
                 │
                 │ bar.low ≤ orb_high × 1.001
                 ▼
       [AWAITING_RETEST]
                 │
                 │ bar.close > orb_high (bounce)
                 ▼
         EntrySignal → [DONE_FOR_DAY]
```

State holdes på `MomentumORBEntry`-instansen i `_ticker_state[ticker]`. `reset_for_day(date, context)` nulstiller alt for én ticker.

### Breakout-betingelser (alle skal være sande)

I `check_entry()` når state == `WAITING`:

```python
bar.close > orb_high
AND bar.volume >= avg_vol * vol_mult     # 3.0 for all_winner
AND rsi(closes) < rsi_max                # 80
AND avg_vol > 0                           # sanity check
```

`rsi()` bruger Wilder's smoothing med periode 14 (samme formel som standard RSI). Returnerer 50.0 hvis < 15 closes, 100.0 hvis ingen tab (undgår div-by-zero).

`closes`-listen akkumuleres bar for bar i `_closes[ticker]` — opdateres altid uanset state.

### Pullback-detektion

I state `BREAKOUT_DETECTED`:

```python
elapsed = (bar.timestamp - self._breakout_time[ticker]).total_seconds()
if elapsed > retest_timeout_sec:    # 300 sek = 5 min
    return to WAITING

if bar.low <= orb_high * 1.001:     # RETEST_TOLERANCE
    transition to AWAITING_RETEST
    self._retest_low[ticker] = bar.low
```

Timeout-logik: hvis pullback aldrig sker, drop forsøget og vent på næste breakout. Det betyder samme ticker kan have flere breakout-forsøg på samme dag indtil den lykkes med break-and-retest.

### Bounce-detektion (entry-trigger)

I state `AWAITING_RETEST`:

```python
if bar.low < self._retest_low[ticker]:
    self._retest_low[ticker] = bar.low    # track laveste pullback

if bar.close > orb_high:
    return EntrySignal(...)               # ENTRY!
    transition to DONE_FOR_DAY
```

`DONE_FOR_DAY` betyder: ingen flere entry-forsøg for denne ticker i dag. Selv hvis position lukkes igen efter 2 minutter, prøver vi ikke et nyt breakout på samme ticker. Det er en designbeslutning — ikke en teknisk begrænsning.

### Entry-vindue cut-off (algo_momentum.py)

I trading-loopet:

```python
entries_allowed = t < ENTRY_END    # 11:00 ET

# I _check_ticker:
if not entries_allowed:
    return
```

Eksisterende positioner berøres ikke — kun nye entry-tjek springes over.

### EntrySignal metadata

```python
EntrySignal(
    ticker=...,
    entry_price=bar.close,
    entry_time=bar.timestamp,
    metadata={
        "orb_high":   ...,    # bruges af exit til ORB Mid-stop
        "orb_low":    ...,
        "retest_low": ...,    # kun bogføring
    },
)
```

`metadata["orb_high"]` og `metadata["orb_low"]` er **påkrævet** af `MomentumORBExit.open_position()`.

---

## 5. Exit-logik (exit.py) — 3-stadie hybrid AKTIV

### ExitState per position

Gemmes på `Position.state`:

```python
@dataclass
class ExitState:
    orb_high: float
    orb_low: float
    orb_mid: float                       # (high + low) / 2
    stop: float
    target: Optional[float]              # None i stage 3
    highest_high: float                  # følges af update()
    trail_stop: Optional[float] = None
    stage: int = STAGE_INITIAL           # 1 → 2 → 3
```

### Initial setup ved `open_position(signal, shares, variant_key)`

```python
stop = _initial_stop(entry_price, orb_high, orb_low, config)
target = entry_price * (1 + target_pct)
```

`_initial_stop()` afhænger af `config.stop_mode`:

| stop_mode | Formel |
|-----------|--------|
| `fixed_pct` | `entry × (1 - fixed_stop_pct)` |
| `orb_mid` | `max(orb_mid, entry × 0.99)` |
| `orb_low` | `max(orb_low, entry × 0.99)` |

**1% gulv**: stop er ALDRIG tættere end 1% under entry. Selv hvis ORB Mid ligger meget tæt på entry-prisen, falder vi tilbage på `entry × 0.99`. Det forhindrer absurd snævre stops på tickers med tæt ORB.

For aktiv variant `all_winner`:
- `stop_mode = "orb_mid"` → stop = max(ORB Mid, entry × 0.99)
- `target = entry × 1.04` → +4% sikkerhedsnet

### 3-stadie hybrid — AKTIV i all_winner

#### Stadie 1 → 2: Break-even

Trigger: `highest_high >= entry × (1 + breakeven_trigger_pct)` (0.01 for all_winner = +1%)

Effekt: `stop = max(stop, entry)` og `stage = 2`. Stop kan kun gå op, aldrig ned.

#### Stadie 2 → 3: Trailing aktiveret

Trigger: `highest_high >= entry × (1 + trail_activate_pct)` (0.015 for all_winner = +1.5%)

Effekt:
- `target = None` (target fjernes — kun trail eller force-close lukker)
- `trail_stop = highest_high × (1 - trail_distance_pct)` (0.005 for all_winner = 0.5%)
- Hvis `trail_stop > stop`, opdater stop. **Stop ratcheter aldrig nedad.**
- `stage = 3`

#### Stadie 3 vedligehold

Hver `update()` der ser ny `highest_high`:
```python
new_trail = highest_high * (1 - trail_distance_pct)
if new_trail > trail_stop:
    trail_stop = new_trail
if trail_stop > stop:
    stop = trail_stop
```

Stop trækkes opad i hælene på prisen, men aldrig hurtigere end `trail_distance_pct` afstand.

### Exit-tjek (rækkefølge er vigtig)

Både `check_exit_live(price, time)` og `check_exit_bar(bar)` følger samme rækkefølge:

```
1. Force-close (15:55 ET)        → exit @ current_price / bar.close
2. Stop loss (price <= stop)     → exit @ stop
3. Target  (price >= target)     → exit @ target (kun stage 1-2)
```

**Konfliktregel for backtest**: hvis én OHLC-bar både rammer stop OG target (kursen svinger gennem begge i samme periode), fyrer **stop først**. Konservativ konvention — antager worst case.

Exit-reasons:
- `REASON_STOP` — almindelig stop loss
- `REASON_TARGET` — target hit
- `REASON_TRAIL` — stop loss men `stage == 3` (kosmetisk distinktion til journal)
- `REASON_FORCE_CLOSE` — 15:55 ET timeout

---

## 6. Live runtime flow (algo_momentum.py)

På handelsdagen:

```
1. Pre-flight (kører én gang ved start)
   ├─ IBKR forbundet?
   ├─ NLV > 0?
   ├─ Kan hente AAPL bars?
   └─ MarketConditions.check()
      └─ Score < 40 → afbryd, sæt position_size_pct = 0

2. Universe (kører én gang ved start)
   ├─ IBKR scanner: STK.US.MAJOR, pris $1-20, vol > 500k
   ├─ Top 25 gainers
   └─ Fallback hvis scanner tom: [GME, AMC, CLOV, SKLZ, MVIS, OCGN, TLRY, SNDL]

3. ORB-beregning (per ticker, én gang)
   ├─ Hent bars 09:30-09:44 (5-min eller 1-min)
   ├─ orb_high = max(highs)
   ├─ orb_low  = min(lows)
   └─ avg_vol  = mean(volumes)

4. Trading loop (hvert 30 sekund)
   t = now_et.time()
   entries_allowed = t < ENTRY_END   # 11:00 ET

   Hvis t >= MARKET_CLOSE (15:55 ET):
   └─ Luk alle positioner, afslut

   For hver ticker:
   ├─ Hent seneste snapshot fra IBKR
   ├─ Hvis position findes: opdatér exit-state + tjek exit
   ├─ Hvis IKKE position OG entries_allowed:
   │  ├─ entry.check_entry(ticker, pseudo_bar, context)
   │  └─ Hvis signal:
   │     ├─ risk_manager.approve_order(...)
   │     ├─ conn.place_paper_order(ticker, "BUY", shares)
   │     ├─ exit.open_position(signal, shares, variant_key)
   │     └─ Journal entry forensics
   │
   Exit-flow (når exit_decision returneres):
   ├─ conn.place_paper_order(ticker, "SELL", shares)
   ├─ Bogfør trade, opdater P&L
   └─ Journal exit forensics
```

---

## 7. Risk Manager (risk_manager.py)

Hver ordre skal igennem `RiskManager.approve_order()` før den sendes til IBKR.

### Afvisningskriterier

1. **Emergency stop aktiv** — sat manuelt (ALT+X) eller udløst af NLV < threshold
2. **Daglig tab-grænse** ramt (`total_pnl <= -daily_loss_limit`, default $300)
3. **Max åbne positioner overskredet** (default 6 på tværs af alle strategier)
4. **Max total eksponering overskredet** (default $20.000)
5. **Duplicate ticker** — samme ticker i 2 strategier (kan slås fra)
6. **Per-strategi grænser** (max_loss_per_trade, max_position_size)

### Cooldown

Afviste ordrer med samme `(strategy, ticker, reason)` blokeres stille i 60 sekunder — undgår spam i log når breakout-betingelser hænger ved.

### NLV emergency

Hvis IBKR rapporterer `NetLiquidation < nlv_emergency_threshold` (default $5.000) → emergency stop udløses automatisk.

**Bemærk**: Threshold er sænket fra $16.000 til $5.000 (16. maj 2026) for paper trading. På live trading bør den justeres op baseret på faktisk kapital.

---

## 8. Market Conditions (market_conditions.py)

Score 0-100 beregnes fra:

| Komponent | Vægt |
|-----------|------|
| VIX | 0-30 (lav=0, ekstrem=halv vægt) |
| SPY gap | 0-30 (positiv=fuld, kraftigt negativ=hard veto) |
| Scanner gainers | 0-25 (mange = flere point) |
| Scanner høj relvol | 0-15 |

Score-bands:
- **≥ 60**: "aktiv" → `position_size_pct = 1.0`, `skal_handle = True`
- **40-59**: "moderat" → `position_size_pct = 0.5`
- **< 40**: "rolig" → `position_size_pct = 0.0`, `skal_handle = False`

**Hard veto**: `spy_gap_pct < -1.5%` → label = "rolig" uanset score. Logikken: når S&P åbner kraftigt ned er risikoen for falske breakouts for høj.

VIX hentes via yfinance (ingen IBKR-abonnement nødvendigt). SPY hentes via IBKR med yfinance-fallback.

---

## 9. Variant-oversigt (config.py)

| Variant | stop_mode | target | vol_mult | BE | Trail | Bemærk |
|---------|-----------|--------|----------|----|----|-----|
| baseline | fixed_pct 2% | +4% | 1.5 | nej | nej | Den oprindelige enkle version |
| A | orb_mid | +4% | 1.5 | +3% | +4%/1.5% | Default hybrid |
| B | orb_low | +4% | 1.5 | +3% | +4%/1.5% | Mere plads til vejrtrækning |
| C | orb_mid | +4% | 1.5 | +3% | +4%/2.0% | Bredere trail |
| D | orb_mid | +4% | 1.5 | +2% | +4%/1.5% | Tidligere break-even |
| **all_winner** | **orb_mid** | **+4%** | **3.0** | **+1%** | **+1.5%/0.5%** | **AKTIV — smal trail oven på BE** |

Skift variant ved at ændre `LIVE_VARIANT_KEY` i `config.py` og genstarte backend.

---

## 10. Designbeslutninger og deres begrundelse

**Hvorfor break-and-retest i stedet for naiv breakout?**
Naive breakouts har høj false-positive rate. Break-and-retest filtrerer det meste støj fra ved at kræve at niveauet "holder" under sælger-pres. Vi handler kun de breakouts hvor køberne demonstrerer kontrol *to gange*.

**Hvorfor 3× volumen i `all_winner`?**
Tidligere version brugte 5× hvilket var for restriktivt — for få handler at lære fra. 3× balancerer selektivitet med tilstrækkelig flow til at validere strategien. Backtest viser stadig kun 46 trades på 11 tickers over flere måneder med 3×.

**Hvorfor +1% break-even + +1.5% trail i stedet for fast +1% target?**
Tidligere version solgte hardcoded ved +1% og kunne ikke deltage i større upside. Med BE-mekanikken sikrer vi at +1% gevinst beskytter mod tab (stop flyttes til entry) mens vi holder muligheden åben for at trail tager over ved +1.5%. Worst case: handlen ender break-even i stedet for +1% gevinst — vi har byttet sikker lille gevinst for mulig stor gevinst.

**Hvorfor 0.5% trail-distance?**
Aggressivt valg fordi setups allerede er højselekterede (3× vol + perfekt break-and-retest). Vi vil ikke give meget tilbage af gevinsten. Sammenlign med varianter A/B/C der bruger 1.5-2% trail — de er bygget til +4% target og længere positioner.

**Hvorfor target på +4% når BE udløses ved +1%?**
Target fungerer som sikkerhedsnet hvis kursen springer direkte fra +1% til +4% uden retracement (sjældent men muligt). I praksis tager BE og trail over først. Target er sat så højt at det næsten aldrig udløses — kun ved meget kraftige bevægelser.

**Hvorfor `DONE_FOR_DAY` efter ét entry-forsøg?**
Hvis vi taber på første breakout og prøver igen 10 minutter senere, jagter vi prisen. Disciplinen siger: ét forsøg, så videre til næste ticker i morgen.

**Hvorfor ENTRY_END på 11:00 ET (ikke 10:30)?**
Strategien blev oprindeligt designet til 10:30 cut-off (Ross Cameron-stilen — momentum løber ud efter første time). Udvidet til 11:00 for at give strategien lidt mere mulighed for at finde setups. Eksisterende positioner får dog hele dagen til at udvikle sig nu.

**Hvorfor 15:55 force-close i stedet for 10:30?**
Tidligere version solgte alle positioner hardcoded ved 10:30 — det dræbte for mange vindere. Nu får positioner lov at løbe indtil markedsluk eller deres egne exit-regler udløses. 15:55 er sikkerhedsnet mod natten-over-eksponering.

**Hvorfor ORB Mid og ikke ORB Low som stop?**
ORB Low er typisk -3-5% under entry — for stort tab per handel. ORB Mid er typisk -1-2%, hvilket matcher den ønskede 1:1 til 1:2 risk/reward med +1-4% target.

**Hvorfor 1% gulv på stops?**
ORB Mid kan ligge meget tæt på entry (måske $0.05 væk på en lavprisaktie). Uden gulv ville stop blive ramt på normal støj. 1% sikrer aktien får plads til at trække vejret.

---

## 11. Forensics-integration

Hver entry og exit logger nu et **forensics-snapshot** til journalen (event_type `trade_forensics`):

**Entry-snapshot indeholder:**
- Indicators ved entry-tid: RSI, MACD, EMA(9/20/50), Bollinger, VWAP, VWAP-distance
- Setup-context: ORB high/mid/low, vol_mult-faktor, retest_low, time since breakout
- Tape (60 sek tilbage): trade-count, buy/sell volume, large prints (>$10k)
- L2 (hvis tilgængelig): bid/ask depth, imbalance, market makers

**Exit-snapshot indeholder:**
- Samme indicators ved exit-tid
- Exit-reason og prisbevægelse siden entry
- Realized P&L og pnl_pct
- Stage ved exit (1/2/3) — fortæller om BE/trail var aktiv

Analyseres via `python show_forensics.py --compare` for at finde mønstre der adskiller vindere fra tabere.

---

## 12. Filreference

```
backend/strategies/momentum_orb/
├── __init__.py       Exports: MomentumORBStrategy, VARIANTS, LIVE_VARIANT_KEY, VariantConfig
├── config.py         VariantConfig + alle 6 varianter + LIVE_VARIANT_KEY
├── entry.py          MomentumORBEntry state-machine
├── exit.py           MomentumORBExit 3-stadie hybrid (TRADE_END_TIME = 15:55)
└── strategy.py       MomentumORBStrategy topnivå-klasse

backend/
├── algo_momentum.py        Live runtime (ENTRY_END = 11:00, MARKET_CLOSE = 15:55)
├── backtest_momentum.py    Backtest (bruger MomentumORBStrategy)
├── strategy_base.py        BaseStrategy + StrategyConfig + StrategyStats
├── strategy_manager.py     StrategyManager med risk_manager
├── risk_manager.py         RiskManager med daglig grænse + NLV emergency (threshold $5.000)
├── market_conditions.py    Score 0-100, VIX/SPY/scanner
├── trade_forensics.py      Snapshot-builder for journal
├── tape_buffer.py          180-sek rolling tape buffer
├── indicators.py           RSI, MACD, EMA, Bollinger, VWAP
└── show_forensics.py       SQL-analyseværktøj
```
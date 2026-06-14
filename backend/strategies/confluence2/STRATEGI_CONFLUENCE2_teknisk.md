# Konfluens 2 — teknisk beskrivelse

Impuls-strategi på 1-minuts bars, long-only. Implementerer projektets
`Strategy/EntryEngine/ExitEngine`-kontrakt (`strategies.base`), så den kører af
den eksisterende live-algo og backtest-engine uden ændringer i dem — som
Konfluens 1 og MomentumORB.

**Kildefiler:**
- Regel/engines: `strategies/confluence2/strategy.py`
- Parametre/varianter: `strategies/confluence2/config.py`
- Live-wrapper: `algo_confluence2.py`
- Indikatorer (genbrugt fra K1): `strategies/confluence/indicators.py`
- Universe-screener: `strategies/confluence/tv_scanner.py`

Alle indikatorer pre-computes over hele bar-serien i `build_session_context`,
så **live == backtest**. Kun FÆRDIGE bars evalueres (caller leverer dem) — dette
undgår intrabar-fejlen K1 led under.

---

## Entry

Et signal kræver **2 obligatoriske impuls-kriterier OG mindst `context_threshold`
(= 2) af 4 kontekst-kriterier** på samme afsluttede 1-min bar. Diagnostikken
udtrykkes som en 7-tegns "bricks"-streng `VBG EKRT`.

### Obligatoriske impuls-kriterier

| Brick | Navn | Betingelse | Parameter (default) |
|---|---|---|---|
| **V** | Volumen-spike | `volume ≥ vol_mult × forrige bars volumen` OG `volume ≥ vol_ma_mult × glidende snit` | `vol_mult=2.0`, `vol_ma_mult=1.5`, `vol_ma_len=20` |
| **B** | Krop/range | `krop ≥ body_mult × snit-krop` (krop = `|close − open|`) | `body_mult=1.5`, `body_ma_len=10` |
| **G** | Stærk grøn | `close > open` OG `close_pos ≥ close_pos_min` (close i øverste del af range) | `close_pos_min=0.60` |

`impulse_ok = V AND B AND G`. Hvis ikke opfyldt → `rejected: "ingen impuls"`.

### Kontekst-kriterier (mindst 2 af 4)

| Brick | Navn | Betingelse | Parameter |
|---|---|---|---|
| **E** | Ikke overudvidet | `close ≤ ema_fast + overext_atr_mult × ATR` | `ema_fast_len=9`, `overext_atr_mult=2.5`, `atr_len=14` |
| **K** | Break | `bar.high > forrige bars high` | — |
| **R** | RSI-momentum | `rsi_min ≤ RSI ≤ rsi_max` | `rsi_len=14`, `rsi_min=50.0`, `rsi_max=78.0` |
| **T** | Trend ikke imod | `close > ema_slow` | `ema_slow_len=20` |

`context_hits = E + K + R + T`. Kræver `context_hits ≥ 2`, ellers
`rejected: "impuls OK men kun N/2 kontekst"`.

### Entry-udførelse
- Entry-pris = **bar.close** (færdig bar, ikke intrabar).
- Side: altid `long`.
- Cutoff: ingen nye entries når `bar.time_et ≥ entry_cutoff_hhmm = 15:00 ET`.
- Warmup: spring over hvis < `min_warmup_bars = 25` bars.
- Metadata gemt på signalet: `impulse_low`, `impulse_high`, `impulse_close`,
  `score` (kontekst-hits), `bricks`.

---

## Exit

Fire arketyper er implementeret som valgbare varianter; **live-varianten er
`A_impulse_low`** (`LIVE_VARIANT_KEY = "A_impulse_low"`).

| Variant-nøgle | `exit_mode` | Logik |
|---|---|---|
| **A_impulse_low** (LIVE) | `impulse_low` | Exit når `bar.low ≤ trail_stop`. `trail_stop` starter = impuls-candlens low. `breakeven_r=None`, `catastrophe_stop=False` (impuls-low *er* stoppet). |
| A_be1r | `impulse_low` | Som A, men flyt stop til entry når `high ≥ entry + 1.0×R`. |
| A_confirm | `impulse_low` | Som A, men fyld kun pending entry hvis næste bars open ≥ impuls-close. |
| B_trail_hl | `trail_hl` | Trailing higher-low (for stram på 1-min — kun reference). |
| C_momentum | `momentum` | Exit på rød candle eller `close < ema_fast`, med impuls-low som katastrofe-stop. |
| D_target_1_5r / 2r / 3r | `target_r` | Fast target ved `entry + target_r×R`, impuls-low som katastrofe-stop. |

Hvor `R = entry − impulse_low` (risiko per aktie).

**Backstop for alle varianter:** session-luk når `bar.time_et ≥
force_close_hhmm = 15:45 ET` → exit til `bar.close`, reason `session_close`.

**Live-sti** (`check_exit_live`) er bevidst konservativ — kun pris/tid, ingen
intrabar candle-mønstre: stop ved `impulse_low`, evt. target, og session-luk.

---

## Universe — "Intraday Volatility"-screener

K2 bruger IKKE top-gainers. Den replikerer Sørens TradingView "Intraday
Volatility"-screener (`fetch_tv_intraday_volatility`):

| Filter | Værdi |
|---|---|
| Pris | $5 – $50 |
| Markedsværdi | $5 B – $1 T |
| Gennemsnitsvolumen (30 d) | ≥ 500.000 |
| ATR(14) ugentlig | > 5 % |
| Børser | NASDAQ, NYSE, AMEX (TV's "NYSE Arca"), CBOE |
| Top-N | 25 |

---

## Sizing & risiko (live, fra `main.py` → `StrategyConfig`)

- **Kapital per handel:** `max_position_size = $1.000`; `shares = int(($1.000 ×
  position_size_pct) / entry_price)`. `position_size_pct` (default 1.0) kan
  skaleres ned af risk-manageren efter markedsforhold.
- **Max samtidige positioner:** 3 (`max_open_positions`).
- **Per-handel tab-grænse:** $150 (`max_loss_per_trade`).
- **Dagligt tab-stop:** $300 (`max_daily_loss`) — strategien pauses resten af dagen.

> Bemærk: live-wrapperen bruger **fast kapital per handel** (ikke risiko-baseret
> %-sizing). R bruges kun til exit-matematik (impuls-low / target), ikke til at
> bestemme antal aktier.

---

## Session-konstanter

| Konstant | Værdi |
|---|---|
| `SESSION_START_HHMM` | 09:30 ET |
| `SESSION_END_HHMM` | 16:00 ET |
| `entry_cutoff_hhmm` | 15:00 ET |
| `force_close_hhmm` | 15:45 ET |
| `min_warmup_bars` | 25 |
| `MINTICK` | 0.01 |

---

## Valideringsstatus

`A_impulse_low` valgt efter validering på:
- **Maj 2026** (in-sample): portefølje profit-faktor **2,85 / 3,01**.
- **April 2026** (out-of-sample): PF **1,83 / 1,69**.

Begge med lav drawdown og robust på tværs af fifo/priority-signalvalg.
Varianterne B/C/D overlevede ikke slippage + positions-loft og er kun reference.

**Status: paper trading.** Endnu ikke valideret live med en live scanner; begge
testmåneder var gunstige for momentum. Rigtige penge overvejes først efter
konsistent paper-performance over en længere periode med blandede markedsforhold.

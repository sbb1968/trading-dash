# Relativ Styrke — teknisk beskrivelse

Long-only intradag **tværsnitlig relativ styrke** på 1-minuts bars. Én beslutning pr.
dag: ved beslutningstid T rangeres dagens scanner-univers tværsnitligt efter morgen-
relativ-styrke, og der gås LONG i top-3 (equal weight), som holdes til EOD force-close.
Ingen continuous rescan, ingen news-gate, ingen target/stop — ét snapshot, ét valg.

Bygger på en bestået offline-backtest (spor D). Edgen er **selection alpha** = (snit top-K)
− (snit hele universet); den måles ikke i selve wrapperen men i den akkumulerede shadow-eval.
Strukturelt spejler wrapperen `BuyTheDipLive` (samme robuste maskineri: scoped reconcile,
bekræftet force-close m. genforsøg, throttlet status, forensik-hooks).

**Kildefiler:**
- Live-wrapper: `algo_relstyrke.py`
- Bestået backtest (score/entry/exit-kilden): `cross_sectional_rs_backtest.py`
- Shadow-eval (Route B-måling, akkumulerer selection alpha): `relstyrke_shadow_eval.py`
- Paritets-harness (live == backtest bevis): `relstyrke_parity.py`
- Universe-screener (delt motor): `strategies/shared/tv_scanner.py`

---

## Beslutning (én pr. dag)

Ved T scores hvert navn i dagens univers, rangeres tværsnitligt, og de øverste K vælges:

| Trin | Beregning | Parameter |
|---|---|---|
| **Score** | `early_rs = (price_T − open_0930) / open_0930` | `SCORE = "early_rs"` |
| **open_0930** | Åbningskurs på første RTH-bar (09:30) | — |
| **price_T** | Luk på sidste færdige bar med tid ≤ T | — |
| **Rangering** | Percentil i dagens univers (tværsnitligt) | — |
| **Valg** | LONG de øverste K navne, equal weight | `TOP_K = 3` |

- **Look-ahead-ren:** scoren bruger KUN bars ≤ T (assert i koden, som i backtesten).
- **Entry:** markedsordre ved beslutningen (~næste bars open efter T).
- **Side:** altid `long` (small-cap borrow-veto → ingen shorts).

---

## Bar-paritet (vigtigt)

IBKR stempler en bar på dens **åbningstid**, så 09:45-baren er først komplet kl. 09:46.
Backtestens `price_T` er 09:45-barens luk. Fyrede vi beslutningen kl. 09:45:xx, ville
09:45-baren ikke være lukket endnu, og live ville se 09:44-luk → en anden top-3 end
backtesten (divergerede på 16 af 43 dage i paritets-testen).

Derfor fyrer beslutningen først når 09:45-baren er **komplet**:

| Konstant | Værdi | Rolle |
|---|---|---|
| `DECISION_ET` | 09:45 ET | score-cutoff (early_rs bruger bars ≤ 09:45) |
| `DECISION_FIRE_ET` | 09:46 ET | fyrings-tid (efter 09:45-baren er lukket) |

Efter fixet matcher live backtestens top-3 på **alle 43 testede dage** (Spearman 1.00).
Dette ændrer kun bar-timingen — ikke T/K/score/exit. (Samme forming-vs-completed-bar-lære
som Konfluens 2.)

---

## Exit

Positionerne holdes til EOD og lukkes samlet — **ingen** target/stop i baseline (afviger
ikke fra den validerede regel; en beskyttende stop skulle backtestes for sig):

| Exit | Betingelse |
|---|---|
| **Force-close** | `bar.time_et ≥ FORCE_CLOSE_ET = 15:51 ET` → luk alt |

Force-close er robust: bekræftet fyldning med op til `FORCE_CLOSE_MAX_ATTEMPTS = 4`
genforsøg (`FORCE_CLOSE_RETRY_DELAY = 4 s`). Ulukkede positioner bevares åbne og fanges
af opstarts-reconcile — aldrig natten over.

---

## Sizing & risiko

Notional-baseret equal-weight (der er ingen entry-stop, så "risiko = entry−stop" giver
ikke mening; størrelsen styres på beløb):

```
strategy_notional = NOTIONAL_PCT × NLV      (udledes ved on_start)
per_name = min( strategy_notional / TOP_K , PER_NAME_NOTIONAL_CAP_USD )
shares   = int( per_name / entry )
```

| Parameter | Værdi |
|---|---|
| `NOTIONAL_PCT` | 3 % af NLV (fordeles på K=3 → ~1 % pr. navn) |
| `PER_NAME_NOTIONAL_CAP_USD` | $1.000 (haleværn pr. navn) |
| `STRATEGY_NOTIONAL_FB` | $500 (fallback hvis NLV ikke kan læses) |
| `max_open_positions` | 3 (= TOP_K) |
| `max_loss_per_trade` | $200 |
| `max_daily_loss` | $300 (pr.-strategi) |

Kræver `shares ≥ 1`, ellers droppes navnet. Long-only → rå P&L indeholder markeds-/
small-cap-beta; edgen er selection alpha (måles i shadow-eval), ikke det rå afkast.

---

## Univers & konstanter

| Konstant | Værdi |
|---|---|
| `SESSION_START` | 09:30 ET |
| `DECISION_ET` / `DECISION_FIRE_ET` | 09:45 / 09:46 ET |
| `FORCE_CLOSE_ET` | 15:51 ET |
| `UNIVERSE_PRICE_MIN … MAX` | $3 … $500 |
| `UNIVERSE_MIN_VOLUME` | 500.000 |
| `REQUIRE_ALL_GREEN` | False (bredt tværsnit at range på) |
| `UNIVERSE_TOP_N` | 25 |
| `LOOP_SLEEP_SECONDS` | 15 |

De fire plateau-parametre (T=09:45, K=3, score=early_rs, exit=eod) er **frosne** — ændres
ALDRIG uden en ny backtest.

---

## Operationelt

- **Konto:** DUO509856 (paper, delt konto). Navn/source = `"Relativ Styrke"` overalt
  (orderRef, journal, events, Studio-meta).
- **Auto-start:** algoserveren via scheduleren, `RELSTYRKE_START_ET = 09:28 ET` (genforsøg
  til `RELSTYRKE_RETRY_UNTIL_ET = 09:44 ET`, inden beslutningen fyrer 09:46). Forskudt efter
  K2/BuyTheDip/Trend Join for TWS-pacing. Instance-guard (`instance_role != "algoserver"`)
  springer auto-start over på workstation → den delte konto dobbelt-startes ikke.
  `start_strategy` er idempotent. Wrapperens egen interne guard er blød (advarsel), så
  manuel smoke-test på workstation er mulig.
- **Reconcile:** scoped ved opstart — rører kun egne `source="Relativ Styrke"` journal-rows,
  aldrig blind-flatten (delt konto). Pålideligt live positions-read; ved degraderet/tomt
  feed springes reconcile over (undgår at forældreløsgøre en ægte position).
- **Shadow-eval (Route B):** `relstyrke_shadow_eval.py` auto-køres efter luk
  (`RELSTYRKE_EVAL_START_ET = 16:05 ET`, egen client-id 49). Wrapperen emitterer ved
  beslutningen ét event med HELE tværsnittet + valgte top-3; eval'en rekonstruerer hver
  handel som backtesten (entry = næste bars open, exit = EOD) og måler dagens realiserede
  selection alpha. Akkumuleres i `relstyrke_shadow_eval_output/alpha_log.csv` + dateret
  summary, så beviset kan trendes over sessioner.

---

## Valideringsstatus

Spor D fra regime-fingeraftrykket ("relativ værdi / stock-picking"). **Første** strategispor
der bestod sin præregistrerede test: selection alpha (top-K minus universe-snit) positiv i
BÅDE in-sample (maj, +0,46 median/dag) OG out-of-sample (apr, +0,58), hit-rate 0,60/0,67,
med et sammenhængende plateau over score/T/K/exit; en uafhængig score (above_vwap) pegede
samme vej. Paritet bevist: live == backtest på score, entry og selection-alpha-måling.

**Status: paper trading.** Forbehold: lille stikprøve (43 dage / 44 navne) + universe-
mismatch (backtesten kørte på et midcap-univers, live på scannerens rigtige liste).
Backtest-hærdning på flere måneder kører parallelt, men **paper er den endelige dommer**.
Rigtige penge overvejes først efter konsistent, positiv selection alpha over mange sessioner.
Retirement-trigger: hvis det ugentlige regime-fingeraftryk holder op med at flage "relativ
værdi / stock-picking", er strategiens forudsætning brudt → re-evaluér.

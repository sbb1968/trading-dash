# BuyTheDip — teknisk beskrivelse

Long-only intradag mean-reversion på 1-minuts bars. Køber dip-bouncen efter en
impuls — K2's komplement (K2 køber impuls-toppen, BuyTheDip køber dykket).
Entry/exit-reglen er den validerede `june_correlation.scan_trade`, live-tilpasset:
detektér dip på **færdige** 1-min bars, entr på bounce-barens **luk** (mere
konservativt end backtestens open-entry — paper er dommeren).

Strukturelt spejler den `Confluence2Live` (samme robuste maskineri: scoped reconcile,
bekræftet force-close m. genforsøg, throttlet status, forensik-hooks), men er ellers
**helt adskilt** fra K2 — eget scan, egne parametre, egen konto-tracking. Kun det
globale dagstab deles.

**Kildefiler:**
- Live-wrapper: `algo_buythedip.py`
- Universe-screener (delt motor m. K2): `strategies/shared/tv_scanner.py`
- Validerings-reference: `june_correlation` (scan_trade-reglen)

---

## Entry

Setup detekteres på hver færdig 1-min bar i et glidende vindue (`LOOKBACK = 20` bars):

| Trin | Betingelse | Parameter |
|---|---|---|
| **Impuls** | Run-up i vinduet: `(ref_high − ref_low) / ref_low ≥ MIN_RUNUP_PCT` | `MIN_RUNUP_PCT = 3.0 %` |
| **Dip** | `bar.low ≤ ref_high × (1 − DIP_PCT/100)` | `DIP_PCT = 1.5 %` |
| **Bounce** | Dip vender → entry på bounce-barens `close` | — |

- **Entry-pris** = `bar.close` (færdig bar).
- **dip_low** = laveste low i vinduet → bruges som stop.
- **Side:** altid `long`.
- **Entry-vindue:** kun `SESSION_START (09:30) … OPEN_UNTIL_ET (10:30)` ET. Ingen nye
  entries efter 10:30.
- Ved flere samtidige kandidater åbnes de efter **dybeste `dip_depth`** først
  (`dip_depth = (ref_high − dip_low) / ref_high × 100`).

---

## Exit

Positionen bæres med tre exits (tjekkes på færdige bars):

| Exit | Betingelse |
|---|---|
| **Stop** | `bar.low ≤ stop` hvor `stop = dip_low` |
| **Target** | `bar.high ≥ target` hvor `target = entry × (1 + TARGET_PCT/100)`, `TARGET_PCT = 2.0 %` |
| **Force-close** | `bar.time_et ≥ FORCE_CLOSE_ET = 15:55 ET` → luk (backstop før 16:00) |

Force-close er robust: bekræftet fyldning med op til `FORCE_CLOSE_MAX_ATTEMPTS = 4`
genforsøg (`FORCE_CLOSE_RETRY_DELAY = 4 s`), så en position aldrig bæres natten over.

---

## Sizing & risiko

Risiko-baseret med notional-loft (deploy-valg — backtesten var %-baseret; tunes på paper):

```
risk_per_share = entry − stop            (= entry − dip_low)
shares = int( min( RISK_BUDGET_USD / risk_per_share ,
                   NOTIONAL_CAP_USD / entry ) )
```

| Parameter | Værdi |
|---|---|
| `RISK_BUDGET_USD` | $100 (risiko entry→stop pr. handel) |
| `NOTIONAL_CAP_USD` | $1.000 (haleværn; værste observerede ~−$116) |
| `max_open_positions` | 3 |
| `max_loss_per_trade` | $150 |
| `max_daily_loss` (globalt) | $300 — pauser strategien resten af dagen |

Kræver `shares ≥ 1` og `risk_per_share > 0`, ellers droppes kandidaten. Risk-manageren
kan skalere sizing efter markedsforhold før ordren sendes.

---

## Session-konstanter

| Konstant | Værdi |
|---|---|
| `SESSION_START` | 09:30 ET |
| `OPEN_UNTIL_ET` (entry-cutoff) | 10:30 ET |
| `FORCE_CLOSE_ET` | 15:55 ET |
| `LOOKBACK` | 20 bars |
| `LOOP_SLEEP_SECONDS` | 15 |
| `CLOSE_FILL_WAIT_SEC` | 8 |

---

## Operationelt

- **Konto:** DUO509856 (paper, delt konto). Navn/source = `"BuyTheDip"` overalt
  (orderRef, journal, events, Studio-meta).
- **Start:** auto-start på algoserveren via scheduleren omkring US-åbning
  (`BTD_START_ET = 09:22 ET`, genforsøg hvert loop-tick til `BTD_RETRY_UNTIL_ET = 09:42 ET`).
  På workstation manuel start — instance-guarden (`instance_role != "algoserver"`) springer
  auto-start over dér, så den delte konto (DUO509856) ikke dobbelt-startes. `start_strategy`
  er idempotent (no-op hvis allerede RUNNING).
- **Reconcile:** scoped ved opstart — rører kun egne `source="BuyTheDip"` journal-rows,
  aldrig blind-flatten (delt konto). Bruger et pålideligt live positions-read; ved
  degraderet/tomt feed springes reconcile over (undgår at forældreløsgøre en ægte
  position).

---

## Valideringsstatus

Valideret på historiske korrelationsdata som K2's komplement: **0/3 tab** på K2's
tabsdage — de to strategier taber ikke samtidig, hvilket er hele pointen med at køre
dem sammen.

**Status: paper trading.** Sizing-parametrene tunes stadig på paper. Rigtige penge
overvejes først efter konsistent paper-performance over en længere periode.

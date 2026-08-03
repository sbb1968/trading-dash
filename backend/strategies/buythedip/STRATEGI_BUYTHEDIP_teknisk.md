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
| **Bounce** | `close > forrige close` **og** `close > open` **og** `volume ≥ BOUNCE_VOL_MULT × snit(20 foregående bars)` → entry på bounce-barens `close` | `BOUNCE_REQUIRE_GREEN = True`, `BOUNCE_VOL_MULT = 2.5`, `BOUNCE_VOL_LEN = 20` |

- **Entry-pris** = `bar.close` (færdig bar).
- **Bounce-kravet er skærpet 3/8-2026 (revision 1).** Tidligere var kravet alene
  `close > forrige close` — ét grønt tick, uden krav om at nogen faktisk købte.
  Målt på et rekonstrueret univers der matcher live-screeneren (inkl. `Perf 1W ≥ 6 %`),
  tre måneder, target 2R, 2 ¢ slippage:

  | bounce-krav | april | maj | juni | poolet (451 handler) |
  |---|---|---|---|---|
  | original | 0,86 | 1,04 | 0,70 | **0,89** — taber penge |
  | + grøn + vol 1,5× | 1,05 | 1,18 | 0,80 | 1,03 |
  | + grøn + vol 2,0× | 1,04 | 1,53 | 0,84 | 1,15 |
  | + grøn + vol 2,25× | 0,95 | 1,77 | 1,02 | 1,24 |
  | **+ grøn + vol 2,5×** | **1,03** | **1,65** | **1,04** | **1,24** ← valgt |
  | + grøn + vol 2,75× | 1,01 | 1,82 | 1,11 | 1,29 |
  | + grøn + vol 3,0× | 1,02 | 1,92 | 0,87 | 1,23 |

  Kravet er et bredt **plateau** fra 2,25× til 3,0× — ikke en spids — hvilket taler
  for at effekten er ægte. 2,5× er valgt som plateauets **midte** frem for toppen
  (2,75×): forskellen er støj, og at vælge sweepets maksimum er præcis den måde man
  overfitter på. 2,5× og 2,75× er de eneste to varianter over 1,0 i alle tre måneder.
  Filteret fjerner næsten ingen setups (n = 451 → 444); det udskyder entry til volumen
  bekræfter. Dip-state bevares imens, så et setup aldrig kasseres (test A7).
- **Kanten er tynd og hviler på maj.** April 1,03 og juni 1,04 er praktisk talt nul,
  og ved 3 ¢ slippage falder april til 0,89. Ingen af de øvrige 10 testede
  bounce-filtre (close-position, reclaim-andel, to grønne bars) er positive i alle
  tre måneder overhovedet.
- **Backtestens forbehold:** universet er rekonstrueret point-in-time fra daglige bars
  (`reconstruct_midcap_universe.py --perf-w-min 6.0`) ud fra en fast pulje på 98 navne.
  Pris/volumen/Perf 1W er eksakt genskabt; TradingViews `Volatility.M` kan kun
  tilnærmes af en daglig range-proxy, og **markedsværdi kan slet ikke rekonstrueres**
  uden historiske fundamentals. Stikprøve på det gamle univers: 84 % af navnene lå
  inden for det nye market cap-filter (300 mio.–10 mia.), median $7,5 mia.
  Marts-26 kan ikke bruges: cachen starter 2. marts, og `min_history = 20` gør den
  første udvælgelsesdag til ~30. marts.
- **dip_low** = laveste low i vinduet → bruges som stop.
- **Side:** altid `long`.
- **Entry-vindue:** kun `SESSION_START (09:30) … OPEN_UNTIL_ET (12:00)` ET. Ingen nye
  entries efter 12:00. (Rykket fra 10:30 den 3/8-2026 — backtesten viser at 95 % af
  opsætningerne alligevel falder før 10:30, men 11-timen var positiv i begge testede
  måneder. Cutoff'et holdes bevidst 3½ time før tvangsluk, så en handel har tid til at
  virke.)
- Ved flere samtidige kandidater åbnes de efter **dybeste `dip_depth`** først
  (`dip_depth = (ref_high − dip_low) / ref_high × 100`).

---

## Exit

Positionen bæres med tre exits (tjekkes på færdige bars):

| Exit | Betingelse |
|---|---|
| **Stop** | `bar.low ≤ stop` hvor `stop = dip_low` |
| **Target** | `bar.high ≥ target` hvor `target = entry + TARGET_R × R`, `R = entry − dip_low`, `TARGET_R = 2.0` (R-baseret siden 3/8-2026; var faste +2 %, hvilket gav vilkårlige risiko/gevinst-forhold fordi stoppet er `dip_low` råt uden gulv — fra 1:20 til 1:0,6 i juli) |
| **Force-close** | `bar.time_et ≥ FORCE_CLOSE_ET = 15:30 ET` → luk (30 min før 16:00, så genforsøg sker mens markedet er åbent) |

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
| `OPEN_UNTIL_ET` (entry-cutoff) | 12:00 ET |
| `FORCE_CLOSE_ET` | 15:30 ET |
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

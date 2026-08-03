# Trend Join Long — teknisk beskrivelse

Long-only gap-and-go efter HumbledTraders pipeline: top-gappere re-scannes hvert
30. min → kun aktier hvis gap er udløst af en **positiv nyhedskatalysator** →
join momentum når det fortsætter (ny HOD over premarket-high). Regel-parametrene
kommer fra `rules.json` (Trend Join Long).

Kernen er **nyhedsfilteret**: en aktie kommer kun i puljen hvis der er en frisk,
positiv nyhed i dag. Det er den del der ikke kan backtestes billigt historisk →
testes live på paper (vej B). Strukturelt spejler den `BuyTheDipLive`/`Confluence2Live`
(scoped reconcile, bekræftet force-close m. genforsøg, throttlet status, forensik),
med tre bevidste forskelle: (1) 30-min gapper-**rescan** i loopet, (2)
nyhedskatalysator-gate, (3) **flertrins-exit**.

**Kildefiler:**
- Live-wrapper + nyheds-gate: `algo_trendjoin.py` (`get_news_provider_codes`,
  `check_positive_catalyst` — IBKR `reqHistoricalNews`)
- Regel-parametre: `rules.json` (Trend Join Long)
- Keyword-sentiment på headline: `finnhub_news.py` (`_guess_sentiment`) — kun
  klassificering af IBKR-overskriften, IKKE nyhedskilden

---

## Universe — top-gappere (rescan hvert 30. min)

| Filter | Værdi |
|---|---|
| Pris | $3 – $500 (large caps gapper sjældent — vidt loft) |
| Volumen (dagens) | > 500.000 |
| Trend-bekræftelse | 1D + 1W + 1M ændring **alle** positive (`REQUIRE_ALL_GREEN`) |
| Top-N | 25 |
| Rescan-interval | 1800 s (30 min) — nyt pull i pipeline-loopet |

---

## Entry

Alle betingelser skal være opfyldt (rules.json):

| Kode | Betingelse | Parameter |
|---|---|---|
| **D3** | Gap ≥ `MIN_GAP_PCT` over forrige luk | `MIN_GAP_PCT = 3.0 %` |
| **D1** | Pris > forrige dags high | — |
| **D2** | Forrige luk > SMA(200) daglig | `SMA_LEN = 200` |
| **I3** | RVOL ≥ `RVOL_MIN` over lookback | `RVOL_MIN = 2.0`, `RVOL_LOOKBACK = 14 d` |
| — | Pris ≥ `MIN_PRICE_USD` | `$3.0` |
| **Katalysator** | Frisk positiv nyhed i dag via IBKR `reqHistoricalNews` (DJ/Briefing.com); netto bullish blandt friske headlines (keyword-`_guess_sentiment`) | `NEWS_MAX_AGE_HOURS = 20` |

**Trigger (I1 + I2):** entry når prisen bryder til **ny intradag-HOD** (I2) *og* ligger
**over premarket-high** (I1) — join af fortsat momentum. Premarket-high låses ved
`SESSION_START = 09:30 ET`. (D2 + katalysator er allerede vettet i watchlist-rescanet.)

**Entry-vindue:** `ENTRY_EARLIEST = 10:05 ET` … `ENTRY_LATEST = 15:00 ET`. Ingen nye
entries uden for vinduet. Bars: 5-min (`BAR_SIZE = "5 mins"`).

---

## Exit — flertrins

| Trin | Regel | Parameter |
|---|---|---|
| **Initial stop** | LOD − 1 % (`lod_minus_1pct`) | `STOP_PCT = 0.01` |
| **Partial** | Sælg `PARTIAL_FRAC` ved `PARTIAL_R` | `1/3 @ 0.75R` |
| **Breakeven** | Flyt stop til entry ved `BE_R` | `1.0R` |
| **Trail** | Post-breakeven: 5-min swing-low (`swing_low_5m_2_2`) | `SWING_LEFT/RIGHT = 2/2` |
| **Force-close** | `bar.time_et ≥ FORCE_CLOSE_ET` | `15:30 ET` |

Hvor `R = entry − initial_stop`. Force-close er bekræftet (op til
`FORCE_CLOSE_MAX_ATTEMPTS = 4` genforsøg) — holder aldrig over natten.

---

## Sizing & risiko (rules.json — %-baseret på NLV)

```
risk_budget   = NLV × RISK_PCT            (fallback RISK_BUDGET_FB hvis NLV ukendt)
notional_cap  = NLV × NOTIONAL_PCT        (fallback NOTIONAL_CAP_FB)
shares        = int( min( risk_budget / (entry − stop) , notional_cap / entry ) )
```

| Parameter | Værdi |
|---|---|
| `RISK_PCT` | 1 % af NLV pr. handel |
| `NOTIONAL_PCT` | 10 % af porteføljen pr. position |
| `RISK_BUDGET_FB` | $160 (~1 % af ~$16k) |
| `NOTIONAL_CAP_FB` | $1.600 (~10 % af ~$16k) |
| `max_open_positions` | 5 (rules.json `max_concurrent_positions`) — åbnes efter STØRSTE gap først |

Det globale dagstab deles med de øvrige strategier.

---

## Session-konstanter

| Konstant | Værdi |
|---|---|
| `SESSION_START` | 09:30 ET (premarket-high låses) |
| `ENTRY_EARLIEST` | 10:05 ET |
| `ENTRY_LATEST` | 15:00 ET |
| `FORCE_CLOSE_ET` | 15:30 ET |
| `RESCAN_INTERVAL_SEC` | 1800 (30 min) |
| `BAR_SIZE` / `BAR_DURATION` | 5 min / 7200 s pr. fetch |
| `NEWS_MAX_AGE_HOURS` | 20 |

---

## Operationelt

- **Konto:** paper. Navn/source = `"Trend Join Long"` overalt (orderRef, journal, events).
- **Start:** **manuel** — IKKE i scheduleren, auto-starter aldrig.
- **Reconcile:** scoped ved opstart (kun egne `source`-rows, aldrig blind-flatten).
  Bruger et pålideligt live positions-read; ved degraderet/tomt feed springes reconcile
  over (undgår at forældreløsgøre en ægte position).

---

## Status & nyhedskilde

**Status: tidlig live paper-test (vej B).** Kernen — nyhedskatalysatoren — kan ikke
valideres historisk og bevises derfor live.

**Nyhedskilde:** Oprindeligt Finnhub (gratis), som var for tynd til micro-cap-gappere.
**Løst** ved at flytte katalysator-gaten til **IBKR `reqHistoricalNews`** over den
forbindelse strategien allerede har — Dow Jones/Briefing.com giver dybere micro-cap-
dækning og rene enkeltnavn-katalysatorer (8-K, halts, insider, partnerskaber).
Gate-semantikken er uændret: mindst én bullish headline nyere end `NEWS_MAX_AGE_HOURS`
og netto bullish. Forudsætter at kontoen er berettiget til de relevante nyheds-providere
(tjekkes i pre-flight via `reqNewsProviders`).

# CSV-feltordbog — `dagens_log.py` handels-eksport

Hver række i CSV'en (`dagens_log_output/dagens_handler_<dato>_<tidsstempel>.csv`) er
**én handel** fra `trades`-tabellen, beriget med det matchede entry- og
exit-forensik-snapshot. Op til ~164 kolonner pr. dag.

## Læs dette først

- **Format:** `;`-separeret, `,` som decimaltegn, UTF-8 med BOM → dobbeltklik
  åbner direkte i dansk Excel.
- **Tomme celler er normalt, ikke fejl:**
  - **Europa-reversion** (MES/M2K) bygger ikke forensik-snapshots → alle
    `entry_*`/`exit_*`-kolonner er tomme. Kerne- og `tp_`-felterne er der.
  - **Tape/depth** er typisk tomme i paper trading (ingen live tape-buffer);
    `*_depth_available` = `0` og `*_depth_reason` forklarer hvorfor.
  - **Åbne handler** (ikke lukket endnu) har tomme `exit_*`-felter.
- **Kerne vinder:** `entry_time_et`, `entry_price` m.fl. kommer ALTID fra
  `trades`-tabellen (præcise fyld-værdier), aldrig fra snapshottet.
- **Matchning:** snapshots kobles til handlen pr. `(source, symbol, nærmeste
  tid)` indenfor 600 s, da journal-events ikke bærer `trade_id`.
- **Prefiks:** `tp_` = strategiens egen payload · `entry_`/`exit_` = forensik-
  snapshot ved entry/exit · `*_indicators_` · `*_setup_` · `*_tape_` ·
  `*_depth_` · `exit_trade_metrics_`.

---

## 1. Identitet

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `trade_id` | uuid | Unik nøgle for handlen i `trades`-tabellen. |
| `source` | tekst | Strategien der lavede handlen (`Konfluens 2`, `Europa-reversion`, `Momentum ORB` …). |
| `variant` | tekst | Strategi-variant hvis relevant (fx K2's `A: Impuls-low stop …`). Tom for Europa. |
| `symbol` | tekst | Instrument/ticker (aktie-symbol eller futures-rod MES/M2K). |
| `side` | tekst | `long` eller `short`. |
| `shares` | antal | Antal aktier (eller kontrakter for futures). |
| `account_id` | tekst | Intern konto-identifikator (fx `iben`). |
| `instance_id` | tekst | Maskinen der kørte strategien (fx `workstation`, `algoserver`). |
| `ibkr_account` | tekst | IBKR-kontonummer (paper, fx `DUN748991`). |

## 2. Tider & priser

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `entry_time_et` | ISO m. offset | Fyld-tidspunkt for entry i **amerikansk østtid** (ET), inkl. tidszone-offset. |
| `entry_time_dk` | ISO | Samme tidspunkt i **dansk lokaltid** (Europe/Copenhagen, DST-korrekt). |
| `entry_price` | pris | Faktisk fyld-pris ved entry. |
| `entry_reason` | tekst | Hvorfor entry blev taget (strategiens entry-begrundelse, fx `z=-2.75`). |
| `exit_time_et` | ISO m. offset | Exit-tidspunkt i ET. Tom hvis handlen stadig er åben. |
| `exit_time_dk` | ISO | Exit-tidspunkt i dansk lokaltid. |
| `exit_price` | pris | Faktisk fyld-pris ved exit. |
| `exit_reason` | tekst | Exit-årsag: `revert` (target/mean), `stop`, `session_end`/`session_close`, `trail`, `signal_exit`, `manual`. |

## 3. Resultat

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `pnl` | USD | Realiseret gevinst/tab i dollar (futures: allerede × multiplikator). |
| `pnl_pct` | % | Gevinst/tab i procent af entry-prisen (notionel). |
| `duration_sec` | sekunder | Holdetid fra entry til exit. |

## 4. Position- & risikostate (ved lukning)

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `capital_used` | USD | Kapital/eksponering bundet i handlen (pris × antal × evt. multiplikator). |
| `current_stop` | pris | Aktivt stop-niveau ved lukning (eller seneste kendte). |
| `current_target` | pris | Aktivt target-niveau, hvis strategien bruger fast target (ellers tom). |
| `trail_stop` | pris | Trailing-stop-niveau, hvis aktivt (ellers tom). |
| `current_stage` | tekst | Exit-trinnet handlen var nået til (fx `initial`, breakeven, trail). |
| `notes` | tekst | Frie noter på handlen (manuelt eller programmatisk). Oftest tom. |

## 5. Strategi-payload (`tp_*`)

Strategiens egne nøgletal, gemt på handlen. **Felterne varierer pr. strategi** —
tomme hvis ikke relevant.

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `tp_entry_score` | 0–4 | **Konfluens 2:** antal opfyldte kontekst-kriterier (E/K/R/T) ved entry. |
| `tp_entry_bricks` | tekst | **K2:** 7-tegns "bricks"-streng `VBGEKRT` (`·` = manglende). V/B/G = obligatorisk impuls; E/K/R/T = kontekst. |
| `tp_entry_z` | z | **Europa-reversion:** z-score ved entry (≥ +2 → short, ≤ −2 → long). |
| `tp_exit_z` | z | **Europa:** z-score ved exit (≈ 0 = vendt til middel). Tom hvis exit var tvangsluk. |
| `tp_std` | prispoint | **Europa:** standardafvigelse af lookback-vinduet (basis for z og sizing). |
| `tp_stop_distance_pts` | prispoint | **Europa:** stop-afstand = (stop_z − entry_z) × std. |
| `tp_contracts` | antal | **Europa:** antal futures-kontrakter. |
| `tp_multiplier` | USD/point | **Europa:** kontrakt-multiplikator ($5/point for MES og M2K). |
| `tp_max_favorable_excursion` | pris | Gunstigste pris set i holdeperioden (hvor tæt på "perfekt exit"). |
| `tp_max_adverse_excursion` | pris | Ugunstigste pris set i holdeperioden (største modgang undervejs). |

---

## 6. Entry-snapshot — "verden på entry-tidspunktet"

Bygges kun for ORB/Konfluens. Tom for Europa-reversion.

### 6a. Meta (delvist redundant med kerne — bevaret for fuldstændighed)

| Kolonne | Beskrivelse |
|---|---|
| `entry_phase` | Altid `entry`. |
| `entry_strategy` | Strategiens interne navn i snapshottet (≈ `source`). |
| `entry_ticker` | Symbol i snapshottet (≈ `symbol`). |
| `entry_variant` | Variant i snapshottet (≈ `variant`). |
| `entry_shares` | Antal aktier registreret i snapshottet (≈ `shares`). |

### 6b. Indikatorer (`entry_indicators_*`)

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `entry_indicators_bars_used` | antal | Antal historiske bars indikatorerne er beregnet på. |
| `entry_indicators_rsi_14` | 0–100 | RSI(14) — momentum/overkøbt-oversolgt. |
| `entry_indicators_macd` | værdi | MACD-linjen. |
| `entry_indicators_macd_signal` | værdi | MACD-signallinjen. |
| `entry_indicators_macd_hist` | værdi | MACD-histogram (macd − signal); momentum-retning. |
| `entry_indicators_ema_9` | pris | Hurtig EMA (9 perioder). |
| `entry_indicators_ema_20` | pris | Langsom EMA (20 perioder). |
| `entry_indicators_bb_upper` | pris | Bollinger-bånd, øvre. |
| `entry_indicators_bb_middle` | pris | Bollinger-bånd, midter (SMA). |
| `entry_indicators_bb_lower` | pris | Bollinger-bånd, nedre. |
| `entry_indicators_bb_width_pct` | % | Båndbredde i % — volatilitetsmål (smal = squeeze). |
| `entry_indicators_bb_position_pct` | % | Prisens placering i båndet (0 % = nedre, 100 % = øvre). |
| `entry_indicators_vwap` | pris | Volumenvægtet gennemsnitspris for dagen. |
| `entry_indicators_vwap_distance_pct` | % | Prisens afstand til VWAP i % (over/under). |

### 6c. Setup-kontekst (`entry_setup_*`)

Strategi-specifik. Felter der ikke gælder strategien er tomme.

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `entry_setup_entry_score` | 0–4 | Kontekst-score ved entry (samme som `tp_entry_score`). |
| `entry_setup_entry_bricks` | tekst | Bricks-streng ved entry (samme som `tp_entry_bricks`). |
| `entry_setup_ema_fast` | pris | Hurtig EMA brugt i strategilogikken. |
| `entry_setup_ema_slow` | pris | Langsom EMA brugt i strategilogikken. |
| `entry_setup_rsi` | 0–100 | RSI som strategien evaluerede på. |
| `entry_setup_vwap` | pris | VWAP som strategien evaluerede på. |
| `entry_setup_vwap_distance_pct` | % | Afstand til VWAP i strategilogikken. |
| `entry_setup_atr` | pris | ATR (gennemsnitlig true range) — volatilitet/stopberegning. |
| `entry_setup_initial_stop` | pris | Det oprindeligt beregnede stop ved entry. |
| `entry_setup_risk_per_share` | pris | Risiko pr. aktie = entry − stop (R). |
| `entry_setup_risk_pct` | % | Risiko i % af entry-prisen. |
| `entry_setup_last_bar_volume` | volumen | Volumen i selve impuls/entry-baren. |
| `entry_setup_avg_vol_20bars` | volumen | Gennemsnitsvolumen over 20 bars. |
| `entry_setup_rel_vol_last_bar` | faktor | Relativ volumen = sidste bar ÷ snit (fx 2,6 = 2,6× normal). |
| `entry_setup_last_swing_high` | pris | Seneste swing-high (struktur). |
| `entry_setup_last_swing_low` | pris | Seneste swing-low (struktur). |
| `entry_setup_htf_ema` | pris | Højere-tidsramme-EMA (trendfilter, Konfluens 1). |
| `entry_setup_htf_bias` | tekst | Højere-tidsramme-bias (bull/bear). Ofte tom for K2. |

### 6d. Tape (`entry_tape_*`) — handler i tidsvinduet før entry

Tom i paper trading uden live tape-feed.

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `entry_tape_lookback_sec` | sekunder | Tidsvindue tapen er aggregeret over (fx 60 s). |
| `entry_tape_trade_count` | antal | Antal handler i vinduet. |
| `entry_tape_total_volume` | volumen | Samlet volumen i vinduet. |
| `entry_tape_up_volume` | volumen | Volumen handlet på upticks (købspres). |
| `entry_tape_down_volume` | volumen | Volumen handlet på downticks (salgspres). |
| `entry_tape_neutral_volume` | volumen | Volumen uden retning. |
| `entry_tape_aggressor_ratio` | 0–1 | Andel købsaggressor (>0,5 = købere driver). |
| `entry_tape_largest_trade_size` | volumen | Største enkelthandel i vinduet. |
| `entry_tape_largest_trade_direction` | tekst | Retning på den største handel (`up`/`down`). |
| `entry_tape_last_5_trades` | JSON | De seneste 5 handler (pris/størrelse/retning) som tekst. |

### 6e. Markedsdybde / Level 2 (`entry_depth_*`)

Tom hvis `entry_depth_available` = `0`.

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `entry_depth_available` | 0/1 | Var L2-data tilgængelig? |
| `entry_depth_reason` | tekst | Forklaring hvis ikke tilgængelig. |
| `entry_depth_best_bid` | pris | Bedste købsbud. |
| `entry_depth_best_ask` | pris | Bedste salgsbud. |
| `entry_depth_spread` | pris | Spænd = ask − bid. |
| `entry_depth_spread_pct` | % | Spænd i % af prisen. |
| `entry_depth_bid_ask_imbalance` | −1…1 | Ubalance bid vs ask (positiv = mere købsside). |
| `entry_depth_bid_levels` | antal | Antal bid-niveauer i bogen. |
| `entry_depth_ask_levels` | antal | Antal ask-niveauer i bogen. |
| `entry_depth_total_bid_size` | volumen | Samlet størrelse på købssiden. |
| `entry_depth_total_ask_size` | volumen | Samlet størrelse på salgssiden. |
| `entry_depth_largest_bid_price` | pris | Pris på det største bid-niveau. |
| `entry_depth_largest_bid_size` | volumen | Størrelse på det største bid-niveau. |
| `entry_depth_largest_ask_price` | pris | Pris på det største ask-niveau. |
| `entry_depth_largest_ask_size` | volumen | Størrelse på det største ask-niveau. |

---

## 7. Exit-snapshot — "verden på exit-tidspunktet"

Samme struktur som entry-snapshottet (`exit_indicators_*`, `exit_setup_*`,
`exit_tape_*`, `exit_depth_*` — se afsnit 6 for feltbetydninger), målt da
positionen blev lukket. Plus to ting der kun findes ved exit:

### 7a. Setup ved exit (afvigelser fra entry)

| Kolonne | Beskrivelse |
|---|---|
| `exit_setup_entry_score_at_open` | Kontekst-scoren handlen blev åbnet med (referenceværdi). |
| `exit_setup_entry_bricks_at_open` | Bricks-strengen handlen blev åbnet med. |

(`exit_setup_*` mangler `initial_stop`/`risk_*`/`last_bar_volume`/`avg_vol`/
`rel_vol` — de er kun meningsfulde ved entry.)

### 7b. Handels-metrikker (`exit_trade_metrics_*`) — facit på handlen

| Kolonne | Type | Beskrivelse |
|---|---|---|
| `exit_trade_metrics_entry_price` | pris | Entry-pris (snapshottets egen kopi). |
| `exit_trade_metrics_exit_price` | pris | Exit-pris. |
| `exit_trade_metrics_shares` | antal | Antal aktier/kontrakter. |
| `exit_trade_metrics_pnl` | USD | P&L beregnet i snapshottet. |
| `exit_trade_metrics_pnl_pct` | % | P&L i procent. |
| `exit_trade_metrics_duration_sec` | sekunder | Holdetid. |
| `exit_trade_metrics_duration_bars` | antal | Holdetid målt i bars. |
| `exit_trade_metrics_reason` | tekst | Exit-årsag (samme som `exit_reason`). |
| `exit_trade_metrics_max_favorable_excursion` | pris | Gunstigste pris set undervejs (MFE). |
| `exit_trade_metrics_max_adverse_excursion` | pris | Ugunstigste pris set undervejs (MAE). |

---

## Tips til analyse i Excel

- **MFE vs exit-pris:** Hvor meget gevinst lod vi ligge? Stor `max_favorable_excursion`
  langt over `exit_price` på en taber = exit'en var for langsom.
- **MAE vs stop:** Hvor tæt var vindere på at blive stoppet ud? `max_adverse_excursion`
  nær `entry_setup_initial_stop` = strammere stop havde dræbt vinderen.
- **`entry_setup_rel_vol_last_bar`:** Filtrér vindere vs tabere — bekræfter impuls-volumen edge.
- **`exit_reason`-fordeling:** Mange `session_end` = strategien rammer sjældent sit target.
- **`tp_entry_z` (Europa):** Korrelér entry-stræk mod P&L — betaler det sig at vente på større z?

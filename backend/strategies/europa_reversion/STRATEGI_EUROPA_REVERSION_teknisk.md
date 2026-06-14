# Europa-reversion — teknisk beskrivelse

Mean-reversion på index-micro futures (MES/M2K) i den europæiske session.
Beslutningslogikken er en **ren z-score-regel** — en funktion af et rullende
vindue af closes, uden state og uden IBKR.

**Kildefiler:**
- Regel (ENESTE sandhedskilde): `strategies/europa_reversion/rule.py`
- Parametre (låst, ingen variant-sweep): `strategies/europa_reversion/config.py`
- Strategi-facade: `strategies/europa_reversion/strategy.py`
- Live-wrapper: `algo_europa_reversion.py`
- Valideret backtest: `meanrev_backtest.py` (15-min futures fra `data_harvest/`)

### Strukturel forskel fra ORB/Konfluens
ORB og Konfluens har stateful entry/exit-ENGINES drevet af den generiske
OHLC-backtest (`backtest_momentum.py`). Europa-reversions regel er derimod en
ren funktion af et z-score-vindue, og dens validerede backtest er den
fritstående `meanrev_backtest.py`. Derfor eksponerer facaden **reglen + config**
i stedet for OHLC-engines. `rule.py` deles 1:1 af live-wrapper og backtest, så de
**aldrig kan divergere** på beslutningslogik.

---

## z-score-reglen (`rule.py`)

På hver FÆRDIG 15-min bar beregnes over de seneste `LOOKBACK` closes:

```
ma = mean(closes)
sd = pstdev(closes)          # POPULATION-std (matcher backtesten præcist)
z  = (closes[-1] − ma) / sd
```

`compute_z` returnerer `None` (intet signal) hvis `sd ≤ 0` eller seneste
`close ≤ 0`, eller hvis der er < 2 closes.

### Entry (`entry_side`) — ved flad position
| Betingelse | Handling |
|---|---|
| `z ≥ +ENTRY_Z` | **short** (prisen strakt op → satser på fald) |
| `z ≤ −ENTRY_Z` | **long** (prisen strakt ned → satser på stigning) |
| ellers | ingen handel |

### Exit (`exit_reason`) — for åben position
Revert tjekkes **før** stop (samme rækkefølge som den validerede backtest):

| Side | `revert` (gevinst) | `stop` (tab) |
|---|---|---|
| long | `z ≥ −EXIT_Z` | `z ≤ −STOP_Z` |
| short | `z ≤ +EXIT_Z` | `z ≥ +STOP_Z` |

Dvs. revert = prisen tilbage inden for ±`EXIT_Z` om middel; stop = strækket
fortsætter til ±`STOP_Z`.

---

## Parametre (`config.py` — låst, ÉN konfiguration)

| Parameter | Værdi | Note |
|---|---|---|
| `LOOKBACK` | 30 | bars til MA/std (prøv 40 ved at ændre dette ene tal) |
| `ENTRY_Z` | 2.0 | \|z\| ≥ dette → entry |
| `EXIT_Z` | 0.5 | tilbage mod middel → exit (revert) |
| `STOP_Z` | 3.5 | stræk fortsætter → stop |
| `BAR_SIZE` | "15 mins" | `BAR_MINUTES = 15` afledt |
| `INSTRUMENTS` | `["MES", "M2K"]` | IKKE MNQ (mean-reverter ikke pålideligt) |
| `MULTIPLIER` | MES=5.0, M2K=5.0 | $ pr. prispoint (verificér mod IBKR ved kvalificering) |
| `RISK_PCT` | 0.01 | 1 % af konto-equity pr. handel |

> `config.py` er eneste sandhedskilde for parametrene: både live-wrapper og
> backtest læser herfra, så de aldrig kan divergere.

---

## Session (ET)

| Konstant | Værdi | Dansk tid |
|---|---|---|
| `SESSION_START_ET` | 02:00 ET | 08:00 |
| `SESSION_END_ET` | 08:00 ET | 14:00 |
| `LAST_SESSION_BAR_ET` | 07:45 ET | sidste 15-min slot der regnes "i sessionen" |
| `FORCE_CLOSE_ET` | 07:55 ET | tvangsluk (sidste sikre bar før 08:00) |

Kun FÆRDIGE bars evalueres, og hver bar behandles kun én gang (dedup via
`_last_bar_processed`). Én åben position pr. instrument ad gangen.

---

## Sizing & risiko (live, `_size_contracts` + `main.py` → `StrategyConfig`)

Risiko-baseret kontrakt-sizing:

```
risk_dollars      = RISK_PCT × equity            # 1 % af net_liquidation
stop_dist         = (STOP_Z − ENTRY_Z) × std     # = 1.5 × std, i prispoint
per_contract_risk = stop_dist × multiplier       # × $5/point
by_risk           = floor(risk_dollars / per_contract_risk)
by_cap            = floor(max_loss_per_trade / per_contract_risk)
contracts         = min(by_risk, by_cap)         # mindst 1 for at handle
```

`StrategyConfig` (fra `main.py`):

| Felt | Værdi |
|---|---|
| `max_loss_per_trade` | $170 (~1 % af ~$17k konto) |
| `max_daily_loss` | $300 — strategien pauses resten af dagen |
| `max_open_positions` | 2 (MES + M2K) |
| `max_position_size` | $2.000 (defensivt; futures sizer på kontrakter) |

P&L pr. handel: `(exit − entry) × contracts × multiplier` (long), omvendt for short.

---

## Status

Paper trading. Reglen spejler den validerede `meanrev_backtest`-logik 1:1
(population-std, revert-før-stop). MNQ bevidst udeladt fordi den ikke
mean-reverterer pålideligt nok i denne session.

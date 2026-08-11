# Tidsplan 11. august — to uafhængige spor

**Skrevet kl. 15:02.**

⚠ **Sporene har intet med hinanden at gøre.** Forskellige maskiner, forskellige
konti, forskellige Gateways, forskellige TWS-brugere. Intet trin i det ene kan
spærre for det andet.

| | **SPOR A — Algoserveren** | **SPOR B — Ibens workstation** |
|---|---|---|
| Maskine | `iben-algo` | `ibenspc` |
| Konto | DUO509856 | DUQ441063 |
| Forbindelse | algoserverens egen Gateway | lokal Gateway :4002, `fasteriben2` |
| Hvad | de seks **automatiske** strategier | Iben handler **manuelt** MES |
| Hvem | Søren alene | Iben (5 min) + Søren |
| Hvornår | **15:20–16:00** | når Iben er hjemme |
| Status | 7 positioner, kode fra `7754d79` | ✓ klar, mangler kun login |

**Det Søren og VS Code Claude har bygget hele dagen, er spor B. Det er færdigt.**

---

# SPOR A — Algoserveren, 15:20–16:00

## Hvorfor det ikke bare kan udskydes

Kl. 15:20 auto-starter strategierne **uanset hvad vi gør**. Lige nu betyder det:

- syv positioner IBKR holder, som journalen ikke kender
  (`TE −86 · NUAI −46 · ALOY −24 · VELO −19 · XE −12 · WOLF −10 · SHAZ +4`)
- gammel kode **uden** over-sell-rettelsen — den fejl der skabte de seks shorts
- risikogrænser der tror eksponeringen er nul

At gøre ingenting er også et valg. Det skal bare være et bevidst et.

## Rækkefølgen

| Tid | Trin | Kommando |
|---|---|---|
| **nu** | Preflight — kan den nye kode overhovedet starte? | `python preflight_genstart.py` |
| 15:20 | strategierne auto-starter på gammel kode — **lad dem** | — |
| **15:30** | markedet åbner. **Stop alle seks** | `POST /algo/stop` × 6 |
| 15:32 | Flatten, preview først | `python flatten_alt.py --konto DUO509856` |
| 15:34 | Flatten, udfør | `… --udfoer` |
| 15:36 | ⚠ verificér flad **hos IBKR** | flatten gør det selv, exit 1 hvis noget hænger |
| 15:40 | Genstart backenden | |
| 15:45 | Verificér | `port_tjek.py` · `/health` · flatten-preview = "allerede flad" |
| 15:50 | Start strategierne manuelt | Studio, eller `POST /algo/start` |
| 15:55 | **Nyt udgangspunkt for vagten** | `python algoserver_vagt.py --gem` |

## ⚠ Afbrydelsesreglen

Kommer du forbi **flatten** men ikke til **genstart**, og må stoppe:
**genstart ikke.** Lad algoserveren køre som den er, og tag resten i morgen.
En genstart oven på en halvt flad bog er den ene tilstand vi bruger hele
øvelsen på at undgå.

**Hænger en position:** gen-afgiv ikke. Find ud af hvorfor (halt, tynd
likviditet, lukket kontrakt) først.

---

# SPOR B — Ibens workstation, når hun er hjemme

Fuld køreseddel i `iben_test_i_aften.md`. Kort:

| # | Trin | Hvem |
|---|---|---|
| 1 | IB Gateway → login `fasteriben2`, **paper** | **Iben** |
| 2 | Configure → API: port 4002, Read-Only ✗, Master ID **tom** | **Iben** |
| 3 | `python konto2_klargoer.py` → **exit 0** | Søren |
| 4 | `python ibkr_session_probe.py --port 4002 --konto DUQ441063` | Søren |
| 5 | **KØB 1 MES** i Trading Dash → grøn kvittering med konto | Søren |
| 6 | ⚠ **T7:** står i DUQ441063 **og beviseligt ikke** på algoserveren | Søren |
| 7 | Journal · **SÆLG 1 MES** · fladkontrol | Søren |

⚠ **Brug ikke `--i-vindue`** på vagten om aftenen — strategierne handler da, så
positionstallet ændrer sig lovligt.

⚠ **Kun ÉN Gateway må være logget ind som `fasteriben2`.** Sørens skal være
lukket. `python port_tjek.py --port 4002` på hans maskine skal sige 0 lyttere.

---

## Den eneste berøringsflade mellem sporene

Under algoserverens **genstart** (15:40, ~1–2 minutter) mister Ibens watchlist
sine kurser, fordi kursproxyen henter derfra. Den backer af og kommer igen af sig
selv.

Det rammer kun hvis hun handler præcis i det minut. Hun er ikke hjemme endnu.

---

## Lagt til side i dag

| | |
|---|---|
| **Crypto** (`BINANCE:LINKUSDT`) | vises i watchlisten, handles ikke — IBKR kan ikke Binance-spot |
| **Europæiske aktier** | 127 af 153 kan prissættes hos IBKR, men Trading Dash slår alt op som USD. ⚠ 29 af symbolerne findes også som US-aktier og ville handle det forkerte selskab |
| **NinjaTrader ATI** | læsevejen bevist, skrivevejen mangler ét fund. MES handler døgnet rundt — den kan tages når som helst |

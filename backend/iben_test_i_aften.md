# Ibens MES-test — nummereret gennemgang

**Skrevet 11-08-2026. Køres når Iben er hjemme fra arbejde.**

Forudsætter at algoserveren er ordnet kl. 15:30 (flatten af de syv positioner +
ny kode + strategier startet manuelt) — se `PLAN_i_morgen.md`.

Alt herunder kører på **Ibens workstation**, i `C:\Projects\Trading_Dash\backend`
med venv aktiv.

⚠ **MES handler næsten døgnet rundt**, så testen er ikke bundet af markedstider.

---

## Iben skal bruges til trin 1–2. Resten kan Søren køre.

### 1 · Log Gatewayen ind

Start IB Gateway → `fasteriben2` → **Paper Trading**.

Passwordet gemmes ikke og tastes hver gang.

⚠ Kun **én** Gateway må være logget ind som `fasteriben2`. Kører Sørens, luk den
først — `python port_tjek.py --port 4002` på hans maskine skal sige 0 lyttere.

### 2 · Configure → Settings → API

- Enable ActiveX and Socket Clients **✓**
- Socket port **4002**
- Read-Only API **✗**
- Allow connections from localhost only **✓**
- ⚠ **Master API client ID: lad stå TOM**

*Sættes den, modtager enhver klient med det id ordreopdateringer fra alle
klienter — og kode der antager "de ordrer jeg kan se, er mine" bliver forkert
uden at fejle.*

---

### 3 · Nyt udgangspunkt for vagten

```bash
python algoserver_vagt.py --gem
```

Skal vise `positioner=0` og den rigtige konto.

⚠ Baselinen fra i morges er ubrugelig: den stod med `algo_running=True` og
`positioner=7`, og **tre af de fire tal har vi selv ændret** i løbet af dagen. En
vagt der råber ad ting man selv har gjort, lærer man at se forbi på en time — og
så er den værdiløs den dag den har ret.

### 4 · Mål at maskinen er klar

```bash
python konto2_klargoer.py
```

⚠ **Exit 0.** Nu skal også `Gateway paa 4002` være grøn.

### 5 · Sessionsprøven — T1 til T5 i én kørsel

```bash
python ibkr_session_probe.py --port 4002 --konto DUQ441063
```

Connect · accountSummary · reqPositions · limitordre AAPL @ $1 (fylder aldrig) ·
annullering — og den verificerer annulleringen før den slutter.

⚠ **Notér IBKR-fejlkoder ordret.** Forskellen mellem `354 not subscribed`
(markedsdata) og en sessionsafvisning er hele spørgsmålet, og de bliver konstant
blandet sammen til "det virkede ikke".

### 6 · Mellemkontrol

```bash
python algoserver_vagt.py
```

⚠ **Uden `--i-vindue`.** Flaget gør ændringer i positionstallet til et
stopsignal, og det giver kun mening i vinduet 08:00–14:00 dansk hvor
strategierne ikke handler (`algoserver_vagt.py:118-124`). Om aftenen handler de.

Uden flag er **mistet forbindelse**, **stoppet algo** og **skiftet konto** stadig
stopsignaler — og det er dem der betyder noget.

---

## Den rigtige handel — T6 til T10

### 7 · KØB 1 MES i Trading Dash

MES i watchlisten, Stk = **1**, tryk **KØB**.

Forventet **grøn** kvittering:

```
KØBT 1 MES @ 7xxx · konto DUQ441063 · lokal ordre-Gateway :4002
```

⚠ Står der **"⚠ DELT forbindelse"** i gult → **stop**. Så gik ordren gennem den
forkerte vej.

### 8 · ⚠ T7 — testens kerne

Positionen skal stå i den rigtige konto **og beviseligt ikke i den anden.**

**a)** I Trading Dash: Portfolio-vinduet viser MES på **DUQ441063**

**b)** På algoserveren — MES må **ikke** dukke op:

```bash
NOEGLE=$(python -c "from accounts import identity;print(identity.internal_key)")
curl -s -H "X-Internal-Key: $NOEGLE" http://100.76.201.59:8000/account/dash-snapshot \
 | python -c "
import json,sys
p=json.load(sys.stdin).get('positions') or []
for x in p: print(f\"   {x.get('ticker'):8} {x.get('position'):+g}\")
print('MES paa algoserveren:', 'JA — STOP' if any(x.get('ticker')=='MES' for x in p) else 'nej')"
```

Skal sige **`nej`**.

*Det er ikke nok at ordren blev fyldt. En ordre der havner forkert, ser ud som en
succes hvis man kun kigger på fill-bekræftelsen — og det er den fejl der ville
koste mest ved flytningen.*

### 9 · T8 — journalen

```bash
curl -s http://127.0.0.1:8000/journal/trades?limit=3
```

Den nyeste række skal have `ibkr_account: DUQ441063`, `paper: 1` og
`order_ref: manuel:…`

⚠ `orderRef` er det der gør Ibens handler tilskrivbare. Uden entydig markering
ville de være ejerløse på præcis samme måde som SHAZ.

### 10 · Se prisen bevæge sig

Med en åben position viser **Aktuel pris** og **Ur. P/L** nu tal — leveret af
kursproxyen fra algoserveren. Står de tomme, kører proxyen ikke (backendloggen
skal vise `[Kursproxy] henter nu MES fra algoserveren`).

### 11 · T9 — SÆLG 1 MES

Tryk **SÆLG**. Grøn kvittering, samme konto.

### 12 · T10 — fladkontrol

```bash
python flatten_alt.py --konto DUQ441063 --port 4002
```

Preview, sender intet. Skal sige **"Kontoen er allerede flad"**.

### 13 · Sidste vagtkontrol

```bash
python algoserver_vagt.py
```

---

## Hvis noget spærrer undervejs

| Sker der | Så |
|---|---|
| Gateway vil ikke forbinde | Er API'et slået til, og porten 4002? |
| ⚠ V1: "FORKERT KONTO" | Gatewayen er logget ind som en anden bruger end `fasteriben2` |
| T5 afvises på tilladelse | Futures-rettigheden på DUQ441063 — det eneste vi aldrig har aflæst i Client Portal. Vi handlede MES på kontoen 10/8, så det er praktisk bevist, ikke papirbevist |
| Vagten siger STOP | Luk Ibens Gateway, bring algoserveren op, og rapportér **hvilket trin** der udløste det. Fortsæt **ikke** for at se om det var et tilfælde |

---

## Hvad der allerede er bevist i dag

| | |
|---|---|
| Kode | `27b171a` på begge maskiner |
| Exe | 07:56-buildet — afvisningstesten kørte på præcis den binær |
| `account.yaml` | `ordre_forbindelse` → DUQ441063, V2 slipper igennem |
| Kurser | ægte, fra algoserveren, opdaterende (`[Kursproxy]`) |
| **Afvisningsvejen** | ⚠ **bestået** — rød kvittering, port 4002 nævnt, nul ordrer, intet fald tilbage til den delte forbindelse |
| Journal | migreret (`paper` + `ibkr_account`) |
| 7497 | tom — et konfigurationsuheld ville fejle højlydt |
| IB Gateway | installeret, logon-vindue verificeret |

**Trin 1–2 er hendes. Trin 3–13 er cirka et kvarter.**

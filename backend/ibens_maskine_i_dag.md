# Ibens workstation — klargøring over TeamViewer, 2026-08-11

Målet: når Iben kommer hjem fra arbejde, er maskinen **bevist** klar til at handle
manuelt på DUQ441063 — ikke antaget klar.

⚠ **Rettet 11-08 kl. 09:10.** Første udgave antog at Søren kunne logge Gatewayen
ind. Det kan han ikke — `fasteriben2` er **Ibens** bruger med hendes password, og
det tastes af hende. Alt herunder er derfor delt i to: hvad der kan gøres uden
hende, og de få minutter der kræver hende.

Fejlen er værd at bemærke frem for bare at rette: en plan der antager en adgang
den ikke har, ser komplet ud lige indtil man rammer trinnet.

---

## 0 · Før du rører hendes maskine

**Din egen Gateway skal være lukket.** En TWS-bruger har én session ad gangen, og
`fasteriben2` kan ikke være logget ind to steder. Målt her kl. 07:40:

```
Port 4002: 0 lyttere        ← din Gateway kører ikke. Fri bane.
```

Starter du din igen senere mens Iben handler, ryger den ene af — og symptomet
ligner alt muligt andet. Se `konto2_opsaetning.md` §6b.

---

## 1 · Filen der ikke følger med `git pull`

⚠ **Det her er dagens vigtigste punkt.**

`app.exe` er et build-artefakt. Det ligger ikke i git. Hendes exe er fra
**5. august**, og siden da er der kommet otte frontend-commits — heriblandt
`17f3350`, **ordrekvitteringen der viser hvilken konto ordren gik til**.

Uden den trykker hun K, ordren går af sted, og hun får ingen bekræftelse på
hvilken konto den landede på. Intet fejler. Hun har bare ingen grund til at
savne noget.

Der ligger en frisk exe klar her:

```
C:\Projects\trading_dash\app.exe          (bygget 11-08 07:56)
```

Overfør den med TeamViewers filoverførsel til **samme sti** på hendes maskine.
Det er den `start_trading_dash.bat` åbner (`%~dp0app.exe`) — ikke den under
`src-tauri\target\release\`.

### ⚠ Verificér summen EFTER overførslen — hver gang

Målt 11-08: den overførte fil havde en **anden sha256** end kilden, og appen
startede ikke. En ufuldstændig overførsel forklarer begge dele.

```powershell
(Get-Item C:\Projects\Trading_Dash\app.exe).Length
(Get-FileHash C:\Projects\Trading_Dash\app.exe -Algorithm SHA256).Hash.Substring(0,32)
```

Skal give **8964096** og **381F1A70C041F06A6B6F97590ECD82F3** (for buildet fra
11-08 07:56).

⚠ **Tiden beviser ingenting her.** `mtime` bliver overførselstidspunktet, så en
halv fil ser lige så frisk ud som en hel. `konto2_klargoer.py` gav tre grønne
flueben — "exe nyere end 17f3350" og to til — på en binær der ikke kunne starte.
Kun summen skiller.

⚠ **Bygger maskinen selv**, er en anden sum derimod normal: Tauri-builds er ikke
byte-reproducerbare, fordi stier og tidsstempler bages ind. Så gælder summen kun
til at sammenligne *kopier*, ikke *builds*.

---

## 2 · Koden

På hendes maskine, i `C:\Projects\trading_dash`:

```bash
git pull
```

Henter blandt andet `ordre_forbindelse.py`, `ibkr_client_ids.py`, `port_tjek.py`,
`flatten_alt.py`, `konto2_klargoer.py` og journalens paper/live-migration.

---

## 3 · Konfigurationen — det eneste der ændres pr. maskine

I hendes `backend\account.yaml`, under `instance:`, tilføj blokken. Alt andet i
filen bliver stående:

```yaml
  # ── ORDRER gaar til en SEPARAT, lokal Gateway ────────────────────────────
  # Kurser kommer fra algoserverens forbindelse (bruger fasteriben, har
  # abonnementet); ordrer fra en lokal Gateway logget ind som fasteriben2.
  ordre_forbindelse:
    host:   127.0.0.1
    port:   4002
    konto:  DUQ441063
    bruger: fasteriben2
```

⚠ **Rør ikke `ibkr_account`.** Den er hendes *læsende* identitet og har ikke med
ordrer at gøre. Journalen stempler den konto ordren **faktisk** gik til (b93eaac),
ikke den der står her.

Tjek at `replication.target_url` og `internal_key` allerede står der — uden dem
får maskinen ingen kurser, og frontenden spærrer en ordre den ikke kan prissætte.

---

## 4 · IB Gateway

⚠ **Kun trin 1 kan gøres uden Iben.** Gatewayens `Configure`-menu findes først
efter login, så API-indstillingerne må vente på hende. Installationen selv koster
ingen adgang — og det er den der tager tid.

1. Installér **IB Gateway** (ikke TWS). ← *kan gøres nu*
2. Log ind som `fasteriben2`, **paper**. ← *kræver Iben*
3. Configure → Settings → API:
   - Enable ActiveX and Socket Clients: **✓**
   - Socket port: **4002**
   - Read-Only API: **✗**
   - Allow connections from localhost only: **✓**
   - **Master API client ID: lad stå tom** — sættes den, modtager enhver klient
     med det id ordreopdateringer fra *alle* klienter, og kode der antager "de
     ordrer jeg kan se, er mine" bliver forkert uden at fejle.

**Passwordet gemmes ikke.** Gateway startes manuelt med login-dialog, hver gang.

---

## 5 · Mål om det virkede

Start backenden, og kør så:

```bash
cd C:\Projects\trading_dash\backend
python konto2_klargoer.py
```

Den måler koden, exe-alderen, konfigurationen, kurserne, portene, journalens
migration og client-id-registret. **Exit 0 = klar.**

Den spørger på **MES**, ikke SPY — MES handler næsten døgnet rundt, så et
manglende svar dér betyder faktisk at feedet er nede, frem for at markedet er
lukket.

⚠ **To ting kan den ikke svare på**, og den siger det selv:

- om Gatewayen er logget ind som den rigtige **bruger** — det ser V1 først når
  forbindelsen åbnes
- om kontoen har **futures-tilladelse** — det afgør IBKR

Den anden er i praksis besvaret: vi handlede MES på DUQ441063 i går. Tilladelsen
sidder på kontoen, ikke på maskinen.

---

## 6 · Bevis det med en rigtig handel

Kør `konto2_opsaetning.md` §7, T1–T10. Kernen er **T7**:

> Positionen står i den rigtige konto — **og ikke** i den anden.

Det er ikke nok at ordren blev fyldt. En ordre der havner forkert, ser ud som en
succes hvis man kun kigger på fill-bekræftelsen, og det er den fejl der ville
koste mest ved flytningen.

Undervejs: `python algoserver_vagt.py --i-vindue` mellem trinnene. Exit ≠ 0
betyder at algoserveren har ændret tilstand — så stop.

Afslut med T9/T10, så hendes konto står flad når hun kommer hjem.

---

## 7 · ⚠ Testen der er BEDRE at lave uden Gateway

Der findes én prøve der kun kan laves mens Gatewayen er nede, og den er vigtig:
**hvad sker der når man trykker K uden ordreforbindelse?**

Svaret skal være en ren afvisning. Det farlige alternativ er at ordren i stilhed
går gennem den **delte** forbindelse og lander på en anden konto — og det er
netop den udgang der ville se ud som en succes.

### Rækkefølgen — og den må ikke byttes om

1. `python konto2_klargoer.py` — **afsnit 3 skal være grønt.** Står der
   "ordre_forbindelse findes: FEJL", så **stop**. Så er blokken ikke læst, og
   et tryk på K ville gå til den delte forbindelse i stedet for at blive afvist.
2. Start Trading Dash, vælg en ticker med kurs
3. Tryk **K**

### Hvad der skal ske

```
Ordreforbindelsen er spaerret: kunne ikke forbinde til Gateway på
127.0.0.1:4002 — kører den, og er API'et slået til?
```

Rød kvittering. **Ingen ordre sendt.**

⚠ **Sker der noget som helst andet** — en grøn kvittering, en position, eller
"⚠ DELT forbindelse" — så stop og sig til. Så falder ordreveje tilbage et sted
de ikke må.

Koden er bygget rigtigt (`main.py:758-766`: en spærret vagt sender fejl og
`continue`, uden fallback), men det er læst, ikke målt på hendes maskine. Det er
forskellen denne prøve lukker.

### Sprængradius, målt af kontrollen selv

Afsnit 5 skriver nu hvad der ligger på **7497**, den delte port:

- **tom** → et konfigurationsuheld ville fejle højlydt
- **en lytter** → ⚠ en ordre uden `ordre_forbindelse` ville gå *derhen*, til
  hvilken konto den TWS nu styrer

På Sørens maskine er den tom. Hendes vides endnu ikke.

---

## Arbejdsdelingen

### Kan gøres nu — uden Iben

| ☐ | |
|---|---|
| ☑ | `git pull` |
| ☑ | `app.exe` overført til repo-roden |
| ☑ | `ordre_forbindelse` i `account.yaml` |
| ☐ | Start backenden én gang — kører journalens `paper`-migration |
| ☐ | `python konto2_klargoer.py` — alt grønt undtagen Gateway |
| ☐ | **Afvisningstesten** (§7) |
| ☐ | Installér IB Gateway (ikke login, bare installationen) |
| ☐ | `python algoserver_vagt.py --gem` — udgangspunkt til senere |

### Kræver Iben — cirka fem minutter

| ☐ | |
|---|---|
| ☐ | Log Gatewayen ind som `fasteriben2`, paper |
| ☐ | Configure → API: port 4002, Read-Only ✗, Master client ID **tom** |
| ☐ | `python konto2_klargoer.py` → **exit 0** |
| ☐ | `konto2_opsaetning.md` §7, T1–T10 |

Hendes tid går til login og til at se testen køre. Alt andet står klar.

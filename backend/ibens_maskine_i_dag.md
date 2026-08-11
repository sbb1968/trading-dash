# Ibens workstation — klargøring over TeamViewer, 2026-08-11

Målet: når Iben kommer hjem fra arbejde, er maskinen **bevist** klar til at handle
manuelt på DUQ441063 — ikke antaget klar.

⚠ **Alt herunder kan gøres nu, uden Iben.** Du loggede selv ind som `fasteriben2`
i går, så du har det der skal til. Hun skal ikke bruges som nøglebærer.

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

1. Installér **IB Gateway** (ikke TWS).
2. Log ind som `fasteriben2`, **paper**.
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

## Hvad der bliver tilbage til Iben selv

Kun at starte Gatewayen og taste sit password. Alt andet er gjort og målt.

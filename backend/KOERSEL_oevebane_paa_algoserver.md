# Øvebanen på algoserveren — installation

Skrevet 13-08-2026. Skal køres **over RDP på iben-algo** — der er hverken SSH,
WinRM eller tilgængelige SMB-shares, så intet af dette kan gøres udefra.

---

## ⚠ 0. Spørgsmålet der afgør resten: har algoserveren markedsdata?

Koden er det lette. **Dataene er ikke i git:**

```
backend/bar_cache/      521 MB   gitignoreret  (.gitignore linje 113)
backend/data_harvest/   466 MB   gitignoreret  (.gitignore linje 123)
```

Et `git clone` giver øvebanen dens kode og *ingen* barer. Uden dem starter den
fint og viser en tom vælger.

**Kør dette på algoserveren FØRST:**

```powershell
cd C:\Projects\trading_dash\backend
"bar_cache:    " + (Get-ChildItem bar_cache -File -ErrorAction SilentlyContinue).Count + " filer"
"data_harvest: " + (Get-ChildItem data_harvest\mes_m2k_stitched -File -ErrorAction SilentlyContinue).Count + " filer"
```

| resultat | betydning |
|---|---|
| ~600 og ~10 filer | ✅ data findes — gå til afsnit 1 |
| 0 filer | ⚠ data mangler — se afsnit 4 **før** du fortsætter |

---

## 1. Hent koden

```powershell
cd C:\projects
git clone https://github.com/sbb1968/trading_practice.git
cd trading_practice
py -3.14 -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
```

⚠ Mappen skal hedde `C:\projects\trading_practice` (lille p som på
workstationen) — `sim/data.py` finder selv sine data via absolutte stier til
`C:\Projects\trading_dash\backend\...`, men de kan overstyres, se afsnit 4.

---

## 2. Byg sessionsindekset

```powershell
venv\Scripts\python -m sim.data
```

Tager ~15 sekunder og skriver `data/sessioner.json`. Forventet output på en
maskine med samme data som workstationen: **16.520 sessioner**.

Er tallet 0, mangler der data — tilbage til afsnit 0.

---

## 3. Start den, så den kan nås fra Ibens maskine

⚠ Som standard binder øvebanen til `127.0.0.1` og kan **kun** nås fra maskinen
selv. Det er med vilje: `0.0.0.0` udstiller den på hele nettet, og der er
**ingen adgangskode** — personvalget er et navn i en drop-down, ikke et login.
På et hjemmenet er det en rimelig afvejning, men den skal træffes bevidst.

```powershell
$env:TRADING_PRACTICE_HOST = "0.0.0.0"
venv\Scripts\python app.py
```

Den skriver en advarsel når den binder bredt. Test fra din workstation:

```powershell
curl http://iben-algo:8100/api/valg?gruppe=MES
```

### Autostart

Læg den i samme Startup-mekanisme som backenden (se
`ALGOSERVER_IBC_udrulning.md`). ⚠ Sæt `TRADING_PRACTICE_HOST` i selve
wrapper-scriptet — en `$env:`-variabel i et RDP-vindue forsvinder når sessionen
lukkes, og så binder den til 127.0.0.1 igen uden at nogen opdager det.

---

## 4. Hvis algoserveren ikke har data

To veje, og de er ikke lige gode:

**A) Kopiér fra workstationen** (~1 GB over nettet, 10–30 min)

```powershell
robocopy C:\Projects\trading_dash\backend\bar_cache `
         \\iben-algo\c$\Projects\trading_dash\backend\bar_cache /E /Z /R:2
robocopy C:\Projects\trading_dash\backend\data_harvest\mes_m2k_stitched `
         \\iben-algo\c$\Projects\trading_dash\backend\data_harvest\mes_m2k_stitched /E /Z /R:2
```

⚠ Kræver admin-share-adgang. Virkede ikke fra min side, så det skal køres med
dine legitimationsoplysninger.

**B) Peg øvebanen på en netværkssti** — ingen kopiering, men langsommere og
afhængig af at workstationen kører:

```powershell
$env:TRADING_PRACTICE_BARS    = "\\soren-pc\c$\Projects\trading_dash\backend\bar_cache"
$env:TRADING_PRACTICE_FUTURES = "\\soren-pc\...\data_harvest\mes_m2k_stitched"
```

⚠ **A er den rigtige**, hvis øvebanen skal være 24/7: hele pointen med
algoserveren er at den kører når workstationen ikke gør. Vej B ophæver det.

---

## 5. Peg knapperne på algoserveren

Når øvebanen kører på iben-algo, skal begge backends vide det. Sæt på **begge**
maskiner, før backenden startes:

```powershell
$env:PRACTICE_URL = "http://iben-algo:8100"
```

Så gør knapperne det rigtige af sig selv:

- **Trading Dash** åbner adressen direkte og forsøger ikke at starte noget —
  ⚠ backenden nægter (409) at "starte" en øvebane den ikke kan nå, i stedet for
  at starte en lokal kopi uden data mens brugeren tror den taler med serveren
- **Studio** henter adressen fra sin egen backend, og da Studio *også* serveres
  fra algoserveren, bliver status-kontrollen nu meningsfuld — modsat i dag, hvor
  den ville måle den forkerte maskine

Ingen URL er hardkodet i frontenden længere.

---

## 6. Fremdriften

`data/handler.db` (SQLite) ligger i øvebanens egen mappe. Kører den ét sted,
samles Sørens og Ibens fremdrift automatisk i den ene fil — hvilket var hele
grunden til at lægge den på algoserveren.

⚠ Kører der en øvebane på workstationen *også*, får den sin egen database, og
de to divergerer lydløst. Stop den lokale når algoserveren overtager.

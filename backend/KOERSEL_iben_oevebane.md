# Ibens workstation — adgang til øvebanen

⚠ **Der skal ikke installeres noget.** Alle maskiner kører øvebanen via
algoserveren, så Iben skal kun have koden der *kalder* den. Én installation
betyder én database — og dermed én fælles fremdrift, som var hele pointen.

Kører hun sin egen, får hun sin egen `handler.db`, og jeres historik divergerer
lydløst.

---

## 1. Ny app.exe — ⚠ `git pull` er ikke nok

Øvebane-knappen ligger i **frontenden** (`Menubar.tsx`), og Iben kører exe'en.
Et `git pull` henter backend-ændringerne, men ikke knappen: `app.exe` er et
build-artefakt og ligger ikke i git.

Bygget 13-08-2026 09:45 på Sørens workstation:

```
C:\Projects\trading_dash\app.exe
  8.965.120 bytes
  SHA256  17A764E030C1DBBCF6140B1661D219052E7D7162DD3DC6BC5FC4521D0F10B1A9
```

Kopiér den til samme sti på Ibens maskine, og **kontrollér hashen bagefter**:

```powershell
(Get-FileHash C:\Projects\trading_dash\app.exe -Algorithm SHA256).Hash
```

⚠ Det er ikke pedanteri. En overførsel er gået galt før (11-08: `90db282d…`
mod forventet `381f1a70…`), og symptomet var at appen slet ikke startede —
uden nogen fejlmeddelelse at gå efter.

---

## 2. `git pull` for backend-ruterne

```powershell
cd C:\Projects\trading_dash
git pull
```

Knappen spørger **hendes egen** backend (`127.0.0.1:8000/practice/status`) om
hvor øvebanen ligger. Uden `git pull` findes ruten ikke, og knappen siger
"kunne ikke nå backenden".

---

## 3. ⚠ Det vigtigste: peg hende på algoserveren

Sæt i den wrapper der starter hendes backend — **ikke** i et PowerShell-vindue,
for den variabel dør med vinduet:

```powershell
$env:PRACTICE_URL = "http://iben-algo:8100"
```

Så gør knappen det rigtige: den åbner algoserverens øvebane direkte og
forsøger ikke at starte noget lokalt.

### Hvis du glemmer det

Backenden nægter nu at starte en øvebane på en maskine uden markedsdata:

```
409  der er ingen markedsdata paa denne maskine (bar_cache mangler).
     Oevebanen koerer paa algoserveren — saet PRACTICE_URL=... foer
     backenden startes.
```

⚠ Den vagt findes fordi det modsatte er værre end en fejlmeddelelse: uden den
ville hendes maskine starte en tom øvebane med sin **egen** database, og
fremdriften ville divergere uden at nogen opdagede det.

Prøvet begge veje: uden data → 409, med data → starter.

---

## 4. Studio

Studio serveres fra algoserveren, så knappen dér kommer med et `git pull` +
genstart af backenden **på iben-algo**. Intet at gøre hos Iben.

---

## 5. Kontrollér

Fra Ibens maskine:

```powershell
curl http://iben-algo:8100/api/valg?gruppe=MES
```

⚠ Svarer den ikke, er det næsten altid ét af to: øvebanen er ikke startet med
`-Bredt` (binder kun til 127.0.0.1), eller firewall-reglen for 8100 mangler på
algoserveren. Se `INSTALLATION.md` i trading_practice, afsnit 4b.

Adressen `iben-algo` virker fra alle Tailscale-forbundne enheder, også uden for
hjemmet — den slår op til 100.76.201.59, ikke en LAN-adresse.

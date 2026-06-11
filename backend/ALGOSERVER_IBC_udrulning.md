# Algoserver — Autonom IBKR-login og opstart (SOM BYGGET)

> Den faktiske opsætning på algoserveren (`iben-algo`), som den endte med at se
> ud efter udrulning og fejlsøgning 7. juni 2026. Bevist ende-til-ende med en
> ægte genstart + en RDP disconnect/reconnect.

---

## Hvad der nu kører (bevist)

Hele kæden kommer op af sig selv efter en genstart, uden manuel indgriben:

1. **Kl. 06:00 dansk tid** genstarter maskinen (Task Scheduler-opgaven
   "Daglig genstart" — fandtes allerede). Frisk login, rydder dagen før,
   håndterer det ugentlige IBKR-søndags-logout (hver dag er et nyt login).
2. **Auto-login** logger automatisk på som `ibens algo` (interaktiv session).
3. **Startup-launcheren** `start_trading_dash.bat` kører wrapperen
   `start_algoserver.ps1`, som:
   - starter IB Gateway via IBC (kun hvis den ikke allerede kører),
   - venter på at port 7497 lytter,
   - kalder `start_backend.bat` (kun hvis intet allerede lytter på 8000).
4. **Gateway logger automatisk på — INGEN 2FA på Ibens konto** (DUO509856),
   bekræftet ved en ægte boot.
5. **Backenden forbinder** til Gateway på 7497 ("API Client: connected" grøn).
6. **ORB-scheduleren** (inde i backenden) handler 09:44 ET (15:44 dansk),
   uafhængigt — Gateway har på det tidspunkt været oppe i timevis.

Backend + Gateway **overlever RDP disconnect** (bekræftet) — Iben kan koble til
og fra uden at vælte noget.

---

## De faktiske komponenter på algoserveren

### 1. Daglig genstart (fandtes allerede — rør ikke)
Task Scheduler-opgave "Daglig genstart":
- `Task To Run: shutdown /r /t 60 /f`
- `Start Time: 06.00.00`, `Schedule Type: Daily`, kører som SYSTEM, Enabled.

Tjek den evt. med:
    schtasks /query /tn "Daglig genstart" /v /fo list | Select-String "Task To Run|Start Time|Schedule Type|Scheduled Task State"

### 2. Gammel backend-opgave — DISABLED (vigtigt)
Der fandtes en gammel opgave "Trading Dash Backend" der kørte
`C:\projects\Trading_Dash\backend\start_backend.bat` som SYSTEM (i session 0).
Den konkurrerede med Startup-launcheren og lavede to backends der sloges om
port 8000 (Errno 10048). **Den er nu DISABLED** (ikke slettet):
    Get-ScheduledTask -TaskName "Trading Dash Backend" | Select-Object TaskName, State   # skal vise Disabled
Genaktivér ALDRIG den — den ville genskabe dobbelt-backend-problemet.

### 3. Startup-launcher (interaktiv session)
Fil: C:\Users\Ibens Algo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\start_trading_dash.bat

    @echo off
    powershell -ExecutionPolicy Bypass -File "C:\projects\trading_dash\start_algoserver.ps1"

### 4. Wrapper (idempotent)
Fil: C:\projects\trading_dash\start_algoserver.ps1

    # start_algoserver.ps1 - starter Gateway, venter paa 7497, kalder start_backend.bat
    # Idempotent: starter ikke dubletter hvis allerede oppe.

    # 1. Gateway: start KUN hvis den ikke allerede koerer
    $gw = Get-Process -Name "ibgateway","java" -ErrorAction SilentlyContinue
    if (-not $gw) {
        Start-Process -FilePath "C:\IBC\StartGateway.bat"
    }

    # 2. Vent paa at port 7497 lytter (poll hvert 5. sek, max ~5 min)
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        $conn = Get-NetTCPConnection -LocalPort 7497 -State Listen -ErrorAction SilentlyContinue
        if ($conn) { $ready = $true; break }
        Start-Sleep -Seconds 5
    }

    # 3. Backend: start KUN hvis intet allerede lytter paa 8000
    if ($ready) {
        Start-Sleep -Seconds 10
        $existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if (-not $existing) {
            Start-Process -FilePath "C:\projects\Trading_Dash\backend\start_backend.bat"
        }
    } else {
        Write-Host "Gateway kom ikke op paa port 7497 inden timeout."
    }

### 5. start_backend.bat (eksisterende — genbrugt, ikke erstattet)
`C:\projects\Trading_Dash\backend\start_backend.bat` aktiverer venv, sætter
`PYTHONUNBUFFERED=1`, og logger til `...\backend\logs\backend_<timestamp>.log`.
Wrapperen kalder den, så vi arver venv-aktivering + logning.

### 6. IBC config (uændret fra workstation-opsætningen)
`%USERPROFILE%\Documents\IBC\config.ini` (Ibens credentials, indtastet af hende):
- `IbLoginId=` Ibens login-brugernavn · `IbPassword=` · `TradingMode=paper`
- `OverrideTwsApiPort=7497` · `AcceptNonBrokerageAccountWarning=yes`
- Gateway-version: 1045 (`TWS_MAJOR_VRSN=1045` i `C:\IBC\StartGateway.bat`)

### 7. TWS-genvej fjernet fra Startup
`Trader Workstation.lnk` blev fjernet fra Startup (den ville starte fuld TWS
og konflikte med Gateway på samme konto).

---

## Genstarts-test (sådan validerer du efter ændringer)

    Restart-Computer -Force

**Vent 15 minutter** (lad auto-login gøre sit færdigt, så RDP-overtagelse ikke
trigger en dobbelt-kørsel midt i opstarten). RDP så ind og tjek:

    curl.exe "http://127.0.0.1:8000/health" -H "X-Internal-Key: z3IU_nkJLarvsaRuzywNC846onou3m7lefjUiCmuZmg"
    Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, SessionId
    Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object OwningProcess

Forventet: `ibkr_connected:true`; to python-processer i **session 1** (parent +
worker — normalt, ikke en dublet); én af dem ejer port 8000. Gateway-vinduet:
alle fire linjer grønne inkl. "API Client".

**To python-processer er normalt** — uvicorn kører en parent + en worker. Tjek
at den ene er den andens forælder:

    Get-CimInstance Win32_Process -Filter "name='python.exe'" | Select-Object ProcessId, ParentProcessId | Format-List

Det er KUN en dublet hvis du ser to processer der startede samme sekund i
session 0 med Errno 10048 i loggen (det gamle problem — nu løst).

---

## RDP-drift — vigtig daglig vane

Forlad altid algoserveren med **Disconnect** (luk RDP-vinduet med X), ALDRIG
**Sign out / Log af**. Disconnect lader auto-login-sessionen (og Gateway +
backend i den) leve videre. Bekræftet: backend forbliver `ibkr_connected:true`
efter disconnect/reconnect. Sign out DRÆBER sessionen og alt der kører i den.

---

## Hold øje med dette den første uge

- **2FA på Ibens konto:** Loggede på uden 2FA ved boot. Bekræft det holder ved
  den daglige 06:00-genstart. Skulle der EN dag dukke en IBKR Mobile-notifikation
  op på hendes telefon, skal hun trykke godkend — så er det ikke 100% hands-off.
- **Mandag efter weekenden:** Tjek at alt er grønt efter det ugentlige
  IBKR-logout. Den daglige reboot skulle håndtere det, men verificér første gang.
- **Backendens reconnect:** Backendens watchdog (`tjekker port 7497 hvert 30.
  sek`) ser ud til at OVERVÅGE, men reconnecter ikke nødvendigvis en Gateway der
  kommer op efter backenden. Derfor er rækkefølgen (Gateway FØR backend) vigtig —
  den er bygget ind i wrapperen, og den daglige reboot sikrer ren rækkefølge.

---

## Faldgruber og læringer fra udrulningen

- **`type config.ini` viser den LOKALE fil i mappen du står i**, ikke
  nødvendigvis den IBC bruger. Autoritativ sti = den `set CONFIG=` i
  `StartGateway.bat` peger på.
- **`AcceptNonBrokerageAccountWarning=yes`** (ikke `no`, ikke udkommenteret) —
  ellers bliver paper-warning-dialogen stående og kræver manuelt klik.
- **`IbLoginId` = IBKR login-brugernavn, IKKE kontonummeret** (ikke DUO509856).
- **Gateway skal køre på engelsk** — IBC genkender kun engelske dialoger.
- **Ét login pr. konto ad gangen** — derfor blev TWS-genvejen fjernet fra Startup.
- **Dobbelt-backend-fælden:** To startmekanismer (gammel SYSTEM-opgave i session
  0 + ny Startup-launcher) startede hver sin backend -> Errno 10048. Løst ved at
  disable den gamle opgave og bruge én idempotent launcher i den interaktive
  session. Tegn på problemet: to python i session 0, samme starttid, Errno 10048.
- **Session 0 = service-session.** Processer dér kan ikke dræbes med
  `Stop-Process` fra din brugersession ("Access denied") — brug
  `taskkill /F /PID <id>` fra en administrator-PowerShell.
- **RDP-overtagelse kan køre Startup-launcheren to gange** (én gang ved
  auto-login, én gang når RDP overtager sessionen). Den idempotente wrapper er
  bagstopperen; at vente 15 min før RDP reducerer risikoen.
- **`schtasks /create` på et eksisterende opgavenavn spørger før overskrivning** —
  svar N og tjek den eksisterende opgave før du overskriver (sådan opdagede vi at
  "Daglig genstart" allerede var sat rigtigt op).

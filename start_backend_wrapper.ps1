# start_backend_wrapper.ps1 - kaldes af scheduled task "Trading Dash Backend"
# (SYSTEM, session 0). Sikrer korrekt raekkefoelge og idempotens:
#   1) venter paa Gateway (7497) FOER uvicorn - backenden reconnecter ikke
#      paalideligt til en Gateway der kommer EFTER den,
#   2) starter kun hvis intet allerede lytter paa 8000,
#   3) kalder start_backend.bat BLOKERENDE, saa task'en forbliver "Running"
#      mens uvicorn koerer - noedvendigt for at genstart-ved-fejl virker.

# 1. Vent paa Gateway (op til ~7,5 min - rigeligt til boot + IBC-login)
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    if (Get-NetTCPConnection -LocalPort 7497 -State Listen -ErrorAction SilentlyContinue) { $ready = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $ready) {
    Write-Host "Gateway (7497) kom ikke op inden timeout - starter ikke backend."
    exit 1
}

# 2. Idempotent: goer intet hvis backenden allerede lytter paa 8000
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port 8000 er allerede i brug - backend koerer. Goer intet."
    exit 0
}

# 3. Start uvicorn (blokerende - task'en forbliver Running mens den koerer)
& "C:\projects\Trading_Dash\backend\start_backend.bat"

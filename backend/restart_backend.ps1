# restart_backend.ps1
# -------------------------------------------------------------------
# Ren genstart af Trading Dash backend paa algoserveren.
# Goer i ET script det du foer gjorde manuelt: stop task, draeb evt.
# efterladte processer paa port 8000, start task igen, bekraeft.
#
# Brug:  .\restart_backend.ps1
# Hvis PowerShell naegter pga. execution policy:
#   powershell -ExecutionPolicy Bypass -File .\restart_backend.ps1
# -------------------------------------------------------------------

$TaskName = "Trading Dash Backend"
$Port     = 8000

Write-Host "=== Genstart af backend ===" -ForegroundColor Cyan

# 1. Stop scheduled task
Write-Host ""
Write-Host "[1/5] Stopper scheduled task '$TaskName'..."
try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "      Task stoppet." -ForegroundColor Green
} catch {
    Write-Host "      Kunne ikke stoppe task (koerer den? findes den?): $_" -ForegroundColor Yellow
}

Start-Sleep -Seconds 2

# 2. Find hvad der stadig lytter paa porten
Write-Host ""
Write-Host "[2/5] Tjekker om noget stadig lytter paa port $Port..."
$conns = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
         Where-Object { $_.LocalPort -eq $Port }

if ($conns) {
    $procIds = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    Write-Host "      Fandt efterladte processer paa port ${Port}: $($procIds -join ', ')" -ForegroundColor Yellow

    # 3. Draeb dem
    Write-Host ""
    Write-Host "[3/5] Draeber efterladte processer..."
    foreach ($procId in $procIds) {
        try {
            $p = Get-Process -Id $procId -ErrorAction Stop
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "      Draebt: PID $procId ($($p.ProcessName))" -ForegroundColor Green
        } catch {
            Write-Host "      Kunne ikke draebe PID ${procId}: $_" -ForegroundColor Red
        }
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "      Port $Port er fri - ingen efterladte processer." -ForegroundColor Green
    Write-Host ""
    Write-Host "[3/5] Intet at draebe - springer over."
}

# 4. Bekraeft port er fri foer genstart
Write-Host ""
Write-Host "[4/5] Bekraefter at port $Port er fri..."
$stillThere = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
              Where-Object { $_.LocalPort -eq $Port }
if ($stillThere) {
    Write-Host "      ADVARSEL: Port $Port er STADIG optaget. Stopper - start ikke" -ForegroundColor Red
    Write-Host "      en ny backend oven i en gammel. Undersoeg manuelt." -ForegroundColor Red
    exit 1
}
Write-Host "      Port $Port er fri." -ForegroundColor Green

# 5. Start task igen
Write-Host ""
Write-Host "[5/5] Starter scheduled task igen..."
try {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "      Task startet." -ForegroundColor Green
} catch {
    Write-Host "      Kunne ikke starte task: $_" -ForegroundColor Red
    exit 1
}

# Vent og bekraeft at backend kommer op
Write-Host ""
Write-Host "Venter paa at backend lytter paa port $Port (op til 20 sek)..."
$up = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    $listening = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
                 Where-Object { $_.LocalPort -eq $Port }
    if ($listening) {
        $up = $true
        $newPid = ($listening | Select-Object -First 1).OwningProcess
        Write-Host "OK: Backend koerer - lytter paa port $Port (PID $newPid)" -ForegroundColor Green
        break
    }
}
if (-not $up) {
    Write-Host "ADVARSEL: Backend lytter ikke paa $Port efter 20 sek." -ForegroundColor Yellow
    Write-Host "  Tjek logfilen i backend\logs\ for opstartsfejl." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Faerdig ===" -ForegroundColor Cyan
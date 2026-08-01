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

# Vent og bekraeft at backend kommer op.
#
# Her stod foer et Get-NetTCPConnection-opslag pr. runde. Det kald tager ~1 sek
# (det gaar gennem CIM), saa 20 runder tog 40-60 sek og ikke de 20 der blev lovet
# — scriptet saa doedt ud netop mens det arbejdede. Et raat TCP-connect-forsoeg
# svarer derimod med det samme: lykkes det, lytter nogen; ellers ikke.
$TimeoutSec = 30
Write-Host ""
Write-Host "Venter paa at backend lytter paa port $Port (op til $TimeoutSec sek)..."
$up = $false
$sw = [Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        $up = $true
    } catch {
        # ingen lytter endnu
    } finally {
        $client.Close()
    }
    if ($up) { break }
    Start-Sleep -Milliseconds 500
    # Vis fremdrift, saa det er tydeligt at der stadig arbejdes
    Write-Host "." -NoNewline
}
Write-Host ""
if ($up) {
    $listening = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
                 Where-Object { $_.LocalPort -eq $Port }
    $newPid = if ($listening) { ($listening | Select-Object -First 1).OwningProcess } else { "?" }
    Write-Host ("OK: Backend koerer - lytter paa port {0} (PID {1}) efter {2:N1} sek." -f `
                $Port, $newPid, $sw.Elapsed.TotalSeconds) -ForegroundColor Green
} else {
    Write-Host "ADVARSEL: Backend lytter ikke paa $Port efter $TimeoutSec sek." -ForegroundColor Yellow
    Write-Host "  Tjek Task Scheduler-historikken og backendens output for opstartsfejl." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Faerdig ===" -ForegroundColor Cyan
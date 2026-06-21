# ─────────────────────────────────────────────────────────────
# Trading Dash launcher
# ─────────────────────────────────────────────────────────────
# Starter backenden (hvis den ikke allerede koerer paa :8000) og derefter
# frontend-appen. Iben dobbeltklikker EN genvej -> alt starter.
#
# Antager at denne fil, trading-dash.exe og backend\ ligger under samme mappe
# ($PSScriptRoot). Backend autostarter typisk allerede ved boot (Task Scheduler),
# saa tjekket springer som regel direkte til at aabne appen.
#
# BEMAERK: TWS skal vaere aabnet og logget ind for LIVE-data (IBKR-login er
# manuelt; launcheren kan ikke logge ind for dig).
# ─────────────────────────────────────────────────────────────
$ErrorActionPreference = "SilentlyContinue"

$root = $PSScriptRoot
# Binaeren kan hedde trading-dash.exe (omdoebt) eller app.exe (raa build-navn).
$exe  = $null
foreach ($name in @("trading-dash.exe", "app.exe")) {
    $p = Join-Path $root $name
    if (Test-Path $p) { $exe = $p; break }
}
$bat  = Join-Path $root "backend\start_backend.bat"

function Test-Backend {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", 8000)
        $c.Close()
        return $true
    } catch { return $false }
}

# 1) Backend oppe? Ellers start den (skjult vindue) og vent paa at :8000 svarer.
if (-not (Test-Backend)) {
    if (Test-Path $bat) {
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$bat`"" -WindowStyle Hidden
        for ($i = 0; $i -lt 40; $i++) {
            if (Test-Backend) { break }
            Start-Sleep -Milliseconds 750
        }
    }
}

# 2) Start frontend-appen.
if ($exe) {
    Start-Process -FilePath $exe -WorkingDirectory $root
} else {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Hverken trading-dash.exe eller app.exe blev fundet i:`n$root`n`nKopiér exe'en hertil og proev igen.",
        "Trading Dash") | Out-Null
}

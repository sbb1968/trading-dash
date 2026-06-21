# ─────────────────────────────────────────────────────────────
# Opret skrivebordsgenvej til Trading Dash (med ikon). Koer EN gang.
# ─────────────────────────────────────────────────────────────
# Genvejen kalder start_trading_dash.ps1 (skjult), som starter backend + app.
# Koer fra mappen hvor start_trading_dash.ps1 + exe'en ligger:
#     powershell -ExecutionPolicy Bypass -File install_shortcut.ps1
# ─────────────────────────────────────────────────────────────
$root = $PSScriptRoot
$ps1  = Join-Path $root "start_trading_dash.ps1"

# Ikon: brug repoets icon.ico hvis det findes, ellers spring (genvej faar default-ikon).
$icon = Join-Path $root "src-tauri\icons\icon.ico"
if (-not (Test-Path $icon)) { $icon = Join-Path $root "icon.ico" }

$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Trading Dash.lnk"

$ws = New-Object -ComObject WScript.Shell
$s  = $ws.CreateShortcut($lnk)
$s.TargetPath       = "powershell.exe"
$s.Arguments        = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ps1`""
$s.WorkingDirectory = $root
$s.WindowStyle      = 7   # minimeret (ingen synlig PowerShell-konsol)
$s.Description       = "Start Trading Dash (backend + app)"
if (Test-Path $icon) { $s.IconLocation = $icon }
$s.Save()

Write-Host "Genvej oprettet: $lnk"
if (Test-Path $icon) { Write-Host "Ikon: $icon" } else { Write-Host "ADVARSEL: intet ikon fundet - genvej faar default-ikon." }

@echo off
REM ─────────────────────────────────────────────────────────────
REM Trading Dash launcher (dobbeltklik direkte)
REM Starter backenden paa :8000 hvis den ikke koerer, venter, aabner appen.
REM Synlige vinduer = man kan se evt. fejl. Skift "start ..." til "start /min ..."
REM naar alt virker, hvis du vil have backend-vinduet minimeret.
REM Ligger i repo-roden; app.exe + backend\ findes relativt via %~dp0.
REM ─────────────────────────────────────────────────────────────
title Trading Dash launcher
cd /d "%~dp0"

REM 1) Backend allerede oppe (port bundet)?
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8000);exit 0}catch{exit 1}"
if errorlevel 1 (
  echo Backend ikke oppe - starter den paa port 8000...
  start "Trading Dash Backend" cmd /k call "%~dp0backend\start_backend.bat"
)

REM 2) Vent paa at backenden SVARER paa HTTP (ikke kun port-bind) - op til 120 sek.
REM    /health-svar = serving. 404 taeller ogsaa (HTTP svarer); kun connection-refused venter.
echo Venter paa at backend svarer paa http://127.0.0.1:8000 (op til 120 sek)...
powershell -NoProfile -Command "for($i=0;$i -lt 120;$i++){try{$null=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8000/health';Write-Host ' backend svarer'; exit 0}catch{if($_.Exception.Response){Write-Host ' backend svarer'; exit 0}; Start-Sleep 1}}; Write-Host ' TIMEOUT - backend svarede ikke'; exit 1"
if errorlevel 1 (
  echo ADVARSEL: backend svarede ikke inden timeout. Tjek "Trading Dash Backend"-vinduet for fejl.
  echo Appen aabnes alligevel - tryk Analyser igen naar backenden er klar.
)
if not exist "%~dp0app.exe" (
  echo FEJL: app.exe blev ikke fundet i %~dp0
  echo Kopiér app.exe hertil og proev igen.
  pause
  exit /b 1
)
echo Starter Trading Dash...
start "" "%~dp0app.exe"
exit

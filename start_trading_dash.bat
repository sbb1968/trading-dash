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

REM 1) Backend allerede oppe paa :8000?
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8000);exit 0}catch{exit 1}"
if not errorlevel 1 goto startapp

echo Backend ikke oppe - starter den paa port 8000...
start "Trading Dash Backend" cmd /k call "%~dp0backend\start_backend.bat"

echo Venter paa at backend svarer paa :8000 (op til 60 sek)...
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8000);Write-Host ' backend oppe';exit 0}catch{Start-Sleep 1}}; Write-Host ' TIMEOUT - backend kom ikke op'; exit 1"

:startapp
if not exist "%~dp0app.exe" (
  echo FEJL: app.exe blev ikke fundet i %~dp0
  echo Kopiér app.exe hertil og proev igen.
  pause
  exit /b 1
)
echo Starter Trading Dash...
start "" "%~dp0app.exe"
exit

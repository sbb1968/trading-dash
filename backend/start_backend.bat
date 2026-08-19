@echo off
REM Trading Dash backend
REM   start_backend.bat            -> stille, alt i logfil (Task Scheduler ved boot)
REM   start_backend.bat --synlig   -> udskrift i vinduet (naar et menneske kigger med)
REM
REM Baggrund: foer havde den KUN den stille tilstand. Startede man den fra
REM skrivebordsgenvejen, aabnede der sig et vindue der stod HELT TOMT, mens alt
REM interessant - herunder om ordre-forbindelsen kom op paa den rigtige konto -
REM laa i en logfil ingen vidste fandtes. Et vindue uden udskrift ligner en
REM backend der ikke laver noget.
REM
REM ⚠ ASCII KUN. En .bat laeses i OEM-kodesiden; danske tegn i echo-linjer
REM bliver til volapyk i konsollen.

REM %~dp0 = mappen denne fil ligger i. Foer stod stien HAARDKODET til
REM C:\projects\Trading_Dash\backend, saa scriptet kun virkede paa én maskine
REM med praecis den stavemaade.
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

set TIMESTAMP=%date:~-4%-%date:~3,2%-%date:~0,2%_%time:~0,2%-%time:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOGFILE=%~dp0logs\backend_%TIMESTAMP%.log

REM Tving Python til ubuffered output saa print() lander med det samme
set PYTHONUNBUFFERED=1

call venv\Scripts\activate.bat

if /i "%~1"=="--synlig" goto synlig

REM ---- Stille tilstand (uaendret adfaerd for Task Scheduler ved boot) ----
echo Starting backend at %date% %time% > "%LOGFILE%"
echo Working dir: %CD% >> "%LOGFILE%"
echo. >> "%LOGFILE%"
python -u -m uvicorn main:app --host 0.0.0.0 --port 8000 >> "%LOGFILE%" 2>&1
goto :eof

:synlig
echo ==========================================================
echo  Trading Dash backend
echo  Tid:    %date% %time%
echo  Mappe:  %CD%
echo  Logfil: %LOGFILE%
echo ==========================================================
echo.
REM ⚠ FOERSTE UDGAVE SKREV INGEN FIL I DENNE TILSTAND, men printede alligevel
REM "Logfil: ..." i headeren ovenfor. Filen fandtes ikke, og et
REM   Get-ChildItem logs\backend_*.log ^| Sort LastWriteTime ^| Select -Last 1
REM fandt derfor den FORRIGE koersels log — altsaa udskrift fra foer den
REM genstart man lige havde lavet. En header der peger paa en fil der ikke
REM skrives, er en paastand uden daekning.
REM
REM Nu tee'es der: skaerm OG fil. Se logtee.py om hvorfor det ikke er
REM PowerShells Tee-Object.
echo Starting backend at %date% %time% > "%LOGFILE%"
echo Working dir: %CD% >> "%LOGFILE%"
echo. >> "%LOGFILE%"
python -u -m uvicorn main:app --host 0.0.0.0 --port 8000 2>&1 | python -u logtee.py "%LOGFILE%"
echo.
echo ==========================================================
echo  BACKENDEN ER STOPPET
echo  Aarsagen staar i de sidste linjer ovenfor, og i logfilen:
echo  %LOGFILE%
echo ==========================================================
REM ⚠ INTET EXITNUMMER HER. Efter et roer er %errorlevel% SIDSTE leds kode,
REM altsaa logtee's — ikke uvicorns. Et forkert tal er vaerre end intet tal.
pause

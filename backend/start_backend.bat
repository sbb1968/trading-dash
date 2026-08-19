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
REM ⚠ INGEN omdirigering her, og ingen tee. En tee ville kraeve en
REM PowerShell-pipe midt i opstarten, og den aendrer baade Ctrl+C og
REM procestraeet paa en server der skal koere hele dagen. Vinduet ER loggen i
REM denne tilstand; boot-stien ovenfor skriver stadig fil.
python -u -m uvicorn main:app --host 0.0.0.0 --port 8000
echo.
echo ==========================================================
echo  BACKENDEN ER STOPPET  (exitkode %errorlevel%)
echo  Vinduet bliver staaende saa fejlen kan laeses.
echo ==========================================================
pause

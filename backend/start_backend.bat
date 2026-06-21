@echo off
REM Trading Dash backend - autostart script
REM Kaldes af Windows Task Scheduler ved boot

cd /d C:\projects\Trading_Dash\backend

REM Sikr at logs-mappen findes (ellers fejler alle log-redirects + uvicorn-start)
if not exist "logs" mkdir "logs"

REM Log med timestamp i filnavnet
set TIMESTAMP=%date:~-4%-%date:~3,2%-%date:~0,2%_%time:~0,2%-%time:~3,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOGFILE=C:\projects\Trading_Dash\backend\logs\backend_%TIMESTAMP%.log

REM Tving Python til ubuffered output saa print() ender i logfilen
set PYTHONUNBUFFERED=1

REM Aktiver venv og start uvicorn
echo Starting backend at %date% %time% > "%LOGFILE%"
echo Working dir: %CD% >> "%LOGFILE%"
echo Python unbuffered: %PYTHONUNBUFFERED% >> "%LOGFILE%"
echo. >> "%LOGFILE%"

call venv\Scripts\activate.bat
python -u -m uvicorn main:app --host 0.0.0.0 --port 8000 >> "%LOGFILE%" 2>&1

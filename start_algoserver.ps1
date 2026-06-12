# start_algoserver.ps1 - starter KUN Gateway.
# Backenden startes af scheduled task "Trading Dash Backend" (SYSTEM, session 0),
# saa den overlever interaktive aflogninger. Denne launcher maa derfor IKKE
# laengere starte backenden (det gav dobbelt-start + session 1-saarbarhed).
# Idempotent: starter ikke Gateway hvis den allerede koerer.

# 1. Gateway: start KUN hvis den ikke allerede koerer
$gw = Get-Process -Name "ibgateway","java" -ErrorAction SilentlyContinue
if (-not $gw) {
    Start-Process -FilePath "C:\IBC\StartGateway.bat"
}

# 2. Vent paa at port 7497 lytter (poll hvert 5. sek, max ~5 min) - kun for at
#    bekraefte at Gateway kom op. Backenden startes IKKE her.
for ($i = 0; $i -lt 60; $i++) {
    if (Get-NetTCPConnection -LocalPort 7497 -State Listen -ErrorAction SilentlyContinue) { break }
    Start-Sleep -Seconds 5
}

# installer_oevebane.ps1 — sæt Trading Practice op på denne maskine
# ════════════════════════════════════════════════════════════════════════════
# Bygget til algoserveren, men virker alle steder. Kør i PowerShell:
#
#     cd C:\Projects\trading_dash\backend
#     .\installer_oevebane.ps1                # installér
#     .\installer_oevebane.ps1 -KunKontrol    # kig, rør ingenting
#     .\installer_oevebane.ps1 -Bredt         # bind til 0.0.0.0 bagefter
#
# ⚠ DEN STOPPER HVIS DER IKKE ER MARKEDSDATA. bar_cache (521 MB) og
# data_harvest (466 MB) er gitignorerede, så et git clone giver koden og INGEN
# barer. Uden dem starter øvebanen fint og viser en tom vælger — altså en
# installation der ser vellykket ud og ikke er det. Derfor kontrolleres data
# FØRST, og scriptet nægter at fortsætte uden.

param(
    [switch]$KunKontrol,
    [switch]$Bredt,
    [string]$Maal = "C:\projects\trading_practice",
    [string]$Repo = "https://github.com/sbb1968/trading_practice.git"
)

$ErrorActionPreference = "Stop"
$backend = Split-Path -Parent $MyInvocation.MyCommand.Path

function Linje { Write-Output ("-" * 74) }
function Trin($n, $t) { Write-Output ""; Write-Output "[$n] $t"; Linje }

Write-Output ("=" * 74)
Write-Output "  TRADING PRACTICE — installation paa $env:COMPUTERNAME"
Write-Output ("=" * 74)

# ── 0. Data ─────────────────────────────────────────────────────────────────
Trin 0 "Har denne maskine markedsdata?"

$barCache  = Join-Path $backend "bar_cache"
$stitched  = Join-Path $backend "data_harvest\mes_m2k_stitched"
$nBar = if (Test-Path $barCache) { (Get-ChildItem $barCache -File).Count } else { 0 }
$nFut = if (Test-Path $stitched) { (Get-ChildItem $stitched -File).Count } else { 0 }

Write-Output ("  bar_cache           {0,6} filer   {1}" -f $nBar, $barCache)
Write-Output ("  mes_m2k_stitched    {0,6} filer   {1}" -f $nFut, $stitched)

if ($nBar -eq 0 -and $nFut -eq 0) {
    Write-Output ""
    Write-Output "  X STOP — der er ingen markedsdata paa denne maskine."
    Write-Output ""
    Write-Output "    Oevebanen ville installere fint og vise en TOM vaelger."
    Write-Output "    Kopiér data fra workstationen foerst (ca. 1 GB):"
    Write-Output ""
    # ⚠ ENKELTCITATER. I dobbeltcitater skal baade $ og backtick escapes, og en
    # enkelt fejl dér vaelter parsingen af HELE filen — som den gjorde her.
    Write-Output '      robocopy \\win11sbb\c$\Projects\trading_dash\backend\bar_cache <maal> /E /Z /R:2'
    Write-Output ("               maal = " + $barCache)
    Write-Output '      robocopy \\win11sbb\c$\...\data_harvest\mes_m2k_stitched <maal> /E /Z /R:2'
    Write-Output ("               maal = " + $stitched)
    Write-Output ""
    Write-Output "    Se KOERSEL_oevebane_paa_algoserver.md afsnit 4."
    exit 1
}
if ($nBar -eq 0) { Write-Output "  ! bar_cache mangler — kun futures vil kunne oeves" }
if ($nFut -eq 0) { Write-Output "  ! futures mangler — kun aktier vil kunne oeves" }

# ── 1. Koden ────────────────────────────────────────────────────────────────
Trin 1 "Koden"

if (Test-Path (Join-Path $Maal "app.py")) {
    Write-Output "  findes allerede: $Maal"
    if (-not $KunKontrol) {
        Push-Location $Maal
        # ⚠ Ikke `git pull` blindt: er der lokale aendringer, stopper den med en
        # merge-konflikt midt i en installation. Vis foerst hvad der er.
        $status = git status --porcelain
        if ($status) {
            Write-Output "  ! lokale aendringer — pull springes over:"
            $status | ForEach-Object { Write-Output "      $_" }
        } else {
            git pull --ff-only 2>&1 | ForEach-Object { Write-Output "      $_" }
        }
        Pop-Location
    }
} elseif ($KunKontrol) {
    Write-Output "  mangler: $Maal  (ville blive klonet)"
} else {
    Write-Output "  kloner $Repo"
    New-Item -ItemType Directory -Force (Split-Path -Parent $Maal) | Out-Null
    git clone $Repo $Maal 2>&1 | ForEach-Object { Write-Output "      $_" }
}

# ── 2. venv ─────────────────────────────────────────────────────────────────
Trin 2 "Python-miljoe"

$py = Join-Path $Maal "venv\Scripts\python.exe"
if (Test-Path $py) {
    Write-Output "  venv findes: $py"
} elseif ($KunKontrol) {
    Write-Output "  venv mangler (ville blive oprettet)"
} else {
    Write-Output "  opretter venv"
    & py -3.14 -m venv (Join-Path $Maal "venv")
    if (-not (Test-Path $py)) { Write-Output "  X venv blev ikke oprettet"; exit 1 }
}
if (-not $KunKontrol -and (Test-Path $py)) {
    & $py -m pip install --quiet --disable-pip-version-check -r (Join-Path $Maal "requirements.txt")
    Write-Output "  afhaengigheder installeret"
}

# ── 3. Indekset ─────────────────────────────────────────────────────────────
Trin 3 "Sessionsindeks"

if ($KunKontrol -or -not (Test-Path $py)) {
    Write-Output "  springes over"
} else {
    Push-Location $Maal
    & $py -m sim.data 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Output "      $_" }
    Pop-Location
    $indeks = Join-Path $Maal "data\sessioner.json"
    if (-not (Test-Path $indeks)) { Write-Output "  X indekset blev ikke bygget"; exit 1 }
}

# ── 4. Start ────────────────────────────────────────────────────────────────
Trin 4 "Start"

$koerer = $false
try {
    $t = New-Object Net.Sockets.TcpClient
    $t.Connect("127.0.0.1", 8100); $koerer = $t.Connected; $t.Close()
} catch { }

if ($koerer) {
    Write-Output "  oevebanen svarer allerede paa 8100"
} elseif ($KunKontrol) {
    Write-Output "  koerer ikke (ville blive startet)"
} else {
    if ($Bredt) {
        # ⚠ 0.0.0.0 udstiller oevebanen paa HELE nettet, og der er INGEN
        # adgangskode — personvalget er et navn i en drop-down, ikke et login.
        # Det er derfor et flag man skal skrive, ikke standarden.
        $env:TRADING_PRACTICE_HOST = "0.0.0.0"
        Write-Output "  ! binder til 0.0.0.0 — naabar fra hele nettet UDEN adgangskode"
    }
    Start-Process -FilePath $py -ArgumentList (Join-Path $Maal "app.py") `
                  -WorkingDirectory $Maal -WindowStyle Hidden
    Write-Output "  startet — venter paa svar ..."
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $t = New-Object Net.Sockets.TcpClient
            $t.Connect("127.0.0.1", 8100); $koerer = $t.Connected; $t.Close()
        } catch { }
        if ($koerer) { break }
    }
    if ($koerer) { Write-Output "  svarer paa 8100" }
    else { Write-Output "  X svarede ikke inden for 20 sekunder" }
}

# ── 5. Resultat ─────────────────────────────────────────────────────────────
Write-Output ""
Linje
if ($koerer) {
    try {
        $j = Invoke-RestMethod "http://127.0.0.1:8100/api/valg?gruppe=aktier" -TimeoutSec 5
        $sum = ($j.grupper | ForEach-Object { "$($_.vaerdi)=$($_.antal)" }) -join "  "
        Write-Output "  KLAR:  http://127.0.0.1:8100"
        Write-Output "  Sessioner:  $sum"
        if ($Bredt) { Write-Output ("  Udefra:     http://" + $env:COMPUTERNAME + ":8100") }
    } catch {
        Write-Output "  ! porten svarer, men API'et gjorde ikke: $_"
    }
} else {
    Write-Output "  Ikke startet."
}
Linje
Write-Output ""
Write-Output "Naeste skridt naar den skal vaere den FASTE oevebane:"
Write-Output "  1. Saet TRADING_PRACTICE_HOST=0.0.0.0 i autostart-wrapperen"
Write-Output '     (ikke i et RDP-vindue - den variabel doer med sessionen)'
Write-Output '  2. Saet PRACTICE_URL=http://iben-algo:8100 paa BEGGE maskiners backend'
Write-Output "  3. Stop en evt. lokal oevebane paa workstationen — to koerende"
Write-Output "     giver hver sin database, og fremdriften divergerer lydloest"

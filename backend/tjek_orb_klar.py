"""
tjek_orb_klar.py
────────────────
Daglig "er ORB klar?"-rapport for algoserveren.

Samler de fire tjek vi ellers laver manuelt hver dag i ÉN rapport:

  1. Python-processer   — kører kun de processer vi forventer (backend),
                          eller er der ukendte/for mange?
  2. Backend kører      — svarer FastAPI-backenden på /status?
  3. TWS + IBKR         — er TWS logget ind (port 7497) ifølge watchdog'en?
  4. ORB-jobbet aktivt  — kører scheduleren, er start_algo-jobbet (09:44 ET /
                          ~15:44 dansk tid) planlagt, er det en handelsdag,
                          og er denne instans algoserveren?

Når Iben har logget ind på TWS skulle ALLE fire vises grønne — så starter
ORB automatisk kl. 09:44 ET uden vi rører noget.

Kør på ALGOSERVEREN (fuldt tjek, alle fire punkter):
    python tjek_orb_klar.py              # kør én gang
    python tjek_orb_klar.py --watch      # kør hvert 15. sek (Ctrl+C stopper)
    python tjek_orb_klar.py --watch 30   # kør hvert 30. sek

Kør EKSTERNT fra workstationen (fjern-overblik over algoserveren):
    python tjek_orb_klar.py --url http://<algoserver-ip>:8000
    python tjek_orb_klar.py --remote --url http://127.0.0.1:8000   # via SSH-tunnel

I remote-tilstand kan to ting IKKE tjekkes (de er bundet til den maskine
scriptet kører på), så de markeres "springes over" i stedet for at risikere
et FALSK grønt resultat:
  - Python-procestjekket (ser kun den lokale maskines processer)
  - Den lokale portprobe af 7497 (ville ramme workstationens egen TWS)
Remote stoler derfor KUN på tws_online-feltet fra algoserverens /status.

Remote aktiveres automatisk når --url ikke peger på localhost, eller
eksplicit med --remote (nyttigt ved SSH-tunnel hvor URL'en er localhost
men TWS/processer reelt sidder på en anden maskine).

Læser kun status — ændrer INTET. Ingen ordrer, ingen start/stop.

Placering: C:\\Projects\\trading_dash\\backend\\tjek_orb_klar.py
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

# ── KONFIG ─────────────────────────────────────────────────────
BACKEND_URL        = "http://127.0.0.1:8000"
STATUS_PATH        = "/status"
HTTP_TIMEOUT_SEC   = 4.0

TWS_HOST           = "127.0.0.1"
TWS_PORT           = 7497
TWS_PROBE_TIMEOUT  = 3.0

# Den instans der må auto-starte ORB. start_algo-jobbet springes over
# på alt andet end "algoserver" (se scheduler._job_start_algo).
FORVENTET_ROLLE    = "algoserver"

# ORB-startjobbets navn og forventede tidspunkt (ET) i scheduleren.
ORB_JOB_NAVN       = "start_algo"
ORB_JOB_ET_TID     = "09:44"        # ~15:44 dansk tid (EDT)

# En python-proces regnes som "forventet" (backend-relateret) hvis dens
# kommandolinje indeholder ét af disse fragmenter. Alt andet markeres ukendt.
BACKEND_MARKORER   = ["uvicorn", "main:app", "trading_dash", "trading-dash"]

# Sæt til et tal hvis du vil håndhæve et præcist antal backend-processer
# (fx 2 når backenden køres med --reload: reloader + worker). None = tæl ikke
# hårdt, kræv blot mindst én backend-proces og nul ukendte.
FORVENTEDE_BACKEND_PROCS = None


# ── ANSI-farver ────────────────────────────────────────────────
if os.name == "nt":
    os.system("")   # aktiverer ANSI-escape i moderne Windows-terminal

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# Status-koder pr. tjek
OK   = "ok"      # opfyldt
WAIT = "wait"    # afventer en handling (typisk Iben-login) — ikke en fejl
WARN = "warn"    # noget at undersøge, men ikke blokerende
FAIL = "fail"    # ikke opfyldt / blokerende
SKIP = "skip"    # kan ikke tjekkes her (remote) — neutralt, blokerer ikke


def _badge(status: str) -> str:
    return {
        OK:   f"{GREEN}[ OK ]{RESET}",
        WAIT: f"{YELLOW}[VENT]{RESET}",
        WARN: f"{YELLOW}[ ! ]{RESET}",
        FAIL: f"{RED}[FEJL]{RESET}",
        SKIP: f"{DIM}[ - ]{RESET}",
    }.get(status, "[????]")


def _line(status: str, titel: str, detalje: str = "") -> None:
    suffix = f"  {DIM}{detalje}{RESET}" if detalje else ""
    print(f"  {_badge(status)} {titel}{suffix}")


# ── Hent /status ───────────────────────────────────────────────
def hent_status(base_url: str):
    """Hent /status fra backenden. Returnerer (dict|None, fejltekst|None)."""
    url = base_url.rstrip("/") + STATUS_PATH
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw), None
    except urllib.error.URLError as e:
        return None, f"kunne ikke nå {url} ({e.reason})"
    except Exception as e:
        return None, f"uventet fejl mod {url}: {e}"


# ── Python-procestabel via PowerShell ──────────────────────────
def list_python_processer():
    """
    Returnerer en liste af {pid, cmd} for alle python-processer (undtagen
    dette script selv). Bruger PowerShell CIM (virker på Win11 uden ekstra
    afhængigheder). Returnerer None hvis det slet ikke kunne lade sig gøre.
    """
    eget_pid = os.getpid()

    ps_cmd = (
        "Get-CimInstance Win32_Process "
        "-Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return _fallback_tasklist(eget_pid)

    data = (out.stdout or "").strip()
    if not data:
        return []   # ingen python-processer overhovedet

    try:
        parsed = json.loads(data)
    except Exception:
        return _fallback_tasklist(eget_pid)

    # ConvertTo-Json giver et enkelt objekt (ikke liste) ved præcis én proces
    if isinstance(parsed, dict):
        parsed = [parsed]

    procs = []
    for p in parsed:
        pid = p.get("ProcessId")
        if pid is None or pid == eget_pid:
            continue
        procs.append({"pid": pid, "cmd": (p.get("CommandLine") or "").strip()})
    return procs


def _fallback_tasklist(eget_pid: int):
    """Reserveplan hvis PowerShell fejler: tasklist giver kun PID, ingen cmd."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    procs = []
    for row in (out.stdout or "").splitlines():
        cols = [c.strip('"') for c in row.split('","')]
        if len(cols) >= 2:
            try:
                pid = int(cols[1])
            except ValueError:
                continue
            if pid != eget_pid:
                procs.append({"pid": pid, "cmd": ""})
    return procs


def _er_backend(cmd: str) -> bool:
    low = cmd.lower()
    return any(m in low for m in BACKEND_MARKORER)


def _kort_cmd(cmd: str, n: int = 70) -> str:
    if not cmd:
        return "(kommandolinje ikke tilgængelig)"
    cmd = " ".join(cmd.split())
    return cmd if len(cmd) <= n else cmd[: n - 1] + "…"


# ── Tjek 1: Python-processer ───────────────────────────────────
def tjek_python_processer(remote=False):
    if remote:
        _line(SKIP, "Python-processer",
              "kan ikke tjekkes eksternt — kør lokalt på algoserveren for dette")
        return SKIP

    procs = list_python_processer()

    if procs is None:
        _line(WARN, "Python-processer", "kunne ikke opgøre processer (PowerShell+tasklist fejlede)")
        return WARN

    backend = [p for p in procs if _er_backend(p["cmd"])]
    ukendt  = [p for p in procs if not _er_backend(p["cmd"])]

    status = OK
    if not backend:
        status = FAIL
    elif ukendt:
        status = WARN
    if FORVENTEDE_BACKEND_PROCS is not None and len(backend) != FORVENTEDE_BACKEND_PROCS:
        status = WARN

    detalje = f"{len(backend)} backend, {len(ukendt)} ukendt (i alt {len(procs)})"
    if FORVENTEDE_BACKEND_PROCS is not None:
        detalje += f" — forventet {FORVENTEDE_BACKEND_PROCS} backend"
    _line(status, "Python-processer", detalje)

    for p in backend:
        print(f"        {GREEN}•{RESET} PID {p['pid']:<6} {DIM}{_kort_cmd(p['cmd'])}{RESET}")
    for p in ukendt:
        print(f"        {RED}•{RESET} PID {p['pid']:<6} {YELLOW}UKENDT{RESET} {DIM}{_kort_cmd(p['cmd'])}{RESET}")

    return status


# ── Tjek 2: Backend kører ──────────────────────────────────────
def tjek_backend(status_data, fejl):
    if status_data is None:
        _line(FAIL, "Backend kører", fejl or "intet svar")
        return FAIL

    ident = status_data.get("identity", {})
    instans = ident.get("instance", "?")
    rolle   = ident.get("role", "?")
    ibkr    = ident.get("ibkr", "?")
    paper   = ident.get("paper")
    paper_s = "paper" if paper else ("LIVE" if paper is False else "?")
    _line(OK, "Backend kører",
          f"{instans} · rolle={rolle} · {ibkr} ({paper_s})")
    return OK


# ── Tjek 3: TWS + IBKR ─────────────────────────────────────────
def tjek_tws(status_data, remote=False):
    # Pålidelig kilde: watchdog'en prober aktivt port 7497.
    wd = (status_data or {}).get("tws_watchdog", {}) or {}
    online = wd.get("tws_online") is True
    fails  = wd.get("fails")

    if remote:
        # Eksternt MÅ vi ikke probe 7497 lokalt — det ville ramme den maskine
        # scriptet kører på (typisk workstationens egen TWS) og kunne give et
        # falsk grønt. Stol derfor kun på algoserverens eget tws_online-felt.
        if online:
            _line(OK, "TWS + IBKR (Iben logget ind)", "watchdog på algoserver: online")
            return OK
        d = "watchdog på algoserver: offline"
        if fails:
            d += f" · {fails} mislykkede probes"
        _line(WAIT, "TWS + IBKR (Iben logget ind)", d + " — afventer login")
        return WAIT

    # Lokalt: krydstjek watchdog mod en uafhængig portprobe (fanger stale state).
    port_aaben = _probe_port(TWS_HOST, TWS_PORT)

    if online or port_aaben:
        detalje = f"port {TWS_PORT} svarer"
        if online and not port_aaben:
            detalje = "watchdog: online (port svarede ikke lige nu)"
        elif port_aaben and not online:
            detalje = f"port {TWS_PORT} åben (watchdog ikke nået at opdatere)"
        _line(OK, "TWS + IBKR (Iben logget ind)", detalje)
        return OK

    d = f"port {TWS_PORT} svarer ikke"
    if fails:
        d += f" · {fails} mislykkede probes"
    _line(WAIT, "TWS + IBKR (Iben logget ind)", d + " — afventer login")
    return WAIT


def _probe_port(host, port):
    try:
        with socket.create_connection((host, port), timeout=TWS_PROBE_TIMEOUT):
            return True
    except Exception:
        return False


# ── Tjek 4: ORB-jobbet aktivt ──────────────────────────────────
def tjek_orb_job(status_data):
    sched = (status_data or {}).get("scheduler", {}) or {}
    ident = (status_data or {}).get("identity", {}) or {}

    running       = sched.get("running") is True
    is_trade_day  = sched.get("is_trading_day")
    next_start    = sched.get("next_start", "?")
    now_et        = sched.get("now_et", "?")
    rolle         = ident.get("role")

    jobs = sched.get("jobs", []) or []
    orb_job = next((j for j in jobs if j.get("name") == ORB_JOB_NAVN), None)

    problemer = []
    if not running:
        problemer.append("scheduler kører ikke")
    if rolle != FORVENTET_ROLLE:
        problemer.append(f"rolle={rolle} (ORB auto-start kun på '{FORVENTET_ROLLE}')")
    if orb_job is None:
        problemer.append(f"jobbet '{ORB_JOB_NAVN}' findes ikke")
    elif orb_job.get("et_time") != ORB_JOB_ET_TID:
        problemer.append(f"starttid {orb_job.get('et_time')} ET (forventet {ORB_JOB_ET_TID})")
    if is_trade_day is False:
        problemer.append("ikke en handelsdag i dag")

    if problemer:
        # Manglende handelsdag er ikke en fejl i opsætningen — det er bare en fridag.
        status = WARN if (is_trade_day is False and len(problemer) == 1) else FAIL
        _line(status, "ORB-jobbet aktivt (09:44 ET)", "; ".join(problemer))
    else:
        kort = (next_start or "").replace(" ET", "")
        _line(OK, "ORB-jobbet aktivt (09:44 ET / ~15:44 DK)",
              f"næste start {kort} ET")
        status = OK

    print(f"        {DIM}nu: {now_et} ET · næste ORB-start: {next_start}{RESET}")
    return status


# ── Samlet rapport ─────────────────────────────────────────────
def koer_rapport(base_url: str, remote: bool = False) -> int:
    status_data, fejl = hent_status(base_url)

    print()
    print(f"{BOLD}{'=' * 64}{RESET}")
    titel = "ER ORB KLAR?"
    if remote:
        titel += "  (remote)"
    print(f"{BOLD}  {titel}  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (dansk tid){RESET}")
    print(f"{BOLD}{'=' * 64}{RESET}")
    print()

    r = {}
    r["proc"]    = tjek_python_processer(remote=remote)
    r["backend"] = tjek_backend(status_data, fejl)
    r["tws"]     = tjek_tws(status_data, remote=remote)
    r["job"]     = tjek_orb_job(status_data)

    print()
    print(f"{BOLD}{'─' * 64}{RESET}")

    # Hvis backenden er nede kan TWS/job ikke vurderes meningsfuldt.
    if r["backend"] == FAIL:
        print(f"  {RED}{BOLD}✗ BACKEND KØRER IKKE — start den først.{RESET}")
        if remote:
            print(f"    {DIM}Tjek at algoserveren kører og at adressen {base_url} er nåelig.{RESET}")
        else:
            print(f"    {DIM}cd C:\\Projects\\trading_dash\\backend && uvicorn main:app --reload{RESET}")
        print(f"{BOLD}{'─' * 64}{RESET}\n")
        return 2

    # SKIP tæller ikke med i dommen — kun de tjek vi faktisk kunne lave.
    sprunget = [k for k, s in r.items() if s == SKIP]
    aktive   = [s for s in r.values() if s != SKIP]

    if all(s == OK for s in aktive):
        print(f"  {GREEN}{BOLD}✓ ALT KLAR — ORB starter automatisk kl. 09:44 ET (~15:44 dansk tid).{RESET}")
        print(f"    {DIM}Ingen handling nødvendig.{RESET}")
        exit_code = 0
    elif any(s == FAIL for s in aktive):
        manglende = [k for k, s in r.items() if s == FAIL]
        print(f"  {RED}{BOLD}✗ IKKE KLAR — problem med: {', '.join(manglende)}{RESET}")
        exit_code = 2
    elif r["tws"] == WAIT and all(s in (OK, WAIT) for s in aktive):
        print(f"  {YELLOW}{BOLD}⏳ AFVENTER IBEN — alt andet er klart. ORB starter automatisk{RESET}")
        print(f"  {YELLOW}{BOLD}   så snart TWS er logget ind.{RESET}")
        exit_code = 1
    else:
        advarsler = [k for k, s in r.items() if s in (WARN, WAIT)]
        print(f"  {YELLOW}{BOLD}! Næsten klar — undersøg: {', '.join(advarsler)}{RESET}")
        exit_code = 1

    if sprunget:
        print(f"  {DIM}(ikke tjekket eksternt: {', '.join(sprunget)} — kør lokalt på algoserveren for fuldt tjek){RESET}")

    print(f"{BOLD}{'─' * 64}{RESET}\n")
    return exit_code


# ── CLI ────────────────────────────────────────────────────────
def _er_lokal_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "")


def parse_args():
    base_url = BACKEND_URL
    watch    = None     # None = kør én gang; ellers interval i sek
    remote   = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--url" and i + 1 < len(args):
            base_url = args[i + 1]; i += 2
        elif a == "--remote":
            remote = True; i += 1
        elif a == "--watch":
            watch = 15
            # valgfrit interval lige efter --watch
            if i + 1 < len(args) and args[i + 1].isdigit():
                watch = int(args[i + 1]); i += 2
            else:
                i += 1
        else:
            i += 1

    # Peger URL'en væk fra denne maskine, er vi reelt remote — også uden flaget.
    if not _er_lokal_url(base_url):
        remote = True
    return base_url, watch, remote


def main() -> int:
    base_url, watch, remote = parse_args()

    if watch is None:
        return koer_rapport(base_url, remote=remote)

    print(f"{DIM}Overvåger hvert {watch}. sek — Ctrl+C for at stoppe{RESET}")
    try:
        while True:
            koer_rapport(base_url, remote=remote)
            time.sleep(watch)
    except KeyboardInterrupt:
        print("\nStoppet.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""
afstem_positioner.py
────────────────────
KUN-LÆSNING. Krydser journalens åbne rows mod de FAKTISKE IBKR-positioner
og klassificerer hver enkelt, så vi kan rydde spøgelses-handler op sikkert.

Den åbner INGEN egen IBKR-forbindelse (undgår client-id-kollision med
backenden) og SKRIVER intet. Den henter blot to eksisterende, auth-frie
endpoints fra den kørende backend og sammenligner dem:

  /journal/open-positions   — journal-rows hvor exit_time_utc IS NULL
  /account/dash-snapshot     — de positioner IBKR faktisk holder lige nu

Klassifikation pr. ticker:

  ÅBEN_REEL       journal: åben  +  IBKR: holder den
                  → ægte position. Hvis entry er fra en TIDLIGERE dag og
                    kilden er en strategi, er den sandsynligvis forældreløs
                    (strategien har tabt den fra hukommelsen). UMAC forventes her.

  GHOST_JOURNAL   journal: åben  +  IBKR: holder den IKKE
                  → regnskabs-spøgelse. Positionen er reelt væk i IBKR, men
                    close blev aldrig logget. Journal-rowen skal lukkes.

  USPORET_IBKR    journal: ingen åben row  +  IBKR: holder den
                  → position uden åben journal-row (manuel handel, anden
                    strategi, eller entry blev aldrig logget). Kræver et kig.

Kør på den maskine hvis backend/IBKR du vil afstemme (typisk algoserveren,
eller workstationen for DUN748991):

    python afstem_positioner.py
    python afstem_positioner.py --url http://127.0.0.1:8000
    python afstem_positioner.py --json afstemning.json   # gem rapport til fil

Placering: C:\\Projects\\trading_dash\\backend\\afstem_positioner.py
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime

BACKEND_URL      = "http://127.0.0.1:8000"
HTTP_TIMEOUT_SEC = 8.0

# ── ANSI-farver ────────────────────────────────────────────────
if os.name == "nt":
    os.system("")

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def _get(base_url, path):
    url = base_url.rstrip("/") + path
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.URLError as e:
        return None, f"kunne ikke nå {url} ({getattr(e, 'reason', e)})"
    except Exception as e:
        return None, f"uventet fejl mod {url}: {e}"


def _open_rows(payload):
    """Normalisér /journal/open-positions til en liste af rows."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # endpoint returnerer typisk {"positions": [...]}
        for key in ("positions", "trades", "open_positions"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _ibkr_positions(payload):
    """Normalisér /account/dash-snapshot til en dict {TICKER: row} for
    positioner med faktisk antal != 0."""
    out = {}
    if not payload or not payload.get("ok"):
        return out, (payload or {}).get("error")
    for p in payload.get("positions", []) or []:
        qty = p.get("position") or 0
        try:
            if abs(float(qty)) < 1e-9:
                continue
        except (TypeError, ValueError):
            continue
        out[(p.get("ticker") or "").upper()] = p
    return out, None


def _today_et():
    # Vi har ikke nødvendigvis pytz her; brug systemets dato som rimelig
    # approksimation — rapporten viser entry-datoen råt, så et evt. skæv
    # midnats-ET-tilfælde er let at se manuelt.
    return datetime.now().date().isoformat()


def _entry_date(row):
    et = row.get("entry_time_et") or row.get("entry_time") or ""
    return et[:10] if len(et) >= 10 else et


def main():
    base_url = BACKEND_URL
    json_out = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--url" and i + 1 < len(args):
            base_url = args[i + 1]; i += 2
        elif args[i] == "--json" and i + 1 < len(args):
            json_out = args[i + 1]; i += 2
        else:
            i += 1

    print()
    print(f"{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  POSITIONS-AFSTEMNING (kun læsning)  —  "
          f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")

    journal_raw, jerr = _get(base_url, "/journal/open-positions")
    snap_raw,    serr = _get(base_url, "/account/dash-snapshot")

    if jerr:
        print(f"  {RED}Kunne ikke hente journal: {jerr}{RESET}")
        return 2

    open_rows = _open_rows(journal_raw)
    ibkr, ibkr_err = _ibkr_positions(snap_raw)

    if serr or ibkr_err:
        print(f"  {YELLOW}Advarsel: IBKR-snapshot ikke tilgængeligt "
              f"({serr or ibkr_err}).{RESET}")
        print(f"  {YELLOW}Uden IBKR-data kan vi ikke skelne ÅBEN_REEL fra "
              f"GHOST_JOURNAL — log ind på TWS og kør igen.{RESET}\n")
        ibkr_available = False
    else:
        ibkr_available = True

    # Byg journal-opslag pr. ticker (kan teoretisk være flere rows pr. ticker)
    journal_by_ticker = {}
    for r in open_rows:
        sym = (r.get("symbol") or r.get("ticker") or "").upper()
        journal_by_ticker.setdefault(sym, []).append(r)

    today = _today_et()
    results = {"ÅBEN_REEL": [], "GHOST_JOURNAL": [], "USPORET_IBKR": []}

    # Gennemgå journalens åbne rows
    for sym, rows in sorted(journal_by_ticker.items()):
        in_ibkr = sym in ibkr
        for r in rows:
            entry_d = _entry_date(r)
            rec = {
                "ticker":      sym,
                "trade_id":    r.get("trade_id"),
                "source":      r.get("source"),
                "instance_id": r.get("instance_id"),
                "side":        r.get("side"),
                "shares":      r.get("shares"),
                "entry_price": r.get("entry_price"),
                "entry_date":  entry_d,
                "stale":       bool(entry_d and entry_d < today),
                "ibkr_qty":    ibkr.get(sym, {}).get("position") if in_ibkr else None,
                "ibkr_pnl":    ibkr.get(sym, {}).get("pnl") if in_ibkr else None,
            }
            if not ibkr_available:
                results.setdefault("UKLAR", []).append(rec)
            elif in_ibkr:
                results["ÅBEN_REEL"].append(rec)
            else:
                results["GHOST_JOURNAL"].append(rec)

    # IBKR-positioner uden åben journal-row
    if ibkr_available:
        for sym, p in sorted(ibkr.items()):
            if sym not in journal_by_ticker:
                results["USPORET_IBKR"].append({
                    "ticker":   sym,
                    "ibkr_qty": p.get("position"),
                    "avg_cost": p.get("avg_cost"),
                    "ibkr_pnl": p.get("pnl"),
                })

    # ── Udskriv ────────────────────────────────────────────────
    def row_line(rec):
        bits = []
        if rec.get("source"):      bits.append(f"kilde={rec['source']}")
        if rec.get("side"):        bits.append(rec["side"])
        if rec.get("shares") is not None: bits.append(f"{rec['shares']} stk")
        if rec.get("entry_price") is not None: bits.append(f"entry ${rec['entry_price']}")
        if rec.get("entry_date"):  bits.append(f"åbnet {rec['entry_date']}")
        if rec.get("ibkr_qty") is not None: bits.append(f"IBKR={rec['ibkr_qty']}")
        if rec.get("ibkr_pnl") is not None: bits.append(f"uP&L ${rec['ibkr_pnl']}")
        if rec.get("avg_cost") is not None: bits.append(f"avg ${rec['avg_cost']}")
        tail = f"  {DIM}{rec.get('trade_id','')}{RESET}" if rec.get("trade_id") else ""
        return f"      {DIM}{' · '.join(bits)}{RESET}{tail}"

    n_real  = len(results["ÅBEN_REEL"])
    n_ghost = len(results["GHOST_JOURNAL"])
    n_untr  = len(results["USPORET_IBKR"])
    n_uklar = len(results.get("UKLAR", []))

    if results.get("UKLAR"):
        print(f"{YELLOW}{BOLD}● UKLAR — {n_uklar} åbne journal-rows "
              f"(IBKR-data mangler){RESET}")
        for rec in results["UKLAR"]:
            print(f"   {YELLOW}{rec['ticker']}{RESET}")
            print(row_line(rec))
        print()

    if ibkr_available:
        print(f"{CYAN}{BOLD}● ÅBEN_REEL — {n_real} (journal åben + IBKR holder den){RESET}")
        if not results["ÅBEN_REEL"]:
            print(f"   {DIM}ingen{RESET}")
        for rec in results["ÅBEN_REEL"]:
            flag = f"  {RED}← FORÆLDRELØS? (åbnet før i dag, ikke styret){RESET}" if rec["stale"] else ""
            print(f"   {CYAN}{rec['ticker']}{RESET}{flag}")
            print(row_line(rec))
        print()

        print(f"{YELLOW}{BOLD}● GHOST_JOURNAL — {n_ghost} (journal åben + IBKR holder den IKKE){RESET}")
        if not results["GHOST_JOURNAL"]:
            print(f"   {DIM}ingen{RESET}")
        for rec in results["GHOST_JOURNAL"]:
            print(f"   {YELLOW}{rec['ticker']}{RESET}  {DIM}→ journal-row skal lukkes{RESET}")
            print(row_line(rec))
        print()

        print(f"{RED}{BOLD}● USPORET_IBKR — {n_untr} (IBKR holder den + ingen åben journal-row){RESET}")
        if not results["USPORET_IBKR"]:
            print(f"   {DIM}ingen{RESET}")
        for rec in results["USPORET_IBKR"]:
            print(f"   {RED}{rec['ticker']}{RESET}")
            print(row_line(rec))
        print()

    # ── Opsummering + anbefalinger ─────────────────────────────
    print(f"{BOLD}{'─' * 70}{RESET}")
    if not ibkr_available:
        print(f"  {n_uklar} åbne journal-rows fundet, men IBKR-data mangler.")
        print(f"  Log ind på TWS og kør igen for fuld klassifikation.")
    else:
        print(f"  {BOLD}Resultat:{RESET} {n_real} reelle åbne · {n_ghost} regnskabs-ghosts "
              f"· {n_untr} usporede IBKR-positioner")
        print()
        if n_real:
            print(f"  {CYAN}ÅBEN_REEL{RESET}: ægte positioner. De markerede 'forældreløs?' bør")
            print(f"    lukkes manuelt i TWS (Sell), da strategien ikke styrer dem længere.")
        if n_ghost:
            print(f"  {YELLOW}GHOST_JOURNAL{RESET}: luk journal-rows for disse (de findes ikke i IBKR).")
            print(f"    trade_id'erne står ovenfor — vi lukker dem målrettet, ikke i flæng.")
        if n_untr:
            print(f"  {RED}USPORET_IBKR{RESET}: undersøg — manuel handel, anden strategi, eller")
            print(f"    et entry der aldrig blev logget.")
    print(f"{BOLD}{'─' * 70}{RESET}\n")

    if json_out:
        try:
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump({
                    "generated_at": datetime.now().isoformat(),
                    "backend":      base_url,
                    "ibkr_available": ibkr_available,
                    "results":      results,
                }, f, indent=2, ensure_ascii=False)
            print(f"  Rapport gemt: {json_out}\n")
        except Exception as e:
            print(f"  Kunne ikke gemme JSON: {e}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
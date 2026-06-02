"""
studio_testdata.py
──────────────────
Indsætter et lille sæt TYDELIGT MÆRKEDE test-handler i journalens trades-
tabel, så Studio's Oversigt-fane (især mobil-visningen) kan testes med
udfyldte felter — strategi-pills, købstider, antal, total købspris, sortering
og realiseret P&L.

Alle test-rækker har trade_id der starter med "TEST-", så de er trivielle
at fjerne igen. Kør med --clean for KUN at slette dem.

VIGTIGT — hvad du KAN og IKKE kan se med test-data:
  • Åbne positioner: strategi-pill, retning, KØBSTID, antal, "Kost" (total
    købspris) og SORTERING vises korrekt — alt fra journalen.
  • MEN den gule "Nu"-pris og urealiseret P&L kommer fra /account/dash-snapshot
    (IBKR's LIVE positioner), ikke journalen. Da IBKR ikke reelt holder disse
    test-tickers, står "Nu" og urealiseret P&L som "—". Det er forventet.
  • Seneste handler: ALLE felter vises fuldt ud (alt kommer fra journalen).

Standard-kørsel rydder først gamle TEST-rækker og indsætter friske, så den
er sikker at køre flere gange. Den rører IKKE rigtige handler.

Brug (på workstationen, i backend-mappen):
    python studio_testdata.py            # ryd gamle TEST-rækker + indsæt friske
    python studio_testdata.py --clean    # fjern KUN test-rækkerne igen
    python studio_testdata.py --db trading_dash.db

Placering: C:\\Projects\\trading_dash\\backend\\studio_testdata.py
"""

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"; CYAN="\033[96m"
BOLD="\033[1m"; DIM="\033[2m"; RESET="\033[0m"
if os.name == "nt":
    os.system("")

DEFAULT_DB_CANDIDATES = ["trading_dash.db", "backend/trading_dash.db"]
TEST_PREFIX = "TEST-"

# Kolonner præcis som log_trade_open bruger dem.
COLUMNS = [
    "trade_id", "account_id", "instance_id", "ibkr_account",
    "source", "variant",
    "symbol", "side", "shares",
    "entry_time_utc", "entry_time_et", "entry_price", "entry_reason",
    "exit_time_utc", "exit_time_et", "exit_price", "exit_reason",
    "pnl", "pnl_pct", "duration_sec",
    "capital_used",
    "current_stop", "current_target", "current_stage", "trail_stop",
    "notes", "payload_json",
]


def find_db(explicit):
    if explicit:
        return explicit if Path(explicit).exists() else None
    for c in DEFAULT_DB_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def et_dt(hh, mm):
    """Returnér (utc_iso, et_iso) for i DAG kl. hh:mm ET."""
    now_et = datetime.now(ET) if ET else datetime.now()
    d = now_et.date()
    if ET:
        local = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
        return local.astimezone(ZoneInfo("UTC")).isoformat(), local.isoformat()
    # fallback uden zoneinfo (bør ikke ske på 3.14)
    local = datetime(d.year, d.month, d.day, hh, mm)
    return local.isoformat(), local.isoformat()


def row(source, variant, symbol, side, shares, entry_hm, entry_price,
        exit_hm=None, exit_price=None, exit_reason=None, stop=None):
    """Byg en komplet trades-row. exit_hm=None => åben position."""
    e_utc, e_et = et_dt(*entry_hm)
    capital = round(entry_price * shares, 2)

    if exit_hm is None:
        # ÅBEN position — exit_time_utc SKAL være NULL
        return {
            "trade_id": TEST_PREFIX + str(uuid.uuid4()),
            "account_id": "test", "instance_id": "workstation",
            "ibkr_account": "TEST", "source": source, "variant": variant,
            "symbol": symbol, "side": side, "shares": shares,
            "entry_time_utc": e_utc, "entry_time_et": e_et,
            "entry_price": entry_price, "entry_reason": "testdata",
            "exit_time_utc": None, "exit_time_et": None, "exit_price": None,
            "exit_reason": None, "pnl": None, "pnl_pct": None,
            "duration_sec": None, "capital_used": capital,
            "current_stop": stop, "current_target": None,
            "current_stage": "initial", "trail_stop": None,
            "notes": "", "payload_json": "{}",
        }

    # LUKKET handel
    x_utc, x_et = et_dt(*exit_hm)
    if side == "long":
        pnl = round((exit_price - entry_price) * shares, 2)
        pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2)
    else:
        pnl = round((entry_price - exit_price) * shares, 2)
        pnl_pct = round((entry_price - exit_price) / entry_price * 100, 2)
    dur = (exit_hm[0] * 60 + exit_hm[1]) - (entry_hm[0] * 60 + entry_hm[1])
    return {
        "trade_id": TEST_PREFIX + str(uuid.uuid4()),
        "account_id": "test", "instance_id": "workstation",
        "ibkr_account": "TEST", "source": source, "variant": variant,
        "symbol": symbol, "side": side, "shares": shares,
        "entry_time_utc": e_utc, "entry_time_et": e_et,
        "entry_price": entry_price, "entry_reason": "testdata",
        "exit_time_utc": x_utc, "exit_time_et": x_et, "exit_price": exit_price,
        "exit_reason": exit_reason, "pnl": pnl, "pnl_pct": pnl_pct,
        "duration_sec": dur * 60, "capital_used": capital,
        "current_stop": stop, "current_target": None,
        "current_stage": "closed", "trail_stop": None,
        "notes": "", "payload_json": "{}",
    }


def build_rows():
    rows = []
    # ── 3 ÅBNE positioner (forskellige strategier, forskellige købstider) ──
    # Sorteres nyest-købt-først i mobil → ELMT (10:05) skal stå øverst.
    rows.append(row("Konfluens",    "baseline",  "VCIG", "long", 218, (9, 35),  5.80, stop=5.40))
    rows.append(row("Momentum ORB", "all_winner","ABSI", "long", 209, (9, 50),  5.99, stop=5.70))
    rows.append(row("Konfluens",    "baseline",  "ELMT", "long", 145, (10, 5),  8.59, stop=8.10))

    # ── 4 LUKKEDE handler (forskellige udfald + retning) ──
    # Sorteres nyest-solgt-først i mobil → ABSI (10:12) øverst.
    rows.append(row("Momentum ORB", "all_winner","CRSR", "long", 100, (9, 30),  18.00, (9, 35),  17.77, "stop",  stop=17.70))
    rows.append(row("Konfluens",    "baseline",  "KSS",  "long",  50, (9, 31),  20.00, (9, 40),  20.61, "trail", stop=19.80))
    rows.append(row("Konfluens",    "baseline",  "ABSI", "long",  80, (9, 33),   5.50, (10, 12),  5.94, "trail", stop=5.30))
    rows.append(row("Momentum ORB", "all_winner","TLRY", "short", 300, (9, 45),  2.40, (10, 2),   2.55, "stop",  stop=2.50))
    return rows


def main():
    db_path = None
    clean_only = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--db" and i + 1 < len(args):
            db_path = args[i + 1]; i += 2
        elif args[i] == "--clean":
            clean_only = True; i += 1
        else:
            i += 1

    db_file = find_db(db_path)
    if not db_file:
        print(f"{RED}Fandt ikke databasen. Angiv med --db <sti>.{RESET}")
        return 2

    conn = sqlite3.connect(db_file)
    try:
        # Bekræft at trades-tabellen findes
        t = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone()
        if not t:
            print(f"{RED}Ingen 'trades'-tabel i {db_file}. Er det den rigtige DB?{RESET}")
            return 2

        # Ryd altid gamle TEST-rækker først (idempotent)
        cur = conn.execute(
            f"DELETE FROM trades WHERE trade_id LIKE '{TEST_PREFIX}%'")
        removed = cur.rowcount
        conn.commit()

        if clean_only:
            print(f"{GREEN}Fjernede {removed} test-række(r). Databasen er ren.{RESET}")
            print(f"{DIM}Hård refresh i Studio (Ctrl+Shift+R) for at se ændringen.{RESET}")
            return 0

        rows = build_rows()
        placeholders = ", ".join(["?"] * len(COLUMNS))
        sql = f"INSERT INTO trades ({', '.join(COLUMNS)}) VALUES ({placeholders})"
        for r in rows:
            conn.execute(sql, [r[c] for c in COLUMNS])
        conn.commit()

        n_open   = sum(1 for r in rows if r["exit_time_utc"] is None)
        n_closed = len(rows) - n_open
        if removed:
            print(f"{DIM}(ryddede {removed} tidligere test-række(r) først){RESET}")
        print(f"{GREEN}{BOLD}Indsatte {len(rows)} test-handler:{RESET} "
              f"{n_open} åbne · {n_closed} lukkede\n")
        print(f"  {BOLD}Åbne positioner{RESET} (nyest købt øverst i mobil):")
        print(f"    ELMT 10:05 Konfluens · ABSI 09:50 ORB · VCIG 09:35 Konfluens")
        print(f"  {BOLD}Seneste handler{RESET} (nyest solgt øverst i mobil):")
        print(f"    ABSI 10:12 +trail · TLRY 10:02 short/stop · KSS 09:40 +trail · CRSR 09:35 stop\n")
        print(f"{CYAN}Test sådan:{RESET}")
        print(f"  1. Studio → Oversigt, gør vinduet smalt (<768px) eller F12 → Ctrl+Shift+M")
        print(f"  2. Hård refresh: {BOLD}Ctrl+Shift+R{RESET}")
        print(f"  3. Tjek åbne: strategi-pill, købstid, antal, 'Kost', sortering")
        print(f"     Tjek seneste: pill, retning, køb→salg-tid, antal, entry, P&L %\n")
        print(f"{YELLOW}Bemærk:{RESET} Den gule 'Nu'-pris og urealiseret P&L står som '—',")
        print(f"  fordi de kommer fra IBKR-snapshot (live positioner), og IBKR holder")
        print(f"  ikke disse test-tickers. Alt det journal-baserede vises korrekt.\n")
        print(f"{DIM}Ryd op bagefter med:  python studio_testdata.py --clean{RESET}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

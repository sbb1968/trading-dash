"""
tjek_ticker_historik.py
───────────────────────
KUN-LÆSNING. Viser alle journal-rows (åbne OG lukkede) for bestemte
tickers, så vi kan klassificere en USPORET_IBKR-position:

  • Findes der en LUKKET row?  → close blev logget, men IBKR-ordren fyldte
    aldrig (divergens — journalens statistik indeholder en fiktiv lukket
    handel, mens IBKR stadig holder aktierne).

  • Findes der INGEN row?      → entry blev aldrig logget (trade_id var None
    ved entry), så positionen har været usynlig for journalen hele tiden.

Åbner databasen i read-only mode (file:...?mode=ro) — kan ikke skrive.

Kør på den maskine hvis journal du undersøger (her: algoserveren):

    python tjek_ticker_historik.py FRSH GLOB MASK
    python tjek_ticker_historik.py FRSH --db trading_dash.db

Placering: C:\\Projects\\trading_dash\\backend\\tjek_ticker_historik.py
"""

import os
import sqlite3
import sys
from pathlib import Path

GREEN  = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"
CYAN   = "\033[96m"; BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"
if os.name == "nt":
    os.system("")

DEFAULT_DB_CANDIDATES = ["trading_dash.db", "backend/trading_dash.db"]

COLS = ["trade_id", "source", "side", "shares",
        "entry_time_et", "entry_price",
        "exit_time_et", "exit_price", "exit_reason", "pnl"]


def find_db(explicit):
    if explicit:
        return explicit if Path(explicit).exists() else None
    for c in DEFAULT_DB_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def main():
    tickers = []
    db_path = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--db" and i + 1 < len(args):
            db_path = args[i + 1]; i += 2
        else:
            tickers.append(args[i].upper()); i += 1

    if not tickers:
        tickers = ["FRSH", "GLOB", "MASK"]

    db_file = find_db(db_path)
    if not db_file:
        print(f"{RED}Fandt ikke databasen. Angiv med --db <sti>.{RESET}")
        print(f"{DIM}Prøvede: {', '.join(DEFAULT_DB_CANDIDATES)}{RESET}")
        return 2

    # Read-only forbindelse — kan ikke ændre noget.
    uri = f"file:{Path(db_file).as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        print(f"{RED}Kunne ikke åbne {db_file} read-only: {e}{RESET}")
        return 2

    print()
    print(f"{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  JOURNAL-HISTORIK (read-only)  —  {db_file}{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")

    col_sql = ", ".join(COLS)

    for t in tickers:
        try:
            cur = conn.execute(
                f"SELECT {col_sql} FROM trades WHERE symbol = ? "
                f"ORDER BY entry_time_et",
                (t,),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            print(f"{RED}SQL-fejl for {t}: {e}{RESET}")
            print(f"{DIM}(kolonnenavne kan afvige — tjek skemaet){RESET}\n")
            continue

        open_rows   = [r for r in rows if r[COLS.index("exit_time_et")] is None]
        closed_rows = [r for r in rows if r[COLS.index("exit_time_et")] is not None]

        # Overskrift + klassifikation
        if not rows:
            verdict = f"{RED}INGEN row → entry blev ALDRIG logget (trade_id None ved entry){RESET}"
        elif open_rows:
            verdict = f"{CYAN}ÅBEN row findes → burde være fanget som ÅBEN_REEL{RESET}"
        else:
            verdict = (f"{YELLOW}KUN lukkede rows → close blev logget, men IBKR-ordren "
                       f"fyldte aldrig (divergens){RESET}")

        print(f"{BOLD}{t}{RESET}  —  {verdict}")

        if not rows:
            print(f"   {DIM}(ingen handler i journalen for {t}){RESET}\n")
            continue

        for r in rows:
            d = dict(zip(COLS, r))
            is_open = d["exit_time_et"] is None
            tag = f"{CYAN}ÅBEN{RESET}" if is_open else f"{DIM}lukket{RESET}"
            line = (f"   [{tag}] {d['trade_id']}  "
                    f"{d.get('source','?')} {d.get('side','')} {d.get('shares','?')} stk  "
                    f"entry {d.get('entry_price','?')} @ {d.get('entry_time_et','?')}")
            if not is_open:
                line += (f"  →  exit {d.get('exit_price','?')} @ {d.get('exit_time_et','?')} "
                         f"({d.get('exit_reason','?')}) P&L {d.get('pnl','?')}")
            print(line)
        print()

    conn.close()

    print(f"{BOLD}{'─' * 70}{RESET}")
    print(f"  {YELLOW}KUN lukkede rows{RESET} → close-divergens: journalens statistik tæller en")
    print(f"     fiktiv lukket handel; IBKR holder stadig aktierne. Begge skal rettes.")
    print(f"  {RED}INGEN row{RESET} → entry aldrig logget: kun en uovervåget IBKR-position,")
    print(f"     ingen forkert statistik. Skal blot lukkes (manuelt i TWS).")
    print(f"{BOLD}{'─' * 70}{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
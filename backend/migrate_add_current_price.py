#!/usr/bin/env python3
"""
migrate_add_current_price.py
────────────────────────────
Engangs-migrering: tilføjer kolonnen `current_price` til en EKSISTERENDE
trades-tabel der blev oprettet før kolonnen blev tilføjet til skemaet.

Idempotent og sikker: tjekker om kolonnen findes før den tilføjes, så den
kan køres flere gange og på allerede-rettede databaser uden at fejle.
Rører ingen data — tilføjer kun en NULL-kolonne.

NB: Den samme migrering kører også automatisk ved hver backend-opstart
(journal.Journal.init() → idempotent ALTER). Dette script er den manuelle
no-restart-vej + til at rette arkiver/algoserverens DB.

Kør fra backend/:
    python migrate_add_current_price.py
    python migrate_add_current_price.py --db trading_dash.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def column_exists(conn, table: str, column: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    return column in cols


def main() -> int:
    ap = argparse.ArgumentParser(description="Tilføj current_price til trades (idempotent)")
    ap.add_argument("--db", default="trading_dash.db", help="sti til SQLite-databasen")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    if not db_path.exists():
        print(f"FEJL: databasen findes ikke: {db_path}")
        print("Kør fra backend/ hvor trading_dash.db ligger.")
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        # Findes trades-tabellen overhovedet?
        t = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone()
        if not t:
            print(f"FEJL: ingen 'trades'-tabel i {db_path.name} — er det den rigtige DB?")
            return 1

        before = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

        if column_exists(conn, "trades", "current_price"):
            print(f"OK: Kolonnen 'current_price' findes allerede — intet at gøre. "
                  f"({before} trades urørt.)")
            return 0

        # ALTER TABLE ... ADD COLUMN er ikke-destruktiv: eksisterende rækker
        # får NULL i den nye kolonne, ingen data ændres.
        conn.execute("ALTER TABLE trades ADD COLUMN current_price REAL")
        conn.commit()

        after = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        ok = column_exists(conn, "trades", "current_price")
        print(f"OK: Tilfoejede 'current_price' (REAL, NULL) til trades.")
        print(f"   Trades før: {before} · efter: {after} (uændret — ingen data rørt).")
        print(f"   Kolonne nu til stede: {ok}")
        print(f"   Genstart backenden, så list_trades virker igen.")
        return 0
    except Exception as e:
        print(f"FEJL under migrering: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

"""
historical_db.py
────────────────
SQLite-database til historisk markedsdata for Gap Fade backtest.

Database: backend/data/historical.db (separat fra trading_dash.db)

Schema (5 tabeller):
  - tickers       : Vores univers af aktier + adaptive filter-flag
  - bars_daily    : Daglige OHLCV-bars (alle aktive tickers, 12 mdr)
  - bars_5min     : 5-min OHLCV-bars med pre-market (kun gap-dage)
  - gap_events    : Kuraterede gap-events fundet i daily-data
  - download_log  : Track hvad vi har downloadet (genstartbar)

Brug:
    from historical_db import get_connection, init_database

    init_database()              # første gang
    with get_connection() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM tickers")
        print(cur.fetchone())

CLI:
    python historical_db.py            # init + status
    python historical_db.py --reset    # WARNING: sletter alt og genopretter

Placering: C:\\Projects\\trading-dash\\backend\\historical_db.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


# ── Sti til database ──────────────────────────────────────────
# Bevidst i backend/data/ så den ligger sammen med CSV-filerne
DB_PATH = Path(__file__).parent / "data" / "historical.db"


# ── Schema-definition ────────────────────────────────────────
# Hver tabel som separat SQL-statement for læsbarhed.
# CREATE TABLE IF NOT EXISTS gør det idempotent — sikker at køre flere gange.

SCHEMA_STATEMENTS = [

    # ── Tabel 1: tickers ──────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS tickers (
        symbol         TEXT PRIMARY KEY,
        name           TEXT,
        exchange       TEXT,
        added_date     TEXT NOT NULL,
        is_active      INTEGER NOT NULL DEFAULT 1,
        is_gap_active  INTEGER NOT NULL DEFAULT 0,
        notes          TEXT
    )
    """,

    # ── Tabel 2: bars_daily ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS bars_daily (
        ticker  TEXT NOT NULL,
        date    TEXT NOT NULL,
        open    REAL NOT NULL,
        high    REAL NOT NULL,
        low     REAL NOT NULL,
        close   REAL NOT NULL,
        volume  INTEGER NOT NULL,
        PRIMARY KEY (ticker, date),
        FOREIGN KEY (ticker) REFERENCES tickers(symbol)
    )
    """,

    # ── Tabel 3: bars_5min ────────────────────────────────────
    # timestamp gemmes som ISO-format med timezone (ET)
    # is_premarket beregnes ved insert for hurtige queries
    """
    CREATE TABLE IF NOT EXISTS bars_5min (
        ticker        TEXT NOT NULL,
        timestamp     TEXT NOT NULL,
        open          REAL NOT NULL,
        high          REAL NOT NULL,
        low           REAL NOT NULL,
        close         REAL NOT NULL,
        volume        INTEGER NOT NULL,
        is_premarket  INTEGER NOT NULL,
        PRIMARY KEY (ticker, timestamp),
        FOREIGN KEY (ticker) REFERENCES tickers(symbol)
    )
    """,

    # ── Tabel 4: gap_events ───────────────────────────────────
    # Kuraterede gap-dage — resultatet af gap-finder
    """
    CREATE TABLE IF NOT EXISTS gap_events (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker           TEXT NOT NULL,
        date             TEXT NOT NULL,
        prev_close       REAL NOT NULL,
        open             REAL NOT NULL,
        gap_pct          REAL NOT NULL,
        gap_direction    TEXT NOT NULL CHECK (gap_direction IN ('up', 'down')),
        day_high         REAL NOT NULL,
        day_low          REAL NOT NULL,
        day_close        REAL NOT NULL,
        gap_filled       INTEGER NOT NULL DEFAULT 0,
        gap_filled_time  TEXT,
        volume           INTEGER NOT NULL,
        has_5min_data    INTEGER NOT NULL DEFAULT 0,
        UNIQUE (ticker, date),
        FOREIGN KEY (ticker) REFERENCES tickers(symbol)
    )
    """,

    # ── Tabel 5: download_log ─────────────────────────────────
    # Sporing af download-jobs — gør genstart efter crash trivielt
    """
    CREATE TABLE IF NOT EXISTS download_log (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        download_type     TEXT NOT NULL CHECK (download_type IN ('daily', '5min')),
        ticker            TEXT NOT NULL,
        date_range_start  TEXT NOT NULL,
        date_range_end    TEXT NOT NULL,
        started_at        TEXT NOT NULL,
        completed_at      TEXT,
        status            TEXT NOT NULL CHECK (status IN ('in_progress', 'success', 'failed')),
        error_msg         TEXT,
        bars_inserted     INTEGER DEFAULT 0
    )
    """,
]


# ── Indekser for hurtige queries ──────────────────────────────
INDEX_STATEMENTS = [
    # bars_daily — primary key dækker (ticker, date), men vi vil også query
    # alle bars på en specifik dato (fx "alle daily bars 2024-06-15")
    "CREATE INDEX IF NOT EXISTS idx_bars_daily_date ON bars_daily(date)",

    # bars_5min — query efter dato uden ticker
    "CREATE INDEX IF NOT EXISTS idx_bars_5min_date ON bars_5min(substr(timestamp, 1, 10))",

    # gap_events — de tre mest brugte queries
    "CREATE INDEX IF NOT EXISTS idx_gap_events_date ON gap_events(date)",
    "CREATE INDEX IF NOT EXISTS idx_gap_events_ticker_date ON gap_events(ticker, date)",
    "CREATE INDEX IF NOT EXISTS idx_gap_events_pct ON gap_events(gap_pct)",

    # download_log — find igangværende/fejlede jobs hurtigt
    "CREATE INDEX IF NOT EXISTS idx_download_log_status ON download_log(status, download_type)",
]


# ─────────────────────────────────────────────────────────────────
# Forbindelse — context manager med automatic commit/close
# ─────────────────────────────────────────────────────────────────

@contextmanager
def get_connection(read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """
    Hent en SQLite-forbindelse til historical.db.

    Brug som context manager:
        with get_connection() as conn:
            conn.execute(...)

    Commit sker automatisk ved succes, rollback ved exception.

    Args:
        read_only: Hvis True, åbn read-only (bruges af backtest til at undgå
                   utilsigtede writes). Default False.
    """
    # Sikr at parent directory eksisterer
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if read_only:
        # SQLite URI-syntax for read-only
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)

    # Returnér rows som dicts (lettere at arbejde med)
    conn.row_factory = sqlite3.Row

    # Aktiver foreign keys (SQLite default = OFF, hvilket er en fælde)
    conn.execute("PRAGMA foreign_keys = ON")

    # WAL-mode = bedre concurrent reads under download
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")

    try:
        yield conn
        if not read_only:
            conn.commit()
    except Exception:
        if not read_only:
            conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────
# Initialisering
# ─────────────────────────────────────────────────────────────────

def init_database() -> None:
    """
    Opret databasen og alle tabeller/indekser hvis de ikke findes.

    Idempotent — sikker at køre flere gange. Vil ikke overskrive data.
    """
    with get_connection() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
        for stmt in INDEX_STATEMENTS:
            conn.execute(stmt)
    print(f"✓ Database initialiseret: {DB_PATH}")


def reset_database() -> None:
    """
    SLET databasen og genopret den tom. ADVARSEL: irreversibel!
    """
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"✗ Slettet: {DB_PATH}")
    init_database()


# ─────────────────────────────────────────────────────────────────
# Status — vis hvad databasen indeholder
# ─────────────────────────────────────────────────────────────────

def print_status() -> None:
    """Print en oversigt over hvad databasen indeholder lige nu."""
    if not DB_PATH.exists():
        print(f"⚠  Database findes ikke endnu: {DB_PATH}")
        print(f"   Kør: python historical_db.py")
        return

    with get_connection(read_only=True) as conn:
        # Database-størrelse
        size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        print(f"\n{'=' * 60}")
        print(f"  Historical Database Status")
        print(f"{'=' * 60}")
        print(f"  Sti:        {DB_PATH}")
        print(f"  Størrelse:  {size_mb:.2f} MB")
        print()

        # Tickers
        cur = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(is_active) AS active,
                SUM(is_gap_active) AS gap_active
            FROM tickers
        """)
        row = cur.fetchone()
        print(f"  Tickers:")
        print(f"    Total:        {row['total'] or 0}")
        print(f"    Aktive:       {row['active'] or 0}")
        print(f"    Gap-aktive:   {row['gap_active'] or 0}  (adaptive filter)")
        print()

        # Daily bars
        cur = conn.execute("""
            SELECT
                COUNT(*) AS total_bars,
                COUNT(DISTINCT ticker) AS distinct_tickers,
                MIN(date) AS earliest,
                MAX(date) AS latest
            FROM bars_daily
        """)
        row = cur.fetchone()
        print(f"  Daily bars:")
        print(f"    Total bars:   {row['total_bars'] or 0:,}")
        print(f"    Tickers:      {row['distinct_tickers'] or 0}")
        if row['earliest']:
            print(f"    Periode:      {row['earliest']} → {row['latest']}")
        print()

        # 5-min bars
        cur = conn.execute("""
            SELECT
                COUNT(*) AS total_bars,
                COUNT(DISTINCT ticker) AS distinct_tickers,
                SUM(is_premarket) AS premarket_bars
            FROM bars_5min
        """)
        row = cur.fetchone()
        print(f"  5-min bars:")
        print(f"    Total bars:   {row['total_bars'] or 0:,}")
        print(f"    Tickers:      {row['distinct_tickers'] or 0}")
        print(f"    Pre-market:   {row['premarket_bars'] or 0:,}")
        print()

        # Gap events
        cur = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN gap_direction = 'up' THEN 1 ELSE 0 END) AS gaps_up,
                SUM(CASE WHEN gap_direction = 'down' THEN 1 ELSE 0 END) AS gaps_down,
                SUM(gap_filled) AS filled,
                SUM(has_5min_data) AS with_5min,
                AVG(gap_pct) AS avg_pct
            FROM gap_events
        """)
        row = cur.fetchone()
        total = row['total'] or 0
        print(f"  Gap events:")
        print(f"    Total:        {total}")
        if total > 0:
            print(f"    Gap-up:       {row['gaps_up'] or 0}")
            print(f"    Gap-down:     {row['gaps_down'] or 0}")
            print(f"    Fyldte:       {row['filled'] or 0}  ({(row['filled'] or 0) / total * 100:.0f}%)")
            print(f"    Med 5-min:    {row['with_5min'] or 0}")
            print(f"    Gns. gap-%:   {row['avg_pct'] or 0:.2f}%")
        print()

        # Download log
        cur = conn.execute("""
            SELECT status, COUNT(*) AS n
            FROM download_log
            GROUP BY status
        """)
        rows = cur.fetchall()
        if rows:
            print(f"  Download log:")
            for row in rows:
                print(f"    {row['status']:14s} {row['n']}")
        else:
            print(f"  Download log: (tom)")

        print(f"{'=' * 60}\n")


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialiser/inspicér Gap Fade historical database"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="ADVARSEL: Slet hele databasen og genopret tom",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Vis kun status, kør ikke init",
    )
    args = parser.parse_args()

    if args.reset:
        confirm = input(f"\nDette sletter {DB_PATH} permanent. Skriv 'JA' for at bekræfte: ")
        if confirm.strip() != "JA":
            print("Afbrudt.")
            return 1
        reset_database()
    elif not args.status_only:
        init_database()

    print_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())

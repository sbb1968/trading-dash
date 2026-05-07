"""
Migrering: tilføj account_id og instance_id til events-tabellen.

Sikkerhedsforanstaltninger:
  - Backup af databasen før migrering
  - Idempotent: kan køres flere gange uden skade
  - Kører kun migrering hvis kolonnerne mangler

Eksisterende rækker tagges med Sørens identitet ('soren', 'workstation')
fordi al historisk data er fra Sørens udviklingsarbejde.

Kør én gang:  python migrate_to_account_id.py
"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "trading_dash.db"


def main():
    if not DB_PATH.exists():
        print(f"Ingen database at migrere: {DB_PATH}")
        return

    # 1. Backup
    backup_path = DB_PATH.with_name(
        f"trading_dash.db.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.copy2(DB_PATH, backup_path)
    print(f"Backup oprettet: {backup_path.name}")

    # 2. Tjek om migrering er nødvendig
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    cols = {row[1] for row in cur.execute("PRAGMA table_info(events)")}
    if "account_id" in cols:
        print("Migrering allerede gennemført — gør ingenting.")
        conn.close()
        return

    print("Migrerer events-tabellen...")

    # 3. Tilføj kolonner med default-værdier (SQLite tillader ikke NOT NULL
    #    på en ny kolonne på en eksisterende tabel uden default)
    cur.execute("ALTER TABLE events ADD COLUMN account_id  TEXT NOT NULL DEFAULT 'soren'")
    cur.execute("ALTER TABLE events ADD COLUMN instance_id TEXT NOT NULL DEFAULT 'workstation'")

    # 4. Omdøb 'account' kolonnen til 'ibkr_account'.
    #    SQLite før 3.25 understøtter ikke RENAME COLUMN, men vi behøver det heller
    #    ikke — vi lader 'account' blive liggende som en historisk skygge-kolonne.
    #    Den nye 'ibkr_account' tilføjes som ny kolonne; gamle rækker har den ikke
    #    sat, men det er fint fordi den er nullable.
    cur.execute("ALTER TABLE events ADD COLUMN ibkr_account TEXT")

    # Kopiér gamle 'account'-værdier ind i 'ibkr_account' så den nye kolonne
    # afspejler den gamle kontos-kolonne for historiske rækker.
    cur.execute("UPDATE events SET ibkr_account = account WHERE account IS NOT NULL")

    # 5. Tilføj indekser
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_account_id  ON events(account_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_instance_id ON events(instance_id)")

    conn.commit()

    # 6. Tæl resultatet
    n = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"Migrering færdig — {n} eksisterende rækker tagget som ('soren', 'workstation').")

    conn.close()


if __name__ == "__main__":
    main()
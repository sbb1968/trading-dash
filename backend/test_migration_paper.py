"""
test_migration_paper.py — kan en GAMMEL database opgraderes?
════════════════════════════════════════════════════════════════════════════════
⚠ DETTE ER HULLET DER SLAP EN STARTFEJL IGENNEM.

Da paper-kolonnen blev tilføjet, kørte alle tests grønt — og backenden nægtede
alligevel at starte mod den rigtige database:

    sqlite3.OperationalError: no such column: paper

Årsagen: db_schema.sql køres med executescript() FØR ALTER TABLE-migrationen. På
en eksisterende database er CREATE TABLE IF NOT EXISTS en no-op, så et
CREATE INDEX ... ON trades(paper) i skemafilen ramte en kolonne der endnu ikke
fandtes. Indeksene ligger nu i journal.init() efter migrationen.

⚠ Grunden til at INGEN test så det: alle tests opretter en FRISK database, hvor
kolonnen kommer med fra CREATE TABLE. Testene målte dermed kun den ene af de to
veje koden faktisk tages — og den vej der aldrig blev målt, var den der kørte i
produktion. En testsuite der kun bygger nye databaser kan pr. konstruktion aldrig
fange en migrationsfejl.

Denne test bygger derfor en database med det GAMLE skema — uden paper-kolonnen,
med rækker i — og kører journal.init() mod den.

    python test_migration_paper.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aiosqlite

from journal import Journal

FEJL: list[str] = []
DB = "test_migration_paper.db"


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# Det gamle skema: som det så ud FØR paper-kolonnen. Skrevet ud i fuld længde
# med vilje — genbruges db_schema.sql her, tester vi den nye verden mod sig selv,
# og kontrollen ville være afgjort på forhånd.
GAMMELT = """
CREATE TABLE events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    ts_local     TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    instance_id  TEXT NOT NULL,
    source       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    ibkr_account TEXT,
    symbol       TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE trades (
    trade_id        TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    instance_id     TEXT NOT NULL,
    ibkr_account    TEXT NOT NULL,
    source          TEXT NOT NULL,
    variant         TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    shares          INTEGER NOT NULL,
    entry_time_utc  TEXT NOT NULL,
    entry_time_et   TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    entry_reason    TEXT,
    exit_time_utc   TEXT,
    exit_time_et    TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    duration_sec    INTEGER,
    capital_used    REAL NOT NULL,
    current_stop    REAL,
    current_target  REAL,
    current_stage   TEXT,
    trail_stop      REAL,
    notes           TEXT,
    payload_json    TEXT NOT NULL DEFAULT '{}'
);
"""


async def hoved():
    for f in (DB, DB + "-wal", DB + "-shm"):
        if os.path.exists(f):
            os.remove(f)

    print("\n1. Byg en database med det GAMLE skema")
    db = await aiosqlite.connect(DB)
    await db.executescript(GAMMELT)
    await db.execute(
        "INSERT INTO trades (trade_id, account_id, instance_id, ibkr_account, "
        "source, symbol, side, shares, entry_time_utc, entry_time_et, "
        "entry_price, capital_used) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("GAMMEL1", "iben", "algoserver", "DUO509856", "Konfluens 2", "AAPL",
         "long", 10, "2026-08-01T14:30:00+00:00", "2026-08-01T09:30:00",
         100.0, 1000.0))
    await db.execute(
        "INSERT INTO events (ts_utc, ts_local, account_id, instance_id, source, "
        "event_type) VALUES (?,?,?,?,?,?)",
        ("2026-08-01T14:30:00+00:00", "2026-08-01T16:30:00+02:00",
         "iben", "algoserver", "Konfluens 2", "trade_forensics"))
    await db.commit()

    cur = await db.execute("PRAGMA table_info(trades)")
    kolonner = [r[1] for r in await cur.fetchall()]
    kraev("paper" not in kolonner,
          "databasen har IKKE paper-kolonnen — ellers testede vi ingenting")
    kraev("current_price" not in kolonner,
          "og heller ikke current_price — den gamle verden er ægte gammel")
    await db.close()

    print("\n2. ⚠ journal.init() mod den gamle database")
    j = Journal(DB)
    try:
        await j.init()
        kraev(True, "init() kørte igennem — backenden kan starte")
    except Exception as e:
        kraev(False, f"init() kastede: {type(e).__name__}: {e}")
        return

    print("\n3. Kolonnerne er kommet til")
    cur = await j.db.execute("PRAGMA table_info(trades)")
    kt = [r[1] for r in await cur.fetchall()]
    kraev("paper" in kt, "trades.paper findes")
    kraev("current_price" in kt, "trades.current_price findes")
    cur = await j.db.execute("PRAGMA table_info(events)")
    ke = [r[1] for r in await cur.fetchall()]
    kraev("paper" in ke, "events.paper findes")

    print("\n4. Den gamle række overlevede og er mærket som paper")
    cur = await j.db.execute("SELECT paper, symbol FROM trades WHERE trade_id = ?",
                             ("GAMMEL1",))
    r = await cur.fetchone()
    kraev(r is not None and r[1] == "AAPL", "rækken er der stadig")
    kraev(r is not None and r[0] == 1,
          f"og den er paper=1 — der er aldrig handlet live "
          f"(fik {r[0] if r else '?'})")

    print("\n5. Indeksene blev oprettet")
    cur = await j.db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE '%paper%'")
    idx = sorted(x[0] for x in await cur.fetchall())
    kraev(idx == ["idx_events_paper", "idx_trades_paper"],
          f"begge paper-indeks findes: {idx}")

    print("\n6. init() er idempotent — den køres ved HVER opstart")
    await j.close()
    try:
        j2 = Journal(DB)
        await j2.init()
        kraev(True, "anden kørsel mod samme database går også igennem")
        await j2.close()
    except Exception as e:
        kraev(False, f"anden kørsel kastede: {type(e).__name__}: {e}")

    print("\n7. ⚠ Falsifikation — ville testen have fanget den oprindelige fejl?")
    # Genskab fejlen: kør skemafilen MED indekset i, mod den gamle database.
    # Kan den ikke fejle her, beviser afsnit 2 ingenting.
    db3 = await aiosqlite.connect(DB + ".falsi")
    await db3.executescript(GAMMELT)
    try:
        await db3.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_paper ON trades(paper)")
        kraev(False, "indeks paa en manglende kolonne blev accepteret")
    except Exception as e:
        kraev("no such column" in str(e),
              f"indeks paa manglende kolonne fejler stadig: {e}")
    await db3.close()

    for f in (DB, DB + "-wal", DB + "-shm", DB + ".falsi"):
        if os.path.exists(f):
            os.remove(f)


asyncio.run(hoved())

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")

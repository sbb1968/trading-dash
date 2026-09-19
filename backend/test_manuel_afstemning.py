"""
test_manuel_afstemning.py — en afstemning der ikke kan køres, må ikke sige "0"
════════════════════════════════════════════════════════════════════════════════
Mellem 31-08 og 14-09-2026 fyrede `fills_uden_journalspor` **35 gange** på
Ibens workstation — hver gang med en push-notifikation, og hver gang forkert.

⚠ ÅRSAGEN VAR ÉT FORKERT KOLONNENAVN.
`_bogfoerte_ordre_ider()` spurgte:

    SELECT payload FROM trades WHERE source = ? AND payload IS NOT NULL

Kolonnen hedder **`payload_json`**. Forespørgslen kastede, undtagelsen blev
fanget, og funktionen returnerede et **tomt sæt** — hvorefter hver eneste
bekræftede fyldning så ubogført ud.

Fejlen var logget hele tiden:

    [ManuelForensik] kunne ikke laese trades: no such column: payload

…men en fejl i en backend-log som ingen læser, er ikke en advarsel. Alarmen
fyrede i stedet, 35 gange i træk, og lærte alle at ignorere den.

⚠ DET ER PROJEKTETS SIGNATURFEJL I RENKULTUR: **en kontrol hvis fejl behandles
som et fund.** Rettelsen er derfor ikke kun kolonnenavnet — det er at en
afstemning der ikke kan gennemføres, nu KASTER (`AfstemningUmulig`) frem for
at svare tomt. "Vi ved det ikke" og "der er nul" må ikke se ens ud.

    python test_manuel_afstemning.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import aiosqlite

import manuel_forensik

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


class FalskJournal:
    """Kun det `_bogfoerte_ordre_ider` rører: en `.db`."""
    def __init__(self, db):
        self.db = db


async def _byg_db(sti: Path):
    """Rigtigt skema fra db_schema.sql — ikke en håndlavet efterligning.

    ⚠ Pointen: en test mod et selvopfundet skema ville have bestået med
    kolonnen `payload`, fordi testen selv havde lavet den. Fejlen levede
    netop i afstanden mellem koden og det VIRKELIGE skema.
    """
    db = await aiosqlite.connect(str(sti))
    skema = (Path(__file__).parent / "db_schema.sql").read_text(encoding="utf-8")
    await db.executescript(skema)
    await db.commit()
    return db


async def koer() -> None:
    with tempfile.TemporaryDirectory() as d:
        sti = Path(d) / "test.db"
        db = await _byg_db(sti)
        try:
            kolonner = [r[1] async for r in
                        await db.execute("PRAGMA table_info(trades)")]
            kraev("payload_json" in kolonner,
                  f"skemaet har kolonnen payload_json ({len(kolonner)} kolonner)")
            kraev("payload" not in kolonner,
                  "skemaet har IKKE en kolonne der hedder 'payload'")

            # Én manuel handel med begge ordre-id'er — som Ibens rigtige data.
            await db.execute(
                "INSERT INTO trades (trade_id, account_id, instance_id, "
                "ibkr_account, source, symbol, side, shares, entry_time_utc, "
                "entry_time_et, entry_price, entry_reason, capital_used, "
                "payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("t1", "iben", "workstation", "DUQ441063",
                 manuel_forensik.KILDE, "MES", "long", 1,
                 "2026-09-01T13:35:26+00:00", "2026-09-01T09:35:26-04:00", 7650.5,
                 manuel_forensik.ENTRY_REASON, 7650.5,
                 json.dumps({"ibkr_order_id": 124, "ibkr_order_id_exit": 127})))
            await db.commit()

            j = FalskJournal(db)
            ider = await manuel_forensik._bogfoerte_ordre_ider(j)
            kraev(ider == {"124", "127"},
                  f"bogfoerte ordre-id'er findes: {sorted(ider)}")

            # ── Afstemningen skal da IKKE alarmere ────────────────────────
            tracker = [{"source": "manual_watchlist", "order_id": 124,
                        "bekraeftet": True, "filled": 1.0, "ticker": "MES",
                        "action": "BUY", "avg_fill": 7650.5, "status": "Filled",
                        "placed_at": "2026-09-01T13:35:26"}]
            r = await manuel_forensik.afstem_mod_tracker(j, tracker)
            kraev(r["ubogfoerte"] == [],
                  f"en bogfoert fyldning giver INGEN alarm (bogfoerte={r['bogfoerte_ordre_ider']})")

            # ── Og en UBOGFOERT skal stadig fanges ───────────────────────
            tracker2 = tracker + [{"source": "manual_watchlist", "order_id": 999,
                                   "bekraeftet": True, "filled": 1.0,
                                   "ticker": "MES", "action": "SELL",
                                   "avg_fill": 7660.0, "status": "Filled",
                                   "placed_at": "2026-09-01T14:00:00"}]
            r2 = await manuel_forensik.afstem_mod_tracker(j, tracker2)
            kraev([u["order_id"] for u in r2["ubogfoerte"]] == [999],
                  "en UBOGFOERT fyldning fanges stadig (kontrollen kan fejle)")
        finally:
            await db.close()

        # ── ⚠ MUTATION: gør forespørgslen umulig igen ────────────────────
        # Det er dén tilstand der gav 35 falske alarmer. Den skal nu KASTE.
        print("\n  ── mutation: databasen kan ikke laeses ──")
        db2 = await aiosqlite.connect(str(Path(d) / "tom.db"))
        try:
            j2 = FalskJournal(db2)   # ingen trades-tabel overhovedet
            try:
                ud = await manuel_forensik._bogfoerte_ordre_ider(j2)
                kraev(False, f"burde have kastet — returnerede {ud!r}")
            except manuel_forensik.AfstemningUmulig as e:
                kraev(True, f"kaster AfstemningUmulig i stedet for tomt saet")
                kraev("trades" in str(e) or "no such" in str(e).lower(),
                      f"fejlen siger hvad der gik galt: {str(e)[:60]}")
        finally:
            await db2.close()

        # journal.db is None må heller ikke blive til "nul bogfoerte"
        try:
            await manuel_forensik._bogfoerte_ordre_ider(FalskJournal(None))
            kraev(False, "db=None burde kaste")
        except manuel_forensik.AfstemningUmulig:
            kraev(True, "db=None kaster ogsaa — 'ved ikke' er ikke 'nul'")


def main() -> int:
    print("  ── manuel-afstemningen ──")
    asyncio.run(koer())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())

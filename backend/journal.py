"""
journal.py — Append-only event journal med SQLite

Brugsmønster:
    journal = Journal("trading_dash.db")
    await journal.init()

    await journal.log_event(
        source="Momentum ORB",
        event_type="order_request",
        ibkr_account="DUxxxxxxx",
        symbol="GME",
        payload={"action": "BUY", "quantity": 100, "limit_price": 12.34, "reason": "ORB breakout"},
    )

Designprincipper:
  - Append-only: ingen UPDATE, ingen DELETE.
  - Skriver fejler aldrig algoritmen — fejl logges men kastes ikke videre.
  - ts_utc og ts_local sættes automatisk på skrive-tidspunktet.
  - payload er fri-form JSON — alt der ikke passer i de faste kolonner.

Placering: C:\\Projects\\Trading_Dash\\backend\\journal.py
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from accounts import identity

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "db_schema.sql"


class Journal:
    """Tynd wrapper omkring aiosqlite — append-only event log."""

    def __init__(self, db_path: str = "trading_dash.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Livscyklus
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Åbn forbindelse og opret skema hvis det ikke findes."""
        self._db = await aiosqlite.connect(self.db_path)

        # WAL-mode: bedre samtidighed mellem skriv (algoritme) og læs
        # (analyse-vinduer der senere vil forespørge mens algoen kører).
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA synchronous = NORMAL")

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await self._db.executescript(schema_sql)
        await self._db.commit()

        logger.info(f"[Journal] Klar — db: {self.db_path}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Skrivning
    # ------------------------------------------------------------------

    async def log_event(
        self,
        source:       str,
        event_type:   str,
        payload:      Optional[dict] = None,
        symbol:       Optional[str]  = None,
        ibkr_account: Optional[str]  = None,
    ) -> None:
        """
        Skriv ét event til journalen.

        account_id og instance_id sættes AUTOMATISK fra accounts.identity —
        kalderen kan ikke overstyre eller udelade dem. Det er den arkitektoniske
        garanti for at hver event entydigt tilhører én skattepligtig identitet.

        ibkr_account er valgfri og refererer til IBKR-kontonummeret for events
        knyttet til en specifik konto-transaktion (defaulter til instansens
        konfigurerede ibkr_account hvis ikke angivet).

        Fejler aldrig kalderen — selv hvis SQLite er nede skal handelsflowet
        fortsætte. Vi vil hellere miste et log-event end at miste en handel.
        """
        if self._db is None:
            logger.warning(f"[Journal] Ikke initialiseret — kasserer event {source}/{event_type}")
            return

        try:
            now_utc   = datetime.now(timezone.utc)
            now_local = datetime.now().astimezone()

            await self._db.execute(
                """
                INSERT INTO events
                    (ts_utc, ts_local, account_id, instance_id, source, event_type,
                     ibkr_account, symbol, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_utc.isoformat(),
                    now_local.isoformat(),
                    identity.account_id,
                    identity.instance_role,
                    source,
                    event_type,
                    ibkr_account or identity.ibkr_account,
                    symbol,
                    json.dumps(payload or {}, default=str),
                ),
            )
            await self._db.commit()

        except Exception as e:
            # Vi sluger fejlen bevidst — journalisering må aldrig
            # nedbryde handelsflowet.
            logger.error(f"[Journal] Skriv-fejl ({source}/{event_type}): {e}")

    # ------------------------------------------------------------------
    # Simpel læsning (til health/debug — analyse kommer senere)
    # ------------------------------------------------------------------

    async def count_events(self) -> int:
        if self._db is None:
            return 0
        async with self._db.execute("SELECT COUNT(*) FROM events") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0
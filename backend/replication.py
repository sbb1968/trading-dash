"""
replication.py — Snapshot-replikering af trading_dash.db til algoserveren.

Afsender-siden. Tager et WAL-sikkert konsistent snapshot af den lokale
trading_dash.db via SQLites online backup-API (ALDRIG en rå filkopi — en
WAL-DB har data i en sidecar-fil) og pusher hele filen til algoserveren.

Kadence:
  - Opstart:    ét push (indhent efter at maskinen har været slukket)
  - Event:      poke fra journal-skrivninger → debounced push (samler bursts)
  - Periodisk:  sikkerhedsnet, push mindst hvert 2. min
  - Nedlukning: ét sidste flush
  - CLI:        python replication.py --push-once  (engangs-backfill)

Ufravigeligt: den lokale handel må ALDRIG vente på eller fejle pga. netværket.
notify_change() er kun et flag-sæt. Snapshot/push kører i baggrundsloopet og
sluger alle fejl (logges, kastes aldrig videre).

Placering: backend/replication.py
"""

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

DEBOUNCE_SEC     = 3.0      # vent til der er ro i 3 sek før event-push
PERIODIC_SEC     = 120.0    # sikkerhedsnet: push mindst hvert 2. min
LOOP_TICK_SEC    = 1.0      # hvor ofte loopet vågner
PUSH_TIMEOUT_SEC = 30.0


def _make_snapshot(src_path: str, dst_path: str) -> None:
    """WAL-sikkert konsistent snapshot via SQLites online backup-API.
    Kører i en tråd (asyncio.to_thread) — blokerer ikke event-loopet.

    Vi åbner kilden som en NORMAL forbindelse (ikke mode=ro): en read-only
    forbindelse til en WAL-DB kan fejle på -shm-håndtering. backup() læser kun.
    """
    dst = Path(dst_path)
    if dst.exists():
        dst.unlink()
    src = sqlite3.connect(src_path)
    try:
        out = sqlite3.connect(dst_path)
        try:
            src.backup(out)   # online backup — sikker mens DB'en skrives
        finally:
            out.close()
    finally:
        src.close()


class Replicator:
    def __init__(self) -> None:
        self._enabled      = False
        self._db_path:  Optional[Path] = None
        self._tmp_dir:  Optional[Path] = None
        self._source_id    = ""
        self._target_url   = ""
        self._internal_key = ""
        self._dirty        = False
        self._last_change  = 0.0
        self._last_push    = 0.0
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()   # kun ét snapshot+push ad gangen

    def init(self, *, db_path: str, source_id: str, target_url: str,
             internal_key: str, enabled: bool) -> None:
        self._db_path = Path(db_path)
        self._tmp_dir = self._db_path.parent / "replication_tmp"
        self._tmp_dir.mkdir(exist_ok=True)
        self._source_id    = source_id
        self._target_url   = (target_url or "").rstrip("/")
        self._internal_key = internal_key
        self._enabled      = bool(enabled) and bool(self._target_url)
        if self._enabled:
            logger.info(f"[Replication] kilde-id={source_id}, "
                        f"mål={self._target_url}, aktiv")
        else:
            logger.info(f"[Replication] inaktiv "
                        f"(enabled={enabled}, target={target_url!r})")

    def notify_change(self) -> None:
        """Kaldes fra journal-skrivninger. EKSTREMT billig, fejler aldrig."""
        if not self._enabled:
            return
        self._dirty = True
        self._last_change = time.monotonic()

    async def start(self) -> None:
        if not self._enabled:
            return
        self._dirty = True   # indhent ved opstart
        self._task = asyncio.create_task(self._loop(), name="replication_loop")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def flush(self) -> None:
        """Ét sidste push ved nedlukning, best-effort."""
        if not self._enabled:
            return
        try:
            await self._snapshot_and_push()
        except Exception as e:
            logger.warning(f"[Replication] flush fejlede: {e}")

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(LOOP_TICK_SEC)
                now = time.monotonic()
                due_event    = self._dirty and (now - self._last_change) >= DEBOUNCE_SEC
                due_periodic = (now - self._last_push) >= PERIODIC_SEC
                if due_event or due_periodic:
                    await self._snapshot_and_push()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"[Replication] loop-fejl: {e}")

    async def _snapshot_and_push(self) -> None:
        if not self._enabled:
            return
        async with self._lock:
            # Ryd dirty FØR snapshot, så ændringer under push fanges næste runde.
            self._dirty = False
            tmp_path = self._tmp_dir / f"{self._source_id}_snapshot.db"
            try:
                await asyncio.to_thread(_make_snapshot,
                                        str(self._db_path), str(tmp_path))
            except Exception as e:
                logger.warning(f"[Replication] snapshot fejlede: {e}")
                self._dirty = True   # prøv igen næste runde
                return
            try:
                await self._push_file(tmp_path, artifact="db")
                self._last_push = time.monotonic()
            except Exception as e:
                logger.warning(f"[Replication] push fejlede: {e}")
                self._dirty = True
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    async def _push_file(self, path: Path, artifact: str) -> None:
        url     = f"{self._target_url}/replication/upload"
        params  = {"source": self._source_id, "artifact": artifact}
        headers = {"X-Internal-Key": self._internal_key,
                   "Content-Type": "application/octet-stream"}
        data    = await asyncio.to_thread(path.read_bytes)
        timeout = aiohttp.ClientTimeout(total=PUSH_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, params=params,
                                    headers=headers, data=data) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")


# Modul-singleton
replicator = Replicator()


def notify_change() -> None:
    """Genvej så journal.py kan poke uden at kende singleton'en."""
    replicator.notify_change()


# ─────────────────────────────────────────────────────────────────
# CLI: engangs-backfill uden at starte hele backenden.
#   python replication.py --push-once
# Nyttig til at backfille en sjældent-tændt maskines historik på én gang
# (fx Ibens workstation). Kør fra backend/ med aktiv venv.
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from accounts import identity

    if "--push-once" not in sys.argv:
        print("Brug: python replication.py --push-once")
        sys.exit(1)

    if not identity.replication_enabled or not identity.replication_target_url:
        print("Replikering er ikke aktiveret i account.yaml — intet at gøre.")
        sys.exit(1)

    async def _main() -> int:
        replicator.init(
            db_path      = "trading_dash.db",
            source_id    = identity.source_id,
            target_url   = identity.replication_target_url,
            internal_key = identity.internal_key,
            enabled      = identity.replication_enabled,
        )
        print(f"[Replication] Engangs-push: {identity.source_id} "
              f"→ {identity.replication_target_url}")
        try:
            await replicator._snapshot_and_push()
        except Exception as e:
            print(f"[Replication] FEJL: {e}")
            return 1
        # _snapshot_and_push sluger fejl og sætter _dirty igen ved problemer
        if replicator._dirty:
            print("[Replication] Push lykkedes IKKE (se advarsel ovenfor). "
                  "Er algoserveren og del 2 oppe?")
            return 1
        print("[Replication] Push lykkedes — historik backfillet.")
        return 0

    sys.exit(asyncio.run(_main()))

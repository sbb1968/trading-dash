"""
replication_store.py — Modtager-siden: gemmer indkomne snapshots i arkivet.

Algoserveren modtager hele DB-filer (+ orders_log.json + logs.zip) fra
workstationerne og lægger dem i et per-kilde navnerum, fuldstændig adskilt
fra algoserverens egen operationelle trading_dash.db.

Arkiv-layout:
    backend/archives/<source>/trading_dash.db
    backend/archives/<source>/orders_log.json
    backend/archives/<source>/logs.zip

Skrivning er ATOMISK: temp-fil i SAMME mappe + os.replace, så en læser
(Studio) aldrig ser en halvskrevet fil.

Placering: backend/replication_store.py
"""

import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_ROOT = Path(__file__).parent / "archives"

_SOURCE_RE = re.compile(r"^[a-z0-9_-]+$")

_ARTIFACT_FILENAMES = {
    "db":         "trading_dash.db",
    "orders_log": "orders_log.json",
    "logs":       "logs.zip",
}


def _valid_source(source: str) -> bool:
    return bool(source) and bool(_SOURCE_RE.match(source)) and ".." not in source


def _looks_like_sqlite(path: Path) -> bool:
    """SQLite-filer starter med en fast 16-byte magic-header."""
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def store_artifact(source: str, artifact: str, data: bytes) -> dict:
    """Gem et modtaget artifact atomisk i arkivet for <source>.
    Kaster ValueError ved ugyldige input."""
    if not _valid_source(source):
        raise ValueError(f"Ugyldigt source-id: {source!r}")
    if artifact not in _ARTIFACT_FILENAMES:
        raise ValueError(f"Ukendt artifact-type: {artifact!r}")
    if not data:
        raise ValueError("Tomt body")

    dest_dir = ARCHIVE_ROOT / source
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / _ARTIFACT_FILENAMES[artifact]

    fd, tmp_name = tempfile.mkstemp(dir=str(dest_dir), suffix=".part")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if artifact == "db" and not _looks_like_sqlite(tmp_path):
            raise ValueError("Modtaget db er ikke en gyldig SQLite-fil")
        os.replace(str(tmp_path), str(final_path))
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    size = final_path.stat().st_size
    logger.info(f"[Replication] modtaget {artifact} fra {source} "
                f"→ {final_path.name} ({size} bytes)")
    return {"ok": True, "source": source, "artifact": artifact, "bytes": size}


def list_sources() -> list[str]:
    """Hvilke kilder har vi arkiv for? (mappenavne under archives/)"""
    if not ARCHIVE_ROOT.is_dir():
        return []
    return sorted(p.name for p in ARCHIVE_ROOT.iterdir() if p.is_dir())


def archive_db_path(source: str):
    """Sti til en kildes arkiverede trading_dash.db, eller None. (Bruges i del 3.)"""
    if not _valid_source(source):
        return None
    p = ARCHIVE_ROOT / source / "trading_dash.db"
    return p if p.exists() else None
"""SQLite backup and restore.

Uses VACUUM INTO rather than copying the database file. Copying ffd.db while
WAL files are live can produce a torn, unusable database; VACUUM INTO takes a
consistent snapshot without stopping writers and emits a compact, defragmented
copy.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import sqlite3
from pathlib import Path

from app.config import Settings, get_settings
from app.db import utcnow

log = logging.getLogger(__name__)

BACKUP_GLOB = "ffd-*.db"


class BackupError(RuntimeError):
    pass


def _integrity_check(path: Path) -> None:
    """Fail loudly if a database file isn't sound. An unverified backup is not a backup."""
    try:
        conn = sqlite3.connect(path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        # A file that isn't a SQLite database at all fails here rather than
        # returning a bad integrity_check result.
        raise BackupError(f"{path} is not a readable SQLite database: {exc}") from exc
    if result != "ok":
        raise BackupError(f"integrity_check failed for {path}: {result}")


def backup_filename(now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    return f"ffd-{now.strftime('%Y%m%dT%H%M%SZ')}.db"


def create_backup(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    source = settings.resolved_db_path
    if not source.exists():
        raise BackupError(f"No database to back up at {source}")

    dest_dir = settings.resolved_backup_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Timestamps have second resolution, so a manual `ffd backup` moments after
    # the timer fires would otherwise collide. VACUUM INTO refuses to overwrite,
    # so pick the next free name instead of failing.
    dest = dest_dir / backup_filename()
    if dest.exists():
        stem = dest.stem
        for n in range(2, 100):
            candidate = dest_dir / f"{stem}-{n}.db"
            if not candidate.exists():
                dest = candidate
                break
        else:
            raise BackupError(f"Could not find a free backup name for {stem}")

    conn = sqlite3.connect(source)
    try:
        # VACUUM INTO refuses to overwrite, so dest must not exist (checked above).
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()

    _integrity_check(dest)
    log.info("Wrote backup %s (%d bytes)", dest, dest.stat().st_size)
    return dest


def prune_backups(settings: Settings | None = None) -> list[Path]:
    """Keep the newest N backups; return the ones removed."""
    settings = settings or get_settings()
    backups = sorted(settings.resolved_backup_dir.glob(BACKUP_GLOB))
    stale = backups[: max(0, len(backups) - settings.backup_keep)]
    for path in stale:
        path.unlink()
        log.info("Pruned old backup %s", path)
    return stale


def restore_backup(source: Path, settings: Settings | None = None) -> Path:
    """Replace the live database with a backup.

    The caller is responsible for stopping the API first (see deploy/README.md);
    this refuses nothing, but the existing database is moved aside rather than
    deleted, so a bad restore is recoverable.
    """
    settings = settings or get_settings()
    if not source.exists():
        raise BackupError(f"Backup not found: {source}")
    _integrity_check(source)

    target = settings.resolved_db_path
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        aside = target.with_suffix(f".db.pre-restore-{utcnow().strftime('%Y%m%dT%H%M%SZ')}")
        shutil.move(str(target), str(aside))
        log.info("Moved existing database aside to %s", aside)

    # Stale -wal/-shm belong to the replaced database, not the restored one.
    for suffix in ("-wal", "-shm"):
        sidecar = target.with_name(target.name + suffix)
        sidecar.unlink(missing_ok=True)

    shutil.copy2(source, target)
    _integrity_check(target)
    log.info("Restored %s -> %s", source, target)
    return target

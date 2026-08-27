"""Backup and restore, exercised as a real cycle against real files.

A restore procedure that has never been run is not a procedure.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import func, select

from app.backup import BackupError, create_backup, prune_backups, restore_backup
from app.db import session_scope, utcnow
from app.models import LeagueUser


def _seed(count: int, start: int = 0) -> None:
    with session_scope() as s:
        for i in range(start, start + count):
            s.add(
                LeagueUser(
                    league_id="L1",
                    user_id=str(i),
                    display_name=f"User {i}",
                    team_name=f"Team {i}",
                )
            )
        s.commit()


def _user_count() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(LeagueUser))


def test_backup_is_a_valid_readable_database(db, settings):
    _seed(3)
    path = create_backup(settings)

    assert path.exists() and path.stat().st_size > 0
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM league_user").fetchone()[0] == 3
    finally:
        conn.close()


def test_backup_captures_writes_made_after_a_previous_backup(db, settings):
    _seed(1)
    first = create_backup(settings)
    _seed(2, start=1)
    second = create_backup(settings)
    assert first != second

    conn = sqlite3.connect(second)
    try:
        assert conn.execute("SELECT COUNT(*) FROM league_user").fetchone()[0] == _user_count()
    finally:
        conn.close()


def test_backup_without_a_database_fails_loudly(settings):
    with pytest.raises(BackupError, match="No database to back up"):
        create_backup(settings)


def test_prune_keeps_only_the_newest(db, settings, monkeypatch):
    _seed(1)
    # FFD_BACKUP_KEEP is 3 in the test fixture.
    made = []
    for i in range(5):
        stamp = f"ffd-2026010{i}T000000Z.db"
        monkeypatch.setattr("app.backup.backup_filename", lambda s=stamp: s)
        made.append(create_backup(settings))

    removed = prune_backups(settings)
    remaining = sorted(p.name for p in settings.resolved_backup_dir.glob("ffd-*.db"))

    assert len(removed) == 2
    assert remaining == [m.name for m in made[2:]]


def test_full_restore_cycle_recovers_lost_data(db, settings):
    """Populate -> back up -> destroy -> restore -> verify content matches."""
    _seed(4)
    backup = create_backup(settings)
    before = _user_count()
    assert before == 4

    # Simulate catastrophe: drop the table contents entirely.
    with session_scope() as s:
        s.query(LeagueUser).delete()
        s.commit()
    assert _user_count() == 0

    # Engine must be dropped before the file is swapped underneath it.
    import app.db as db_module

    db_module.get_engine().dispose()
    db_module._engine = None
    db_module._session_factory = None

    restore_backup(backup, settings)

    assert _user_count() == before
    with session_scope() as s:
        restored = s.get(LeagueUser, ("L1", "2"))
        assert restored.display_name == "User 2"
        assert restored.team_name == "Team 2"
        assert restored.first_seen_at.tzinfo is not None


def test_restore_moves_the_existing_database_aside(db, settings):
    """A bad restore must be recoverable."""
    _seed(1)
    backup = create_backup(settings)

    import app.db as db_module

    db_module.get_engine().dispose()
    db_module._engine = None
    db_module._session_factory = None

    restore_backup(backup, settings)

    aside = list(settings.resolved_db_path.parent.glob("*.pre-restore-*"))
    assert len(aside) == 1, "previous database was not preserved"


def test_restore_rejects_a_corrupt_backup(db, settings, tmp_path):
    _seed(1)
    create_backup(settings)
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database" * 100)

    with pytest.raises(BackupError):
        restore_backup(corrupt, settings)


def test_restore_rejects_a_missing_file(db, settings, tmp_path):
    with pytest.raises(BackupError, match="not found"):
        restore_backup(tmp_path / "nope.db", settings)


def test_backup_filename_is_utc_stamped():
    from app.backup import backup_filename

    name = backup_filename(utcnow())
    assert name.startswith("ffd-") and name.endswith("Z.db")

"""Shared test fixtures.

Each test gets its own on-disk SQLite file. On-disk rather than :memory: because
the backup/restore path exercises real files, and WAL behaviour only exists for
real files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.config as config_module
import app.db as db_module

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def league_payload():
    return load_fixture("league.json")


@pytest.fixture
def users_payload():
    return load_fixture("league_users.json")


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Point the app at a throwaway database and league id."""
    monkeypatch.setenv("FFD_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("FFD_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("FFD_SLEEPER_LEAGUE_ID", "999999999999999999")
    monkeypatch.setenv("FFD_BACKUP_KEEP", "3")
    monkeypatch.setenv("FFD_STATIC_DIR", str(tmp_path / "no-frontend"))
    # Settings are cached, and db holds module-level engine/session globals;
    # both must be reset or tests leak into each other.
    config_module.get_settings.cache_clear()
    db_module._engine = None
    db_module._session_factory = None

    yield config_module.get_settings()

    config_module.get_settings.cache_clear()
    db_module._engine = None
    db_module._session_factory = None


@pytest.fixture
def db(settings):
    """A migrated, empty database plus a session factory."""
    from app import models  # noqa: F401  (registers tables on the metadata)

    engine = db_module.get_engine()
    db_module.Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(db):
    with db_module.session_scope() as s:
        yield s

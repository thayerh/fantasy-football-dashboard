"""Guard against schema drift.

The classic failure is editing models.py and forgetting to generate a migration;
everything passes locally (tests create tables from metadata) and then production,
which is built by Alembic, is missing a column.
"""

from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from app.config import REPO_ROOT
from app.db import Base, get_engine


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def test_alembic_head_matches_models(settings):
    """Upgrading a fresh database must reproduce exactly what models.py declares."""
    from app import models  # noqa: F401

    command.upgrade(_alembic_config(), "head")

    engine = get_engine()
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    # alembic_version is Alembic's own bookkeeping table and is not in our metadata.
    diff = [d for d in diff if "alembic_version" not in str(d)]
    assert diff == [], (
        f"models.py and migrations disagree; run `alembic revision --autogenerate`: {diff}"
    )

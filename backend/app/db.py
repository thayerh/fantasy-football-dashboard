"""Engine, session factory, SQLite pragmas, and UTC-safe timestamp handling."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import get_settings


def utcnow() -> dt.datetime:
    """The only way this codebase produces 'now'.

    Never use datetime.utcnow(): it returns a naive value and is deprecated.
    """
    return dt.datetime.now(dt.UTC)


class UtcDateTime(TypeDecorator):
    """A timezone-aware datetime that survives a round trip through SQLite.

    SQLAlchemy's DateTime(timezone=True) does not preserve an offset on SQLite:
    the value is stored as a string and comes back *naive*, which silently
    produces naive/aware comparison errors later. This normalises everything to
    UTC on the way in and re-attaches UTC on the way out, so application code
    only ever sees aware datetimes.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"Refusing to store naive datetime {value!r}; use app.db.utcnow() "
                "or attach a timezone before persisting."
            )
        return value.astimezone(dt.UTC)

    def process_result_value(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


class Base(DeclarativeBase):
    pass


def _apply_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    # WAL lets the read-only API serve requests while `ffd sync` writes.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_db_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    if url is None:
        settings.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
        url = settings.database_url
    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    event.listen(engine, "connect", _apply_pragmas)
    return engine


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session


def get_db() -> Iterator[Session]:
    """FastAPI dependency. The API only ever reads, so it never commits."""
    with get_session_factory()() as session:
        yield session


# Annotated alias: the current FastAPI idiom, and it keeps Depends() out of
# argument defaults (ruff B008).
DbSession = Annotated[Session, Depends(get_db)]

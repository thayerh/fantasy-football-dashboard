"""Timestamp handling — the rules that are easiest to violate accidentally."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.exc import StatementError

from app.db import utcnow
from app.models import SyncRun


def test_utcnow_is_aware_and_utc():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == dt.timedelta(0)


def test_timestamp_survives_round_trip_as_aware_utc(session):
    """SQLite drops offsets; UtcDateTime must put them back."""
    original = dt.datetime(2026, 9, 14, 17, 30, 0, tzinfo=dt.UTC)
    session.add(SyncRun(started_at=original, finished_at=original, status="ok"))
    session.commit()
    session.expunge_all()

    run = session.get(SyncRun, 1)
    assert run.started_at.tzinfo is not None, "came back naive"
    assert run.started_at == original


def test_non_utc_input_is_normalised_not_mangled(session):
    """An aware non-UTC value must be converted, preserving the instant."""
    eastern = dt.timezone(dt.timedelta(hours=-4))
    original = dt.datetime(2026, 9, 14, 13, 30, 0, tzinfo=eastern)
    session.add(SyncRun(started_at=original, status="ok"))
    session.commit()
    session.expunge_all()

    run = session.get(SyncRun, 1)
    assert run.started_at == original
    assert run.started_at.utcoffset() == dt.timedelta(0)
    assert run.started_at.hour == 17


def test_naive_datetime_is_rejected(session):
    """Storing naive values is the bug this type exists to prevent."""
    session.add(SyncRun(started_at=dt.datetime(2026, 9, 14, 17, 30), status="ok"))  # noqa: DTZ001
    # SQLAlchemy wraps bind-param errors, so the ValueError arrives as StatementError.
    with pytest.raises(StatementError, match="naive datetime"):
        session.commit()

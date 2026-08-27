"""API responses, including the staleness signal the UI depends on."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope, utcnow
from app.models import League, LeagueUser, SyncRun

LEAGUE_ID = "999999999999999999"


@pytest.fixture
def client(db, settings):
    from app.main import app

    return TestClient(app)


def _add_league_and_users() -> None:
    with session_scope() as s:
        s.add(
            League(
                league_id=LEAGUE_ID,
                name="The League",
                season="2026",
                season_type="regular",
                status="pre_draft",
                total_rosters=12,
                raw={"secret": "should never be exposed"},
            )
        )
        s.add(LeagueUser(league_id=LEAGUE_ID, user_id="1", display_name="Zed", team_name="Z"))
        s.add(LeagueUser(league_id=LEAGUE_ID, user_id="2", display_name="Amy", team_name="A"))
        s.commit()


def test_users_are_sorted_by_display_name(client):
    _add_league_and_users()
    body = client.get("/api/users").json()
    assert [u["display_name"] for u in body] == ["Amy", "Zed"]


def test_raw_payload_is_never_exposed(client):
    """Responses are Pydantic models, so the raw column cannot leak."""
    _add_league_and_users()
    assert "secret" not in client.get("/api/league").text
    assert "raw" not in client.get("/api/league").json()


def test_league_returns_503_before_first_ingest(client):
    response = client.get("/api/league")
    assert response.status_code == 503
    assert "ffd sync" in response.json()["detail"]


def test_health_is_stale_when_no_sync_has_run(client):
    body = client.get("/api/health").json()
    assert body["db_ok"] is True
    assert body["stale"] is True
    assert body["last_sync_at"] is None


def test_health_is_fresh_after_a_recent_ok_sync(client):
    with session_scope() as s:
        s.add(SyncRun(started_at=utcnow(), finished_at=utcnow(), status="ok", records_written=4))
        s.commit()

    body = client.get("/api/health").json()
    assert body["stale"] is False
    assert body["status"] == "ok"
    assert body["last_sync_status"] == "ok"
    # Serialised as UTC so the browser can localise it.
    assert dt.datetime.fromisoformat(body["last_sync_at"]).utcoffset() == dt.timedelta(0)


def test_health_is_stale_when_the_last_sync_errored(client):
    """A failing timer must be visible, not silently serve old data as current."""
    with session_scope() as s:
        s.add(
            SyncRun(
                started_at=utcnow(),
                finished_at=utcnow(),
                status="error",
                error="SleeperError: boom",
            )
        )
        s.commit()

    body = client.get("/api/health").json()
    assert body["stale"] is True
    assert body["status"] == "degraded"
    assert body["last_sync_status"] == "error"


def test_health_is_stale_when_the_last_sync_is_too_old(client, settings):
    old = utcnow() - dt.timedelta(seconds=settings.stale_after_seconds + 60)
    with session_scope() as s:
        s.add(SyncRun(started_at=old, finished_at=old, status="ok"))
        s.commit()

    assert client.get("/api/health").json()["stale"] is True


def test_sync_status_lists_runs_newest_first(client):
    with session_scope() as s:
        for _ in range(3):
            s.add(SyncRun(started_at=utcnow(), finished_at=utcnow(), status="ok"))
        s.commit()

    body = client.get("/api/sync/status").json()
    assert [r["id"] for r in body] == [3, 2, 1]

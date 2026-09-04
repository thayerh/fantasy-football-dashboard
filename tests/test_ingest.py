"""Ingestion: upsert semantics, idempotency, and failure recording."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import func, select
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.db import session_scope
from app.ingest import sync_league, sync_users
from app.models import Draft, League, LeagueUser, SyncRun
from app.sleeper import SleeperClient

BASE = "https://api.sleeper.app/v1"
LEAGUE_ID = "999999999999999999"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.sleeper.time.sleep", lambda _: None)


def _mock_sleeper(league_payload, users_payload, drafts=None, draft=None, picks=None):
    respx.get(f"{BASE}/league/{LEAGUE_ID}").mock(
        return_value=httpx.Response(200, json=league_payload)
    )
    respx.get(f"{BASE}/league/{LEAGUE_ID}/users").mock(
        return_value=httpx.Response(200, json=users_payload)
    )
    # `ffd sync` also syncs the draft, so the board stays current with the fast
    # ffd-draft.timer disabled. Draft-specific behaviour is in test_draft.py.
    respx.get(f"{BASE}/league/{LEAGUE_ID}/drafts").mock(
        return_value=httpx.Response(200, json=drafts if drafts is not None else [])
    )
    if draft is not None:
        respx.get(f"{BASE}/draft/{draft['draft_id']}").mock(
            return_value=httpx.Response(200, json=draft)
        )
        respx.get(f"{BASE}/draft/{draft['draft_id']}/picks").mock(
            return_value=httpx.Response(200, json=picks or [])
        )


@respx.mock
def test_sync_league_stores_metadata_and_raw(session, settings, league_payload, users_payload):
    _mock_sleeper(league_payload, users_payload)
    with SleeperClient(settings) as client:
        assert sync_league(session, client, LEAGUE_ID) == 1
    session.commit()

    league = session.get(League, LEAGUE_ID)
    assert league.name == league_payload["name"]
    assert league.total_rosters == 12
    # The season-chain pointer must be kept for future multi-season features.
    assert league.previous_league_id == "111111111111111111"
    # raw preserves fields we did not model, e.g. scoring_settings.
    assert league.raw["scoring_settings"]["pass_td"] == 4.0
    assert league.fetched_at.tzinfo is not None


@respx.mock
def test_sync_users_maps_fields_including_missing_team_name(
    session, settings, league_payload, users_payload
):
    _mock_sleeper(league_payload, users_payload)
    with SleeperClient(settings) as client:
        assert sync_users(session, client, LEAGUE_ID) == 3
    session.commit()

    users = {u.user_id: u for u in session.scalars(select(LeagueUser)).all()}
    assert users["1001"].team_name == "Gridiron Gurus"
    assert users["1001"].is_owner is True
    assert users["1002"].display_name == "Ben"

    # The live endpoint omits `username` entirely, despite the docs example
    # showing it; nothing may depend on it being present.
    assert users["1001"].username is None
    # Empty metadata must yield NULL, not a KeyError.
    assert users["1003"].team_name is None
    assert users["1003"].avatar is None
    assert users["1003"].avatar_url is None
    assert users["1003"].is_owner is False


@respx.mock
def test_custom_avatar_is_captured_separately(session, settings, league_payload, users_payload):
    """metadata.avatar is the member's real image.

    The top-level avatar id is frequently a shared Sleeper default: in the real
    league, seven distinct ids covered twelve members, so relying on it alone
    renders different people with the same picture.
    """
    _mock_sleeper(league_payload, users_payload)
    with SleeperClient(settings) as client:
        sync_users(session, client, LEAGUE_ID)
    session.commit()

    users = {u.user_id: u for u in session.scalars(select(LeagueUser)).all()}
    assert users["1001"].avatar_url == "https://sleepercdn.com/uploads/custom-commish.jpg"
    # Same shared default id, but only one of them has a custom image.
    assert users["1002"].avatar == users["1001"].avatar
    assert users["1002"].avatar_url is None


@respx.mock
def test_resync_is_idempotent_and_preserves_first_seen(
    session, settings, league_payload, users_payload
):
    """Re-running must update in place, never duplicate."""
    _mock_sleeper(league_payload, users_payload)

    with SleeperClient(settings) as client:
        sync_users(session, client, LEAGUE_ID)
        session.commit()

        original = session.get(LeagueUser, (LEAGUE_ID, "1001"))
        first_seen = original.first_seen_at
        first_last_seen = original.last_seen_at

        # A member renames their team between syncs.
        renamed = [dict(u) for u in users_payload]
        renamed[0]["metadata"] = {"team_name": "Gurus Reborn"}
        respx.get(f"{BASE}/league/{LEAGUE_ID}/users").mock(
            return_value=httpx.Response(200, json=renamed)
        )
        sync_users(session, client, LEAGUE_ID)
        session.commit()

    assert session.scalar(select(func.count()).select_from(LeagueUser)) == 3

    session.expunge_all()
    updated = session.get(LeagueUser, (LEAGUE_ID, "1001"))
    assert updated.team_name == "Gurus Reborn"
    assert updated.first_seen_at == first_seen, "first_seen_at must not be overwritten"
    assert updated.last_seen_at >= first_last_seen


@respx.mock
def test_departed_member_is_retained(session, settings, league_payload, users_payload):
    """Leaving the league must not erase history."""
    _mock_sleeper(league_payload, users_payload)
    with SleeperClient(settings) as client:
        sync_users(session, client, LEAGUE_ID)
        session.commit()

        respx.get(f"{BASE}/league/{LEAGUE_ID}/users").mock(
            return_value=httpx.Response(200, json=users_payload[:2])
        )
        sync_users(session, client, LEAGUE_ID)
        session.commit()

    assert session.scalar(select(func.count()).select_from(LeagueUser)) == 3
    assert session.get(LeagueUser, (LEAGUE_ID, "1003")) is not None


@respx.mock
def test_cli_sync_records_ok_run(
    db, settings, league_payload, users_payload, drafts_payload, draft_payload, draft_picks_payload
):
    _mock_sleeper(
        league_payload, users_payload, drafts_payload, draft_payload, draft_picks_payload
    )
    result = CliRunner().invoke(cli_app, ["sync"])
    assert result.exit_code == 0, result.output

    with session_scope() as s:
        run = s.scalars(select(SyncRun).order_by(SyncRun.id.desc())).first()
        assert run.status == "ok"
        # 1 league + 3 users + 1 draft + 2 picks
        assert run.records_written == 7
        assert run.finished_at is not None
        assert run.error is None
        # The draft rides along with the five-minute sync, not only the fast one.
        assert s.scalar(select(func.count()).select_from(Draft)) == 1


@respx.mock
def test_cli_sync_records_error_and_exits_nonzero(db, settings, league_payload):
    """systemd needs a non-zero exit to mark the unit failed."""
    respx.get(f"{BASE}/league/{LEAGUE_ID}").mock(return_value=httpx.Response(500))

    result = CliRunner().invoke(cli_app, ["sync"])
    assert result.exit_code == 1

    with session_scope() as s:
        run = s.scalars(select(SyncRun).order_by(SyncRun.id.desc())).first()
        assert run.status == "error"
        assert "SleeperError" in run.error
        assert run.finished_at is not None
        # Nothing should have been half-written.
        assert s.scalar(select(func.count()).select_from(League)) == 0


def test_cli_sync_without_league_id_exits_2(db, settings, monkeypatch):
    import app.config as config_module

    monkeypatch.setenv("FFD_SLEEPER_LEAGUE_ID", "")
    config_module.get_settings.cache_clear()

    result = CliRunner().invoke(cli_app, ["sync"])
    assert result.exit_code == 2

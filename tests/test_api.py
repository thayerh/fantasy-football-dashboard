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


@pytest.fixture
def spa_client(db, settings, tmp_path, monkeypatch):
    """A client with a built frontend present, so the SPA catch-all is mounted."""
    static_dir = tmp_path / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><title>SPA</title>")
    (static_dir / "assets" / "app.js").write_text("console.log('app')")
    # A public/ file: a real file with a fixed name, so it exercises the
    # served-file branch rather than falling through to the SPA index.
    (static_dir / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    # The file the traversal is trying to reach, two levels above the build.
    (tmp_path / "secret.env").write_text("FFD_SECRET=canary-must-not-leak")

    monkeypatch.setenv("FFD_STATIC_DIR", str(static_dir))
    import app.config as config_module

    config_module.get_settings.cache_clear()

    import importlib

    import app.main as main_module

    importlib.reload(main_module)
    yield TestClient(main_module.app)

    config_module.get_settings.cache_clear()
    importlib.reload(main_module)


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


# --- SPA static serving ----------------------------------------------------
# The catch-all joins an attacker-controlled path to the build directory. Before
# the containment check, GET /%2e%2e/%2e%2e/.env served the real .env — the app
# sits behind Cloudflare Access, but Access authenticates all ~15 leaguemates,
# so "reachable by a member" is the same as "leaked".

@pytest.mark.parametrize(
    "path",
    [
        "../../secret.env",
        "../secret.env",
        "assets/../../secret.env",
        "./../../secret.env",
        "../../../../../../etc/passwd",
        "/etc/passwd",
    ],
)
def test_static_paths_outside_the_build_are_refused(tmp_path, path):
    """These are the decoded values the ASGI scope carries.

    Requesting them through an HTTP client proves nothing: httpx collapses
    "../" before the request is sent, so a request-level test passes even
    against the vulnerable version. This exercises the decision itself.
    """
    from app.main import resolve_static_file

    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (tmp_path / "secret.env").write_text("FFD_SECRET=canary-must-not-leak")

    assert resolve_static_file(root.resolve(), path) is None


def test_static_paths_inside_the_build_are_served(tmp_path):
    from app.main import resolve_static_file

    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "app.js").write_text("console.log('app')")

    resolved = resolve_static_file(root.resolve(), "assets/app.js")
    assert resolved == (root / "assets" / "app.js").resolve()
    # A path inside the build that simply does not exist falls back to index.
    assert resolve_static_file(root.resolve(), "assets/missing.js") is None


def test_the_html_revalidates_and_hashed_assets_do_not(spa_client):
    """The two halves of a deploy actually reaching anyone.

    Vite renames every asset on each build, so an asset filename can never
    change meaning and is safe to keep for a year. index.html is the file that
    *names* those assets and keeps a stable name of its own, so a browser that
    caches it goes on requesting last week's bundle by name — which is exactly
    what a missing Cache-Control produces, since browsers then invent their own
    freshness window.
    """
    index = spa_client.get("/")
    assert index.headers["cache-control"] == "no-cache"

    asset = spa_client.get("/assets/app.js")
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

    # public/ files keep fixed names too, so they revalidate with the HTML.
    assert spa_client.get("/favicon.ico").headers["cache-control"] == "no-cache"


def test_spa_still_serves_real_files_and_falls_back_to_index(spa_client):
    """The guard must not break the thing it is guarding."""
    assert spa_client.get("/assets/app.js").text == "console.log('app')"
    # An unknown route is client-side routing, not a 404.
    assert "<title>SPA</title>" in spa_client.get("/draft").text

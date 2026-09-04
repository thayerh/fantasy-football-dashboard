"""The player pool and "best available".

The pool is the one table that is a snapshot rather than a history, and the one
fetch big enough that Sleeper asks for it daily at most — both of which are
assertions here rather than comments.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope
from app.ingest import sync_players
from app.models import Draft, DraftPick, Player
from app.sleeper import SleeperClient

BASE = "https://api.sleeper.app/v1"
LEAGUE_ID = "999999999999999999"
DRAFT_ID = "222000000000000002"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.sleeper.time.sleep", lambda _: None)


def _player(pid, name, position, rank, **extra):
    first, _, last = name.partition(" ")
    return {
        "player_id": pid,
        "full_name": name,
        "first_name": first,
        "last_name": last or first,
        "position": position,
        "fantasy_positions": [position],
        "team": extra.pop("team", "SF"),
        "search_rank": rank,
        "active": True,
        **extra,
    }


POOL = {
    "1": _player("1", "Star Back", "RB", 1),
    "2": _player("2", "Good Arm", "QB", 3),
    "3": _player("3", "Deep Threat", "WR", 2),
    "4": _player("4", "Kicks It", "K", 94),
    # Ranked far past the ceiling: real payloads carry ~10,000 of these.
    "5": _player("5", "Practice Squad", "WR", 9999999),
    # A position no fantasy league drafts.
    "6": _player("6", "Long Snapper", "LS", 12, fantasy_positions=["LS"]),
    # Team defenses have no rank and no full_name.
    "7": {
        "player_id": "SF",
        "full_name": None,
        "last_name": "49ers",
        "position": "DEF",
        "fantasy_positions": ["DEF"],
        "team": "SF",
        "search_rank": None,
        "active": True,
    },
}


def _mock_pool(pool):
    respx.get(f"{BASE}/players/nfl").mock(return_value=httpx.Response(200, json=pool))


@respx.mock
def test_sync_players_keeps_only_draftable_players(session, settings):
    _mock_pool(POOL)
    with SleeperClient(settings) as client:
        assert sync_players(session, client) == 5
    session.commit()

    stored = {p.player_id: p for p in session.scalars(select(Player)).all()}
    assert set(stored) == {"1", "2", "3", "4", "SF"}
    # Noise past the rank ceiling and non-fantasy positions are both dropped.
    assert "5" not in stored and "6" not in stored
    # Defenses are kept despite having no rank, and take a name from last_name.
    assert stored["SF"].search_rank is None
    assert stored["SF"].full_name == "49ers"


@respx.mock
def test_sync_players_replaces_the_pool(session, settings):
    """A snapshot, not an accumulation: a player who leaves the pool goes."""
    _mock_pool(POOL)
    with SleeperClient(settings) as client:
        sync_players(session, client)
        session.commit()
        assert session.scalar(select(func.count()).select_from(Player)) == 5

        shrunk = {k: v for k, v in POOL.items() if k in ("1", "2")}
        _mock_pool(shrunk)
        sync_players(session, client)
        session.commit()

    assert {p.player_id for p in session.scalars(select(Player)).all()} == {"1", "2"}


def _seed_pool_and_draft() -> None:
    with session_scope() as s:
        for pid, payload in POOL.items():
            if pid in ("5", "6"):
                continue
            s.add(
                Player(
                    player_id=payload["player_id"],
                    full_name=payload["full_name"] or payload["last_name"],
                    position=payload["position"],
                    team=payload["team"],
                    search_rank=payload["search_rank"],
                )
            )
        s.add(Draft(draft_id=DRAFT_ID, league_id=LEAGUE_ID, teams=4, rounds=3, raw={}))
        # The best available player is already gone — a keeper.
        s.add(DraftPick(draft_id=DRAFT_ID, pick_no=1, round=1, draft_slot=1, player_id="1"))
        s.commit()


def test_available_excludes_drafted_players(db, settings):
    from app.main import app

    _seed_pool_and_draft()
    body = TestClient(app).get("/api/draft/available").json()

    names = [p["full_name"] for p in body]
    assert "Star Back" not in names  # drafted
    # Ranked order, and the unranked defense sorts last rather than first.
    assert names == ["Deep Threat", "Good Arm", "Kicks It", "49ers"]


def test_available_filters_by_position_and_limit(db, settings):
    from app.main import app

    _seed_pool_and_draft()
    client = TestClient(app)

    qbs = client.get("/api/draft/available", params={"position": "qb"}).json()
    assert [p["full_name"] for p in qbs] == ["Good Arm"]

    assert len(client.get("/api/draft/available", params={"limit": 2}).json()) == 2
    assert client.get("/api/draft/available", params={"limit": 0}).status_code == 422

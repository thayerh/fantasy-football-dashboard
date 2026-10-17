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
from app.ingest import FANTASY_POSITIONS, sync_players
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
    # A fullback: Sleeper's position is FB but he is a running back for lineup
    # purposes, which is what fantasy_positions says and what the pool keeps.
    "4": _player("4", "Lead Block", "FB", 88, fantasy_positions=["RB"]),
    # Ranked far past the ceiling: real payloads carry ~10,000 of these.
    "5": _player("5", "Practice Squad", "WR", 9999999),
    # A position no fantasy league drafts.
    "6": _player("6", "Long Snapper", "LS", 12, fantasy_positions=["LS"]),
    # A kicker. Drafted in redraft, started by nobody in this league.
    "8": _player("8", "Kicks It", "K", 94, fantasy_positions=["K"]),
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
        assert sync_players(session, client) == 4
    session.commit()

    stored = {p.player_id: p for p in session.scalars(select(Player)).all()}
    assert set(stored) == {"1", "2", "3", "4"}
    # Noise past the rank ceiling and non-fantasy positions are both dropped.
    assert "5" not in stored and "6" not in stored
    # The fullback survives on fantasy_positions, not on `position` — matching
    # on the latter would drop all 35 of them from the real pool.
    assert stored["4"].position == "FB"


@respx.mock
def test_kickers_and_defenses_never_enter_the_pool(session, settings):
    """The league starts neither, so the guard is at ingestion rather than in
    every query. The migration cleared the rows already stored; this is what
    stops the next sync putting them straight back."""
    _mock_pool(POOL)
    with SleeperClient(settings) as client:
        sync_players(session, client)
    session.commit()

    stored = {p.player_id for p in session.scalars(select(Player)).all()}
    assert "8" not in stored  # the kicker
    assert "SF" not in stored  # the defense


@respx.mock
def test_sync_players_replaces_the_pool(session, settings):
    """A snapshot, not an accumulation: a player who leaves the pool goes."""
    _mock_pool(POOL)
    with SleeperClient(settings) as client:
        sync_players(session, client)
        session.commit()
        assert session.scalar(select(func.count()).select_from(Player)) == 4

        shrunk = {k: v for k, v in POOL.items() if k in ("1", "2")}
        _mock_pool(shrunk)
        sync_players(session, client)
        session.commit()

    assert {p.player_id for p in session.scalars(select(Player)).all()} == {"1", "2"}


def _seed_pool_and_draft() -> None:
    """Seed the pool directly, holding to what `sync_players` would have kept.

    The API tests below are about querying the pool, not filling it, so they
    skip the sync — but a seed that admitted a kicker would be testing against
    a pool this league can never actually have.
    """
    with session_scope() as s:
        for pid, payload in POOL.items():
            if not set(payload["fantasy_positions"]) & FANTASY_POSITIONS:
                continue
            if pid == "5":  # past the rank ceiling
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
    # No board has been imported, so this is the fallback: Sleeper's own order.
    assert names == ["Deep Threat", "Good Arm", "Lead Block"]


def test_available_filters_by_position_and_limit(db, settings):
    from app.main import app

    _seed_pool_and_draft()
    client = TestClient(app)

    qbs = client.get("/api/draft/available", params={"position": "qb"}).json()
    assert [p["full_name"] for p in qbs] == ["Good Arm"]

    assert len(client.get("/api/draft/available", params={"limit": 2}).json()) == 2
    assert client.get("/api/draft/available", params={"limit": 0}).status_code == 422


def test_flex_and_superflex_filter_by_lineup_slot(db, settings):
    """The two flexes are the league's own slots, not positions. FLEX admits
    RB/WR/TE and SUPERFLEX adds the quarterback, which is the difference that
    decides what a superflex owner is looking at."""
    from app.main import app

    _seed_pool_and_draft()
    client = TestClient(app)

    def names(**params):
        return {p["full_name"] for p in client.get("/api/draft/available", params=params).json()}

    # The fullback is a running back everywhere it matters, so both flexes
    # admit him even though Sleeper files his position as FB.
    assert names(position="FLEX") == {"Deep Threat", "Lead Block"}
    assert names(position="SUPERFLEX") == {"Deep Threat", "Good Arm", "Lead Block"}
    # Which, in a league that starts no K and no DEF, is everyone.
    assert names(position="SUPERFLEX") == names()


def test_an_unknown_board_is_refused(db, settings):
    """Rather than silently falling back to a different ranking than asked for."""
    from app.main import app

    _seed_pool_and_draft()
    response = TestClient(app).get("/api/draft/available", params={"board": "vibes"})
    assert response.status_code == 422

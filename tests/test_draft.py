"""Draft ingestion and the assembled board.

The draft is the only job on a 20-second timer, so two things matter more here
than elsewhere: it must be idempotent (it runs ~4,000 times a day), and it must
stop doing work once the draft is over.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope
from app.ingest import sync_draft
from app.models import Draft, DraftPick, LeagueUser, PlayerAdp, PlayerRanking
from app.sleeper import SleeperClient

BASE = "https://api.sleeper.app/v1"
LEAGUE_ID = "999999999999999999"
DRAFT_ID = "222000000000000002"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.sleeper.time.sleep", lambda _: None)


def _mock_draft(drafts_payload, draft_payload, draft_picks_payload):
    """Returns the picks route so tests can assert whether it was called."""
    respx.get(f"{BASE}/league/{LEAGUE_ID}/drafts").mock(
        return_value=httpx.Response(200, json=drafts_payload)
    )
    respx.get(f"{BASE}/draft/{DRAFT_ID}").mock(
        return_value=httpx.Response(200, json=draft_payload)
    )
    return respx.get(f"{BASE}/draft/{DRAFT_ID}/picks").mock(
        return_value=httpx.Response(200, json=draft_picks_payload)
    )


@respx.mock
def test_sync_draft_picks_the_newest_draft_and_stores_picks(
    session, settings, drafts_payload, draft_payload, draft_picks_payload
):
    _mock_draft(drafts_payload, draft_payload, draft_picks_payload)
    with SleeperClient(settings) as client:
        assert sync_draft(session, client, LEAGUE_ID) == 3  # 1 draft + 2 picks
    session.commit()

    draft = session.get(Draft, DRAFT_ID)
    # The 2025 draft in the list is older; the 2026 one must win.
    assert draft.season == "2026"
    assert draft.rounds == 3
    assert draft.teams == 4
    assert draft.type == "snake"
    # slot_to_roster_id exists only on the detail endpoint, so its presence
    # proves the detail call happened rather than the list payload being used.
    assert draft.slot_to_roster_id == {"1": 3, "2": 1, "3": 4, "4": 2}
    # Epoch milliseconds must arrive as aware UTC, never as raw ints.
    assert draft.start_time == dt.datetime(2026, 8, 29, 23, 30, tzinfo=dt.UTC)
    assert draft.last_picked_at.tzinfo is not None

    keeper = session.get(DraftPick, (DRAFT_ID, 2))
    assert (keeper.first_name, keeper.last_name) == ("Jonathan", "Taylor")
    assert (keeper.position, keeper.nfl_team) == ("RB", "IND")
    assert keeper.is_keeper is True

    # Sleeper reports is_keeper: null for some real keepers. Before the draft
    # starts a pick cannot be anything but a keeper, so the null one is stored
    # as True — not as None, which the column would reject anyway.
    assert session.get(DraftPick, (DRAFT_ID, 6)).is_keeper is True
    # An empty picked_by is stored as NULL rather than "".
    assert session.get(DraftPick, (DRAFT_ID, 6)).picked_by is None


@respx.mock
def test_sync_draft_is_idempotent(
    session, settings, drafts_payload, draft_payload, draft_picks_payload
):
    _mock_draft(drafts_payload, draft_payload, draft_picks_payload)
    with SleeperClient(settings) as client:
        sync_draft(session, client, LEAGUE_ID)
        session.commit()
        first = session.get(Draft, DRAFT_ID).fetched_at

        sync_draft(session, client, LEAGUE_ID)
        session.commit()

    assert session.scalar(select(func.count()).select_from(DraftPick)) == 2
    assert session.scalar(select(func.count()).select_from(Draft)) == 1
    assert session.get(Draft, DRAFT_ID).fetched_at >= first


@respx.mock
def test_keeper_survives_the_draft_starting(
    session, settings, drafts_payload, draft_payload, draft_picks_payload
):
    """The whole point of the inference: it has to outlive pre_draft.

    Once the draft is live, Sleeper still reports is_keeper: null for these
    picks, and every 20 seconds the ingest re-reads them. Without the sticky
    OR the board would quietly lose three keepers the moment the draft opened.
    """
    _mock_draft(drafts_payload, draft_payload, draft_picks_payload)
    with SleeperClient(settings) as client:
        sync_draft(session, client, LEAGUE_ID)
        session.commit()
        assert session.get(DraftPick, (DRAFT_ID, 6)).is_keeper is True

        # The draft opens. Same picks, same nulls, plus a genuine live pick.
        live = dict(draft_payload, status="drafting")
        live_picks = [
            *draft_picks_payload,
            {
                "draft_id": DRAFT_ID,
                "pick_no": 1,
                "round": 1,
                "draft_slot": 1,
                "roster_id": 3,
                "picked_by": "1003",
                "player_id": "9999",
                "is_keeper": None,
                "metadata": {
                    "first_name": "Real",
                    "last_name": "Pick",
                    "position": "WR",
                    "team": "GB",
                },
            },
        ]
        _mock_draft(drafts_payload, live, live_picks)
        sync_draft(session, client, LEAGUE_ID)
        session.commit()

    assert session.get(DraftPick, (DRAFT_ID, 6)).is_keeper is True
    assert session.get(DraftPick, (DRAFT_ID, 2)).is_keeper is True
    # A pick actually made during the draft is not a keeper.
    assert session.get(DraftPick, (DRAFT_ID, 1)).is_keeper is False


@respx.mock
def test_withdrawn_keeper_is_removed_from_the_board(
    session, settings, drafts_payload, draft_payload, draft_picks_payload
):
    """Keepers get edited before draft day; a dropped one must not linger."""
    _mock_draft(drafts_payload, draft_payload, draft_picks_payload)
    with SleeperClient(settings) as client:
        sync_draft(session, client, LEAGUE_ID)
        session.commit()
        assert session.scalar(select(func.count()).select_from(DraftPick)) == 2

        _mock_draft(drafts_payload, draft_payload, draft_picks_payload[:1])
        sync_draft(session, client, LEAGUE_ID)
        session.commit()

    assert session.scalar(select(func.count()).select_from(DraftPick)) == 1
    assert session.get(DraftPick, (DRAFT_ID, 6)) is None

    # And the empty case: every keeper withdrawn clears the board.
    with SleeperClient(settings) as client:
        _mock_draft(drafts_payload, draft_payload, [])
        sync_draft(session, client, LEAGUE_ID)
        session.commit()
    assert session.scalar(select(func.count()).select_from(DraftPick)) == 0


@respx.mock
def test_completed_draft_stops_refetching_picks(session, settings):
    """The timer may be left enabled year-round, so a finished draft must cost
    two requests, not three — and no writes to 180 pick rows every 20s."""
    drafts = [
        {
            "draft_id": DRAFT_ID,
            "created": 1,
            "league_id": LEAGUE_ID,
            "season": "2026",
            "status": "complete",
            "type": "snake",
            "settings": {"rounds": 1, "teams": 2},
        }
    ]
    detail = dict(drafts[0], draft_order={"1001": 1, "1002": 2}, slot_to_roster_id={"1": 1, "2": 2})
    picks = [
        {
            "draft_id": DRAFT_ID,
            "pick_no": n,
            "round": 1,
            "draft_slot": n,
            "roster_id": n,
            "picked_by": "1001",
            "player_id": str(n),
            "is_keeper": False,
            "metadata": {"first_name": "P", "last_name": str(n), "position": "RB", "team": "SF"},
        }
        for n in (1, 2)
    ]
    picks_route = _mock_draft(drafts, detail, picks)

    with SleeperClient(settings) as client:
        sync_draft(session, client, LEAGUE_ID)
        session.commit()
        assert picks_route.call_count == 1

        # Second run: the stored draft is complete and every pick is present.
        assert sync_draft(session, client, LEAGUE_ID) == 1
        session.commit()

    assert picks_route.call_count == 1
    assert session.scalar(select(func.count()).select_from(DraftPick)) == 2


@respx.mock
def test_api_resolves_slots_to_members(
    db, settings, drafts_payload, draft_payload, draft_picks_payload
):
    from app.main import app

    _mock_draft(drafts_payload, draft_payload, draft_picks_payload)
    with session_scope() as s:
        s.add(LeagueUser(league_id=LEAGUE_ID, user_id="1001", display_name="Commish",
                         team_name="Gridiron Gurus"))
        s.add(LeagueUser(league_id=LEAGUE_ID, user_id="1003", display_name="Alex"))
        s.commit()
        with SleeperClient(settings) as client:
            sync_draft(s, client, LEAGUE_ID)
        s.commit()

    body = TestClient(app).get("/api/draft").json()

    assert [slot["slot"] for slot in body["slots"]] == [1, 2, 3, 4]
    by_slot = {slot["slot"]: slot for slot in body["slots"]}
    # draft_order is user -> slot; the board needs the inverse.
    assert by_slot[2]["team_name"] == "Gridiron Gurus"
    assert by_slot[1]["display_name"] == "Alex"
    # Slot 4 is user 1002, who is not in league_user: an unclaimed column, not
    # an error.
    assert by_slot[4]["display_name"] is None
    assert by_slot[3]["roster_id"] == 4

    assert [pick["pick_no"] for pick in body["picks"]] == [2, 6]
    # raw must never reach the client.
    assert "raw" not in body and "raw" not in body["picks"][0]


def test_api_reports_503_before_the_first_draft_sync(db, settings):
    from app.main import app

    response = TestClient(app).get("/api/draft")
    assert response.status_code == 503
    assert "sync-draft" in response.json()["detail"]


def test_picks_carry_what_the_market_thought(db, settings):
    """Each pick ships its ADP and consensus rank so the board can show what the
    pick cost. Both are nullable — FantasyPros ranks far more players than it
    gives ADP for, so a pick can have one and not the other."""
    from app.main import app

    with session_scope() as s:
        s.add(Draft(draft_id=DRAFT_ID, league_id=LEAGUE_ID, teams=4, rounds=3, raw={}))
        s.add(DraftPick(draft_id=DRAFT_ID, pick_no=20, round=2, draft_slot=1, player_id="6813"))
        s.add(DraftPick(draft_id=DRAFT_ID, pick_no=25, round=3, draft_slot=2, player_id="4034"))
        s.add(PlayerAdp(player_id="6813", adp=6.4, adp_rank=7))
        s.add(PlayerRanking(player_id="6813", ecr_overall=10))
        # Ranked but no ADP: the coverage gap, which must not break the join.
        s.add(PlayerRanking(player_id="4034", ecr_overall=14))
        s.commit()

    picks = {p["pick_no"]: p for p in TestClient(app).get("/api/draft").json()["picks"]}

    assert picks[20]["adp"] == 6.4
    assert picks[20]["ecr"] == 10
    assert picks[25]["adp"] is None
    assert picks[25]["ecr"] == 14

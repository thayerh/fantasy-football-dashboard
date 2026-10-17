"""The league's fantasy rosters: ingest, and the assembled /api/rosters view.

Named for the league's rosters to keep it apart from test_rosters.py, which
covers the historical NFL roster tables — a different module answering a
different question.

The assertions worth having here are the ones about arrangement: Sleeper sends
four flat lists and no bench at all, and the route's job is to turn them into
a lineup with holes in it, a bench in a useful order, and no player either
duplicated or dropped on the way.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import session_scope
from app.ingest import sync_rosters, sync_traded_picks
from app.models import DynastyRanking, League, LeagueUser, Player, Roster, TradedPick
from app.sleeper import SleeperClient

BASE = "https://api.sleeper.app/v1"
LEAGUE_ID = "999999999999999999"

#: The dynasty league's actual shape (rulebook §2.1): ten starters, thirteen
#: bench, three taxi, three IR.
LINEUP = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "FLEX", "SUPER_FLEX"]
ROSTER_POSITIONS = [*LINEUP, *(["BN"] * 13)]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.sleeper.time.sleep", lambda _: None)


def _mock_rosters(payload):
    respx.get(f"{BASE}/league/{LEAGUE_ID}/rosters").mock(
        return_value=httpx.Response(200, json=payload)
    )


@respx.mock
def test_sync_rosters_stores_every_team(session, settings, rosters_payload):
    _mock_rosters(rosters_payload)
    with SleeperClient(settings) as client:
        assert sync_rosters(session, client, LEAGUE_ID) == 3
    session.commit()

    first = session.get(Roster, (LEAGUE_ID, 1))
    assert first.owner_id == "1001"
    assert first.players == ["100", "200", "300", "400", "500", "600"]
    # Positional, and stored unsorted: index 1 is the empty second lineup slot.
    assert first.starters == ["100", "0", "300"]
    assert first.taxi == ["500"]
    assert first.reserve == ["400"]
    assert first.wins == 2 and first.losses == 1
    assert first.waiver_budget_used == 15


@respx.mock
def test_points_are_recombined_and_absent_points_stay_null(
    session, settings, rosters_payload
):
    """Sleeper splits a score across two integer fields; neither means anything
    alone. Before Week 1 it sends neither, which is not the same as zero."""
    _mock_rosters(rosters_payload)
    with SleeperClient(settings) as client:
        sync_rosters(session, client, LEAGUE_ID)
    session.commit()

    assert session.get(Roster, (LEAGUE_ID, 1)).fpts == pytest.approx(254.68)
    assert session.get(Roster, (LEAGUE_ID, 1)).fpts_against == pytest.approx(201.05)
    assert session.get(Roster, (LEAGUE_ID, 2)).fpts is None


@respx.mock
def test_unclaimed_roster_is_kept(session, settings, rosters_payload):
    """A team nobody owns is a real state in a league still filling up."""
    _mock_rosters(rosters_payload)
    with SleeperClient(settings) as client:
        sync_rosters(session, client, LEAGUE_ID)
    session.commit()

    empty = session.get(Roster, (LEAGUE_ID, 3))
    assert empty.owner_id is None
    assert empty.players is None
    assert empty.wins == 0


@respx.mock
def test_resync_updates_in_place_and_drops_departed_teams(
    session, settings, rosters_payload
):
    _mock_rosters(rosters_payload)
    with SleeperClient(settings) as client:
        sync_rosters(session, client, LEAGUE_ID)
        session.commit()

        # A team is removed from the league, and another makes a trade.
        traded = [dict(roster) for roster in rosters_payload[:2]]
        traded[0] = {**traded[0], "players": ["100", "200"], "starters": ["200", "0", "100"]}
        _mock_rosters(traded)
        sync_rosters(session, client, LEAGUE_ID)
        session.commit()

    assert session.scalar(select(func.count()).select_from(Roster)) == 2
    session.expunge_all()
    assert session.get(Roster, (LEAGUE_ID, 1)).players == ["100", "200"]
    assert session.get(Roster, (LEAGUE_ID, 3)) is None


LEDGER = [
    {"season": "2027", "round": 3, "roster_id": 1, "owner_id": 2, "previous_owner_id": 1},
    {"season": "2027", "round": 4, "roster_id": 2, "owner_id": 1, "previous_owner_id": 2},
]


def _mock_traded(payload):
    respx.get(f"{BASE}/league/{LEAGUE_ID}/traded_picks").mock(
        return_value=httpx.Response(200, json=payload)
    )


@respx.mock
def test_sync_traded_picks_stores_the_ledger(session, settings):
    _mock_traded(LEDGER)
    with SleeperClient(settings) as client:
        assert sync_traded_picks(session, client, LEAGUE_ID) == 2
    session.commit()

    moved = session.get(TradedPick, (LEAGUE_ID, "2027", 3, 1))
    assert moved.owner_id == 2
    assert moved.previous_owner_id == 1


@respx.mock
def test_a_reversed_pick_trade_disappears(session, settings):
    """A pick trade the commissioner reverses (§8.5) leaves this endpoint
    rather than coming back with its original owner, so a row Sleeper no longer
    reports has to go — including when that leaves the ledger empty."""
    _mock_traded(LEDGER)
    with SleeperClient(settings) as client:
        sync_traded_picks(session, client, LEAGUE_ID)
        session.commit()

        _mock_traded([])
        assert sync_traded_picks(session, client, LEAGUE_ID) == 0
        session.commit()

    assert session.scalar(select(func.count()).select_from(TradedPick)) == 0


def _seed(rosters: list[dict] | None = None, ledger: list[dict] | None = None) -> None:
    """A league with a dynasty lineup, two members and a small player pool."""
    with session_scope() as s:
        s.add(
            League(
                league_id=LEAGUE_ID,
                name="The League",
                season="2026",
                status="in_season",
                total_rosters=2,
                raw={
                    "roster_positions": ROSTER_POSITIONS,
                    "settings": {"taxi_slots": 3, "reserve_slots": 3, "waiver_budget": 100},
                    "secret": "should never be exposed",
                },
            )
        )
        s.add(
            LeagueUser(
                league_id=LEAGUE_ID,
                user_id="1001",
                display_name="Zed",
                team_name="Zebras",
                is_owner=True,
            )
        )
        s.add(LeagueUser(league_id=LEAGUE_ID, user_id="1002", display_name="Amy"))

        for player_id, name, position, rank in [
            ("100", "Star Back", "RB", 3),
            ("200", "Good Arm", "QB", 1),
            ("300", "Deep Threat", "WR", 12),
            ("400", "Hurt Guy", "TE", 40),
            ("500", "Young Legs", "RB", 25),
            ("600", "Depth Piece", "WR", None),
        ]:
            s.add(Player(player_id=player_id, full_name=name, position=position, age=25))
            if rank is not None:
                for source in ("fantasypros", "fantasycalc", "thetradecalc"):
                    s.add(
                        DynastyRanking(source=source, player_id=player_id, overall_rank=rank)
                    )

        for roster in rosters or _default_rosters():
            s.add(Roster(league_id=LEAGUE_ID, **roster))
        for pick in ledger or []:
            s.add(TradedPick(league_id=LEAGUE_ID, **pick))
        s.commit()


def _default_rosters() -> list[dict]:
    return [
        {
            "roster_id": 1,
            "owner_id": "1001",
            # Deliberately out of any useful order, the way Sleeper sends it.
            "players": ["600", "100", "500", "300", "400", "200"],
            "starters": ["200", "100", "0"],
            "taxi": ["500"],
            "reserve": ["400"],
            "wins": 1,
            "losses": 0,
            "ties": 0,
            "raw": {"secret": "should never be exposed"},
        },
        {
            "roster_id": 2,
            "owner_id": "1002",
            "players": None,
            "starters": None,
            "taxi": None,
            "reserve": None,
            "wins": 0,
            "losses": 1,
            "ties": 0,
            "raw": {},
        },
    ]


@pytest.fixture
def client(db, settings):
    from app.main import app

    return TestClient(app)


def test_rosters_return_503_before_first_ingest(client):
    assert client.get("/api/rosters").status_code == 503
    assert "ffd sync" in client.get("/api/rosters").json()["detail"]


def test_lineup_and_limits_come_from_the_league_settings(client):
    _seed()
    body = client.get("/api/rosters").json()

    # The bench is not a lineup slot, so it is not in the lineup.
    assert body["lineup"] == LINEUP
    assert body["limits"] == {
        "starters": 10,
        "bench": 13,
        "active": 23,
        "taxi": 3,
        "reserve": 3,
    }
    # What every team's waiver_budget_used is spent against (rulebook §7.1).
    assert body["waiver_budget"] == 100


def test_starters_are_padded_to_the_lineup(client):
    """Index i must be lineup slot i, however short Sleeper's list is."""
    _seed()
    team = client.get("/api/rosters").json()["teams"][1]  # Zebras

    starters = team["starters"]
    assert len(starters) == len(LINEUP)
    assert starters[0]["full_name"] == "Good Arm"  # QB
    assert starters[1]["full_name"] == "Star Back"  # RB
    # The empty slot Sleeper writes as "0", and the seven it never sent.
    assert starters[2] is None
    assert all(slot is None for slot in starters[3:])


def test_every_controlled_player_appears_exactly_once(client):
    _seed()
    team = client.get("/api/rosters").json()["teams"][1]

    placed = [
        player["player_id"]
        for group in ("starters", "bench", "taxi", "reserve")
        for player in team[group]
        if player is not None
    ]
    assert sorted(placed) == ["100", "200", "300", "400", "500", "600"]
    assert len(placed) == len(set(placed)), "a player must not be in two groups"
    assert [player["player_id"] for player in team["taxi"]] == ["500"]
    assert [player["player_id"] for player in team["reserve"]] == ["400"]


def test_bench_is_ordered_by_the_dynasty_aggregate(client):
    """Sleeper's own order is roughly by player id. Nobody reads a bench that
    way, and a player no board ranks belongs at the bottom, not the top."""
    _seed()
    team = client.get("/api/rosters").json()["teams"][1]

    assert [player["full_name"] for player in team["bench"]] == [
        "Deep Threat",  # 12
        "Depth Piece",  # unranked, and last for it
    ]
    assert team["bench"][0]["consensus"] == 12
    assert team["bench"][0]["consensus_sources"] == 3
    assert team["bench"][1]["consensus"] is None


def test_a_player_outside_the_pool_still_holds_his_roster_spot(client):
    """`ffd sync-players` keeps only QB/RB/WR/TE, and runs daily: a rostered
    player can legitimately be missing. Dropping him would silently shorten
    somebody's roster."""
    rosters = _default_rosters()
    rosters[0]["players"] = ["100", "9999"]
    rosters[0]["starters"] = ["9999"]
    _seed(rosters)

    team = client.get("/api/rosters").json()["teams"][1]
    assert team["starters"][0] == {
        "player_id": "9999",
        "full_name": None,
        "position": None,
        "team": None,
        "injury_status": None,
        "years_exp": None,
        "age": None,
        "consensus": None,
        "consensus_sources": 0,
        "vegas_points": None,
    }
    assert [player["player_id"] for player in team["bench"]] == ["100"]


def test_teams_are_sorted_by_the_name_the_switcher_shows(client):
    """Team name where there is one, member name where there is not — roster
    ids are Sleeper's join order and mean nothing to a reader."""
    _seed()
    body = client.get("/api/rosters").json()

    assert [team["team_name"] or team["display_name"] for team in body["teams"]] == [
        "Amy",
        "Zebras",
    ]
    assert body["teams"][1]["is_owner"] is True


def test_an_empty_roster_is_all_empty_slots(client):
    _seed()
    team = client.get("/api/rosters").json()["teams"][0]  # Amy, no players

    assert team["starters"] == [None] * len(LINEUP)
    assert team["bench"] == []
    assert team["taxi"] == []
    assert team["reserve"] == []


def test_every_team_holds_its_own_picks_by_default(client):
    """Sleeper stores only trades; untraded picks exist by assumption, and the
    window is the next three rookie drafts at four rounds each (§6.1, §8.3)."""
    _seed()
    body = client.get("/api/rosters").json()

    # The league's season is 2026, so the next three drafts are 2027-2029.
    assert body["pick_seasons"] == ["2027", "2028", "2029"]
    assert body["pick_rounds"] == 4

    for team in body["teams"]:
        assert len(team["picks"]) == 12, "3 seasons x 4 rounds"
        assert team["picks_traded"] == []
        # Every one of them its own, and in reading order.
        assert all(pick["roster_id"] == team["roster_id"] for pick in team["picks"])
        assert [(pick["season"], pick["round"]) for pick in team["picks"]] == [
            (season, round_)
            for season in ("2027", "2028", "2029")
            for round_ in (1, 2, 3, 4)
        ]


def test_a_traded_pick_moves_to_its_new_owner(client):
    _seed(ledger=[{"season": "2027", "round": 3, "roster_id": 1, "owner_id": 2}])
    teams = {team["roster_id"]: team for team in client.get("/api/rosters").json()["teams"]}

    seller, buyer = teams[1], teams[2]
    assert len(seller["picks"]) == 11
    assert len(buyer["picks"]) == 13

    # The seller's side of it: still their pick, now somebody else's asset.
    assert seller["picks_traded"] == [{"season": "2027", "round": 3, "roster_id": 2}]
    assert not any(
        pick["season"] == "2027" and pick["round"] == 3 for pick in seller["picks"]
    )
    # The buyer's side: a pick that is not their own, labelled with whose it was.
    acquired = [pick for pick in buyer["picks"] if pick["roster_id"] != 2]
    assert acquired == [{"season": "2027", "round": 3, "roster_id": 1}]
    assert buyer["picks_traded"] == []


def test_picks_outside_the_window_are_not_shown(client):
    """A pick from a draft already held is spent, and one past the window
    cannot have been traded — neither is an asset anybody can act on."""
    _seed(
        ledger=[
            {"season": "2026", "round": 1, "roster_id": 1, "owner_id": 2},
            {"season": "2031", "round": 1, "roster_id": 1, "owner_id": 2},
        ]
    )
    teams = {team["roster_id"]: team for team in client.get("/api/rosters").json()["teams"]}

    assert len(teams[1]["picks"]) == 12
    assert teams[1]["picks_traded"] == []
    assert all(pick["season"] in ("2027", "2028", "2029") for pick in teams[2]["picks"])


def test_raw_payloads_are_never_exposed(client):
    _seed()
    assert "secret" not in client.get("/api/rosters").text

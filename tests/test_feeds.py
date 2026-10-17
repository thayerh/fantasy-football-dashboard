"""The fetched boards: parsing what the sites serve, and the daily fetch.

Payloads are trimmed from real responses — the field names and the quirks are
the sites' own: KTC's board hidden in a script tag, FantasyPros' in a JS
assignment, TheTradeCalc giving values with no ranks, FantasyCalc and
TheTradeCalc carrying draft picks among the players.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app import feeds
from app.dynasty import (
    DynastyParseError,
    identify,
    parse_fantasycalc_api,
    parse_fantasypros_page,
    parse_ktc_page,
    parse_thetradecalc_api,
    sleeper_ids_by_mfl,
    write_snapshot,
)
from app.feeds import FeedClient, fetch_boards
from app.ingest import sync_dynasty_rankings
from app.models import DynastyRanking, DynastyRankingHistory, Player

FANTASYCALC_API = [
    {
        "player": {"id": 5856, "name": "Ja'Marr Chase", "mflId": "15281", "sleeperId": "7564",
                   "position": "WR", "maybeTeam": "CIN", "maybeAge": 26.5},
        "value": 9859, "overallRank": 1, "positionRank": 1, "trend30Day": 205,
        "redraftValue": 9500, "combinedValue": 19359, "maybeTier": 1,
    },
    {
        "player": {"id": 15351, "name": "2027 1st (Early)", "mflId": None, "sleeperId": None,
                   "position": "PICK", "maybeTeam": None},
        "value": 4706, "overallRank": 2, "positionRank": 1,
    },
    {
        "player": {"id": 11702, "name": "Brock Bowers", "mflId": "16000", "sleeperId": "11604",
                   "position": "TE", "maybeTeam": "LV", "maybeAge": 23.8},
        "value": 8000, "overallRank": 3, "positionRank": 1, "maybeTier": 2,
    },
]


def _ktc_player(name, ktc_id, mfl, position, team, rank, pos_rank, value):
    values = {"value": value, "rank": rank, "positionalRank": pos_rank, "overallTier": 1,
              "overall7DayTrend": 0, "tep": {"value": value}}
    return {"playerName": name, "playerID": ktc_id, "position": position, "team": team,
            "age": 25.0, "rookie": False, "mflid": mfl,
            "oneQBValues": {**values, "value": value - 100, "rank": rank + 1},
            "superflexValues": values}


KTC_PLAYERS = [
    _ktc_player("Brock Bowers", 1500, 16000, "TE", "LV", 2, 1, 8500),
    _ktc_player("Ja'Marr Chase", 1000, 15281, "WR", "CIN", 1, 1, 9900),
    _ktc_player("2027 Early 1st", 2000, None, "RDP", None, 0, 0, 7000),
]
KTC_PAGE = (
    "<html><script>var playersArray = JSON.parse(document.getElementById('ktc-players')"
    ".textContent);</script>"
    f'<script type="application/json" id="ktc-players">{json.dumps(KTC_PLAYERS)}</script>'
    "</html>"
)

FANTASYPROS_PAGE = (
    "<script>var ecrData = "
    + json.dumps({"players": [
        {"player_id": 19788, "player_name": "Ja'Marr Chase", "player_team_id": "CIN",
         "player_position_id": "WR", "player_age": "26", "rank_ecr": 1, "rank_min": "1",
         "rank_max": "3", "rank_ave": "1.40", "rank_std": "0.80", "pos_rank": "WR1",
         "tier": 1, "note": "};"},
        {"player_id": 1, "player_name": "Cameron Dicker", "player_team_id": "LAC",
         "player_position_id": "K", "rank_ecr": 2, "pos_rank": "K1"},
    ]})
    + ";\nvar sosData = [];</script>"
)

THETRADECALC_API = [
    {"id": "te_brockbowers", "name": "Brock Bowers", "type": "player", "position": "TE",
     "team": "LV", "final_value": 8111, "trade_value": 7201, "redraft_value": 9000,
     "sleeper_id": "11604", "is_rookie": 0},
    {"id": "wr_jamarrchase", "name": "Ja'Marr Chase", "type": "player", "position": "WR",
     "team": "CIN", "final_value": 8956, "trade_value": 8508, "redraft_value": 8000,
     "sleeper_id": "7564", "is_rookie": 0},
    {"id": "pick_2027_1", "name": "2027 1st", "type": "pick", "position": None,
     "final_value": 9500},
    {"id": "k_someone", "name": "Some Kicker", "type": "player", "position": "K",
     "final_value": 100},
    {"id": "wr_nobody", "name": "Deep Nobody", "type": "player", "position": "WR",
     "final_value": 0, "redraft_value": 0},
]


def test_fantasycalc_api_drops_picks_and_keeps_the_sleeper_id():
    rows = parse_fantasycalc_api(FANTASYCALC_API)
    assert [r["player_name"] for r in rows] == ["Ja'Marr Chase", "Brock Bowers"]
    assert rows[0]["sleeper_id"] == "7564"
    # Ranks are FantasyCalc's own, pick included — Bowers is 3rd, not 2nd.
    assert rows[1]["overall_rank"] == 3
    assert rows[1]["pos_rank"] == "TE1"


def test_ktc_reads_superflex_values_and_matches_through_mfl_ids():
    """KTC has no Sleeper id; FantasyCalc's MFL map supplies it."""
    rows = parse_ktc_page(KTC_PAGE, sleeper_ids_by_mfl(FANTASYCALC_API))
    assert [r["player_name"] for r in rows] == ["Ja'Marr Chase", "Brock Bowers"]  # rank order
    assert rows[0]["sleeper_id"] == "7564"
    assert rows[0]["value"] == 9900  # superflex, not the 1QB 9800
    assert rows[0]["oneqb_value"] == 9800


def test_ktc_without_its_script_tag_is_a_parse_error():
    with pytest.raises(DynastyParseError, match="ktc-players"):
        parse_ktc_page("<html>redesigned</html>")


def test_fantasypros_page_survives_a_closing_brace_inside_the_data():
    rows = parse_fantasypros_page(FANTASYPROS_PAGE)
    assert len(rows) == 1  # the kicker is dropped
    assert rows[0]["rank_std"] == 0.8
    assert rows[0]["position_rank"] == 1


def test_thetradecalc_ranks_players_by_value():
    """No rank in the payload: it is the value order, players only."""
    dynasty = parse_thetradecalc_api(THETRADECALC_API)
    assert [(r["player_name"], r["overall_rank"]) for r in dynasty] == [
        ("Ja'Marr Chase", 1), ("Brock Bowers", 2)
    ]
    redraft = parse_thetradecalc_api(THETRADECALC_API, redraft=True)
    assert [r["player_name"] for r in redraft] == ["Brock Bowers", "Ja'Marr Chase"]
    assert redraft[0]["value"] == 9000
    assert redraft[0]["pos_rank"] == "TE1"


def test_a_snapshot_reads_back_as_the_board_it_was():
    rows = parse_ktc_page(KTC_PAGE, sleeper_ids_by_mfl(FANTASYCALC_API))
    source, back = identify(write_snapshot("ktc", rows))
    assert source == "ktc"
    assert back[0]["overall_rank"] == 1
    assert back[0]["value"] == 9900.0
    assert back[0]["sleeper_id"] == "7564"


def test_a_snapshot_naming_an_unknown_board_is_refused():
    with pytest.raises(DynastyParseError, match="not a known board"):
        identify("source,player_name,player_position_id,overall_rank\nvibes,X,WR,1\n")


# --- the fetch -------------------------------------------------------------


def _mock_sites(router, ktc_page=KTC_PAGE):
    router.get(feeds.FANTASYCALC_URL).mock(return_value=httpx.Response(200, json=FANTASYCALC_API))
    router.get(feeds.THETRADECALC_URL).mock(
        return_value=httpx.Response(200, json=THETRADECALC_API)
    )
    router.get(feeds.KTC_DYNASTY_URL).mock(return_value=httpx.Response(200, text=ktc_page))
    router.get(feeds.KTC_REDRAFT_URL).mock(return_value=httpx.Response(200, text=ktc_page))
    router.get(feeds.FANTASYPROS_URL).mock(
        return_value=httpx.Response(200, text=FANTASYPROS_PAGE)
    )


def test_one_broken_site_costs_only_its_own_boards(settings, monkeypatch):
    monkeypatch.setattr(feeds, "MIN_ROWS", 1)
    with respx.mock(assert_all_called=False) as router:
        _mock_sites(router, ktc_page="<html>redesigned</html>")
        with FeedClient() as client:
            boards, failures = fetch_boards(client)

    assert set(failures) == {"ktc", "ktc-redraft"}
    assert set(boards) == {
        "fantasycalc", "fantasycalc-redraft", "thetradecalc", "thetradecalc-redraft",
        "fantasypros",
    }


def test_a_board_too_short_to_be_real_is_refused(settings):
    """A truncated page must not replace yesterday's board with a fragment."""
    with respx.mock(assert_all_called=False) as router:
        _mock_sites(router)
        with FeedClient() as client:
            boards, failures = fetch_boards(client)

    assert not boards
    assert "fewer than" in str(failures["fantasycalc"])


# --- history ---------------------------------------------------------------


def _seed_pool(session) -> None:
    session.add(Player(player_id="7564", full_name="Ja'Marr Chase", last_name="Chase",
                       position="WR", team="CIN", fantasy_positions=["WR"]))
    session.add(Player(player_id="11604", full_name="Brock Bowers", last_name="Bowers",
                       position="TE", team="LV", fantasy_positions=["TE"]))
    session.commit()


def test_each_day_is_kept_and_a_rerun_replaces_only_its_day(session):
    _seed_pool(session)
    rows = parse_thetradecalc_api(THETRADECALC_API)
    day1, day2 = dt.date(2026, 10, 15), dt.date(2026, 10, 16)

    sync_dynasty_rankings(session, "thetradecalc", rows, as_of=day1)
    sync_dynasty_rankings(session, "thetradecalc", rows, as_of=day2)
    sync_dynasty_rankings(session, "thetradecalc", rows[:1], as_of=day2)  # a re-run
    session.commit()

    history = session.scalars(select(DynastyRankingHistory)).all()
    by_day: dict[dt.date, int] = {}
    for row in history:
        by_day[row.as_of] = by_day.get(row.as_of, 0) + 1
    assert by_day == {day1: 2, day2: 1}
    # The live board is still a snapshot: only the latest import.
    assert len(session.scalars(select(DynastyRanking)).all()) == 1


def test_a_hand_import_writes_no_history(session):
    _seed_pool(session)
    sync_dynasty_rankings(session, "thetradecalc", parse_thetradecalc_api(THETRADECALC_API))
    session.commit()
    assert session.scalars(select(DynastyRankingHistory)).all() == []

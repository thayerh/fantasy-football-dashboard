"""The dynasty ranking exports: parsing them, storing them, averaging them.

Four files in four formats, each with a quirk that would silently corrupt a
board if it were mishandled — a tier separator read as a player, a draft pick
read as a player, a kicker whose position label carries its rank. Those quirks
are the reason this file exists; every one of them is a real row from a real
export, kept here verbatim.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.dynasty import (
    DynastyParseError,
    identify,
    parse_fantasycalc,
    parse_fantasypros,
    parse_sleeper_adp,
    parse_thetradecalc,
)
from app.ingest import sync_dynasty_rankings
from app.models import Draft, DynastyRanking, Player

LEAGUE_ID = "999999999999999999"

# Verbatim from the export, header and all — including the two-cell tier
# separator rows the file interleaves among the players.
_FP_HEADER = (
    '"RK",TIERS,"PLAYER NAME",TEAM,"POS","AGE","BEST","WORST","AVG.","STD.DEV",'
    '"ECR VS. ADP"'
)
FANTASYPROS_CSV = _FP_HEADER + '''
"1",1,"Ja'Marr Chase",CIN,"WR1","26","1","1","1.0","0.0","+2"
"",4
"2",1,"Puka Nacua",LAR,"WR2","25","2","5","2.9","1.0","-"
"3",2,"Brock Bowers",LV,"TE1","23","3","9","5.2","2.1","+1"
"4",2,"Cameron Dicker",LAC,"K1","25","40","60","50.0","5.0","-"
"5",2,"Denver Broncos",DEN,"DST1","0","55","70","60.0","4.0","-"
'''

FANTASYCALC_CSV = (
    "﻿name;team;position;age;fantasycalcId;sleeperId;mflId;value;overallRank;"
    "positionRank;trend30day\n"
    '"Ja\'Marr Chase";"CIN";"WR";26.1;5000;"7564";"14100";9800;1;1;12\n'
    '"2027 1st (Early)";"null";"PICK";"null";15351;"FP_2027_early_0";"FP_2027_early_0";'
    "4706;2;1;224\n"
    '"Brock Bowers";"LV";"TE";23.4;9000;"11604";"16000";9100;3;1;-40\n'
    '"Free Agent";"null";"WR";28.0;9100;"9999";"16100";100;4;120;0\n'
)

THETRADECALC_CSV = """Rank,Player,Position,Pos Rank,Team,Value
1,Ja'Marr Chase,WR,WR1,CIN,9999
2,Brock Bowers,TE,TE1,LV,9100
3,Some Corner,DB,DB1,SEA,50
"""

SLEEPER_ADP_CSV = """Sleeper ADP,Name,Position
1,Ja'Marr Chase,WR
2,Brock Bowers,TE
3,Cameron Dicker,K1
4,Los Angeles Rams,DE
"""


# --- parsing ---------------------------------------------------------------


def test_fantasypros_skips_tier_separators_and_unstarted_positions():
    rows = parse_fantasypros(FANTASYPROS_CSV)

    # The `"",4` row is a tier separator, not a player ranked fourth.
    assert [r["player_name"] for r in rows] == ["Ja'Marr Chase", "Puka Nacua", "Brock Bowers"]
    # The kicker and the defense are gone with them.
    assert all(r["player_position_id"] in ("WR", "TE") for r in rows)

    chase = rows[0]
    assert chase["overall_rank"] == 1
    assert (chase["player_position_id"], chase["position_rank"]) == ("WR", 1)
    assert chase["pos_rank"] == "WR1"
    assert (chase["rank_min"], chase["rank_max"]) == (1, 1)
    assert chase["rank_std"] == 0.0
    assert chase["age"] == 26.0


def test_fantasypros_reads_a_dash_as_no_number():
    """"-" is how the export writes a missing cell, and float("-") would throw."""
    nacua = parse_fantasypros(FANTASYPROS_CSV)[1]
    assert nacua["rank_ave"] == 2.9  # the cells that are present still parse


def test_fantasycalc_drops_draft_picks_and_carries_the_sleeper_id():
    rows = parse_fantasycalc(FANTASYCALC_CSV)

    # "2027 1st (Early)" is a tradeable asset but not a player, and nothing in
    # the pool could ever match it.
    assert [r["player_name"] for r in rows] == ["Ja'Marr Chase", "Brock Bowers", "Free Agent"]
    assert rows[0]["sleeper_id"] == "7564"
    assert rows[0]["value"] == 9800
    assert rows[0]["age"] == 26.1
    # A free agent's team is the literal string "null", which must not be
    # matched against as though it were a club.
    assert rows[2]["player_team_id"] is None


def test_thetradecalc_drops_positions_the_league_does_not_start():
    rows = parse_thetradecalc(THETRADECALC_CSV)

    assert [r["player_name"] for r in rows] == ["Ja'Marr Chase", "Brock Bowers"]
    assert rows[0]["value"] == 9999
    assert (rows[0]["pos_rank"], rows[0]["position_rank"]) == ("WR1", 1)


def test_sleeper_adp_splits_the_glued_position_labels():
    """Kickers arrive as "K1".."K9" and defenses as a truncated "DE". Splitting
    the label is what makes all of them fall out together, rather than a "K1"
    slipping through as an unrecognised position."""
    rows = parse_sleeper_adp(SLEEPER_ADP_CSV)

    assert [r["player_name"] for r in rows] == ["Ja'Marr Chase", "Brock Bowers"]
    assert rows[0]["overall_rank"] == 1
    # The export has no team column at all, which is why the match ladder has
    # to be able to fall through to last name and position alone.
    assert rows[0]["player_team_id"] is None


# --- identification --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (FANTASYPROS_CSV, "fantasypros"),
        (FANTASYCALC_CSV, "fantasycalc"),
        (THETRADECALC_CSV, "thetradecalc"),
        (SLEEPER_ADP_CSV, "sleeper-adp"),
    ],
)
def test_each_export_is_identified_by_its_header(text, expected):
    """By content, not by filename: these are downloads, and one that landed as
    "rankings (1).csv" should still import as what it is."""
    source, rows = identify(text)
    assert source == expected
    assert rows


def test_an_unrecognised_header_is_refused():
    """The right outcome for a stray file in data/, and for an export whose
    format has changed under us — both are worth stopping for."""
    with pytest.raises(DynastyParseError, match="Unrecognised export header"):
        identify("player,rank\nSomeone,1\n")


# --- storage ---------------------------------------------------------------


def _seed_pool(session) -> None:
    session.add(
        Player(player_id="7564", full_name="Ja'Marr Chase", last_name="Chase",
               position="WR", team="CIN", fantasy_positions=["WR"])
    )
    session.add(
        Player(player_id="11604", full_name="Brock Bowers", last_name="Bowers",
               position="TE", team="LV", fantasy_positions=["TE"])
    )
    session.commit()


def test_the_sleeper_id_skips_the_match_ladder(session):
    """FantasyCalc is the one export that carries Sleeper's own id, so its rows
    are matched exactly rather than guessed at by name."""
    _seed_pool(session)
    written = sync_dynasty_rankings(session, "fantasycalc", parse_fantasycalc(FANTASYCALC_CSV))
    session.commit()

    # Two of three: the third row's sleeperId is not in the pool, and an id
    # that resolves to nothing is a miss rather than a row joined to no player.
    assert written == 2
    stored = {r.player_id: r for r in session.scalars(select(DynastyRanking)).all()}
    assert stored["7564"].value == 9800


def test_a_board_replaces_only_its_own_rows(session):
    """Each export is downloaded and imported by hand at its own moment, so
    re-importing one must leave the other three exactly as they were."""
    _seed_pool(session)
    sync_dynasty_rankings(session, "fantasypros", parse_fantasypros(FANTASYPROS_CSV))
    sync_dynasty_rankings(session, "sleeper-adp", parse_sleeper_adp(SLEEPER_ADP_CSV))
    session.commit()

    # A later FantasyPros export with one player in it.
    trimmed = FANTASYPROS_CSV.replace(
        '"3",2,"Brock Bowers",LV,"TE1","23","3","9","5.2","2.1","+1"\n', ""
    )
    sync_dynasty_rankings(session, "fantasypros", parse_fantasypros(trimmed))
    session.commit()

    rows = session.scalars(select(DynastyRanking)).all()
    by_source: dict[str, set[str]] = {}
    for row in rows:
        by_source.setdefault(row.source, set()).add(row.player_id)

    assert by_source["fantasypros"] == {"7564"}  # Bowers dropped off this board
    assert by_source["sleeper-adp"] == {"7564", "11604"}  # and is untouched on that one


def test_an_unknown_source_is_refused(session):
    _seed_pool(session)
    with pytest.raises(ValueError, match="Unknown ranking source"):
        sync_dynasty_rankings(session, "vibes", parse_fantasypros(FANTASYPROS_CSV))


# --- the aggregate ---------------------------------------------------------


def test_the_aggregate_excludes_sleeper_adp(db, settings):
    """The whole reason the aggregate is defined by exclusion. Sleeper ADP is
    what the room *did*; averaging it into a mean of what three analysts think
    would fold the benchmark into the thing being benchmarked."""
    from app.main import app

    with session_scope() as s:
        _seed_pool(s)
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        for source, rank in (("fantasypros", 10), ("fantasycalc", 20), ("thetradecalc", 30)):
            s.add(DynastyRanking(source=source, player_id="7564", overall_rank=rank))
        # An ADP far away from all three: if it were counted the mean moves.
        s.add(DynastyRanking(source="sleeper-adp", player_id="7564", overall_rank=200))
        s.commit()

    row = TestClient(app).get("/api/draft/available").json()[0]

    assert row["consensus"] == 20.0  # not 65.0
    assert row["consensus_sources"] == 3
    # The ADP is still returned — it is shown beside the aggregate, just not
    # inside it. That gap is the most useful number on the page.
    assert row["ranks"]["sleeper-adp"] == 200


def test_a_partial_aggregate_says_how_many_boards_made_it(db, settings):
    """A player one board loves outranks a player all three merely like, which
    is a real property of an average over a smaller sample. The count travels
    with the number so the table can say so rather than hide it."""
    from app.main import app

    with session_scope() as s:
        _seed_pool(s)
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        # Ranked 5th by one board and unseen by the other two.
        s.add(DynastyRanking(source="fantasypros", player_id="7564", overall_rank=5))
        for source in ("fantasypros", "fantasycalc", "thetradecalc"):
            s.add(DynastyRanking(source=source, player_id="11604", overall_rank=8))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    by_name = {row["full_name"]: row for row in body}

    assert [row["full_name"] for row in body] == ["Ja'Marr Chase", "Brock Bowers"]
    chase, bowers = by_name["Ja'Marr Chase"], by_name["Brock Bowers"]
    assert (chase["consensus"], chase["consensus_sources"]) == (5.0, 1)
    assert (bowers["consensus"], bowers["consensus_sources"]) == (8.0, 3)


def test_each_board_orders_the_list_and_every_row_carries_them_all(db, settings):
    """The tab decides the ordering and nothing else: the value of the page is
    in reading across the boards, so every row ships every rank whichever one
    is sorting."""
    from app.main import app

    with session_scope() as s:
        _seed_pool(s)
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        # The two boards disagree about these two players, on purpose.
        s.add(DynastyRanking(source="fantasypros", player_id="7564", overall_rank=1))
        s.add(DynastyRanking(source="fantasypros", player_id="11604", overall_rank=2))
        s.add(DynastyRanking(source="sleeper-adp", player_id="7564", overall_rank=9))
        s.add(DynastyRanking(source="sleeper-adp", player_id="11604", overall_rank=4))
        s.commit()

    client = TestClient(app)

    def names(board):
        return [r["full_name"] for r in client.get(
            "/api/draft/available", params={"board": board}
        ).json()]

    assert names("fantasypros") == ["Ja'Marr Chase", "Brock Bowers"]
    assert names("sleeper-adp") == ["Brock Bowers", "Ja'Marr Chase"]

    row = client.get("/api/draft/available", params={"board": "sleeper-adp"}).json()[0]
    assert row["ranks"] == {"fantasypros": 2, "sleeper-adp": 4}


def test_a_board_admits_only_the_players_it_ranks(db, settings):
    """Ordering by a board over players it has never heard of would be ordering
    by nothing, and would fill a tab with third-stringers rather than letting
    it run out honestly."""
    from app.main import app

    with session_scope() as s:
        _seed_pool(s)
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.add(DynastyRanking(source="fantasypros", player_id="7564", overall_rank=1))
        s.commit()

    body = TestClient(app).get("/api/draft/available", params={"board": "fantasypros"}).json()
    assert [r["full_name"] for r in body] == ["Ja'Marr Chase"]

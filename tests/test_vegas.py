"""Vegas projections: parsing the export, and storing what it matches.

The export runs name, team, status, position, rank and tag together with no
delimiter between them, so most of these tests are about where the boundaries
actually fall. None of them touch the network — the file is supplied by hand,
so the parser is a pure function over text.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.ingest import sync_vegas_projections
from app.models import Draft, DynastyRanking, Player, PlayerProjection
from app.vegas import VegasParseError, parse_projections

LEAGUE_ID = "999999999999999999"


def one(line: str) -> dict:
    rows = parse_projections(line)
    assert len(rows) == 1
    return rows[0]


def test_a_plain_row_splits_into_every_field():
    assert one("Jahmyr GibbsDETRB1High Floor,320.3") == {
        "player_name": "Jahmyr Gibbs",
        "player_team_id": "DET",
        "player_position_id": "RB",
        "status": None,
        "pos_rank": 1,
        "tag": "High Floor",
        "points": 320.3,
    }


def test_indianapolis_is_a_team_not_a_doubtful_flag():
    """The case that motivated matching team and status in one pass.

    Strip a status first and `IND` gives up its `D`, leaving the name as
    "Jonathan TaylorIN" with no team at all. He is RB4 — this is not something
    that only shows up in the tail of the file.
    """
    row = one("Jonathan TaylorINDRB4High Floor,267.9")
    assert row["player_name"] == "Jonathan Taylor"
    assert row["player_team_id"] == "IND"
    assert row["status"] is None


def test_a_status_after_a_team_is_still_read():
    """`NO` + `D`: the genuinely doubtful case the one above is mistaken for."""
    row = one("Jordyn TysonNODWR73,95.3")
    assert (row["player_name"], row["player_team_id"], row["status"]) == (
        "Jordyn Tyson", "NO", "D",
    )


@pytest.mark.parametrize(
    ("line", "team", "status"),
    [
        ("Ja'Marr ChaseCINQWR1High Floor,300.9", "CIN", "Q"),
        ("Zach CharbonnetSEAPUPRB44,90", "SEA", "PUP"),
        ("Jaren KanakTENIRTE67,28.7", "TEN", "IR"),
        ("Brandon AiyukSFDNRWR111,44.9", "SF", "DNR"),
        # No team at all: the export omits it rather than writing a placeholder.
        ("Zach ErtzTE52,33.3", None, None),
        ("Tyreek HillOWR80,84.7", None, "O"),
        ("Miles KitselmanQTE69,28.7", None, "Q"),
    ],
)
def test_team_and_status_are_both_optional(line, team, status):
    row = one(line)
    assert (row["player_team_id"], row["status"]) == (team, status)


@pytest.mark.parametrize(
    ("line", "name", "team"),
    [
        ("Ja'Marr ChaseCINQWR1High Floor,300.9", "Ja'Marr Chase", "CIN"),
        ("Amon-Ra St. BrownDETWR3High Floor,285.8", "Amon-Ra St. Brown", "DET"),
        ("Nick Westbrook-IkhineINDWR156,21.1", "Nick Westbrook-Ikhine", "IND"),
        # Leading capitals, and a capital run right against the team.
        ("DJ GiddensINDRB78,29.1", "DJ Giddens", "IND"),
        ("Mo Alie-CoxINDTE81,19.4", "Mo Alie-Cox", "IND"),
        ("KC ConcepcionCLEWR53Balanced,138.3", "KC Concepcion", "CLE"),
    ],
)
def test_punctuation_and_capitals_in_names_survive(line, name, team):
    """The tail is anchored and at most five characters, which is what stops a
    two-letter team like `KC` being lifted out of the middle of a name."""
    row = one(line)
    assert (row["player_name"], row["player_team_id"]) == (name, team)


def test_the_tag_is_optional():
    assert one("Rashee RiceKCWR11,222.2")["tag"] is None
    assert one("Chris OlaveNOWR10Volatile,223.6")["tag"] == "Volatile"


def test_blank_lines_are_skipped():
    assert len(parse_projections("\n\nJahmyr GibbsDETRB1,320.3\n\n")) == 1


@pytest.mark.parametrize(
    "line",
    [
        "Jahmyr GibbsDETRB1High Floor",   # no points
        "Jahmyr GibbsDETRB1,not-a-number",
        "Jahmyr GibbsDET,320.3",          # no position and rank
        "RB1,320.3",                      # no name
    ],
)
def test_a_malformed_line_raises_rather_than_vanishing(line):
    """The file is imported by hand and rarely, so a row that doesn't fit is
    far more likely to be a changed export format than a stray typo."""
    with pytest.raises(VegasParseError):
        parse_projections(line)


# --- ingest ----------------------------------------------------------------

POOL = [
    dict(player_id="6813", full_name="Jonathan Taylor", position="RB", team="IND",
         search_rank=4),
    dict(player_id="1234", full_name="A.J. Brown", position="WR", team="NE", search_rank=20),
    dict(player_id="JAX", full_name="Jaguars", position="DEF", team="JAX", search_rank=None),
]


def _seed_pool():
    with session_scope() as s:
        for row in POOL:
            s.add(Player(**row))
        s.commit()


def test_projections_are_stored_against_matched_players(session):
    _seed_pool()
    written = sync_vegas_projections(
        session,
        parse_projections(
            "Jonathan TaylorINDRB4High Floor,267.9\nA.J. BrownNEWR8Balanced,232.3\n"
        ),
    )
    session.commit()

    assert written == 2
    stored = {r.player_id: r for r in session.scalars(select(PlayerProjection)).all()}
    assert stored["6813"].points == 267.9
    assert stored["6813"].pos_rank == 4
    assert stored["6813"].tag == "High Floor"
    assert stored["6813"].source == "vegas-csv"


def test_unmatched_rows_are_skipped_not_orphaned(session):
    """Every stored projection must join to a real player."""
    _seed_pool()
    written = sync_vegas_projections(
        session, parse_projections("Some CampBodyNEWR200,3.1\nA.J. BrownNEWR8,232.3\n")
    )
    session.commit()

    assert written == 1
    known = {p.player_id for p in session.scalars(select(Player)).all()}
    assert all(r.player_id in known for r in session.scalars(select(PlayerProjection)).all())


def test_a_re_import_replaces_the_table(session):
    """The only way to update a projection is to import again, so a second
    import must not leave the first one's rows behind."""
    _seed_pool()
    sync_vegas_projections(session, parse_projections("Jonathan TaylorINDRB4,267.9\n"))
    session.commit()

    sync_vegas_projections(session, parse_projections("A.J. BrownNEWR8,232.3\n"))
    session.commit()

    stored = session.scalars(select(PlayerProjection)).all()
    assert [(r.player_id, r.points) for r in stored] == [("1234", 232.3)]


def test_an_empty_pool_raises_rather_than_storing_nothing(session):
    with pytest.raises(RuntimeError, match="sync-players"):
        sync_vegas_projections(session, parse_projections("A.J. BrownNEWR8,232.3\n"))


# --- API -------------------------------------------------------------------


def test_available_carries_the_projection(db, settings):
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(DynastyRanking(source="fantasypros", player_id="1234", overall_rank=2))
        s.add(DynastyRanking(source="fantasypros", player_id="6813", overall_rank=9))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()
    with session_scope() as s:
        sync_vegas_projections(s, parse_projections("A.J. BrownNEWR8,232.3\n"))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    assert body[0]["full_name"] == "A.J. Brown"
    assert body[0]["vegas_points"] == 232.3
    # Taylor is ranked but absent from the export, and says so rather than
    # borrowing someone else's number.
    assert body[1]["vegas_points"] is None


def test_the_projection_survives_a_position_filter(db, settings):
    """A different query shape — the filtered path — must still carry it."""
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(DynastyRanking(source="fantasypros", player_id="1234", overall_rank=2))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()
    with session_scope() as s:
        sync_vegas_projections(s, parse_projections("A.J. BrownNEWR8,232.3\n"))
        s.commit()

    row = TestClient(app).get("/api/draft/available?position=WR").json()[0]
    assert row["vegas_points"] == 232.3

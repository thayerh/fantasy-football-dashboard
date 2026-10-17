"""NFL roster history: franchise identity, stint collapsing, and the count.

The fixtures here are hand-built CSVs rather than recorded season files. A real
file is 15 MB, and every behaviour worth pinning down — a relocation, a
mid-season trade, a practice-squad row, two players with one name — fits in a
dozen rows where it can be read.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx
from sqlalchemy import func, select
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.ingest import sync_nfl_rosters
from app.models import NflPlayer, NflStint
from app.nflverse import NflverseClient, franchise_of, resolve_team
from app.rosters import build_report, format_history, parse_submission, search_players

RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"

COLUMNS = [
    "season", "team", "position", "jersey_number", "status", "full_name",
    "first_name", "last_name", "birth_date", "college", "gsis_id",
    "espn_id", "sleeper_id", "week", "game_type", "entry_year", "rookie_year",
    "draft_club", "draft_number", "pfr_id", "headshot_url",
]


def row(gsis_id, name, team, season, week, status="ACT", position="WR", college="State"):
    first, _, last = name.partition(" ")
    return {
        "season": season, "team": team, "position": position, "jersey_number": "11",
        "status": status, "full_name": name, "first_name": first, "last_name": last,
        "birth_date": "1995-01-01", "college": college, "gsis_id": gsis_id,
        "espn_id": "", "sleeper_id": "", "week": week, "game_type": "REG",
        "entry_year": "2016", "rookie_year": "2016", "draft_club": "",
        "draft_number": "", "pfr_id": "", "headshot_url": "",
    }


def to_csv(rows):
    lines = [",".join(COLUMNS)]
    lines.extend(",".join(str(r[c]) for c in COLUMNS) for r in rows)
    return "\n".join(lines) + "\n"


def mock_season(season, rows):
    respx.get(f"{RELEASE}/weekly_rosters/roster_weekly_{season}.csv").mock(
        return_value=httpx.Response(200, text=to_csv(rows))
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.nflverse.time.sleep", lambda _: None)


# --- franchise identity ----------------------------------------------------


def test_relocated_franchises_resolve_to_one_code():
    # The whole reason a franchise column exists: a Raiders query must not
    # silently lose the three Oakland seasons inside the window.
    assert franchise_of("OAK") == franchise_of("LV") == "LV"
    assert franchise_of("STL") == franchise_of("LAR") == franchise_of("LA") == "LA"
    assert franchise_of("SD") == franchise_of("LAC") == "LAC"


def test_resolve_team_accepts_names_and_abbreviations():
    assert resolve_team("lv") == "LV"
    assert resolve_team("Raiders") == "LV"
    assert resolve_team("Las Vegas Raiders") == "LV"
    assert resolve_team("Oakland Raiders") == "LV"
    assert resolve_team("New York Giants") == "NYG"
    assert resolve_team("New York Jets") == "NYJ"
    assert resolve_team("Narnia Dragons") is None


def test_unmapped_team_code_is_dropped_not_guessed(session, caplog):
    # An unknown abbreviation means a franchise's history would be incomplete.
    # It has to be loud, because the resulting undercount looks like a right
    # answer.
    with respx.mock, caplog.at_level(logging.ERROR, logger="app.ingest"):
        mock_season(2024, [row("00-1", "Real Player", "XYZ", 2024, 1)])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2024])
    assert session.scalar(select(func.count()).select_from(NflStint)) == 0
    assert "unmapped team codes" in caplog.text


# --- ingestion -------------------------------------------------------------


def test_only_active_rows_are_stored(session):
    """DEV/RES/CUT are read and discarded — that is what makes 2026 the 53-man."""
    with respx.mock:
        mock_season(2024, [
            row("00-1", "Active Guy", "BUF", 2024, 1),
            row("00-2", "Practice Squad", "BUF", 2024, 1, status="DEV"),
            row("00-3", "Injured Reserve", "BUF", 2024, 1, status="RES"),
            row("00-4", "Cut Guy", "BUF", 2024, 1, status="CUT"),
        ])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2024])

    names = session.scalars(select(NflPlayer.full_name)).all()
    assert names == ["Active Guy"]


def test_weekly_rows_collapse_to_one_stint_with_a_week_range(session):
    with respx.mock:
        mock_season(2024, [row("00-1", "Steady Eddie", "BUF", 2024, w) for w in (1, 2, 3, 4)])
        with NflverseClient() as client:
            written = sync_nfl_rosters(session, client, [2024])

    assert written == 1
    stint = session.scalars(select(NflStint)).one()
    assert (stint.first_week, stint.last_week, stint.weeks) == (1, 4, 4)


def test_midseason_trade_produces_a_stint_for_each_team(session):
    """The reason the weekly file is used instead of the season-level one."""
    with respx.mock:
        mock_season(2024, [
            row("00-1", "Traded Guy", "DAL", 2024, 1),
            row("00-1", "Traded Guy", "DAL", 2024, 2),
            row("00-1", "Traded Guy", "CLE", 2024, 3),
            row("00-1", "Traded Guy", "CLE", 2024, 4),
        ])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2024])

    stints = {s.franchise: s for s in session.scalars(select(NflStint)).all()}
    assert set(stints) == {"DAL", "CLE"}
    assert (stints["DAL"].first_week, stints["DAL"].last_week) == (1, 2)
    assert (stints["CLE"].first_week, stints["CLE"].last_week) == (3, 4)


def test_relocation_keeps_the_era_abbreviation_but_shares_a_franchise(session):
    with respx.mock:
        mock_season(2019, [row("00-1", "Long Server", "OAK", 2019, 1)])
        mock_season(2020, [row("00-1", "Long Server", "LV", 2020, 1)])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2019, 2020])

    stints = session.scalars(select(NflStint).order_by(NflStint.season)).all()
    assert [s.franchise for s in stints] == ["LV", "LV"]
    assert [s.team for s in stints] == ["OAK", "LV"]

    player = session.scalars(select(NflPlayer)).one()
    assert (player.first_season, player.last_season) == (2019, 2020)


def test_resyncing_a_season_replaces_it_and_is_idempotent(session):
    with respx.mock:
        mock_season(2024, [row("00-1", "First Load", "BUF", 2024, 1)])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2024])
            sync_nfl_rosters(session, client, [2024])
    assert session.scalar(select(func.count()).select_from(NflStint)) == 1

    # nflverse corrects its own files; a re-sync has to pick the correction up
    # rather than leave both versions behind.
    with respx.mock:
        mock_season(2024, [row("00-1", "First Load", "MIA", 2024, 1)])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2024])
    stint = session.scalars(select(NflStint)).one()
    assert stint.franchise == "MIA"


def test_a_back_sync_does_not_overwrite_newer_identity(session):
    """Re-loading 2019 alone must not revert a player's 2026 name and position."""
    with respx.mock:
        mock_season(2019, [row("00-1", "Old Name", "BUF", 2019, 1, position="RB")])
        mock_season(2026, [row("00-1", "New Name", "BUF", 2026, 1, position="WR")])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2019, 2026])
            sync_nfl_rosters(session, client, [2019])

    player = session.scalars(select(NflPlayer)).one()
    assert (player.full_name, player.position) == ("New Name", "WR")
    assert (player.first_season, player.last_season) == (2019, 2026)


def test_a_player_dropping_out_of_the_window_is_removed(session):
    with respx.mock:
        mock_season(2024, [
            row("00-1", "Stays Put", "BUF", 2024, 1),
            row("00-2", "Vanishes", "BUF", 2024, 1),
        ])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2024])
        mock_season(2024, [row("00-1", "Stays Put", "BUF", 2024, 1)])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2024])

    assert session.scalars(select(NflPlayer.full_name)).all() == ["Stays Put"]


# --- submitted lists -------------------------------------------------------


def test_parse_submission_handles_how_people_actually_paste_lists():
    text = """
    # my shortlist
    1. Derek Carr
    - Josh Jacobs
    * Maxx Crosby, DL
    Michael Bennett (DL)

    Marvin Harrison Jr.
    """
    assert parse_submission(text) == [
        ("Derek Carr", None),
        ("Josh Jacobs", None),
        ("Maxx Crosby", "DL"),
        ("Michael Bennett", "DL"),
        # "Jr." must not be eaten as a position hint.
        ("Marvin Harrison Jr.", None),
    ]


@pytest.fixture
def loaded(session):
    """Two seasons spanning the Raiders relocation, plus a duplicate name."""
    with respx.mock:
        mock_season(2019, [
            row("00-1", "Derek Carr", "OAK", 2019, 1, position="QB"),
            row("00-2", "Josh Jacobs", "OAK", 2019, 1, position="RB"),
            row("00-3", "Patrick Mahomes", "KC", 2019, 1, position="QB"),
            row("00-4", "Michael Bennett", "NE", 2019, 1, position="DL", college="Texas A&M"),
            row("00-5", "Michael Bennett", "ATL", 2019, 1, position="DL", college="Ohio State"),
        ])
        mock_season(2020, [
            row("00-1", "Derek Carr", "LV", 2020, 1, position="QB"),
            row("00-2", "Josh Jacobs", "LV", 2020, 1, position="RB"),
            row("00-3", "Patrick Mahomes", "KC", 2020, 1, position="QB"),
            row("00-5", "Michael Bennett", "LV", 2020, 1, position="DL", college="Ohio State"),
        ])
        with NflverseClient() as client:
            sync_nfl_rosters(session, client, [2019, 2020])
    # Committed, because the CLI tests below open their own session.
    session.commit()
    return session


def test_the_count_spans_a_relocation(loaded):
    """Asking for OAK or LV must give the same answer over both eras."""
    names = "Derek Carr\nJosh Jacobs\nPatrick Mahomes\n"
    for team in ("LV", "OAK", "Raiders", "Oakland Raiders"):
        report = build_report(loaded, team, names)
        assert report.count == 2, team
        assert report.total == 3


def test_unknown_names_are_reported_not_counted(loaded):
    report = build_report(loaded, "LV", "Derek Carr\nJhon Notaplayer\n")
    assert report.count == 1
    assert [lk.query for lk in report.unknown] == ["Jhon Notaplayer"]


def test_a_near_miss_gets_a_suggestion(loaded):
    report = build_report(loaded, "LV", "Derek Car\n")
    assert report.unknown[0].suggestions == ["Derek Carr"]


def test_an_ambiguous_name_is_counted_but_flagged(loaded):
    """One of the two Michael Bennetts was a Raider; the answer says so."""
    report = build_report(loaded, "LV", "Michael Bennett\n")
    assert report.count == 1
    assert [lk.query for lk in report.uncertain] == ["Michael Bennett"]


def test_a_position_hint_narrows_an_ambiguous_name(loaded):
    report = build_report(loaded, "KC", "Derek Carr, QB\n")
    assert report.count == 0  # right position, wrong team
    assert report.others[0].candidates[0].full_name == "Derek Carr"


def test_a_wrong_position_hint_does_not_lose_an_unambiguous_player(loaded):
    """Someone typing the wrong position still means the only player by that name."""
    report = build_report(loaded, "LV", "Derek Carr, RB\n")
    assert report.count == 1


def test_unknown_team_is_an_error_not_an_empty_answer(loaded):
    with pytest.raises(ValueError):
        build_report(loaded, "Narnia Dragons", "Derek Carr\n")


# --- presentation ----------------------------------------------------------


def test_format_history_collapses_runs_and_keeps_era_names(loaded):
    carr = search_players(loaded, name="derek carr")[0]
    assert format_history(carr) == "OAK 2019, LV 2020"
    assert format_history(carr, "LV") == "OAK 2019, LV 2020"

    mahomes = search_players(loaded, name="mahomes")[0]
    assert format_history(mahomes) == "KC 2019-2020"


def test_search_filters_combine(loaded):
    assert len(search_players(loaded, team="LV")) == 3
    assert len(search_players(loaded, team="LV", season=2019)) == 2
    assert len(search_players(loaded, team="LV", position="QB")) == 1
    assert [c.full_name for c in search_players(loaded, name="jacobs")] == ["Josh Jacobs"]


def _run(*args):
    result = CliRunner().invoke(cli_app, ["roster-check", *args])
    return result


def test_roster_check_splits_did_and_did_not(loaded, tmp_path):
    listing = tmp_path / "players.txt"
    listing.write_text("Derek Carr\nJosh Jacobs\nPatrick Mahomes\nJhon Notaplayer\n")

    result = _run("OAK", "--file", str(listing), "--json")
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["team"] == "LV"  # asked as OAK, answered as one franchise
    assert payload["submitted"] == 4
    assert payload["played_count"] == 2
    assert payload["did_not_play_count"] == 1
    assert payload["not_found_count"] == 1

    assert [p["query"] for p in payload["played"]] == ["Derek Carr", "Josh Jacobs"]
    assert [p["query"] for p in payload["did_not_play"]] == ["Patrick Mahomes"]
    assert [p["query"] for p in payload["not_found"]] == ["Jhon Notaplayer"]

    # A "did not play" answer carries where they did play, so it is checkable.
    assert payload["did_not_play"][0]["matches"][0]["all_teams"] == "KC 2019-2020"


def test_unfound_players_are_not_reported_as_having_not_played(loaded):
    """The database cannot say a player it has never heard of did not play."""
    payload = json.loads(_run("LV", "Jhon Notaplayer", "--json").output)
    assert payload["not_found_count"] == 1
    assert payload["did_not_play_count"] == 0
    assert payload["played_count"] == 0


def test_players_can_be_given_as_arguments(loaded):
    result = _run("LV", "Derek Carr", "Patrick Mahomes")
    assert result.exit_code == 0, result.output
    assert "PLAYED FOR LV (1)" in result.output
    assert "DID NOT PLAY FOR LV (1)" in result.output
    assert "1 played for LV, 1 did not" in result.output


def test_a_position_hint_survives_being_passed_as_an_argument(loaded):
    payload = json.loads(_run("LV", "Michael Bennett, DL", "--json").output)
    assert payload["played_count"] == 1


def test_arguments_and_file_together_are_refused(loaded, tmp_path):
    """Rather than silently ignoring one of the two lists the user supplied."""
    listing = tmp_path / "players.txt"
    listing.write_text("Derek Carr\n")
    result = _run("LV", "Josh Jacobs", "--file", str(listing))
    assert result.exit_code == 2
    assert "not both" in result.output


def test_an_empty_list_is_an_error_not_a_zero(loaded, tmp_path):
    """"0 of 0 played" would look like a real answer to a script."""
    listing = tmp_path / "players.txt"
    listing.write_text("# nothing but a comment\n")
    result = _run("LV", "--file", str(listing))
    assert result.exit_code == 2
    assert "No player names" in result.output


def test_a_missing_file_is_an_error(loaded, tmp_path):
    result = _run("LV", "--file", str(tmp_path / "nope.txt"))
    assert result.exit_code == 2


def test_roster_check_without_loaded_history_says_so(session, tmp_path):
    listing = tmp_path / "players.txt"
    listing.write_text("Derek Carr\n")
    result = CliRunner().invoke(cli_app, ["roster-check", "LV", "--file", str(listing)])
    assert result.exit_code == 2
    assert "sync-nfl-rosters" in result.output

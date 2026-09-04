"""The Sleeper ADP scrape: the DOM extractor, and the ingest that stores it.

The extractor runs inside a browser, so it is tested inside one — against
`set_content` fixtures rather than the live page. That keeps the tests offline
and deterministic while still exercising the real JavaScript, which is where
the fragility lives: a scraper's failure mode is a page whose shape changed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.fantasypros_adp import _EXTRACT_JS, AdpScrapeError, fetch_sleeper_adp
from app.ingest import sync_sleeper_adp
from app.models import Player, PlayerAdp

POOL = [
    dict(player_id="4034", full_name="Christian McCaffrey", position="RB", team="SF",
         last_name="McCaffrey", search_rank=5),
    dict(player_id="6813", full_name="Jonathan Taylor", position="RB", team="IND",
         last_name="Taylor", search_rank=4),
    dict(player_id="1234", full_name="A.J. Brown", position="WR", team="PHI",
         last_name="Brown", search_rank=20),
    dict(player_id="JAX", full_name="Jaguars", position="DEF", team="JAX", search_rank=None),
]


def _page_html(headers: list[str], rows: list[list[str]]) -> str:
    """A table shaped like FantasyPros', with whatever columns a test wants."""
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


REAL_HEADERS = ["RK", "NAME", "POS.RK", "REAL-TIME", "TREND (24H)", "TREND (7D)",
                "PICK NUM.", "ESPN", "YAHOO", "SLEEPER"]


def _row(rk, name, team, bye, posrk, sleeper):
    link = f'<a href="/nfl/players/x.php">{name}</a><br>{team} ({bye})'
    return [rk, link, posrk, "1.1", "-", "-", "1.01", "1", "—", sleeper]


@pytest.fixture(scope="module")
def page():
    """A real browser page, or a skip. Never a mock: the point is the JS."""
    playwright = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error as PlaywrightError

    with playwright.sync_playwright() as pw:
        for launch in (lambda: pw.chromium.launch(channel="chrome"), pw.chromium.launch):
            try:
                browser = launch()
                break
            except PlaywrightError:
                continue
        else:
            pytest.skip("no browser available for the extractor tests")

        try:
            yield browser.new_page()
        finally:
            browser.close()


def _extract(page, html):
    page.set_content(html)
    return page.evaluate(_EXTRACT_JS)


def test_extractor_reads_the_sleeper_column(page):
    html = _page_html(REAL_HEADERS, [
        _row("1", "Jahmyr Gibbs", "DET", "6", "RB1", "1"),
        _row("2", "Ja'Marr Chase", "CIN", "6", "WR1", "3"),
    ])
    result = _extract(page, html)

    assert [r["player_name"] for r in result["rows"]] == ["Jahmyr Gibbs", "Ja'Marr Chase"]
    assert [r["sleeper_adp"] for r in result["rows"]] == [1, 3]
    assert result["rows"][0] == {
        "player_name": "Jahmyr Gibbs",
        "player_team_id": "DET",
        "player_position_id": "RB",
        "player_bye_week": "6",
        "pos_rank": "RB1",
        "sleeper_adp": 1,
        "overall_rank": 1,
    }


def test_column_is_found_by_header_not_by_position(page):
    """The whole robustness claim: FantasyPros may reorder their table.

    The same two players, with SLEEPER moved from last to second — a positional
    `td[9]` reader would silently return the ESPN numbers here.
    """
    headers = ["RK", "SLEEPER", "NAME", "POS.RK", "ESPN"]
    rows = [
        ["1", "7", '<a href="#">Jahmyr Gibbs</a><br>DET (6)', "RB1", "1"],
        ["2", "9", '<a href="#">Ja\'Marr Chase</a><br>CIN (6)', "WR1", "3"],
    ]
    result = _extract(page, _page_html(headers, rows))

    assert [r["sleeper_adp"] for r in result["rows"]] == [7, 9]
    assert [r["player_name"] for r in result["rows"]] == ["Jahmyr Gibbs", "Ja'Marr Chase"]


def test_a_missing_sleeper_column_is_an_error_not_an_empty_set(page):
    """Silence would look exactly like 'nobody has an ADP yet'."""
    headers = ["RK", "NAME", "POS.RK", "ESPN"]
    result = _extract(page, _page_html(headers, [["1", "Gibbs", "RB1", "1"]]))

    assert result["error"]
    assert "SLEEPER" in result["error"]


def test_players_without_a_sleeper_adp_are_dropped(page):
    """An em dash means Sleeper has no ADP for him, not an ADP of zero."""
    html = _page_html(REAL_HEADERS, [
        _row("1", "Jahmyr Gibbs", "DET", "6", "RB1", "1"),
        _row("2", "Justice Hill", "BAL", "7", "RB78", "—"),
    ])
    rows = _extract(page, html)["rows"]

    assert [r["sleeper_adp"] for r in rows] == [1, None]
    # fetch_sleeper_adp applies the filter; the extractor reports what it saw.


def test_a_row_with_no_bye_still_yields_a_player(page):
    """Free agents render without the '(6)', and must not vanish."""
    rows = [["1", '<a href="#">Some Guy</a><br>FA', "RB1", "1.1", "-", "-",
             "1.01", "1", "—", "12"]]
    result = _extract(page, _page_html(REAL_HEADERS, rows))

    assert result["rows"][0]["player_name"] == "Some Guy"
    assert result["rows"][0]["sleeper_adp"] == 12
    assert result["rows"][0]["player_bye_week"] is None


def test_unknown_scoring_names_the_options(settings, monkeypatch):
    monkeypatch.setattr(settings, "fantasypros_scoring", "SUPERFLEX")
    with pytest.raises(AdpScrapeError, match="No ADP page known"):
        fetch_sleeper_adp(settings=settings)


# --- ingest ----------------------------------------------------------------


def _seed_pool():
    with session_scope() as s:
        for row in POOL:
            s.add(Player(**row))
        s.commit()


def _entry(name, team, position, adp, pos_rank=None):
    return {
        "player_name": name,
        "player_team_id": team,
        "player_position_id": position,
        "player_bye_week": "6",
        "pos_rank": pos_rank,
        "sleeper_adp": adp,
        "overall_rank": None,
    }


def test_sync_stores_matched_players_with_a_derived_ordinal(db):
    _seed_pool()
    entries = [
        _entry("Christian McCaffrey", "SF", "RB", 5.0),
        _entry("A.J. Brown", "PHI", "WR", 2.0),
        _entry("Jaguars", "JAX", "DST", 140.0),
    ]

    with session_scope() as s:
        written = sync_sleeper_adp(s, entries, "PPR")
        s.commit()

    assert written == 3
    with session_scope() as s:
        rows = s.scalars(select(PlayerAdp).order_by(PlayerAdp.adp_rank)).all()

    # The ordinal is assigned by ADP, not by the order the page listed them.
    assert [(r.player_id, r.adp, r.adp_rank) for r in rows] == [
        ("1234", 2.0, 1),
        ("4034", 5.0, 2),
        ("JAX", 140.0, 3),
    ]
    assert all(r.source == "sleeper-web" and r.sources == 1 for r in rows)
    # One platform has no spread to report, and inventing one would be a lie.
    assert all(r.adp_min is None and r.adp_max is None and r.adp_std is None for r in rows)


def test_sync_replaces_whatever_was_there(db):
    """The table must never hold a mix of Sleeper and cross-platform ADP."""
    _seed_pool()
    with session_scope() as s:
        s.add(PlayerAdp(player_id="6813", adp=99.0, source="fp-api", sources=5))
        s.commit()

    with session_scope() as s:
        sync_sleeper_adp(s, [_entry("A.J. Brown", "PHI", "WR", 2.0)], "PPR")
        s.commit()

    with session_scope() as s:
        rows = s.scalars(select(PlayerAdp)).all()

    assert [r.player_id for r in rows] == ["1234"]
    assert {r.source for r in rows} == {"sleeper-web"}


def test_duplicate_matches_keep_the_earlier_adp(db):
    """Two scraped rows can resolve to one Sleeper player; the lower ADP wins."""
    _seed_pool()
    entries = [
        _entry("A.J. Brown", "PHI", "WR", 40.0),
        _entry("AJ Brown", "PHI", "WR", 12.0),
    ]

    with session_scope() as s:
        written = sync_sleeper_adp(s, entries, "PPR")
        s.commit()

    assert written == 1
    with session_scope() as s:
        assert s.scalars(select(PlayerAdp.adp)).one() == 12.0


def test_unmatched_players_are_skipped_not_stored(db, caplog):
    """A ranked free agent Sleeper does not carry is a warning, not a failure."""
    _seed_pool()
    entries = [
        _entry("A.J. Brown", "PHI", "WR", 2.0),
        _entry("Nobody At All", "XXX", "WR", 3.0),
    ]

    with session_scope() as s:
        written = sync_sleeper_adp(s, entries, "PPR")
        s.commit()

    assert written == 1
    with session_scope() as s:
        assert s.scalars(select(PlayerAdp.player_id)).all() == ["1234"]


def test_an_empty_pool_is_an_error(db):
    """Storing ADP against ids that do not exist yet would join to nothing."""
    with session_scope() as s, pytest.raises(RuntimeError, match="sync-players"):
        sync_sleeper_adp(s, [_entry("A.J. Brown", "PHI", "WR", 2.0)], "PPR")

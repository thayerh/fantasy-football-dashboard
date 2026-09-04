"""FantasyPros rankings scraped from the cheat sheet — the source of truth.

The page renders its table from an embedded JSON blob, so the extractor reads
the blob rather than the markup. What is worth testing is the part that is
genuinely hard: reconciling two player databases that disagree about names,
positions and team abbreviations.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.db import session_scope
from app.fantasypros_web import CheatsheetError, extract_rankings, fetch_cheatsheet
from app.ingest import sync_rankings_web
from app.models import Player, PlayerRanking

CHEATSHEET_URL = "https://www.fantasypros.com/nfl/rankings/ppr-cheatsheets.php"


def _entry(name, position, rank, pos_rank, **extra):
    return {
        "player_id": extra.pop("fp_id", 1000 + rank),
        "player_name": name,
        "player_position_id": position,
        "player_team_id": extra.pop("team", "SF"),
        "player_bye_week": extra.pop("bye", "9"),
        "rank_ecr": rank,
        "pos_rank": pos_rank,
        "tier": extra.pop("tier", 1),
        "rank_min": extra.pop("rank_min", "1"),
        "rank_max": extra.pop("rank_max", "4"),
        "rank_ave": extra.pop("rank_ave", "1.52"),
        "rank_std": extra.pop("rank_std", "0.92"),
        **extra,
    }


def _payload(players):
    return {
        "sport": "NFL",
        "ranking_type_name": "draft",
        "year": "2026",
        "week": "0",
        "position_id": "ALL",
        "scoring": "PPR",
        "count": len(players),
        "total_experts": 107,
        "last_updated": "8/27",
        "last_updated_ts": 1787863166,
        "players": players,
    }


def _page(payload):
    """The blob is inline in a script tag, surrounded by unrelated markup."""
    return (
        "<!doctype html><html><head><script>var other={\"a\":1};</script></head>"
        f"<body><script>window.__data = {json.dumps(payload)};</script>"
        "<div>table markup we deliberately do not parse</div></body></html>"
    )


# The Sleeper pool, with the three disagreements the real data contains.
POOL = [
    # Straightforward: same name, same position.
    dict(player_id="4034", full_name="Christian McCaffrey", first_name="Christian",
         last_name="McCaffrey", position="RB", team="SF", search_rank=5),
    # Punctuation differs between the sources.
    dict(player_id="1234", full_name="A.J. Brown", first_name="A.J.", last_name="Brown",
         position="WR", team="PHI", search_rank=20),
    # FantasyPros files fullbacks as RB; Sleeper calls them FB.
    dict(player_id="5555", full_name="Kyle Juszczyk", first_name="Kyle", last_name="Juszczyk",
         position="FB", team="SF", search_rank=900),
    # FantasyPros uses a nickname; Sleeper uses the legal name.
    dict(player_id="6666", full_name="Marquise Brown", first_name="Marquise", last_name="Brown",
         position="WR", team="KC", search_rank=300),
    dict(player_id="JAX", full_name="Jaguars", first_name=None, last_name="Jaguars",
         position="DEF", team="JAX", search_rank=None),
]


def _seed_pool():
    with session_scope() as s:
        for row in POOL:
            s.add(Player(**row))
        s.commit()


def test_extract_reads_the_embedded_blob_not_the_markup():
    payload = _payload([_entry("Christian McCaffrey", "RB", 1, "RB1")])
    extracted = extract_rankings(_page(payload))
    assert extracted["scoring"] == "PPR"
    assert [p["player_name"] for p in extracted["players"]] == ["Christian McCaffrey"]


def test_extract_fails_loudly_when_the_page_changes():
    """A silent empty result would look like 'no players ranked today'."""
    with pytest.raises(CheatsheetError, match="page shape"):
        extract_rankings("<html><body>redesigned, no blob</body></html>")

    with pytest.raises(CheatsheetError, match="no players"):
        extract_rankings(_page(_payload([])))


@respx.mock
def test_fetch_uses_the_scoring_specific_page(settings):
    payload = _payload([_entry("Christian McCaffrey", "RB", 1, "RB1")])
    route = respx.get(CHEATSHEET_URL).mock(return_value=httpx.Response(200, text=_page(payload)))

    assert fetch_cheatsheet("PPR", settings=settings)["count"] == 1
    assert route.called

    with pytest.raises(CheatsheetError, match="No cheat sheet for scoring"):
        fetch_cheatsheet("DOUBLE-PPR", settings=settings)


def test_matching_ladder_reconciles_the_two_databases(session, settings):
    """Each rung fixes a disagreement the real payload actually contains."""
    _seed_pool()
    payload = _payload(
        [
            _entry("Christian McCaffrey", "RB", 1, "RB1", team="SF"),
            _entry("AJ Brown", "WR", 2, "WR1", team="PHI"),          # punctuation
            _entry("Kyle Juszczyk", "RB", 3, "RB2", team="SF"),      # FB filed as RB
            _entry("Hollywood Brown", "WR", 4, "WR2", team="KC"),    # nickname
            _entry("Jacksonville Jaguars", "DST", 5, "DST1", team="JAC"),  # JAC vs JAX
        ]
    )
    assert sync_rankings_web(session, payload) == 5
    session.commit()

    stored = {r.player_id: r for r in session.scalars(select(PlayerRanking)).all()}
    assert set(stored) == {"4034", "1234", "5555", "6666", "JAX"}
    assert stored["6666"].fp_name == "Hollywood Brown"
    assert stored["JAX"].ecr_overall == 5


def test_positional_rank_comes_from_the_label(session, settings):
    """One payload carries both ranks: rank_ecr overall, pos_rank positional.

    The API needed a second call per position for this; the cheat sheet labels
    it, so a position-filtered board works from the same single request.
    """
    _seed_pool()
    payload = _payload([_entry("Christian McCaffrey", "RB", 14, "RB7")])
    sync_rankings_web(session, payload)
    session.commit()

    row = session.get(PlayerRanking, "4034")
    assert row.ecr_overall == 14
    assert row.ecr_position == 7
    assert row.pos_rank == "RB7"
    assert row.source == "web"


def test_expert_spread_is_stored(session, settings):
    _seed_pool()
    payload = _payload(
        [_entry("Christian McCaffrey", "RB", 1, "RB1", rank_min="1", rank_max="9",
                rank_ave="3.40", rank_std="2.10")]
    )
    sync_rankings_web(session, payload)
    session.commit()

    row = session.get(PlayerRanking, "4034")
    assert (row.rank_min, row.rank_max) == (1, 9)
    assert (row.rank_ave, row.rank_std) == (3.40, 2.10)


def test_two_ranked_players_resolving_to_one_keeps_the_better(session, settings, monkeypatch):
    """There are two WRs named Isaiah Williams in the real 2026 set."""
    _seed_pool()
    payload = _payload(
        [
            _entry("A.J. Brown", "WR", 24, "WR9", fp_id=111, team="PHI"),
            _entry("A.J. Brown", "WR", 88, "WR30", fp_id=222, team="PHI"),
        ]
    )
    # Captured off the logger directly. caplog comes back empty once another
    # test in the run has invoked the CLI and reconfigured logging, which made
    # this assertion silently vacuous.
    warnings: list[str] = []
    monkeypatch.setattr(
        "app.ingest.log.warning",
        lambda msg, *args, **kw: warnings.append(msg % args if args else msg),
    )

    assert sync_rankings_web(session, payload) == 1
    session.commit()

    # The better-ranked entry is the one kept...
    assert session.get(PlayerRanking, "1234").ecr_overall == 24
    # ...and dropping the other is reported rather than silent.
    assert any("Two ranked players resolve to" in w for w in warnings)


def test_sync_replaces_previous_rankings(session, settings):
    """Source of truth: the newest fetch wins outright, with no merge."""
    _seed_pool()
    sync_rankings_web(session, _payload([_entry("A.J. Brown", "WR", 2, "WR1", team="PHI")]))
    session.commit()

    sync_rankings_web(
        session, _payload([_entry("Christian McCaffrey", "RB", 1, "RB1", team="SF")])
    )
    session.commit()

    rows = session.scalars(select(PlayerRanking)).all()
    assert [r.player_id for r in rows] == ["4034"]


def test_sync_refuses_to_run_without_a_player_pool(session, settings):
    with pytest.raises(RuntimeError, match="sync-players"):
        sync_rankings_web(session, _payload([_entry("Anyone", "RB", 1, "RB1")]))

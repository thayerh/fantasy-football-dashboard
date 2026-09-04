"""FantasyPros rankings from the JSON API — the fallback source.

The cheat sheet (test_rankings_web.py) is the source of truth; this path exists
for when its markup changes. The free tier gives 50 calls a day and caps every
response at ten players, so the sync is shaped around that: seven calls, no
retries beyond one, and nothing calling FantasyPros from a request path. These
tests use recorded payloads and never touch the network.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.fantasypros import FantasyProsClient, FantasyProsError
from app.ingest import sync_adp, sync_rankings_api
from app.models import Draft, DraftPick, Player, PlayerAdp, PlayerRanking

BASE = "https://api.fantasypros.com/public/v2/json"
SEASON = "2026"
LEAGUE_ID = "999999999999999999"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.fantasypros.time.sleep", lambda _: None)


@pytest.fixture
def fp_settings(settings, monkeypatch):
    monkeypatch.setenv("FFD_FANTASYPROS_KEY", "test-key")
    import app.config as config_module

    config_module.get_settings.cache_clear()
    yield config_module.get_settings()
    config_module.get_settings.cache_clear()


def _entry(name, position, rank, **extra):
    return {
        # Stable per player, as the real API is: the same player carries the
        # same id in the overall call and in his position's call.
        "player_id": extra.pop("fp_id", abs(hash(name)) % 100000),
        "player_name": name,
        "player_position_id": position,
        "player_team_id": extra.pop("team", "SF"),
        "player_yahoo_id": extra.pop("yahoo", None),
        "player_bye_week": extra.pop("bye", "9"),
        "rank_ecr": rank,
        "pos_rank": f"{position}{rank}",
        "tier": 1,
        **extra,
    }


def _payload(players, **extra):
    return {
        "sport": "NFL",
        "year": SEASON,
        "week": "0",
        "scoring": "PPR",
        "ranking_type_name": "draft",
        "total_experts": 107,
        "count": 517,
        "limit": 10,
        "public_api_limited": True,
        "last_updated_ts": 1787863166,
        "players": players,
        **extra,
    }


# The pool the rankings must match against.
POOL = [
    dict(player_id="4034", full_name="Christian McCaffrey", position="RB", team="SF",
         yahoo_id="30123", search_rank=5),
    dict(player_id="6813", full_name="Jonathan Taylor", position="RB", team="IND",
         yahoo_id=None, search_rank=4),
    dict(player_id="1234", full_name="A.J. Brown", position="WR", team="PHI",
         yahoo_id=None, search_rank=20),
    dict(player_id="JAX", full_name="Jaguars", position="DEF", team="JAX", search_rank=None),
]


def _seed_pool():
    with session_scope() as s:
        for row in POOL:
            s.add(Player(**row))
        s.commit()


def _mock(all_players, per_position=None):
    per_position = per_position or {}
    route = respx.get(f"{BASE}/nfl/{SEASON}/consensus-rankings")

    def handler(request):
        position = request.url.params.get("position")
        players = all_players if position == "ALL" else per_position.get(position, [])
        return httpx.Response(200, json=_payload(players))

    route.mock(side_effect=handler)
    return route


@respx.mock
def test_rankings_match_by_yahoo_id_then_name(session, fp_settings):
    _seed_pool()
    _mock(
        [
            # Yahoo id present on both sides.
            _entry("Christian McCaffrey", "RB", 1, yahoo="30123", team="SF"),
            # No yahoo id: falls back to normalised name + position. The
            # punctuation differs between the two sources.
            _entry("AJ Brown", "WR", 2, team="PHI"),
        ]
    )
    with FantasyProsClient(fp_settings) as client:
        assert sync_rankings_api(session, client, SEASON) == 2
    session.commit()

    ranked = {r.player_id: r for r in session.scalars(select(PlayerRanking)).all()}
    assert set(ranked) == {"4034", "1234"}
    assert ranked["4034"].ecr_overall == 1
    assert ranked["4034"].tier == 1
    assert ranked["4034"].bye_week == 9


@respx.mock
def test_defense_team_abbreviations_are_reconciled(session, fp_settings):
    """FantasyPros says JAC, Sleeper says JAX.

    Before the alias table this wrote a row keyed 'JAC' that joined to no
    player at all — invisible until someone counted the defenses.
    """
    _seed_pool()
    _mock([], {"DST": [_entry("Jacksonville Jaguars", "DST", 1, team="JAC")]})
    with FantasyProsClient(fp_settings) as client:
        sync_rankings_api(session, client, SEASON)
    session.commit()

    stored = session.scalars(select(PlayerRanking)).all()
    assert [r.player_id for r in stored] == ["JAX"]


@respx.mock
def test_unmatched_players_are_skipped_not_orphaned(session, fp_settings):
    """Every stored ranking must join to a real player."""
    _seed_pool()
    _mock(
        [
            _entry("Christian McCaffrey", "RB", 1, yahoo="30123"),
            _entry("Rookie Nobody", "WR", 2, team="SF"),
            _entry("Nonexistent Defense", "DST", 3, team="ZZZ"),
        ]
    )
    with FantasyProsClient(fp_settings) as client:
        assert sync_rankings_api(session, client, SEASON) == 1
    session.commit()

    known = {p.player_id for p in session.scalars(select(Player)).all()}
    assert all(r.player_id in known for r in session.scalars(select(PlayerRanking)).all())


@respx.mock
def test_overall_and_positional_ranks_are_stored_separately(session, fp_settings):
    """rank_ecr means different things depending on how the API was asked.

    In the position=ALL call it is the overall rank; in a position call it is
    the rank within that position. Conflating them would rank Josh Allen first
    overall on a page that never asked about quarterbacks.
    """
    _seed_pool()
    _mock(
        [_entry("Christian McCaffrey", "RB", 7, yahoo="30123")],
        {"RB": [_entry("Christian McCaffrey", "RB", 1, yahoo="30123")]},
    )
    with FantasyProsClient(fp_settings) as client:
        sync_rankings_api(session, client, SEASON)
    session.commit()

    row = session.get(PlayerRanking, "4034")
    assert row.ecr_overall == 7
    assert row.ecr_position == 1


@respx.mock
def test_sync_costs_seven_calls_and_replaces_previous_rankings(session, fp_settings):
    _seed_pool()
    route = _mock([_entry("Christian McCaffrey", "RB", 1, yahoo="30123")])
    with FantasyProsClient(fp_settings) as client:
        sync_rankings_api(session, client, SEASON)
        session.commit()
        # One overall call plus one per position: QB RB WR TE K DST.
        assert route.call_count == 7
        assert client.calls == 7

        # A later sync where that player is no longer ranked clears the row.
        route.mock(side_effect=lambda request: httpx.Response(200, json=_payload([])))
        sync_rankings_api(session, client, SEASON)
        session.commit()

    assert session.scalars(select(PlayerRanking)).all() == []


def test_missing_key_fails_before_any_request(settings, monkeypatch):
    monkeypatch.setenv("FFD_FANTASYPROS_KEY", "")
    import app.config as config_module

    config_module.get_settings.cache_clear()
    with pytest.raises(FantasyProsError, match="FFD_FANTASYPROS_KEY"):
        FantasyProsClient(config_module.get_settings())
    config_module.get_settings.cache_clear()


@respx.mock
def test_rate_limit_is_reported_not_retried(session, fp_settings):
    """A 429 means the daily budget is gone; retrying spends what is left."""
    _seed_pool()
    route = respx.get(f"{BASE}/nfl/{SEASON}/consensus-rankings").mock(
        return_value=httpx.Response(429)
    )
    with FantasyProsClient(fp_settings) as client:
        with pytest.raises(FantasyProsError, match="rate limit"):
            sync_rankings_api(session, client, SEASON)
    assert route.call_count == 1


def test_available_lists_ranked_players_in_ecr_order(db, settings):
    """Ranked players only, best consensus first.

    Jonathan Taylor has the best *Sleeper* rank but no ECR. Sleeper's pool
    carries every rostered player in the league, so admitting the unranked
    would pad a position tab out to the full limit with third-stringers; the
    ranking is what makes "best available" mean anything.
    """
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="1234", ecr_overall=2))
        s.add(PlayerRanking(player_id="4034", ecr_overall=9))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.add(DraftPick(draft_id="d1", pick_no=1, player_id="JAX"))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    assert [p["full_name"] for p in body] == [
        "A.J. Brown",           # ecr 2
        "Christian McCaffrey",  # ecr 9
    ]
    assert body[0]["ecr"] == 2


def test_available_falls_back_to_sleeper_order_when_nothing_is_ranked(db, settings):
    """An empty ranking table must not empty the tab.

    This is the state on a fresh install between `ffd sync-players` and
    `ffd sync-rankings`, and it is also what an IDP position looks like
    permanently — FantasyPros ranks no linebackers.
    """
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.add(DraftPick(draft_id="d1", pick_no=1, player_id="JAX"))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    # Sleeper's own search_rank now decides, so Taylor leads.
    assert body[0]["full_name"] == "Jonathan Taylor"
    assert len(body) == 3
    assert all(p["ecr"] is None for p in body)


def test_available_carries_sleeper_adp(db, settings):
    """The ADP shown beside a player is the one stored against him."""
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="1234", ecr_overall=2))
        s.add(PlayerAdp(player_id="1234", adp=5.0, source="sleeper-web"))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    assert body[0]["full_name"] == "A.J. Brown"
    assert body[0]["adp"] == 5.0


def test_sort_by_adp_orders_by_the_market_and_needs_one(db, settings):
    """The ADP ordering admits only players who have an ADP.

    McCaffrey is ranked but has no ADP; sorting the market by a player who is
    not in it would put him at an arbitrary position, so he is left out.
    """
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="1234", ecr_overall=2))
        s.add(PlayerRanking(player_id="4034", ecr_overall=9))
        s.add(PlayerAdp(player_id="1234", adp=30.0, source="sleeper-web"))
        s.add(PlayerAdp(player_id="6813", adp=12.0, source="sleeper-web"))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()

    client = TestClient(app)

    by_adp = client.get("/api/draft/available?sort=adp").json()
    assert [p["full_name"] for p in by_adp] == ["Jonathan Taylor", "A.J. Brown"]
    assert [p["adp"] for p in by_adp] == [12.0, 30.0]

    # The same players, ordered by consensus instead: Taylor has no ECR, and
    # McCaffrey — absent from the ADP list — reappears.
    by_ecr = client.get("/api/draft/available?sort=ecr").json()
    assert [p["full_name"] for p in by_ecr] == ["A.J. Brown", "Christian McCaffrey"]


def test_both_sorts_accept_a_position_filter(db, settings):
    """The two controls are independent: either ordering, any position."""
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="4034", ecr_overall=9, ecr_position=2))
        s.add(PlayerRanking(player_id="6813", ecr_overall=3, ecr_position=1))
        s.add(PlayerAdp(player_id="4034", adp=4.0, source="sleeper-web"))
        s.add(PlayerAdp(player_id="6813", adp=40.0, source="sleeper-web"))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()

    client = TestClient(app)
    names = lambda url: [p["full_name"] for p in client.get(url).json()]  # noqa: E731

    # Both are RBs, and the two orderings disagree about them.
    assert names("/api/draft/available?position=RB&sort=ecr") == [
        "Jonathan Taylor", "Christian McCaffrey",
    ]
    assert names("/api/draft/available?position=RB&sort=adp") == [
        "Christian McCaffrey", "Jonathan Taylor",
    ]
    # The filter still excludes other positions under either.
    assert names("/api/draft/available?position=WR&sort=adp") == ["A.J. Brown"]


def test_an_unknown_sort_is_rejected(db, settings):
    from app.main import app

    assert TestClient(app).get("/api/draft/available?sort=vibes").status_code == 422


def test_overall_ecr_survives_a_position_filter(db, settings):
    """The ECR column is a gap between two *overall* pick numbers.

    Filtered, `ecr` holds the positional rank — so the overall one has to be
    carried separately or the column would compute "QB1 minus ADP 22".
    """
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="1234", ecr_overall=18, ecr_position=4))
        s.add(PlayerAdp(player_id="1234", adp=25.0, source="sleeper-web"))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()

    row = TestClient(app).get("/api/draft/available?position=WR").json()[0]
    assert row["ecr"] == 4          # positional, for the ordering
    assert row["ecr_overall"] == 18  # what the ECR column subtracts ADP from

    unfiltered = TestClient(app).get("/api/draft/available").json()[0]
    assert unfiltered["ecr"] == 18 and unfiltered["ecr_overall"] == 18


def _seed_run_order(picks=(("JAX", 1),)):
    """Three ranked players behind however many picks have already gone."""
    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="6813", ecr_overall=1, ecr_position=1))
        s.add(PlayerRanking(player_id="4034", ecr_overall=2, ecr_position=2))
        s.add(PlayerRanking(player_id="1234", ecr_overall=3, ecr_position=1))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        for player_id, pick_no in picks:
            s.add(DraftPick(draft_id="d1", pick_no=pick_no, player_id=player_id))
        s.commit()


def test_expected_pick_is_the_run_order_offset_by_picks_made(db, settings):
    """Best available goes next, not first: one pick is already gone."""
    from app.main import app

    _seed_run_order()

    body = TestClient(app).get("/api/draft/available").json()
    assert [p["full_name"] for p in body] == [
        "Jonathan Taylor", "Christian McCaffrey", "A.J. Brown",
    ]
    assert [p["expected_pick"] for p in body] == [2, 3, 4]


def test_expected_pick_counts_the_players_a_filter_hides(db, settings):
    """The whole point of computing this server-side.

    A.J. Brown is the first WR on the board but the *third* player, so he is
    projected at pick 4, not pick 2. A WR tab cannot see the two running backs
    ahead of him, so it could never work this out for itself — and a marker
    placed from the filtered list alone would sit two rows too high.
    """
    from app.main import app

    _seed_run_order()

    wrs = TestClient(app).get("/api/draft/available?position=WR").json()
    assert [p["full_name"] for p in wrs] == ["A.J. Brown"]
    assert wrs[0]["expected_pick"] == 4

    rbs = TestClient(app).get("/api/draft/available?position=RB").json()
    assert [p["expected_pick"] for p in rbs] == [2, 3]


def test_expected_pick_advances_as_the_draft_goes_on(db, settings):
    """Nothing is re-selected when a pick lands; the offset just moves."""
    from app.main import app

    _seed_run_order(picks=(("JAX", 1), ("6813", 2)))

    body = TestClient(app).get("/api/draft/available").json()
    # Taylor is off the board, so McCaffrey is now up at pick 3.
    assert [p["full_name"] for p in body] == ["Christian McCaffrey", "A.J. Brown"]
    assert [p["expected_pick"] for p in body] == [3, 4]


def test_expected_pick_follows_the_adp_ordering_too(db, settings):
    """Each ordering projects its own run — the room's, not the experts'."""
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="6813", ecr_overall=1))
        s.add(PlayerRanking(player_id="1234", ecr_overall=3))
        s.add(PlayerAdp(player_id="1234", adp=8.0, source="sleeper-web"))
        s.add(PlayerAdp(player_id="6813", adp=20.0, source="sleeper-web"))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()

    body = TestClient(app).get("/api/draft/available?sort=adp").json()
    assert [p["full_name"] for p in body] == ["A.J. Brown", "Jonathan Taylor"]
    # Brown is third by consensus but first in the market, so the market's run
    # order has him going next.
    assert [p["expected_pick"] for p in body] == [1, 2]


def test_keepers_are_stepped_over_not_counted(db, settings):
    """The state this league's board is actually in before draft day.

    A keeper is written in at the pick it cost — here pick 3, with nothing
    drafted yet — so the highest pick number on the board is 3 while the next
    pick to be made is 1. Counting up from the last pick number would project
    the best available at 4. And because nobody spends pick 3, the run order
    steps over it: the second-best player goes at 4, not 3.
    """
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="6813", ecr_overall=1))
        s.add(PlayerRanking(player_id="4034", ecr_overall=2))
        s.add(PlayerRanking(player_id="1234", ecr_overall=3))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, rounds=2, teams=2, raw={}))
        s.add(DraftPick(draft_id="d1", pick_no=3, player_id="JAX", is_keeper=True))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    assert [p["expected_pick"] for p in body] == [1, 2, 4]


def test_nobody_is_projected_past_the_last_pick(db, settings):
    """A two-round, two-team draft has four picks and no fifth."""
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(PlayerRanking(player_id="6813", ecr_overall=1))
        s.add(PlayerRanking(player_id="4034", ecr_overall=2))
        s.add(PlayerRanking(player_id="1234", ecr_overall=3))
        s.add(PlayerRanking(player_id="JAX", ecr_overall=4))
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, rounds=2, teams=2, raw={}))
        s.add(DraftPick(draft_id="d1", pick_no=1, player_id="0000"))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    # Picks 2, 3 and 4 are open; the fourth-ranked player has nowhere to go.
    assert [p["expected_pick"] for p in body] == [2, 3, 4, None]


def test_the_unranked_fallback_projects_nothing(db, settings):
    """No run order, no projection — an invented one would place markers."""
    from app.main import app

    _seed_pool()
    with session_scope() as s:
        s.add(Draft(draft_id="d1", league_id=LEAGUE_ID, raw={}))
        s.commit()

    body = TestClient(app).get("/api/draft/available").json()
    assert all(p["expected_pick"] is None for p in body)


# --- ADP -------------------------------------------------------------------


@respx.mock
def test_adp_keeps_both_the_average_and_the_ordinal(session, fp_settings):
    """"Went at 5.6 on average" and "was 5th off the board" are different facts.

    FantasyPros puts the average in rank_ave and the ordinal in rank_ecr. The
    board's pick-vs-ADP maths wants the average, so keeping only the ordinal
    would be a silently worse answer.
    """
    _seed_pool()
    _mock([_entry("Christian McCaffrey", "RB", 5, yahoo="30123", rank_ave="5.60",
                  rank_min="1", rank_max="14", rank_std="3.20")])

    with FantasyProsClient(fp_settings) as client:
        assert sync_adp(session, client, SEASON) == 1
    session.commit()

    row = session.get(PlayerAdp, "4034")
    assert row.adp == 5.60
    assert row.adp_rank == 5
    assert (row.adp_min, row.adp_max, row.adp_std) == (1, 14, 3.20)
    # Recorded so the cross-platform caveat lives in the data, not only in a
    # comment: this is an average over several sources, not Sleeper alone.
    assert row.sources == 107


@respx.mock
def test_adp_sync_replaces_and_leaves_rankings_alone(session, fp_settings):
    """Separate tables: syncing one must not empty the other."""
    _seed_pool()
    _mock([_entry("Christian McCaffrey", "RB", 1, yahoo="30123")])
    with FantasyProsClient(fp_settings) as client:
        sync_rankings_api(session, client, SEASON)
        session.commit()
        assert session.get(PlayerRanking, "4034") is not None

        sync_adp(session, client, SEASON)
        session.commit()

    assert session.get(PlayerAdp, "4034") is not None
    assert session.get(PlayerRanking, "4034") is not None

"""Database models.

Every table keeps a `raw` column holding the latest Sleeper payload verbatim.
When a future feature needs a field we didn't model yet, it can be backfilled
from stored data instead of re-fetching (which for historical data is often
impossible).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UtcDateTime, utcnow


class League(Base):
    __tablename__ = "league"

    league_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    season: Mapped[str | None] = mapped_column(String)
    season_type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    total_rosters: Mapped[int | None] = mapped_column(Integer)
    # Sleeper's season-chain pointer. Storing it now is what makes a future
    # multi-season history feature possible without re-deriving the chain.
    previous_league_id: Mapped[str | None] = mapped_column(String)
    avatar: Mapped[str | None] = mapped_column(String)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class LeagueUser(Base):
    __tablename__ = "league_user"

    # Composite PK, not a bare user_id: the same person recurs across seasons,
    # each of which is a distinct league_id.
    league_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)

    username: Mapped[str | None] = mapped_column(String)
    display_name: Mapped[str | None] = mapped_column(String)
    # Sleeper exposes two avatars: a top-level id that is often a shared
    # default, and metadata.avatar, a full URL for a user-uploaded image.
    # Prefer the latter; the id alone renders several members identically.
    avatar: Mapped[str | None] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String)
    team_name: Mapped[str | None] = mapped_column(String)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Members who leave are kept, not deleted: last_seen_at records when they
    # were last present in the league.
    first_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class Roster(Base):
    """One fantasy team: who owns it, who is on it, and how it is doing.

    Keyed by (league_id, roster_id) rather than roster_id alone. Sleeper numbers
    rosters from 1 within a league, so the id is only unique beside the league —
    and this database already holds two leagues, the redraft one this started as
    and the dynasty one that replaced it.

    The four player lists are stored exactly as Sleeper sends them, as arrays of
    player ids:

    * `players` is everyone controlled — starters, bench, taxi and IR included.
    * `starters` is **positional**: it lines up index-for-index with the
      league's non-bench `roster_positions`, and an empty slot is the string
      "0". Sorting it would destroy the only thing it says.
    * `taxi` and `reserve` are subsets of `players`, and neither counts against
      the 23-man active limit (rulebook §2.2).

    The bench is therefore not stored, because Sleeper does not send one: it is
    `players` minus the other three, which the API derives per request.
    """

    __tablename__ = "roster"

    league_id: Mapped[str] = mapped_column(String, primary_key=True)
    roster_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Null for an unclaimed team — a real state in a league still filling up.
    owner_id: Mapped[str | None] = mapped_column(String, index=True)
    co_owners: Mapped[list[str] | None] = mapped_column(JSON)

    players: Mapped[list[str] | None] = mapped_column(JSON)
    starters: Mapped[list[str] | None] = mapped_column(JSON)
    taxi: Mapped[list[str] | None] = mapped_column(JSON)
    reserve: Mapped[list[str] | None] = mapped_column(JSON)

    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    ties: Mapped[int] = mapped_column(Integer, default=0)

    # Sleeper splits points into whole and decimal halves (fpts 1234 +
    # fpts_decimal 56). They are recombined on the way in: two integers that
    # only mean anything together are not two facts.
    fpts: Mapped[float | None] = mapped_column(Float)
    fpts_against: Mapped[float | None] = mapped_column(Float)

    waiver_budget_used: Mapped[int | None] = mapped_column(Integer)
    waiver_position: Mapped[int | None] = mapped_column(Integer)
    total_moves: Mapped[int | None] = mapped_column(Integer)

    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class TradedPick(Base):
    """One future rookie pick that has changed hands.

    Sleeper stores only the exceptions: a pick nobody has traded never appears
    here, because every roster owns its own picks by default. The API turns the
    two into holdings.

    Keyed by the pick itself — season, round, and the roster it *originally*
    belongs to — which is what identifies "the 2027 3rd" no matter how many
    times it moves. `owner_id` is who holds it now; both it and `roster_id` are
    roster ids, not user ids.
    """

    __tablename__ = "traded_pick"

    league_id: Mapped[str] = mapped_column(String, primary_key=True)
    season: Mapped[str] = mapped_column(String, primary_key=True)
    round: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The roster the pick was originally issued to.
    roster_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: The roster holding it now, and the one before that.
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    previous_owner_id: Mapped[int | None] = mapped_column(Integer)

    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class Draft(Base):
    """The league's draft, one row per draft.

    Sleeper exposes the board as two things: the draft object (order, rounds,
    status) and a flat list of picks. Both are stored; the grid is assembled at
    the API boundary rather than in the browser.
    """

    __tablename__ = "draft"

    draft_id: Mapped[str] = mapped_column(String, primary_key=True)
    league_id: Mapped[str] = mapped_column(String, index=True)

    status: Mapped[str | None] = mapped_column(String)
    type: Mapped[str | None] = mapped_column(String)  # snake / linear / auction
    season: Mapped[str | None] = mapped_column(String)
    rounds: Mapped[int | None] = mapped_column(Integer)
    teams: Mapped[int | None] = mapped_column(Integer)

    start_time: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    last_picked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)

    # user_id -> draft slot (1..teams), and slot -> roster_id. Kept as JSON
    # because they are Sleeper's own shape and are only ever read whole.
    draft_order: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    slot_to_roster_id: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class DraftPick(Base):
    """One made pick. Absent picks are absent rows — the board's empty cells
    are computed from round/slot, never stored as placeholder rows."""

    __tablename__ = "draft_pick"

    draft_id: Mapped[str] = mapped_column(String, primary_key=True)
    # pick_no is Sleeper's overall pick number and is unique within a draft,
    # which makes it the natural second half of the key.
    pick_no: Mapped[int] = mapped_column(Integer, primary_key=True)

    round: Mapped[int | None] = mapped_column(Integer)
    draft_slot: Mapped[int | None] = mapped_column(Integer)
    roster_id: Mapped[int | None] = mapped_column(Integer)
    picked_by: Mapped[str | None] = mapped_column(String)

    player_id: Mapped[str | None] = mapped_column(String)
    # Flattened out of metadata so the board needs no player database: Sleeper
    # ships the name, position and NFL team on the pick itself.
    first_name: Mapped[str | None] = mapped_column(String)
    last_name: Mapped[str | None] = mapped_column(String)
    position: Mapped[str | None] = mapped_column(String)
    nfl_team: Mapped[str | None] = mapped_column(String)
    is_keeper: Mapped[bool] = mapped_column(Boolean, default=False)

    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class Player(Base):
    """The NFL player pool, for "best available".

    Deliberately the one table with **no `raw` column**. Everywhere else the raw
    payload exists so a new field can be backfilled without re-fetching, which
    for historical data is often impossible. Players are the opposite: the
    endpoint always returns the current truth, it is re-fetched daily anyway,
    and a yesterday's-copy of a player row is worth nothing. Storing raw for
    ~1,900 players would add ~2.6 MB to the database and to all fourteen
    backups to preserve data nobody would ever read.

    The table is a snapshot, not a history: each sync replaces it wholesale.
    """

    __tablename__ = "player"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)

    full_name: Mapped[str | None] = mapped_column(String)
    first_name: Mapped[str | None] = mapped_column(String)
    last_name: Mapped[str | None] = mapped_column(String)
    position: Mapped[str | None] = mapped_column(String, index=True)
    team: Mapped[str | None] = mapped_column(String)

    status: Mapped[str | None] = mapped_column(String)
    injury_status: Mapped[str | None] = mapped_column(String)
    years_exp: Mapped[int | None] = mapped_column(Integer)
    age: Mapped[int | None] = mapped_column(Integer)

    # Sleeper's own overall ranking — the only ordering the players endpoint
    # offers, and a decent ADP proxy. NULL for team defenses, which have no
    # rank at all; those sort last.
    search_rank: Mapped[int | None] = mapped_column(Integer, index=True)
    fantasy_positions: Mapped[list[str] | None] = mapped_column(JSON)

    # Sleeper's cross-reference to Yahoo. Kept because it is the only shared
    # key with FantasyPros, whose rankings carry player_yahoo_id.
    yahoo_id: Mapped[str | None] = mapped_column(String, index=True)

    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class DynastyRanking(Base):
    """One source's opinion of one player, one row per (source, player).

    The table the draft board is actually read from. Four sources sit in it at
    once — three published dynasty rankings and the Sleeper market — which is
    why `source` is half the primary key rather than a label on a
    single-source snapshot. Every tab in the UI is one value of that column,
    and the aggregate is an average across `CONSENSUS_SOURCES`, computed at
    query time and never stored: a stored average is a number that goes wrong
    quietly the moment one source is re-imported.

    A snapshot per source, with no history. Each import replaces only its own
    rows, so re-importing one export cannot disturb the other three — the
    property the old single-table-per-feed design could not offer, and the
    reason this one is keyed the way it is.

    Columns are deliberately a union of what the four exports carry, and most
    are NULL for most sources. That is the honest shape: `rank_std` exists
    only because FantasyPros polls several experts, and `value` only because
    FantasyCalc and TheTradeCalc price players rather than order them. Forcing
    them into a shared abstraction would mean inventing numbers the exports do
    not contain.

    `overall_rank` is the one column every source fills, and the only one the
    aggregate reads. For Sleeper ADP it is the draft slot itself.
    """

    __tablename__ = "dynasty_ranking"

    # "fantasypros" | "fantasycalc" | "thetradecalc" | "sleeper-adp",
    # from app.dynasty. Half the key, not an attribute: two sources ranking the
    # same player is the normal case, not a conflict.
    source: Mapped[str] = mapped_column(String, primary_key=True)
    player_id: Mapped[str] = mapped_column(String, primary_key=True)

    # Where this source puts him overall. Indexed with the source because
    # every read is "this source's board, in order".
    overall_rank: Mapped[int] = mapped_column(Integer, index=True)
    position_rank: Mapped[int | None] = mapped_column(Integer)
    # The published label ("WR12"), kept as written. It answers "how good is
    # he", not "how many are left", and is never recomputed against who is
    # still on the board.
    pos_rank: Mapped[str | None] = mapped_column(String)

    tier: Mapped[int | None] = mapped_column(Integer)
    # Trade value on the source's own scale — FantasyCalc's and TheTradeCalc's
    # are different currencies and are not comparable with each other. Useful
    # within one board for reading the size of the gap between two players,
    # which a rank cannot show.
    value: Mapped[float | None] = mapped_column(Float)
    # The export's own age. Stored rather than read off `player`, because in
    # dynasty age *is* the valuation and each source prices it as of the day
    # it published; Sleeper's current age would silently redate the ranking.
    age: Mapped[float | None] = mapped_column(Float)

    # The spread of expert opinion. FantasyPros only.
    rank_min: Mapped[int | None] = mapped_column(Integer)
    rank_max: Mapped[int | None] = mapped_column(Integer)
    rank_ave: Mapped[float | None] = mapped_column(Float)
    rank_std: Mapped[float | None] = mapped_column(Float)

    # The name as the export wrote it, for debugging a bad match. The only
    # thing that tells you *why* a row points at the wrong player.
    source_name: Mapped[str | None] = mapped_column(String)

    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class PlayerProjection(Base):
    """Vegas-derived season fantasy points, one row per matched Sleeper player.

    Its own table for the reason `player_adp` is its own table: it comes from a
    different source with different coverage, and a sync of one must not wipe
    the other. Each import replaces the whole table — a snapshot, no history.

    The source is a file the user exports by hand, so there is no timer and no
    network call behind this; `ffd sync-vegas <path>` is the only writer.

    Team and injury status arrive in the export and are deliberately **not**
    stored. Sleeper already carries both on `player`, refreshed daily, and a
    second copy that goes stale between hand-run imports would be the one
    someone reads by accident.
    """

    __tablename__ = "player_projection"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)

    # Projected fantasy points for the season, at the export's scoring.
    points: Mapped[float | None] = mapped_column(Float, index=True)
    # The export's own rank within the position. Not FantasyPros' `pos_rank`:
    # this one ranks by projected points, so the two disagree, which is the
    # interesting part rather than a reason to drop it.
    pos_rank: Mapped[int | None] = mapped_column(Integer)
    # "High Floor" / "Balanced" / "Volatile", where the export gives one.
    tag: Mapped[str | None] = mapped_column(String)

    # Only "vegas-csv" today. Stored anyway, so that if a second projection
    # source ever lands here the column's meaning stays answerable.
    source: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)

    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String, default="running")
    error: Mapped[str | None] = mapped_column(Text)
    records_written: Mapped[int] = mapped_column(Integer, default=0)


class NflPlayer(Base):
    """A player who has appeared on an NFL active roster, from nflverse.

    Deliberately separate from `player`, which is the *Sleeper* pool: that
    table is a fantasy-filtered snapshot of who is rostered right now (~1,900
    rows, replaced wholesale every sync), and it has no memory. This one is a
    history — ~5,900 players over ten seasons, including the long snappers and
    guards Sleeper never lists — and it must not be wiped by a player sync.

    Keyed on gsis_id, the NFL's own identifier, which is present on every
    active-roster row in the window. `sleeper_id` is carried across so the two
    tables can be joined where they overlap; it is NULL for the ~35% of NFL
    players Sleeper has no fantasy reason to track.
    """

    __tablename__ = "nfl_player"

    gsis_id: Mapped[str] = mapped_column(String, primary_key=True)

    full_name: Mapped[str | None] = mapped_column(String)
    first_name: Mapped[str | None] = mapped_column(String)
    last_name: Mapped[str | None] = mapped_column(String)

    # Normalised forms, stored rather than computed per query: a name lookup
    # has to match "A.J. Brown" against "aj brown" for every submitted name,
    # and an indexed column does that as a lookup instead of a table scan.
    name_key: Mapped[str] = mapped_column(String, index=True, default="")
    last_name_key: Mapped[str] = mapped_column(String, index=True, default="")

    position: Mapped[str | None] = mapped_column(String, index=True)
    # Kept as the source's own YYYY-MM-DD string. It is an identity attribute
    # used to tell two same-named players apart, never arithmetic, and parsing
    # it would only invent a failure mode for the rows where it is blank.
    birth_date: Mapped[str | None] = mapped_column(String)
    college: Mapped[str | None] = mapped_column(String)

    entry_year: Mapped[int | None] = mapped_column(Integer)
    rookie_year: Mapped[int | None] = mapped_column(Integer)
    draft_club: Mapped[str | None] = mapped_column(String)
    draft_number: Mapped[int | None] = mapped_column(Integer)

    # The span this database knows about, not the player's real career: a
    # 2015 retiree shows first_season 2017 because that is where the window
    # starts. Denormalised from nfl_stint because every result line prints it.
    first_season: Mapped[int | None] = mapped_column(Integer)
    last_season: Mapped[int | None] = mapped_column(Integer, index=True)

    sleeper_id: Mapped[str | None] = mapped_column(String, index=True)
    espn_id: Mapped[str | None] = mapped_column(String)
    pfr_id: Mapped[str | None] = mapped_column(String)
    headshot_url: Mapped[str | None] = mapped_column(String)

    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class NflStint(Base):
    """One player's active-roster time with one franchise in one season.

    The weekly source is 246,663 active rows over ten seasons; collapsing each
    player-team-season to a single row with a week range makes it 22,506, which
    is both the shape the question needs ("did X ever play for this team") and
    small enough to answer without an index scan mattering.

    `franchise` is the canonical code and is what queries filter on; `team` is
    the abbreviation the source used that season. Both, because a relocation
    makes them disagree and each answer is the right one somewhere: a 2018
    Raider is franchise LV (so a search for the Raiders finds him) and team OAK
    (so the result does not claim Las Vegas had a team in 2018).

    A player who leaves a team and returns within the same season collapses to
    one row spanning both spells — `weeks` is the true count of weeks active,
    so it stays honest where first_week/last_week cannot.
    """

    __tablename__ = "nfl_stint"

    gsis_id: Mapped[str] = mapped_column(String, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    franchise: Mapped[str] = mapped_column(String, primary_key=True, index=True)

    team: Mapped[str | None] = mapped_column(String)
    position: Mapped[str | None] = mapped_column(String)
    jersey_number: Mapped[int | None] = mapped_column(Integer)

    first_week: Mapped[int | None] = mapped_column(Integer)
    last_week: Mapped[int | None] = mapped_column(Integer)
    weeks: Mapped[int] = mapped_column(Integer, default=0)

    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)

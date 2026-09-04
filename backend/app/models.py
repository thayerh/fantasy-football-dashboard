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


class PlayerRanking(Base):
    """FantasyPros consensus rankings, one row per matched Sleeper player.

    Like `player`, a snapshot with no `raw` column and no history: each sync
    replaces it. Unlike `player`, it is *small* — the free FantasyPros tier
    returns ten players per call, so a sync of seven calls stores at most
    seventy rows.

    Two ranks, because the API gives two different things depending on how it
    is asked. `ecr_overall` comes from the position=ALL call and is a true
    overall rank. `ecr_position` comes from the per-position call, where
    rank_ecr is scoped to that position (the top QB is 1). The available-players
    query picks whichever matches the filter in play.
    """

    __tablename__ = "player_ranking"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)

    ecr_overall: Mapped[int | None] = mapped_column(Integer, index=True)
    ecr_position: Mapped[int | None] = mapped_column(Integer, index=True)
    pos_rank: Mapped[str | None] = mapped_column(String)
    tier: Mapped[int | None] = mapped_column(Integer)
    bye_week: Mapped[int | None] = mapped_column(Integer)

    # What the ranking actually is, so the UI never has to guess or hardcode it.
    scoring: Mapped[str | None] = mapped_column(String)
    ranking_type: Mapped[str | None] = mapped_column(String)
    experts: Mapped[int | None] = mapped_column(Integer)

    # The spread of expert opinion, which the JSON API does not return but the
    # cheat sheet does. A tight range is a consensus; a wide one is a coin flip,
    # and that difference is worth more on draft night than the rank itself.
    rank_min: Mapped[int | None] = mapped_column(Integer)
    rank_max: Mapped[int | None] = mapped_column(Integer)
    rank_ave: Mapped[float | None] = mapped_column(Float)
    rank_std: Mapped[float | None] = mapped_column(Float)

    # "web" (cheat sheet) or "api". Which one produced a row matters when the
    # numbers look wrong, because the two disagree in coverage.
    source: Mapped[str | None] = mapped_column(String)

    # FantasyPros' own identifiers, kept for debugging a bad match.
    fp_player_id: Mapped[str | None] = mapped_column(String)
    fp_name: Mapped[str | None] = mapped_column(String)

    published_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class PlayerAdp(Base):
    """Average draft position, one row per matched Sleeper player.

    A separate table from `player_ranking` on purpose: the two come from
    different calls with different coverage, and a sync of one must not wipe
    the other. Like the rankings, a snapshot with no history.

    **Which ADP this is depends on `source`, and they are not interchangeable.**
    Both syncs replace the whole table, so it never holds a mix:

    - `sleeper-web` — Sleeper's own ADP, scraped from the rendered real-time
      ADP page. ~270 players. This is the default and what the UI shows, because
      it is the market this league actually drafts in.
    - `fp-api` — FantasyPros' cross-platform average (five platforms, Sleeper
      among them), from the JSON API. ~60 players, because the free tier caps
      every response at ten. The fallback for when the page changes shape.

    Storing both in one `adp` column is only safe because of that replacement:
    a column meaning two different things at once would be a bug, so a partial
    sync must never be allowed to leave halves of each behind.
    """

    __tablename__ = "player_adp"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)

    # The average draft position itself (5.60), and its ordinal (5).
    adp: Mapped[float | None] = mapped_column(Float, index=True)
    adp_rank: Mapped[int | None] = mapped_column(Integer)

    adp_min: Mapped[int | None] = mapped_column(Integer)
    adp_max: Mapped[int | None] = mapped_column(Integer)
    adp_std: Mapped[float | None] = mapped_column(Float)

    scoring: Mapped[str | None] = mapped_column(String)
    sources: Mapped[int | None] = mapped_column(Integer)

    # "sleeper-web" or "fp-api". Which one produced a row decides what the
    # number means, so it is stored rather than inferred from the row count.
    source: Mapped[str | None] = mapped_column(String)

    fp_player_id: Mapped[str | None] = mapped_column(String)
    fp_name: Mapped[str | None] = mapped_column(String)

    published_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
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

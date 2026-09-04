"""Response models.

The API returns these, never ORM objects, so the `raw` payload column cannot
leak to the client.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class LeagueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    league_id: str
    name: str | None
    season: str | None
    season_type: str | None
    status: str | None
    total_rosters: int | None
    avatar: str | None
    fetched_at: dt.datetime


class LeagueUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    username: str | None
    display_name: str | None
    avatar: str | None
    avatar_url: str | None
    team_name: str | None
    is_owner: bool
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime


class DraftSlotOut(BaseModel):
    """One column of the board: which member drafts from this slot."""

    slot: int
    user_id: str | None
    display_name: str | None
    team_name: str | None
    avatar_url: str | None
    roster_id: int | None


class DraftPickOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pick_no: int
    round: int | None
    draft_slot: int | None
    picked_by: str | None
    player_id: str | None
    first_name: str | None
    last_name: str | None
    position: str | None
    nfl_team: str | None
    is_keeper: bool
    # What the pick cost against the market. `adp` is Sleeper's own average
    # draft position; `ecr` is FantasyPros' consensus rank. Both nullable:
    # coverage stops at roughly the top 270 players.
    adp: float | None = None
    ecr: int | None = None


class DraftOut(BaseModel):
    """The board, assembled. Slots are resolved to members here rather than in
    the browser, so the client only has to place picks on a grid."""

    draft_id: str
    status: str | None
    type: str | None
    season: str | None
    rounds: int | None
    teams: int | None
    start_time: dt.datetime | None
    last_picked_at: dt.datetime | None
    fetched_at: dt.datetime
    slots: list[DraftSlotOut]
    picks: list[DraftPickOut]


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: str
    full_name: str | None
    position: str | None
    team: str | None
    injury_status: str | None
    years_exp: int | None
    age: int | None
    search_rank: int | None
    # FantasyPros, when we have it. `ecr` is the rank that matches the query:
    # overall for an unfiltered list, positional when filtered by position.
    ecr: int | None = None
    # The *overall* rank, always, even when `ecr` above holds the positional
    # one. The best-available ECR column measures ADP against this, and a
    # positional rank cannot stand in: "an ADP of 22 minus QB1" would read as
    # +21 and mean nothing.
    ecr_overall: int | None = None
    tier: int | None = None
    bye_week: int | None = None
    pos_rank: str | None = None
    # The spread of expert opinion behind the consensus.
    rank_min: int | None = None
    rank_max: int | None = None
    # Sleeper's own average draft position — the market this league drafts in.
    adp: float | None = None
    # The overall pick number this player is projected to come off the board
    # at: his place in the run order, mapped onto the picks still unspent.
    # Computed here rather than in the browser because a position-filtered list
    # cannot see how many players of other positions go ahead of it — and that
    # count is exactly what decides where a pick lands. None when nothing can
    # be ranked and the list has fallen back to Sleeper's own order, or when he
    # ranks past the last pick of the draft.
    expected_pick: int | None = None
    # Vegas-derived projected fantasy points for the season, from the imported
    # export. Null for the players it does not cover — every kicker and defense,
    # and the deep end of the bench.
    vegas_points: float | None = None


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: dt.datetime
    finished_at: dt.datetime | None
    status: str
    error: str | None
    records_written: int


class HealthOut(BaseModel):
    status: str
    db_ok: bool
    last_sync_at: dt.datetime | None
    last_sync_status: str | None
    stale: bool

"""Response models.

The API returns these, never ORM objects, so the `raw` payload column cannot
leak to the client.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


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


class RosterPlayerOut(BaseModel):
    """A player as he appears on somebody's roster.

    A deliberately shorter row than `PlayerOut`: the draft table exists to
    compare five boards against each other, while a roster answers "what do I
    have", so it carries one market number (the aggregate) rather than five.
    """

    model_config = ConfigDict(from_attributes=True)

    player_id: str
    full_name: str | None
    position: str | None
    team: str | None
    injury_status: str | None
    years_exp: int | None
    age: int | None

    # The mean of the dynasty boards' ranks and how many produced it, on the
    # same terms as the draft board's AGG column — see `PlayerOut.consensus`.
    consensus: float | None = None
    consensus_sources: int = 0
    # Vegas-derived projected points for the season. Null past the depth the
    # export reaches, which on a 24-man roster is a handful of players.
    vegas_points: float | None = None


class RosterPickOut(BaseModel):
    """A future rookie pick, from one team's point of view.

    `roster_id` is always *the other end of the pick* — for one a team holds it
    is the roster the pick was originally issued to (its own, for a pick never
    traded), and for one a team has traded away it is the roster holding it
    now. The client already has every team in the same payload, so the name is
    resolved there rather than repeated on each of a dozen picks.
    """

    season: str
    round: int
    roster_id: int


class RosterOut(BaseModel):
    """One team, arranged the way it is actually looked at.

    The four lists are disjoint and together they are the whole roster.
    `starters` is padded to the length of the league's lineup and holds a null
    wherever a slot is unfilled, so index *i* is always the lineup slot *i* —
    an owner who has not set a lineup has ten nulls, not a shorter list.
    """

    roster_id: int
    owner_id: str | None
    display_name: str | None
    team_name: str | None
    avatar_url: str | None
    avatar: str | None
    is_owner: bool = False

    wins: int
    losses: int
    ties: int
    fpts: float | None
    fpts_against: float | None
    waiver_budget_used: int | None
    waiver_position: int | None

    starters: list[RosterPlayerOut | None]
    bench: list[RosterPlayerOut]
    taxi: list[RosterPlayerOut]
    reserve: list[RosterPlayerOut]

    # The other half of what a dynasty team owns. `picks` is what it holds in
    # the tradeable window, its own and anyone else's; `picks_traded` is its
    # own picks that are now somebody else's — sent because a roster reads
    # differently when you can see what has already been spent off it.
    picks: list[RosterPickOut]
    picks_traded: list[RosterPickOut]


class RosterLimitsOut(BaseModel):
    """What a legal roster looks like, from the league's own settings.

    Read from Sleeper rather than from the rulebook: if the two ever disagree,
    the platform is what actually enforces the limit, and the page should show
    the number that will bite.
    """

    starters: int
    bench: int
    active: int
    taxi: int
    reserve: int


class RostersOut(BaseModel):
    """Every team in the league, plus the lineup they are all filling.

    `lineup` is the league's starting slots in order — QB, RB, RB, WR, WR, WR,
    TE, FLEX, FLEX, SUPER_FLEX here — and it is what makes `RosterOut.starters`
    readable, since that list is positional against it.
    """

    league_id: str
    season: str | None
    lineup: list[str]
    limits: RosterLimitsOut
    # The rookie drafts whose picks are tradeable right now, earliest first,
    # and how many rounds each of them runs.
    pick_seasons: list[str]
    pick_rounds: int
    # What each team's `waiver_budget_used` is spent against. The league's
    # setting rather than the rulebook's $100 (§7.1), for the reason the limits
    # come from Sleeper: the platform is what enforces the number.
    waiver_budget: int | None
    fetched_at: dt.datetime
    teams: list[RosterOut]


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
    # What the pick cost against the market. `adp` is where Sleeper drafters
    # actually took him; `consensus` is the aggregate of the dynasty boards.
    # Both nullable: no board covers every rostered player.
    adp: int | None = None
    consensus: float | None = None


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
    """One available player, with every board's opinion of him at once.

    `ranks` is the whole point: a dict keyed by source, so adding a fifth board
    changes no schema and no client type. The tab in play decides the ordering,
    not what is returned — every row carries every source, which is what lets
    the table show where the boards disagree instead of only the one you asked
    for.
    """

    model_config = ConfigDict(from_attributes=True)

    player_id: str
    full_name: str | None
    position: str | None
    team: str | None
    injury_status: str | None
    years_exp: int | None
    age: int | None
    search_rank: int | None

    # source -> that board's overall rank. Sources with no opinion of him are
    # absent rather than present-and-null, so a client can count the keys to
    # get "ranked by 2 of 3" without filtering.
    ranks: dict[str, int] = Field(default_factory=dict)
    # The mean of the consensus boards' ranks, and how many of them ranked him.
    # Computed per request, never stored: an average of four snapshots taken on
    # different days goes wrong quietly the moment one of them is re-imported.
    consensus: float | None = None
    consensus_sources: int = 0

    # From whichever board is in play, so they change with the tab. Tier and
    # the expert spread come from FantasyPros; value from FantasyCalc and
    # TheTradeCalc, on their own scales and not comparable across the two.
    tier: int | None = None
    pos_rank: str | None = None
    value: float | None = None
    rank_min: int | None = None
    rank_max: int | None = None
    # The source's own age for him, which in dynasty is most of the valuation.
    ranked_age: float | None = None

    # The overall pick number this player is projected to come off the board
    # at: his place in the run order, mapped onto the picks still unspent.
    # Computed here rather than in the browser because a position-filtered list
    # cannot see how many players of other positions go ahead of it — and that
    # count is exactly what decides where a pick lands. None when nothing can
    # be ranked and the list has fallen back to Sleeper's own order, or when he
    # ranks past the last pick of the draft.
    expected_pick: int | None = None
    # Vegas-derived projected fantasy points for the season, from the imported
    # export. Null for the players it does not cover — the deep end of the
    # bench. A season quantity, not a dynasty valuation: it says how good he is
    # this year, which is the one thing the dynasty boards deliberately do not.
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

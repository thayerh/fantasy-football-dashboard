"""The league's twelve fantasy rosters.

Not to be confused with `app.rosters`, which answers a different question
entirely — that module reads the historical *NFL* roster tables. This one is
the fantasy league's own teams.

Assembled server-side for the same reason the draft board is: Sleeper returns
four flat lists of player ids per team and nothing else, and turning those into
"who starts where" needs the league's lineup, its members and the player pool
anyway. The browser receives rows it can render.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.draft import consensus_ranks
from app.config import get_settings
from app.db import DbSession
from app.models import League, LeagueUser, Player, PlayerProjection, Roster, TradedPick
from app.schemas import (
    RosterLimitsOut,
    RosterOut,
    RosterPickOut,
    RosterPlayerOut,
    RostersOut,
)

router = APIRouter(tags=["rosters"])

#: Slots in `roster_positions` that are not part of the starting lineup.
#: Sleeper lists the bench there, and some leagues list IR and taxi too; this
#: one keeps those in `settings` instead, so the set is defensive rather than
#: load-bearing.
NON_STARTING = frozenset({"BN", "IR", "TAXI"})

#: What Sleeper puts in `starters` where a lineup slot is empty.
EMPTY_SLOT = "0"

#: Rounds in the rookie draft (`rulebook/06-rookie-draft.md` §6.1).
#:
#: A constant, unlike the roster limits, because there is nothing to read: the
#: draft object for a rookie draft does not exist on Sleeper until the
#: commissioner creates it in the spring, and these picks are traded years
#: before that. The rulebook is the only source there is.
ROOKIE_ROUNDS = 4

#: How many rookie drafts' picks are tradeable at once — "the current league
#: year plus the next two" (`rulebook/08-trades.md` §8.3). Beyond that nobody
#: can have traded anything, so every team's holdings would be identical and
#: the rows would say nothing.
PICK_SEASONS = 3


def _pick_seasons(league: League) -> list[str]:
    """The rookie drafts whose picks are in play, earliest first.

    The next one is the season after the league's current one: a rookie draft
    runs in the spring, and Sleeper does not roll `season` forward until the
    league year turns over — so through the 2026 season this is 2027-2029, and
    once the league reads 2027 the 2027 draft has been held and the window has
    moved to 2028-2030, exactly as §8.3 describes it.

    Empty if the league's season is not a year, which no live league does; the
    picks panel then simply has nothing to show rather than inventing seasons.
    """
    if not (league.season or "").isdigit():
        return []
    first = int(league.season) + 1
    return [str(first + offset) for offset in range(PICK_SEASONS)]


def _pick_holdings(
    db: DbSession, league_id: str, seasons: list[str], roster_ids: list[int]
) -> tuple[dict[int, list[RosterPickOut]], dict[int, list[RosterPickOut]]]:
    """Who holds which future picks, and which of their own they have spent.

    Sleeper stores only the trades. The holdings are the default — every roster
    owns its own picks in every future draft — with that ledger applied over
    the top, so this walks every pick in the window rather than reading a list
    of them: 3 seasons x 4 rounds x 12 teams is 144 picks, and there is no
    smaller honest way to know that a team holds its own 2028 2nd.
    """
    ledger = {
        (pick.season, pick.round, pick.roster_id): pick.owner_id
        for pick in db.scalars(
            select(TradedPick).where(TradedPick.league_id == league_id)
        ).all()
    }

    held: dict[int, list[RosterPickOut]] = {roster_id: [] for roster_id in roster_ids}
    traded: dict[int, list[RosterPickOut]] = {roster_id: [] for roster_id in roster_ids}

    # Season, then round, then original owner — which is the order a pick list
    # is read in, so neither side needs to sort afterwards.
    for season in seasons:
        for round_ in range(1, ROOKIE_ROUNDS + 1):
            for origin in roster_ids:
                holder = ledger.get((season, round_, origin)) or origin
                if holder in held:
                    held[holder].append(
                        RosterPickOut(season=season, round=round_, roster_id=origin)
                    )
                if holder != origin:
                    traded[origin].append(
                        RosterPickOut(season=season, round=round_, roster_id=holder)
                    )
    return held, traded


def _people(db: DbSession, player_ids: set[str]) -> dict[str, RosterPlayerOut]:
    """Every rostered player, resolved in one query.

    Rows are keyed by id and shared between teams' lists — a player is on
    exactly one roster, so nothing is duplicated in practice, but building them
    once also means the aggregate is computed once for the whole league rather
    than twelve times.
    """
    if not player_ids:
        return {}

    consensus = consensus_ranks().subquery()
    rows = db.execute(
        select(Player, consensus.c.consensus, consensus.c.sources, PlayerProjection.points)
        .outerjoin(consensus, consensus.c.player_id == Player.player_id)
        .outerjoin(PlayerProjection, PlayerProjection.player_id == Player.player_id)
        .where(Player.player_id.in_(player_ids))
    ).all()

    return {
        player.player_id: RosterPlayerOut.model_validate(player).model_copy(
            update={
                "consensus": round(consensus_rank, 1) if consensus_rank else None,
                "consensus_sources": sources or 0,
                "vegas_points": points,
            }
        )
        for player, consensus_rank, sources, points in rows
    }


def _resolve(player_id: str, people: dict[str, RosterPlayerOut]) -> RosterPlayerOut:
    """A player row, or a bare one for somebody the pool has not heard of.

    `ffd sync-players` runs daily and drops everyone outside QB/RB/WR/TE, so a
    rostered player can legitimately be missing: signed this morning, or a
    position this league does not start. He is still on the roster, and a row
    carrying only his id is the truth about what is known — dropping him would
    silently shorten somebody's team.
    """
    known = people.get(player_id)
    if known is not None:
        return known
    return RosterPlayerOut(
        player_id=player_id,
        full_name=None,
        position=None,
        team=None,
        injury_status=None,
        years_exp=None,
        age=None,
    )


def _by_market(players: list[RosterPlayerOut]) -> list[RosterPlayerOut]:
    """Best first, by the dynasty aggregate.

    Sleeper sends `players` in whatever order it likes (roughly by id, which is
    roughly by NFL debut). Nobody reads a bench that way. Players no board
    ranks sort last rather than first, which is what a missing rank means here:
    not "unrated", but "past the depth every board stops at".
    """
    return sorted(
        players,
        key=lambda player: (
            player.consensus is None,
            player.consensus or 0,
            player.full_name or player.player_id,
        ),
    )


@router.get("/api/rosters", response_model=RostersOut)
def list_rosters(db: DbSession) -> RostersOut:
    """Every team in the league, each arranged into lineup, bench, taxi and IR.

    All four lists together are exactly the players a team controls, with no
    player in two of them and none dropped: whatever the lineup cannot place
    falls to the bench rather than disappearing off the page.
    """
    league_id = get_settings().sleeper_league_id

    league = db.get(League, league_id)
    if league is None:
        raise HTTPException(
            status_code=503,
            detail="League not ingested yet. Run `ffd sync`.",
        )

    rosters = list(
        db.scalars(select(Roster).where(Roster.league_id == league_id)).all()
    )
    if not rosters:
        raise HTTPException(
            status_code=503,
            detail="Rosters not ingested yet. Run `ffd sync`.",
        )

    members = {
        user.user_id: user
        for user in db.scalars(select(LeagueUser).where(LeagueUser.league_id == league_id)).all()
    }

    # The lineup, and the limits, come from the league's own settings rather
    # than from the rulebook: Sleeper is what actually enforces them.
    positions: list[str] = league.raw.get("roster_positions") or []
    lineup = [slot for slot in positions if slot not in NON_STARTING]
    league_settings = league.raw.get("settings") or {}
    limits = RosterLimitsOut(
        starters=len(lineup),
        bench=positions.count("BN"),
        active=len(positions),
        taxi=league_settings.get("taxi_slots") or 0,
        reserve=league_settings.get("reserve_slots") or 0,
    )

    seasons = _pick_seasons(league)
    held, traded_away = _pick_holdings(
        db, league_id, seasons, [roster.roster_id for roster in rosters]
    )

    wanted = {
        player_id
        for roster in rosters
        for group in (roster.players, roster.starters, roster.taxi, roster.reserve)
        for player_id in (group or [])
        if player_id != EMPTY_SLOT
    }
    people = _people(db, wanted)

    teams: list[RosterOut] = []
    for roster in rosters:
        user = members.get(roster.owner_id or "")

        # Positional, and padded to the lineup: index i is lineup slot i, so an
        # owner who has set no lineup gets ten empty slots rather than a short
        # list the client would have to guess about.
        raw_starters = (roster.starters or [])[: len(lineup)]
        raw_starters += [EMPTY_SLOT] * (len(lineup) - len(raw_starters))
        starters = [
            None if player_id == EMPTY_SLOT else _resolve(player_id, people)
            for player_id in raw_starters
        ]

        taxi = [_resolve(player_id, people) for player_id in (roster.taxi or [])]
        reserve = [_resolve(player_id, people) for player_id in (roster.reserve or [])]

        # The bench is the remainder, and Sleeper never sends one. Computed
        # against the starters actually shown, so a lineup that shrank under a
        # team's feet leaves its extra starters on the bench instead of nowhere.
        placed = {player.player_id for player in starters if player is not None}
        placed |= {player.player_id for player in taxi + reserve}
        bench = [
            _resolve(player_id, people)
            for player_id in (roster.players or [])
            if player_id not in placed and player_id != EMPTY_SLOT
        ]

        teams.append(
            RosterOut(
                roster_id=roster.roster_id,
                owner_id=roster.owner_id,
                display_name=user.display_name if user else None,
                team_name=user.team_name if user else None,
                avatar_url=user.avatar_url if user else None,
                avatar=user.avatar if user else None,
                is_owner=bool(user.is_owner) if user else False,
                wins=roster.wins,
                losses=roster.losses,
                ties=roster.ties,
                fpts=roster.fpts,
                fpts_against=roster.fpts_against,
                waiver_budget_used=roster.waiver_budget_used,
                waiver_position=roster.waiver_position,
                starters=starters,
                bench=_by_market(bench),
                taxi=_by_market(taxi),
                reserve=_by_market(reserve),
                picks=held[roster.roster_id],
                picks_traded=traded_away[roster.roster_id],
            )
        )

    # Alphabetical by the name the switcher shows, the same ordering /api/users
    # uses. Roster ids are Sleeper's join order and mean nothing to a reader.
    teams.sort(key=lambda team: (team.team_name or team.display_name or "").lower())

    return RostersOut(
        league_id=league_id,
        season=league.season,
        lineup=lineup,
        limits=limits,
        pick_seasons=seasons,
        pick_rounds=ROOKIE_ROUNDS,
        waiver_budget=league_settings.get("waiver_budget"),
        fetched_at=max(roster.fetched_at for roster in rosters),
        teams=teams,
    )

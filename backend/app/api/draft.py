"""Draft board route.

The board is assembled server-side: Sleeper hands back a flat pick list plus a
`draft_order` map of user_id -> slot, and turning that into columns needs the
league's member names anyway. Doing it here means the client receives something
it can render directly, and a second client (or a future rewrite) doesn't have
to re-derive the same join.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, null, select

from app.config import get_settings
from app.db import DbSession
from app.models import (
    Draft,
    DraftPick,
    LeagueUser,
    Player,
    PlayerAdp,
    PlayerProjection,
    PlayerRanking,
)
from app.schemas import DraftOut, DraftPickOut, DraftSlotOut, PlayerOut

router = APIRouter(tags=["draft"])


@router.get("/api/draft", response_model=DraftOut)
def get_draft(db: DbSession) -> DraftOut:
    league_id = get_settings().sleeper_league_id

    draft = db.scalars(
        select(Draft).where(Draft.league_id == league_id).order_by(Draft.season.desc()).limit(1)
    ).first()
    if draft is None:
        raise HTTPException(
            status_code=503,
            detail="Draft not ingested yet. Run `ffd sync-draft`.",
        )

    members = {
        user.user_id: user
        for user in db.scalars(select(LeagueUser).where(LeagueUser.league_id == league_id)).all()
    }
    slot_to_roster = {int(k): v for k, v in (draft.slot_to_roster_id or {}).items()}

    # draft_order is user_id -> slot; the board needs the inverse. It is absent
    # until the commissioner sets an order, which is a normal pre-draft state,
    # not an error — the columns are then simply unnamed.
    slots: list[DraftSlotOut] = []
    by_slot = {slot: user_id for user_id, slot in (draft.draft_order or {}).items()}
    for slot in range(1, (draft.teams or 0) + 1):
        user = members.get(by_slot.get(slot, ""))
        slots.append(
            DraftSlotOut(
                slot=slot,
                user_id=user.user_id if user else None,
                display_name=user.display_name if user else None,
                team_name=user.team_name if user else None,
                avatar_url=user.avatar_url if user else None,
                roster_id=slot_to_roster.get(slot),
            )
        )

    # Every pick carries what the market thought of the player, so the board can
    # show what each pick cost relative to ADP and to consensus rank.
    picks = db.execute(
        select(DraftPick, PlayerAdp.adp, PlayerRanking.ecr_overall)
        .outerjoin(PlayerAdp, PlayerAdp.player_id == DraftPick.player_id)
        .outerjoin(PlayerRanking, PlayerRanking.player_id == DraftPick.player_id)
        .where(DraftPick.draft_id == draft.draft_id)
        .order_by(DraftPick.pick_no)
    ).all()

    return DraftOut(
        draft_id=draft.draft_id,
        status=draft.status,
        type=draft.type,
        season=draft.season,
        rounds=draft.rounds,
        teams=draft.teams,
        start_time=draft.start_time,
        last_picked_at=draft.last_picked_at,
        fetched_at=draft.fetched_at,
        slots=slots,
        picks=[
            DraftPickOut.model_validate(pick).model_copy(update={"adp": adp, "ecr": ecr})
            for pick, adp, ecr in picks
        ],
    )


@router.get("/api/draft/available", response_model=list[PlayerOut])
def list_available(
    db: DbSession,
    position: Annotated[str | None, Query(description="QB, RB, WR, TE, K or DEF")] = None,
    sort: Annotated[
        Literal["ecr", "adp"],
        Query(description="Rank by FantasyPros consensus, or by Sleeper ADP."),
    ] = "ecr",
    limit: Annotated[int, Query(ge=1, le=300)] = 150,
) -> list[PlayerOut]:
    """Best available: the player pool minus everyone already drafted.

    Two orderings, because "best" has two defensible answers on draft night.
    `ecr` is what the experts think, `adp` is what the room will actually do,
    and the gap between them is where a draft is won — which is why the ECR
    column shows that gap under either ordering.

    Each sort admits only the players it can rank: sorting by ADP over players
    who have none would be sorting by nothing. Sleeper's pool carries every
    rostered player in the league, so without that rule a position tab would
    fill to the limit with third-stringers instead of running out honestly.
    A position filter therefore returns fewer than the overall limit, and
    `adp` returns fewer than `ecr` — ~270 players carry an ADP against ~515
    ranked.

    Ordering is always done over the **whole** pool by the overall measure, and
    a position filter subsets that run order rather than re-ranking it. Within
    a position the two give the same sequence — FantasyPros' positional rank is
    derived from the overall one — but only the pool ordering can number the
    rows, and `expected_pick` is that number turned into a pick. Without it a
    filtered tab could not say where a pick lands, because it cannot see how
    many players of other positions go ahead of it.

    `ecr` still reports the positional rank when filtered, since that is the
    rank the query asked about. `pos_rank` is the label FantasyPros published
    ("WR12") and is not recomputed against who is still available — it answers
    "how good is this player" rather than "how many are left".
    """
    league_id = get_settings().sleeper_league_id

    drafted = (
        select(DraftPick.player_id)
        .join(Draft, Draft.draft_id == DraftPick.draft_id)
        .where(Draft.league_id == league_id, DraftPick.player_id.is_not(None))
    )

    # Which pick numbers are already spoken for, and how many there are in all.
    #
    # Not "the picks made so far", and emphatically not the highest pick number
    # on the board: keepers are written in at the pick they cost, often rounds
    # ahead of the draft. This league's board holds twelve keepers as far out
    # as pick 174 with the draft yet to start, so counting up from the last
    # pick number would project the next player at 175. A keeper is also a pick
    # nobody spends, so the run order has to step over it rather than count it.
    assigned = set(
        db.scalars(
            select(DraftPick.pick_no)
            .join(Draft, Draft.draft_id == DraftPick.draft_id)
            .where(Draft.league_id == league_id)
        ).all()
    )
    shape = db.execute(
        select(Draft.rounds, Draft.teams)
        .where(Draft.league_id == league_id)
        .order_by(Draft.season.desc())
        .limit(1)
    ).first()
    total = (shape.rounds or 0) * (shape.teams or 0) if shape else 0

    if sort == "adp":
        # Ties in ADP are common — the page rounds to whole picks — so the
        # consensus breaks them rather than leaving the order arbitrary.
        ranked = PlayerAdp.adp.is_not(None)
        ordering = (
            PlayerAdp.adp,
            PlayerRanking.ecr_overall.is_(None),
            PlayerRanking.ecr_overall,
            Player.full_name,
        )
    else:
        ranked = PlayerRanking.player_id.is_not(None)
        ordering = (
            # NULLs last at each level, without relying on a dialect default.
            PlayerRanking.ecr_overall.is_(None),
            PlayerRanking.ecr_overall,
            Player.search_rank.is_(None),
            Player.search_rank,
            Player.full_name,
        )

    # The run order over the whole undrafted pool, numbered. Numbering before
    # the position filter is the point: row 1 of the QB tab still knows how
    # many players of every position are expected to go ahead of him.
    pool = (
        select(
            Player.player_id.label("player_id"),
            func.row_number().over(order_by=ordering).label("ordinal"),
        )
        .outerjoin(PlayerRanking, PlayerRanking.player_id == Player.player_id)
        .outerjoin(PlayerAdp, PlayerAdp.player_id == Player.player_id)
        .where(Player.player_id.not_in(drafted), ranked)
        .subquery()
    )

    # The projection joins here and nowhere else. It takes no part in the pool
    # subquery above, so importing or clearing it cannot shift `expected_pick`.
    query = (
        select(Player, PlayerRanking, PlayerAdp.adp, PlayerProjection.points, pool.c.ordinal)
        .join(pool, pool.c.player_id == Player.player_id)
        .outerjoin(PlayerRanking, PlayerRanking.player_id == Player.player_id)
        .outerjoin(PlayerAdp, PlayerAdp.player_id == Player.player_id)
        .outerjoin(PlayerProjection, PlayerProjection.player_id == Player.player_id)
    )
    if position:
        query = query.where(Player.position == position.upper())

    rows = db.execute(query.order_by(pool.c.ordinal).limit(limit)).all()

    # Nothing carries the chosen measure at this position (an IDP slot, or a
    # sync that has not run yet): fall back to Sleeper's own order rather than
    # showing an empty tab. There is no run order to speak of here, so the rows
    # carry no projection rather than an invented one.
    if not rows:
        fallback = (
            select(
                Player, PlayerRanking, PlayerAdp.adp, PlayerProjection.points,
                null().label("ordinal"),
            )
            .outerjoin(PlayerRanking, PlayerRanking.player_id == Player.player_id)
            .outerjoin(PlayerAdp, PlayerAdp.player_id == Player.player_id)
            .outerjoin(PlayerProjection, PlayerProjection.player_id == Player.player_id)
            .where(Player.player_id.not_in(drafted))
        )
        if position:
            fallback = fallback.where(Player.position == position.upper())
        rows = db.execute(
            fallback.order_by(
                Player.search_rank.is_(None), Player.search_rank, Player.full_name
            ).limit(limit)
        ).all()

    # The run order lands on the open picks in turn: first player available at
    # the next unspent pick, second at the one after, and so on. Past the last
    # round there is no pick left to project onto, and the row says so.
    needed = max((ordinal for *_, ordinal in rows if ordinal is not None), default=0)
    ceiling = total or needed + len(assigned)
    open_picks = [n for n in range(1, ceiling + 1) if n not in assigned][:needed]
    projected = dict(enumerate(open_picks, start=1))

    return [
        PlayerOut(
            player_id=player.player_id,
            full_name=player.full_name,
            position=player.position,
            team=player.team,
            injury_status=player.injury_status,
            years_exp=player.years_exp,
            age=player.age,
            search_rank=player.search_rank,
            ecr=(ranking.ecr_position if position else ranking.ecr_overall) if ranking else None,
            ecr_overall=ranking.ecr_overall if ranking else None,
            tier=ranking.tier if ranking else None,
            bye_week=ranking.bye_week if ranking else None,
            pos_rank=ranking.pos_rank if ranking else None,
            rank_min=ranking.rank_min if ranking else None,
            rank_max=ranking.rank_max if ranking else None,
            adp=adp,
            expected_pick=projected.get(ordinal) if ordinal is not None else None,
            vegas_points=vegas,
        )
        for player, ranking, adp, vegas, ordinal in rows
    ]

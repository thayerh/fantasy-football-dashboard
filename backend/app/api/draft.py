"""Draft board route.

The board is assembled server-side: Sleeper hands back a flat pick list plus a
`draft_order` map of user_id -> slot, and turning that into columns needs the
league's member names anyway. Doing it here means the client receives something
it can render directly, and a second client (or a future rewrite) doesn't have
to re-derive the same join.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Select, func, null, select

from app.config import get_settings
from app.db import DbSession
from app.dynasty import (
    ALL_SOURCES,
    CONSENSUS_SOURCES,
    FANTASYCALC,
    FANTASYPROS,
    REDRAFT_SOURCES,
    SLEEPER_ADP,
)
from app.models import (
    Draft,
    DraftPick,
    DynastyRanking,
    LeagueUser,
    Player,
    PlayerProjection,
)
from app.schemas import DraftOut, DraftPickOut, DraftSlotOut, PlayerOut

router = APIRouter(tags=["draft"])

#: The boards a client may order by. `aggregate` is not a source — it is the
#: mean of CONSENSUS_SOURCES, computed per request.
AGGREGATE = "aggregate"
BOARDS = (AGGREGATE, *ALL_SOURCES)

#: What each position filter actually admits.
#:
#: FLEX and SUPERFLEX are the league's own lineup slots (§2.1), so the filter
#: answers "who can I put in this slot" rather than "who plays this position",
#: which is the question a superflex owner is really asking on draft night.
#:
#: FB is folded into RB — and therefore into both flexes — because Sleeper
#: files a fullback as position FB with fantasy_positions ["RB"]. He is a
#: running back everywhere it matters, and the ranking sources file him as one.
POSITION_GROUPS: dict[str, tuple[str, ...]] = {
    "RB": ("RB", "FB"),
    "FLEX": ("RB", "FB", "WR", "TE"),
    "SUPERFLEX": ("QB", "RB", "FB", "WR", "TE"),
}


def _positions_for(filter_: str) -> tuple[str, ...]:
    wanted = filter_.upper()
    return POSITION_GROUPS.get(wanted, (wanted,))


def consensus_ranks() -> Select:
    """Each player's mean rank across the consensus boards, and how many ranked him.

    Public because the rosters route needs the same number: "the aggregate"
    has to mean one thing in this app, and two selects that both average some
    ranking columns would be one refactor away from quietly disagreeing.

    Computed per request and never stored. The alternative — a materialised
    average column — is a number that silently goes wrong the moment one of the
    three exports is re-imported and the other two are not, which is the normal
    case: they are downloaded by hand, on different days.

    Sleeper ADP is excluded, deliberately and by construction (see
    `dynasty.CONSENSUS_SOURCES`). It is what the market *did*, not what anyone
    thinks; averaging it in would fold the thing the rankings are being read
    against into the rankings themselves.

    A player ranked by only one of the three gets that one rank as his mean,
    which flatters deep players that a single board happens to like. That is
    why `sources` is returned beside it rather than hidden: "3rd, on one board
    of three" is a different claim from "3rd on all three", and the table says
    which it is.
    """
    return (
        select(
            DynastyRanking.player_id.label("player_id"),
            func.avg(DynastyRanking.overall_rank).label("consensus"),
            func.count().label("sources"),
        )
        .where(DynastyRanking.source.in_(CONSENSUS_SOURCES))
        .group_by(DynastyRanking.player_id)
    )


def _board(board: str) -> Select:
    """(player_id, rank) for whichever board is doing the ordering."""
    if board == AGGREGATE:
        agg = consensus_ranks().subquery()
        return select(agg.c.player_id.label("player_id"), agg.c.consensus.label("rank"))
    return select(
        DynastyRanking.player_id.label("player_id"),
        DynastyRanking.overall_rank.label("rank"),
    ).where(DynastyRanking.source == board)


def _from(
    opinions: dict[str, DynastyRanking], attr: str, *preferred: str
) -> DynastyRanking | None:
    """The first board that actually publishes `attr` for this player.

    Preference order first, then anyone else. The distinction that matters is
    *has an opinion* rather than *has a row*: a Sleeper ADP row exists for most
    players and carries no tier, no value and no positional label, so asking it
    first and stopping there would empty three columns on that one tab.
    """
    ordered = [opinions.get(source) for source in preferred] + list(opinions.values())
    return next(
        (row for row in ordered if row is not None and getattr(row, attr) is not None), None
    )


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
    # show what each pick cost: where Sleeper drafters actually took him,
    # against the aggregate of the dynasty boards.
    adp = (
        select(DynastyRanking.player_id, DynastyRanking.overall_rank.label("adp"))
        .where(DynastyRanking.source == SLEEPER_ADP)
        .subquery()
    )
    consensus = consensus_ranks().subquery()
    picks = db.execute(
        select(DraftPick, adp.c.adp, consensus.c.consensus)
        .outerjoin(adp, adp.c.player_id == DraftPick.player_id)
        .outerjoin(consensus, consensus.c.player_id == DraftPick.player_id)
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
            DraftPickOut.model_validate(pick).model_copy(
                update={"adp": pick_adp, "consensus": round(cons, 1) if cons else None}
            )
            for pick, pick_adp, cons in picks
        ],
    )


@router.get("/api/draft/available", response_model=list[PlayerOut])
def list_available(
    db: DbSession,
    position: Annotated[
        str | None,
        Query(description="QB, RB, WR, TE, FLEX (RB/WR/TE) or SUPERFLEX (QB/RB/WR/TE)."),
    ] = None,
    board: Annotated[
        str,
        Query(description=f"Which ranking to order by: one of {', '.join(BOARDS)}."),
    ] = AGGREGATE,
    limit: Annotated[int, Query(ge=1, le=300)] = 150,
) -> list[PlayerOut]:
    """Best available: the player pool minus everyone already drafted.

    Several orderings, because in dynasty "best" has no single answer: four
    published boards disagree about it by design, the Sleeper market disagrees
    with all of them, and the aggregate is the average of the four that are
    opinions rather than outcomes. The redraft boards order by this season
    alone, and stay out of the aggregate.

    **Every row carries every board's rank whichever one is ordering.** That is
    the point of the page — a player the aggregate has 14th and Sleeper drafters
    take 40th is the actual find, and you cannot see him on a table that shows
    one number.

    Each board admits only the players it ranks: ordering by a board over
    players it has never heard of would be ordering by nothing. Sleeper's pool
    carries every rostered player, so without that rule a position tab would
    fill to the limit with third-stringers instead of running out honestly.

    Ordering is always done over the **whole** pool, and a position filter
    subsets that run order rather than re-ranking it. Only the pool ordering can
    number the rows, and `expected_pick` is that number turned into a pick.
    Without it a filtered tab could not say where a pick lands, because it
    cannot see how many players of other positions go ahead of it.
    """
    if board not in BOARDS:
        raise HTTPException(status_code=422, detail=f"Unknown board {board!r}.")

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
    # ahead of the draft. A keeper is also a pick nobody spends, so the run
    # order has to step over it rather than count it.
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

    ordering_board = _board(board).subquery()

    # The run order over the whole undrafted pool, numbered. Numbering before
    # the position filter is the point: row 1 of the QB tab still knows how
    # many players of every position are expected to go ahead of him.
    pool = (
        select(
            Player.player_id.label("player_id"),
            func.row_number()
            .over(order_by=(ordering_board.c.rank, Player.full_name))
            .label("ordinal"),
        )
        .join(ordering_board, ordering_board.c.player_id == Player.player_id)
        .where(Player.player_id.not_in(drafted))
        .subquery()
    )

    # The projection joins here and nowhere else. It takes no part in the pool
    # subquery above, so importing or clearing it cannot shift `expected_pick`.
    consensus = consensus_ranks().subquery()
    query = (
        select(
            Player,
            PlayerProjection.points,
            consensus.c.consensus,
            consensus.c.sources,
            pool.c.ordinal,
        )
        .join(pool, pool.c.player_id == Player.player_id)
        .outerjoin(PlayerProjection, PlayerProjection.player_id == Player.player_id)
        .outerjoin(consensus, consensus.c.player_id == Player.player_id)
    )
    if position:
        query = query.where(Player.position.in_(_positions_for(position)))

    rows = db.execute(query.order_by(pool.c.ordinal).limit(limit)).all()

    # Nothing on this board at this position (an export not imported yet, or a
    # position no source covers): fall back to Sleeper's own order rather than
    # showing an empty tab. There is no run order to speak of here, so the rows
    # carry no projected pick rather than an invented one.
    if not rows:
        fallback = (
            select(
                Player,
                PlayerProjection.points,
                consensus.c.consensus,
                consensus.c.sources,
                null().label("ordinal"),
            )
            .outerjoin(PlayerProjection, PlayerProjection.player_id == Player.player_id)
            .outerjoin(consensus, consensus.c.player_id == Player.player_id)
            .where(Player.player_id.not_in(drafted))
        )
        if position:
            fallback = fallback.where(Player.position.in_(_positions_for(position)))
        rows = db.execute(
            fallback.order_by(
                Player.search_rank.is_(None), Player.search_rank, Player.full_name
            ).limit(limit)
        ).all()

    # Every board's opinion of the players actually being returned, in one
    # query. Four outer joins onto the main select would say the same thing and
    # be unreadable, and this way a fifth source needs no query change at all.
    opinions: dict[str, dict[str, DynastyRanking]] = {}
    player_ids = [player.player_id for player, *_ in rows]
    if player_ids:
        for row in db.scalars(
            select(DynastyRanking).where(DynastyRanking.player_id.in_(player_ids))
        ).all():
            opinions.setdefault(row.player_id, {})[row.source] = row

    # The run order lands on the open picks in turn: first player available at
    # the next unspent pick, second at the one after, and so on. Past the last
    # round there is no pick left to project onto, and the row says so.
    needed = max((ordinal for *_, ordinal in rows if ordinal is not None), default=0)
    ceiling = total or needed + len(assigned)
    open_picks = [n for n in range(1, ceiling + 1) if n not in assigned][:needed]
    projected = dict(enumerate(open_picks, start=1))

    out: list[PlayerOut] = []
    redraft = board in REDRAFT_SOURCES
    for player, vegas, cons, sources, ordinal in rows:
        by_source = opinions.get(player.player_id, {})
        # The detail columns draw only from boards of the kind in play: a
        # redraft value is a different currency from a dynasty one, and a
        # dynasty tab quietly showing one would read as a dynasty opinion.
        same_kind = {
            source: row
            for source, row in by_source.items()
            if (source in REDRAFT_SOURCES) == redraft
        }

        # The detail columns are deliberately **not** tied to the board in
        # play. They come from whichever board publishes each thing, so the
        # columns hold still while the tabs change — which is the whole point
        # of a page built for comparing boards. Tying them to the active tab
        # blanks Tier on four tabs of five and the value on two, which reads
        # as missing data rather than as a difference between the exports.
        detail = _from(same_kind, "tier", FANTASYPROS)
        spread = _from(same_kind, "rank_min", FANTASYPROS)
        # A value only on the boards that price players rather than order
        # them, and the active one wins between those — their scales are
        # different currencies and are not comparable with each other.
        priced = _from(same_kind, "value", board, FANTASYCALC)
        labelled = _from(same_kind, "pos_rank", board, FANTASYPROS)

        out.append(
            PlayerOut(
                player_id=player.player_id,
                full_name=player.full_name,
                position=player.position,
                team=player.team,
                injury_status=player.injury_status,
                years_exp=player.years_exp,
                age=player.age,
                search_rank=player.search_rank,
                ranks={source: row.overall_rank for source, row in by_source.items()},
                consensus=round(cons, 1) if cons is not None else None,
                consensus_sources=sources or 0,
                tier=detail.tier if detail else None,
                pos_rank=labelled.pos_rank if labelled else None,
                value=priced.value if priced else None,
                rank_min=spread.rank_min if spread else None,
                rank_max=spread.rank_max if spread else None,
                # Whichever board has one. Only FantasyPros and FantasyCalc
                # publish an age, and they agree to within a year.
                ranked_age=(age.age if (age := _from(same_kind, "age")) else None),
                expected_pick=projected.get(ordinal) if ordinal is not None else None,
                vegas_points=vegas,
            )
        )
    return out

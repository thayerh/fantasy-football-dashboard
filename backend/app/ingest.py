"""Sleeper -> SQLite ingestion.

Plain functions, deliberately. A registry/plugin layer isn't warranted at this
size; extract one when the shape is actually known.

Each is a pure `fetch -> upsert` given an injected session and client, which is
what makes them testable against recorded payloads with no network.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any, NamedTuple

from sqlalchemy import delete, func, select, true, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db import utcnow
from app.dynasty import ALL_SOURCES
from app.models import (
    Draft,
    DraftPick,
    DynastyRanking,
    DynastyRankingHistory,
    League,
    LeagueUser,
    NflPlayer,
    NflStint,
    Player,
    PlayerProjection,
    Roster,
    TradedPick,
)
from app.nflverse import NflverseClient, active_rows, franchise_of
from app.sleeper import SleeperClient, ms_to_utc

log = logging.getLogger(__name__)


def sync_league(session: Session, client: SleeperClient, league_id: str) -> int:
    """Upsert league metadata. Returns rows written."""
    payload = client.get_league(league_id)
    now = utcnow()

    values = {
        "league_id": payload["league_id"],
        "name": payload.get("name"),
        "season": payload.get("season"),
        "season_type": payload.get("season_type"),
        "status": payload.get("status"),
        "total_rosters": payload.get("total_rosters"),
        "previous_league_id": payload.get("previous_league_id"),
        "avatar": payload.get("avatar"),
        "raw": payload,
        "fetched_at": now,
    }

    stmt = insert(League).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[League.league_id],
        set_={k: v for k, v in values.items() if k != "league_id"},
    )
    session.execute(stmt)
    log.info("Synced league %s (%s)", values["league_id"], values["name"])
    return 1


def sync_users(session: Session, client: SleeperClient, league_id: str) -> int:
    """Upsert every user in the league. Returns rows written.

    Users who have left are intentionally not deleted; their last_seen_at simply
    stops advancing, which preserves league history.
    """
    payload: list[dict[str, Any]] = client.get_league_users(league_id)
    now = utcnow()
    written = 0

    for user in payload:
        metadata = user.get("metadata") or {}
        values = {
            "league_id": league_id,
            "user_id": user["user_id"],
            "username": user.get("username"),
            "display_name": user.get("display_name"),
            "avatar": user.get("avatar"),
            "avatar_url": metadata.get("avatar"),
            "team_name": metadata.get("team_name"),
            "is_owner": bool(user.get("is_owner")),
            "raw": user,
            "first_seen_at": now,
            "last_seen_at": now,
        }

        stmt = insert(LeagueUser).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[LeagueUser.league_id, LeagueUser.user_id],
            # first_seen_at is deliberately absent: it must survive re-syncs.
            set_={
                k: v
                for k, v in values.items()
                if k not in ("league_id", "user_id", "first_seen_at")
            },
        )
        session.execute(stmt)
        written += 1

    log.info("Synced %d users for league %s", written, league_id)
    return written


def _points(settings: dict[str, Any], whole: str, decimal: str) -> float | None:
    """Recombine Sleeper's split point totals into one number.

    Points arrive as two integers — 1234 and 56 mean 1234.56 — and either half
    can be missing. Absent entirely (the whole preseason, before a game is
    played) is None rather than 0.0: nobody has scored nothing yet, they simply
    have not played.
    """
    if settings.get(whole) is None and settings.get(decimal) is None:
        return None
    return float(settings.get(whole) or 0) + float(settings.get(decimal) or 0) / 100


def sync_rosters(session: Session, client: SleeperClient, league_id: str) -> int:
    """Upsert every team in the league. Returns rows written.

    One request for all twelve, so it belongs on the ordinary `ffd sync`
    cadence rather than a timer of its own. Rosters change on waivers and
    trades — daily in season, and the whole endpoint is a few kilobytes.
    """
    payload: list[dict[str, Any]] = client.get_league_rosters(league_id)
    now = utcnow()
    written = 0

    for roster in payload:
        settings = roster.get("settings") or {}
        values = {
            "league_id": league_id,
            "roster_id": roster["roster_id"],
            "owner_id": roster.get("owner_id"),
            "co_owners": roster.get("co_owners"),
            "players": roster.get("players"),
            # Positional, and stored unsorted for that reason — see the model.
            "starters": roster.get("starters"),
            "taxi": roster.get("taxi"),
            "reserve": roster.get("reserve"),
            "wins": settings.get("wins") or 0,
            "losses": settings.get("losses") or 0,
            "ties": settings.get("ties") or 0,
            "fpts": _points(settings, "fpts", "fpts_decimal"),
            "fpts_against": _points(settings, "fpts_against", "fpts_against_decimal"),
            "waiver_budget_used": settings.get("waiver_budget_used"),
            "waiver_position": settings.get("waiver_position"),
            "total_moves": settings.get("total_moves"),
            "raw": roster,
            "fetched_at": now,
        }

        stmt = insert(Roster).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Roster.league_id, Roster.roster_id],
            set_={k: v for k, v in values.items() if k not in ("league_id", "roster_id")},
        )
        session.execute(stmt)
        written += 1

    # A team that Sleeper no longer reports has been removed from the league,
    # and leaving it would put a phantom in the switcher. Safe because the
    # fetch either returns the whole league or raises.
    kept = {roster["roster_id"] for roster in payload}
    if kept:
        removed = session.execute(
            delete(Roster).where(
                Roster.league_id == league_id, Roster.roster_id.notin_(kept)
            )
        ).rowcount
        if removed:
            log.info("Removed %d roster(s) no longer in league %s", removed, league_id)

    log.info("Synced %d rosters for league %s", written, league_id)
    return written


def sync_traded_picks(session: Session, client: SleeperClient, league_id: str) -> int:
    """Upsert the future-pick ledger. Returns rows written.

    A short list — only picks that have actually moved — and it rides along
    with the rosters for the same reason: one small request, and a trade
    changes both at once.

    Unlike every other sync here, an *empty* response is meaningful. A pick
    trade can be reversed by the commissioner (`rulebook/08-trades.md` §8.5),
    and the pick then vanishes from this endpoint rather than coming back with
    its original owner. So rows Sleeper no longer reports are deleted, empty
    list included — otherwise a reversed trade would be permanent here.
    """
    payload: list[dict[str, Any]] = client.get_traded_picks(league_id)
    now = utcnow()
    written = 0

    for pick in payload:
        values = {
            "league_id": league_id,
            "season": str(pick["season"]),
            "round": pick["round"],
            "roster_id": pick["roster_id"],
            "owner_id": pick.get("owner_id"),
            "previous_owner_id": pick.get("previous_owner_id"),
            "raw": pick,
            "fetched_at": now,
        }
        stmt = insert(TradedPick).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                TradedPick.league_id,
                TradedPick.season,
                TradedPick.round,
                TradedPick.roster_id,
            ],
            set_={
                k: v
                for k, v in values.items()
                if k not in ("league_id", "season", "round", "roster_id")
            },
        )
        session.execute(stmt)
        written += 1

    kept = {(str(pick["season"]), pick["round"], pick["roster_id"]) for pick in payload}
    removed = 0
    for stored in session.scalars(
        select(TradedPick).where(TradedPick.league_id == league_id)
    ).all():
        if (stored.season, stored.round, stored.roster_id) not in kept:
            session.delete(stored)
            removed += 1
    if removed:
        log.info("Removed %d reverted pick trade(s) in league %s", removed, league_id)

    log.info("Synced %d traded picks for league %s", written, league_id)
    return written


def sync_draft(session: Session, client: SleeperClient, league_id: str) -> int:
    """Upsert the league's current draft and every pick made so far.

    Runs on its own fast timer during the draft, so it is written to be cheap:
    a finished draft whose picks are all stored skips the picks request
    entirely, which makes leaving the timer enabled year-round harmless.

    Returns rows written.
    """
    drafts: list[dict[str, Any]] = client.get_league_drafts(league_id)
    if not drafts:
        log.info("League %s has no drafts", league_id)
        return 0

    # Newest first is Sleeper's own ordering, but sort rather than trust it.
    draft_id = max(drafts, key=lambda d: d.get("created") or 0)["draft_id"]

    # The list endpoint omits slot_to_roster_id; only the draft endpoint returns
    # the complete object. Discover the id from the list (so a re-created draft
    # is picked up), then always read the detail.
    payload = client.get_draft(draft_id)
    settings = payload.get("settings") or {}
    now = utcnow()

    stored = session.get(Draft, draft_id)
    expected_picks = (settings.get("rounds") or 0) * (settings.get("teams") or 0)
    stored_picks = session.scalar(
        select(func.count()).select_from(DraftPick).where(DraftPick.draft_id == draft_id)
    )
    complete = (
        stored is not None
        and stored.status == "complete"
        and payload.get("status") == "complete"
        and expected_picks
        and stored_picks >= expected_picks
    )

    values = {
        "draft_id": draft_id,
        "league_id": payload.get("league_id") or league_id,
        "status": payload.get("status"),
        "type": payload.get("type"),
        "season": payload.get("season"),
        "rounds": settings.get("rounds"),
        "teams": settings.get("teams"),
        "start_time": ms_to_utc(payload.get("start_time")),
        "last_picked_at": ms_to_utc(payload.get("last_picked")),
        "draft_order": payload.get("draft_order"),
        "slot_to_roster_id": payload.get("slot_to_roster_id"),
        "raw": payload,
        "fetched_at": now,
    }
    stmt = insert(Draft).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Draft.draft_id],
        set_={k: v for k, v in values.items() if k != "draft_id"},
    )
    session.execute(stmt)
    written = 1

    if complete:
        log.info("Draft %s complete with %d picks stored; skipping picks", draft_id, stored_picks)
        return written

    picks = client.get_draft_picks(draft_id)

    # Sleeper cannot be trusted to say which picks are keepers. Measured against
    # this league's twelve, on 2026-08-27:
    #
    #     draft picks with is_keeper=true .... 9
    #     rosters[].keepers .................. 10
    #     the union of both .................. 10   (two keepers in neither)
    #
    # But a pick that exists while the draft has not started cannot be anything
    # else: you cannot draft before the draft. So while status is pre_draft,
    # every pick on the board is a keeper — which is the only rule that
    # identifies all twelve.
    #
    # That inference has to survive the draft starting, which is what the sticky
    # OR in the upsert below does: once a pick is stored as a keeper it stays
    # one, even though later syncs see status="drafting" and Sleeper's own null.
    # Picks made during the draft get Sleeper's flag and nothing else, so real
    # picks are never mislabelled.
    #
    # The one gap: rebuilding the database from empty *after* the draft has
    # started loses the inference, because status is no longer pre_draft. A
    # restore from backup is fine — the flags are in the backup. If it ever
    # happens, the union above recovers 10 of 12 and the last two are manual.
    pre_draft = payload.get("status") == "pre_draft"

    for pick in picks:
        metadata = pick.get("metadata") or {}
        pick_values = {
            "draft_id": draft_id,
            "pick_no": pick["pick_no"],
            "round": pick.get("round"),
            "draft_slot": pick.get("draft_slot"),
            "roster_id": pick.get("roster_id"),
            "picked_by": pick.get("picked_by") or None,
            "player_id": pick.get("player_id"),
            "first_name": metadata.get("first_name"),
            "last_name": metadata.get("last_name"),
            "position": metadata.get("position"),
            "nfl_team": metadata.get("team"),
            "is_keeper": bool(pick.get("is_keeper")) or pre_draft,
            "raw": pick,
            "fetched_at": now,
        }
        stmt = insert(DraftPick).values(**pick_values)
        updates = {k: v for k, v in pick_values.items() if k not in ("draft_id", "pick_no")}
        # Sticky: a pick that was ever a keeper stays one. Sleeper's flag is
        # unreliable (see above) and this is the only place the correction is
        # recorded — the untouched payload is still in `raw` either way.
        updates["is_keeper"] = stmt.excluded.is_keeper | DraftPick.is_keeper
        stmt = stmt.on_conflict_do_update(
            index_elements=[DraftPick.draft_id, DraftPick.pick_no],
            set_=updates,
        )
        session.execute(stmt)
        written += 1

    # Keepers get edited before draft day. A pick Sleeper no longer reports has
    # been withdrawn, and leaving it would leave a phantom on the board. Safe
    # because the fetch either returns the whole list or raises.
    kept = {pick["pick_no"] for pick in picks}
    removed = session.execute(
        delete(DraftPick).where(
            DraftPick.draft_id == draft_id,
            DraftPick.pick_no.notin_(kept) if kept else true(),
        )
    ).rowcount
    if removed:
        log.info("Removed %d pick(s) no longer on draft %s", removed, draft_id)

    log.info("Synced draft %s (%s) with %d picks", draft_id, values["status"], len(picks))
    return written


# Positions the dashboard cares about. Sleeper's pool includes long snappers,
# practice-squad linemen and retired players; none belong in "best available".
#
# No K and no DEF: this league starts neither, so a kicker in the pool is a
# row that can only ever be a wrong suggestion. The filter is the guard that
# keeps them out — the migration cleared the ones already stored, but this is
# what stops the next `ffd sync-players` putting them straight back.
FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})

# search_rank is 9999999 for players Sleeper doesn't rank. Anything past this is
# noise, and keeping it would push 10,000 irrelevant rows into the table.
RANK_CEILING = 100_000


def sync_players(session: Session, client: SleeperClient) -> int:
    """Replace the player pool. Returns rows written.

    A wholesale replace rather than an upsert: this table is a snapshot with no
    history worth preserving, and the delete plus insert share one transaction,
    so readers see the old pool until the new one is complete.

    ~12,000 players come back; ~1,900 survive the filter.
    """
    payload = client.get_players()
    now = utcnow()

    rows = []
    for player in payload.values():
        positions = set(player.get("fantasy_positions") or [])
        if not positions & FANTASY_POSITIONS:
            continue

        rank = player.get("search_rank")
        if not isinstance(rank, int) or rank >= RANK_CEILING:
            continue

        rows.append(
            {
                "player_id": player["player_id"],
                "full_name": player.get("full_name") or player.get("last_name"),
                "first_name": player.get("first_name"),
                "last_name": player.get("last_name"),
                "position": player.get("position"),
                "team": player.get("team"),
                "status": player.get("status"),
                "injury_status": player.get("injury_status") or None,
                "years_exp": player.get("years_exp"),
                "age": player.get("age"),
                "search_rank": rank if isinstance(rank, int) and rank < RANK_CEILING else None,
                # A cross-reference kept for joining to sources that carry it;
                # str because their side is a string and SQLite would happily
                # compare 32711 to "32711" as unequal.
                "yahoo_id": str(player["yahoo_id"]) if player.get("yahoo_id") else None,
                "fantasy_positions": sorted(positions),
                "fetched_at": now,
            }
        )

    session.execute(delete(Player))
    if rows:
        session.execute(insert(Player), rows)

    log.info("Synced %d players (from %d in the pool)", len(rows), len(payload))
    return len(rows)


# Team abbreviations that differ between the ranking sources and Sleeper.
# Jacksonville is the one that actually bit: FantasyPros says JAC, Sleeper says
# JAX, and a row resolved by team abbreviation silently pointed at no player.
_TEAM_ALIASES = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "ARZ": "ARI"}

_NAME_NOISE = re.compile(r"[^a-z ]")
_NAME_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def _normalize_name(name: str | None) -> str:
    """A name in a form both sources agree on: 'A.J. Brown' -> 'aj brown'."""
    if not name:
        return ""
    cleaned = _NAME_NOISE.sub("", name.lower())
    cleaned = _NAME_SUFFIX.sub("", cleaned)
    return " ".join(cleaned.split())


class _PlayerIndex(NamedTuple):
    by_name_position: dict[tuple[str, str], str]
    by_name: dict[str, list[str]]
    by_last_team_position: dict[tuple[str, str, str], list[str]]
    by_last_position: dict[tuple[str, str], list[str]]
    valid: set[str]


def _match_index(session: Session) -> _PlayerIndex:
    """Lookups from the Sleeper player pool, in decreasing order of confidence.

    `valid` is every known player id. Every match is checked against it, so a
    resolved id that does not exist becomes a logged miss rather than a row
    that silently joins to nothing.
    """
    by_name_position: dict[tuple[str, str], str] = {}
    by_name: dict[str, list[str]] = {}
    by_last_team_position: dict[tuple[str, str, str], list[str]] = {}
    by_last_position: dict[tuple[str, str], list[str]] = {}
    valid: set[str] = set()

    for player in session.scalars(select(Player)).all():
        valid.add(player.player_id)
        position = player.position or ""
        name = _normalize_name(player.full_name)
        if name:
            by_name_position.setdefault((name, position), player.player_id)
            by_name.setdefault(name, []).append(player.player_id)
        last = _normalize_name(player.last_name)
        if last:
            key = (last, player.team or "", position)
            by_last_team_position.setdefault(key, []).append(player.player_id)
            by_last_position.setdefault((last, position), []).append(player.player_id)

    return _PlayerIndex(
        by_name_position, by_name, by_last_team_position, by_last_position, valid
    )


def _resolve_player(entry: dict[str, Any], index: _PlayerIndex) -> str | None:
    """Find the Sleeper player_id for a ranking or projection entry.

    A ladder, because the outside sources and Sleeper disagree in specific
    ways and each rung fixes one: most rows match on name and position, a few
    on name alone (several sources file fullbacks as RB while Sleeper calls
    them FB), and the rest on a last name, which is what catches the nicknames
    — "Hollywood Brown" is Sleeper's "Marquise Brown".

    Above all of it sits `sleeper_id`, which is not a guess at all. Only
    FantasyCalc carries one, and where it does the ladder is skipped entirely.

    The last-name rungs are tried with the team first and then without it,
    because one source cannot supply a team at all: the Sleeper ADP export is
    three columns and none of them is the NFL club. Dropping to last name and
    position alone recovers the five players it otherwise loses, every one of
    them a first-name nickname Sleeper disagrees about — Cam Ward, Cam
    Skattebo, Kenny Gainwell, Chig Okonkwo, Tank Dell.

    Ambiguity always loses: a rung that finds two candidates falls through
    rather than guessing between two real players. That is what makes the
    weakest rung safe — two WRs named Brown are simply left unmatched.
    """
    # The exact answer, where a source happens to know it.
    sleeper_id = entry.get("sleeper_id")
    if sleeper_id:
        return sleeper_id if sleeper_id in index.valid else None

    position = entry.get("player_position_id") or ""

    name = _normalize_name(entry.get("player_name"))
    if (name, position) in index.by_name_position:
        return index.by_name_position[(name, position)]

    unique = index.by_name.get(name, [])
    if len(unique) == 1:
        return unique[0]

    last = _normalize_name((entry.get("player_name") or "").split(" ")[-1])
    team = entry.get("player_team_id") or ""
    key = (last, _TEAM_ALIASES.get(team, team), position)
    candidates = index.by_last_team_position.get(key, [])
    if len(candidates) == 1:
        return candidates[0]

    candidates = index.by_last_position.get((last, position), [])
    if len(candidates) == 1:
        return candidates[0]

    return None


def sync_dynasty_rankings(
    session: Session,
    source: str,
    rows: list[dict[str, Any]],
    as_of: dt.date | None = None,
) -> int:
    """Replace one source's board with a parsed export. Returns rows written.

    With `as_of`, the board is also recorded in `dynasty_ranking_history` for
    that day, replacing whatever that source had there already. `ffd
    fetch-rankings` passes it; hand imports do not, since a file imported today
    may say what a site said last month.

    Only that source's rows are touched. Each export is downloaded and imported
    by hand at its own moment, and a re-import of one board must not disturb
    the other three — which is exactly what the composite key buys and what the
    old one-table-per-feed shape could not do.

    The parser hands back entries shaped like the other importers', so this
    reuses the same match ladder rather than growing a fourth one.
    """
    if source not in ALL_SOURCES:
        raise ValueError(f"Unknown ranking source {source!r}; expected one of {ALL_SOURCES}.")

    index = _match_index(session)
    if not index.valid:
        raise RuntimeError("Player pool is empty. Run `ffd sync-players` before importing.")

    now = utcnow()
    matched: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []

    for entry in rows:
        player_id = _resolve_player(entry, index)
        if player_id is None:
            unmatched.append(f"{entry.get('player_name')} ({entry.get('player_position_id')})")
            continue

        # Two export rows can resolve to one Sleeper player — two players share
        # a name and Sleeper lists one of them. Rows arrive rank-ordered, so
        # the first is the better-ranked; keep it, and say so rather than
        # letting the second silently overwrite it.
        existing = matched.get(player_id)
        if existing is not None:
            log.warning(
                "Two %s rows resolve to Sleeper %s: keeping %s (#%s), dropping %s (#%s)",
                source, player_id, existing["source_name"], existing["overall_rank"],
                entry.get("player_name"), entry.get("overall_rank"),
            )
            continue

        matched[player_id] = {
            "source": source,
            "player_id": player_id,
            "overall_rank": entry["overall_rank"],
            "position_rank": entry.get("position_rank"),
            "pos_rank": entry.get("pos_rank"),
            "tier": entry.get("tier"),
            "value": entry.get("value"),
            "age": entry.get("age"),
            "rank_min": entry.get("rank_min"),
            "rank_max": entry.get("rank_max"),
            "rank_ave": entry.get("rank_ave"),
            "rank_std": entry.get("rank_std"),
            "source_name": entry.get("player_name"),
            "fetched_at": now,
        }

    session.execute(delete(DynastyRanking).where(DynastyRanking.source == source))
    if matched:
        session.execute(insert(DynastyRanking), list(matched.values()))

    if as_of is not None:
        session.execute(
            delete(DynastyRankingHistory).where(
                DynastyRankingHistory.as_of == as_of, DynastyRankingHistory.source == source
            )
        )
        if matched:
            session.execute(
                insert(DynastyRankingHistory),
                [
                    {
                        "as_of": as_of,
                        "source": source,
                        "player_id": row["player_id"],
                        "overall_rank": row["overall_rank"],
                        "position_rank": row["position_rank"],
                        "tier": row["tier"],
                        "value": row["value"],
                    }
                    for row in matched.values()
                ],
            )

    if unmatched:
        # Not an error: every one of these boards ranks rookies and free agents
        # ahead of Sleeper adding them, and rates players this league's pool
        # filter has already dropped.
        log.warning(
            "No Sleeper match for %d %s entr%s: %s",
            len(unmatched), source, "y" if len(unmatched) == 1 else "ies",
            ", ".join(unmatched[:10]),
        )
    log.info("Stored %d ranked players from %s", len(matched), source)
    return len(matched)


def sync_vegas_projections(session: Session, rows: list[dict[str, Any]]) -> int:
    """Replace the projections with a parsed Vegas export.

    Returns rows written. Reads no network at all: the export is a file the
    user supplies, so there is no client, no budget and no timer behind this.

    The parser hands back FantasyPros-shaped entries, so this reuses the same
    match ladder as the ranking and ADP syncs rather than growing a third one.
    """
    index = _match_index(session)
    if not index.valid:
        raise RuntimeError("Player pool is empty. Run `ffd sync-players` before sync-vegas.")

    now = utcnow()
    matched: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []

    for entry in rows:
        player_id = _resolve_player(entry, index)
        if player_id is None:
            unmatched.append(f"{entry['player_name']} ({entry['player_position_id']})")
            continue

        # Two export rows resolving to one Sleeper player would mean the export
        # lists a player twice; the higher projection is the one worth keeping,
        # since the duplicate is usually a stale row for a departed team.
        existing = matched.get(player_id)
        if existing is not None:
            if existing["points"] >= entry["points"]:
                continue
            log.warning(
                "Two projection rows resolve to Sleeper %s: keeping %s (%s), dropping %s (%s)",
                player_id, entry["player_name"], entry["points"],
                existing["name"], existing["points"],
            )

        matched[player_id] = {
            "player_id": player_id,
            "points": float(entry["points"]),
            "pos_rank": entry.get("pos_rank"),
            "tag": entry.get("tag"),
            "source": "vegas-csv",
            "name": entry["player_name"],
            "fetched_at": now,
        }

    session.execute(delete(PlayerProjection))
    if matched:
        session.execute(insert(PlayerProjection), list(matched.values()))

    if unmatched:
        # Not an error: the export ranks free agents and camp bodies that
        # Sleeper's pool may not carry.
        log.warning(
            "No Sleeper match for %d projections: %s",
            len(unmatched), ", ".join(unmatched[:10]),
        )
    log.info("Stored Vegas projections for %d players", len(matched))
    return len(matched)


# --- nflverse historical rosters -------------------------------------------
#
# Not a Sleeper sync, but the same fetch -> upsert shape, and it shares
# _normalize_name with the FantasyPros matchers: a submitted name has to be
# normalised the same way whichever table it is looked up in.


def _int_or_none(value: str | None) -> int | None:
    """nflverse writes absent numbers as an empty field, not a zero."""
    if not value:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def sync_nfl_rosters(
    session: Session,
    client: NflverseClient,
    seasons: list[int],
) -> int:
    """Load active-roster history for the given seasons. Returns stints written.

    Seasons are processed in ascending order and each one replaces its own rows
    outright, so re-running a season picks up nflverse's own corrections and
    re-running the whole window is idempotent. A season's rows are deleted only
    once its file has downloaded and parsed, so a mid-backfill network failure
    leaves the previous data in place rather than a hole.
    """
    total = 0
    for season in sorted(seasons):
        total += _absorb_season(session, client, season)

    _refresh_player_spans(session)
    return total


def _absorb_season(session: Session, client: NflverseClient, season: int) -> int:
    """Fetch one season, collapse its weekly rows to stints, and store them."""
    text = client.fetch_season(season)
    now = utcnow()

    # (gsis_id, franchise) -> stint under construction. The week range is built
    # by min/max rather than assuming the file is ordered.
    stints: dict[tuple[str, str], dict[str, Any]] = {}
    # gsis_id -> the row from that player's latest week, which is the most
    # current spelling of his name and position within this season.
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    unknown_teams: set[str] = set()

    for row in active_rows(text):
        gsis_id = row["gsis_id"]
        franchise = franchise_of(row.get("team"))
        if franchise is None:
            unknown_teams.add(row.get("team") or "")
            continue

        week = _int_or_none(row.get("week")) or 0
        seen = latest.get(gsis_id)
        if seen is None or week >= seen[0]:
            latest[gsis_id] = (week, row)

        key = (gsis_id, franchise)
        stint = stints.get(key)
        if stint is None:
            stints[key] = {
                "gsis_id": gsis_id,
                "season": season,
                "franchise": franchise,
                "team": row.get("team"),
                "position": row.get("position"),
                "jersey_number": _int_or_none(row.get("jersey_number")),
                "first_week": week,
                "last_week": week,
                "weeks": 1,
                "fetched_at": now,
            }
        else:
            stint["first_week"] = min(stint["first_week"], week)
            if week >= stint["last_week"]:
                stint["last_week"] = week
                # A position change mid-season is rare but real; the latest
                # week is the one worth keeping.
                stint["position"] = row.get("position") or stint["position"]
                stint["jersey_number"] = (
                    _int_or_none(row.get("jersey_number")) or stint["jersey_number"]
                )
            stint["weeks"] += 1

    if unknown_teams:
        # Loud rather than silent: an unmapped abbreviation means a franchise's
        # history is incomplete, which is exactly the bug this table exists to
        # avoid. Add it to nflverse.FRANCHISES.
        log.error(
            "Season %s has unmapped team codes %s; those rows were dropped",
            season,
            sorted(unknown_teams),
        )

    session.execute(delete(NflStint).where(NflStint.season == season))
    if stints:
        session.execute(insert(NflStint), list(stints.values()))

    _upsert_nfl_players(session, season, latest, now)
    log.info("nflverse %s: %s players, %s stints", season, len(latest), len(stints))
    return len(stints)


def _upsert_nfl_players(
    session: Session,
    season: int,
    latest: dict[str, tuple[int, dict[str, Any]]],
    now: dt.datetime,
) -> None:
    """Store player identity, letting the most recent season win.

    The `where` clause is what makes that true regardless of call order:
    re-syncing 2019 on its own must not overwrite a player's 2026 name and
    position with his 2019 ones.
    """
    for gsis_id, (_week, row) in latest.items():
        full_name = row.get("full_name") or ""
        values = {
            "gsis_id": gsis_id,
            "full_name": full_name,
            "first_name": row.get("first_name") or None,
            "last_name": row.get("last_name") or None,
            "name_key": _normalize_name(full_name),
            "last_name_key": _normalize_name(row.get("last_name")),
            "position": row.get("position") or None,
            "birth_date": row.get("birth_date") or None,
            "college": row.get("college") or None,
            "entry_year": _int_or_none(row.get("entry_year")),
            "rookie_year": _int_or_none(row.get("rookie_year")),
            "draft_club": row.get("draft_club") or None,
            "draft_number": _int_or_none(row.get("draft_number")),
            "first_season": season,
            "last_season": season,
            "sleeper_id": row.get("sleeper_id") or None,
            "espn_id": row.get("espn_id") or None,
            "pfr_id": row.get("pfr_id") or None,
            "headshot_url": row.get("headshot_url") or None,
            "fetched_at": now,
        }
        stmt = insert(NflPlayer).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[NflPlayer.gsis_id],
            set_={k: v for k, v in values.items() if k not in ("gsis_id", "first_season")},
            where=NflPlayer.last_season <= season,
        )
        session.execute(stmt)


def _refresh_player_spans(session: Session) -> None:
    """Recompute first/last season from the stints, and drop orphans.

    These two columns are denormalised so every result line can print a career
    span without a second query, which means they are only ever as correct as
    the last thing that recomputed them. Deriving them from nfl_stint here —
    rather than tracking them incrementally during the load — is what keeps
    them right after a season is re-synced or the window moves on.
    """
    session.execute(
        delete(NflPlayer).where(
            ~select(NflStint.gsis_id)
            .where(NflStint.gsis_id == NflPlayer.gsis_id)
            .exists()
        )
    )
    session.execute(
        update(NflPlayer).values(
            first_season=(
                select(func.min(NflStint.season))
                .where(NflStint.gsis_id == NflPlayer.gsis_id)
                .scalar_subquery()
            ),
            last_season=(
                select(func.max(NflStint.season))
                .where(NflStint.gsis_id == NflPlayer.gsis_id)
                .scalar_subquery()
            ),
        )
    )

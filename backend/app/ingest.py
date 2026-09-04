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
from collections.abc import Callable
from typing import Any, NamedTuple

from sqlalchemy import delete, func, select, true
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db import utcnow
from app.fantasypros import RANKED_POSITIONS, FantasyProsClient
from app.models import (
    Draft,
    DraftPick,
    League,
    LeagueUser,
    Player,
    PlayerAdp,
    PlayerProjection,
    PlayerRanking,
)
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
FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})

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
        # Team defenses have no rank at all but are draftable, so they are kept
        # and sorted last rather than filtered out with the noise.
        is_def = player.get("position") == "DEF"
        if not is_def and (not isinstance(rank, int) or rank >= RANK_CEILING):
            continue

        rows.append(
            {
                "player_id": player["player_id"],
                # Team defenses carry no full_name; last_name is "Texans".
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
                # The join key to FantasyPros; str because their side is a
                # string and SQLite would happily compare 32711 to "32711" as
                # unequal.
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


# FantasyPros calls team defenses DST; Sleeper calls them DEF.
_FP_TO_SLEEPER_POSITION = {"DST": "DEF"}

# Team abbreviations that differ between the two. Jacksonville is the one that
# actually bit: FantasyPros says JAC, Sleeper says JAX, and a defense resolved
# by team abbreviation silently became a row pointing at no player at all.
_TEAM_ALIASES = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "ARZ": "ARI"}

_NAME_NOISE = re.compile(r"[^a-z ]")
_NAME_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
_POS_RANK = re.compile(r"(\d+)$")


def _normalize_name(name: str | None) -> str:
    """A name in a form both sources agree on: 'A.J. Brown' -> 'aj brown'."""
    if not name:
        return ""
    cleaned = _NAME_NOISE.sub("", name.lower())
    cleaned = _NAME_SUFFIX.sub("", cleaned)
    return " ".join(cleaned.split())


def _number(entry: dict[str, Any], key: str, cast: Callable[[Any], Any]) -> Any:
    """FantasyPros sends its numbers as strings, and sometimes as ""."""
    value = entry.get(key)
    if value in (None, ""):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _positional_rank(pos_rank: str | None) -> int | None:
    """'WR12' -> 12. The cheat sheet gives the positional rank as a label."""
    if not pos_rank:
        return None
    match = _POS_RANK.search(pos_rank)
    return int(match.group(1)) if match else None


class _PlayerIndex(NamedTuple):
    by_name_position: dict[tuple[str, str], str]
    by_name: dict[str, list[str]]
    by_last_team_position: dict[tuple[str, str, str], list[str]]
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

    return _PlayerIndex(by_name_position, by_name, by_last_team_position, valid)


def _resolve_player(entry: dict[str, Any], index: _PlayerIndex) -> str | None:
    """Find the Sleeper player_id for a FantasyPros ranking entry.

    A ladder, because the two databases disagree in three specific ways and
    each rung fixes one. Measured against the full 517-player cheat sheet:
    475 by name and position, 7 by name alone (FantasyPros files fullbacks as
    RB while Sleeper calls them FB), 2 by last name and team (nicknames —
    "Hollywood Brown" is Sleeper's "Marquise Brown"), 32 defenses by team.
    One free agent went unmatched.

    Ambiguity always loses: a rung that finds two candidates falls through
    rather than guessing between two real players.
    """
    position = entry.get("player_position_id") or ""
    position = _FP_TO_SLEEPER_POSITION.get(position, position)

    # Team defenses: Sleeper's player_id for a defense IS the team abbreviation,
    # and the names never match ("Texans" vs "Houston Texans").
    if position == "DEF":
        team = entry.get("player_team_id") or ""
        team = _TEAM_ALIASES.get(team, team)
        return team if team in index.valid else None

    name = _normalize_name(entry.get("player_name"))
    if (name, position) in index.by_name_position:
        return index.by_name_position[(name, position)]

    unique = index.by_name.get(name, [])
    if len(unique) == 1:
        return unique[0]

    last = _normalize_name((entry.get("player_name") or "").split(" ")[-1])
    key = (last, entry.get("player_team_id") or "", position)
    candidates = index.by_last_team_position.get(key, [])
    if len(candidates) == 1:
        return candidates[0]

    return None


def _absorb(
    rows: dict[str, dict[str, Any]],
    payload: dict[str, Any],
    index: _PlayerIndex,
    unmatched: list[str],
    *,
    overall: bool,
    source: str,
    now: dt.datetime,
) -> None:
    """Fold one ranking payload into the rows to be written."""
    published = ms_to_utc((payload.get("last_updated_ts") or 0) * 1000) or None

    for entry in payload.get("players") or []:
        player_id = _resolve_player(entry, index)
        if player_id is None:
            unmatched.append(f"{entry.get('player_name')} ({entry.get('player_position_id')})")
            continue

        # Two FantasyPros players can resolve to one Sleeper player — there are
        # two WRs named Isaiah Williams in the 2026 set and Sleeper lists one.
        # Payloads arrive rank-ordered, so the first is the better-ranked; keep
        # it, and say so rather than letting the second silently vanish.
        existing = rows.get(player_id)
        fp_id = str(entry.get("player_id") or "") or None
        if existing is not None and existing["fp_player_id"] not in (None, fp_id):
            log.warning(
                "Two ranked players resolve to Sleeper %s: keeping %s (ECR %s), dropping %s "
                "(ECR %s)",
                player_id, existing["fp_name"], existing["ecr_overall"],
                entry.get("player_name"), entry.get("rank_ecr"),
            )
            continue

        row = rows.setdefault(
            player_id,
            {
                "player_id": player_id,
                "ecr_overall": None,
                "ecr_position": None,
                "pos_rank": None,
                "tier": None,
                "bye_week": None,
                "rank_min": None,
                "rank_max": None,
                "rank_ave": None,
                "rank_std": None,
                "source": source,
                "scoring": payload.get("scoring"),
                "ranking_type": payload.get("ranking_type_name"),
                "experts": payload.get("total_experts"),
                "fp_player_id": str(entry.get("player_id") or "") or None,
                "fp_name": entry.get("player_name"),
                "published_at": published,
                "fetched_at": now,
            },
        )

        rank = entry.get("rank_ecr")
        if overall:
            row["ecr_overall"] = rank
            # The cheat sheet labels the positional rank rather than giving a
            # second ranking call, so both ranks come out of one payload.
            row["ecr_position"] = row["ecr_position"] or _positional_rank(entry.get("pos_rank"))
        else:
            row["ecr_position"] = rank

        row["pos_rank"] = row["pos_rank"] or entry.get("pos_rank")
        row["tier"] = row["tier"] if row["tier"] is not None else entry.get("tier")

        bye = entry.get("player_bye_week")
        if row["bye_week"] is None and str(bye or "").isdigit():
            row["bye_week"] = int(bye)

        for field, cast in (("rank_min", int), ("rank_max", int),
                            ("rank_ave", float), ("rank_std", float)):
            if row[field] is None:
                row[field] = _number(entry, field, cast)


def _write_rankings(
    session: Session,
    rows: dict[str, dict[str, Any]],
    unmatched: list[str],
    source: str,
) -> int:
    """Replace the table. The newest fetch is the truth; there is no merge."""
    session.execute(delete(PlayerRanking))
    if rows:
        session.execute(insert(PlayerRanking), list(rows.values()))

    if unmatched:
        # Not an error: a ranked free agent can be absent from Sleeper's pool.
        log.warning(
            "No Sleeper match for %d ranked player(s): %s",
            len(unmatched),
            ", ".join(unmatched[:10]),
        )
    log.info("Stored %d ranked players from %s", len(rows), source)
    return len(rows)


def sync_rankings_web(session: Session, payload: dict[str, Any]) -> int:
    """Replace rankings from the cheat sheet. Returns rows written.

    The whole ranking set arrives in one payload — overall ECR, positional
    rank, tier, bye and the expert spread — so this costs one HTTP request and
    none of the JSON API's fifty-a-day budget.
    """
    index = _match_index(session)
    if not index.valid:
        raise RuntimeError("Player pool is empty. Run `ffd sync-players` before sync-rankings.")

    rows: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []
    _absorb(rows, payload, index, unmatched, overall=True, source="web", now=utcnow())
    return _write_rankings(session, rows, unmatched, "web")


def sync_rankings_api(session: Session, client: FantasyProsClient, season: str) -> int:
    """Replace rankings from the JSON API. Returns rows written.

    The fallback for when the cheat sheet's shape changes. Costs **seven** calls
    of the fifty-a-day budget: the free tier caps every response at ten players
    and ignores a client-supplied limit, so the position filter is the only
    lever on which ten come back — one call for the overall top ten, one for
    each of six positions.
    """
    index = _match_index(session)
    if not index.valid:
        raise RuntimeError("Player pool is empty. Run `ffd sync-players` before sync-rankings.")

    now = utcnow()
    rows: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []

    _absorb(rows, client.get_consensus_rankings(season, position="ALL"), index, unmatched,
            overall=True, source="api", now=now)
    for position in RANKED_POSITIONS:
        _absorb(rows, client.get_consensus_rankings(season, position=position), index, unmatched,
                overall=False, source="api", now=now)

    written = _write_rankings(session, rows, unmatched, "api")
    log.info("Used %d FantasyPros API calls", client.calls)
    return written


def sync_adp(session: Session, client: FantasyProsClient, season: str) -> int:
    """Replace average draft position. Returns rows written.

    Costs **seven** calls of the fifty-a-day budget, for the same reason the
    API ranking path does: ten players per response, and the position filter is
    the only lever on which ten.

    This is FantasyPros' cross-platform ADP, not Sleeper's alone — see
    PlayerAdp. `rank_ave` is the actual average draft position and `rank_ecr`
    its ordinal; both are kept, because "went at 5.6 on average" and "was the
    5th player off the board" answer different questions.
    """
    index = _match_index(session)
    if not index.valid:
        raise RuntimeError("Player pool is empty. Run `ffd sync-players` before sync-adp.")

    now = utcnow()
    rows: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []

    def absorb(payload: dict[str, Any]) -> None:
        published = ms_to_utc((payload.get("last_updated_ts") or 0) * 1000) or None
        for entry in payload.get("players") or []:
            player_id = _resolve_player(entry, index)
            if player_id is None:
                unmatched.append(f"{entry.get('player_name')} ({entry.get('player_position_id')})")
                continue
            if player_id in rows:
                continue  # already seen in the overall call

            rows[player_id] = {
                "player_id": player_id,
                "adp": _number(entry, "rank_ave", float),
                "adp_rank": entry.get("rank_ecr"),
                "adp_min": _number(entry, "rank_min", int),
                "adp_max": _number(entry, "rank_max", int),
                "adp_std": _number(entry, "rank_std", float),
                "scoring": payload.get("scoring"),
                "sources": payload.get("total_experts"),
                "source": "fp-api",
                "fp_player_id": str(entry.get("player_id") or "") or None,
                "fp_name": entry.get("player_name"),
                "published_at": published,
                "fetched_at": now,
            }

    absorb(client.get_consensus_rankings(season, position="ALL", ranking_type="ADP"))
    for position in RANKED_POSITIONS:
        absorb(client.get_consensus_rankings(season, position=position, ranking_type="ADP"))

    session.execute(delete(PlayerAdp))
    if rows:
        session.execute(insert(PlayerAdp), list(rows.values()))

    if unmatched:
        log.warning("No Sleeper match for %d ADP entries: %s", len(unmatched),
                    ", ".join(unmatched[:10]))
    log.info("Stored ADP for %d players in %d API calls", len(rows), client.calls)
    return len(rows)


def sync_sleeper_adp(session: Session, entries: list[dict[str, Any]], scoring: str) -> int:
    """Replace ADP with Sleeper's own, scraped from the rendered page.

    Returns rows written. Costs none of the fifty-a-day API budget.

    The entries arrive shaped like FantasyPros' JSON rows, so this reuses the
    same match ladder as the ranking syncs rather than growing a second one.

    Unlike the API path there is no expert spread to record: this is one
    platform's average, not a consensus of several, so `adp_min`, `adp_max` and
    `adp_std` stay null and `sources` is 1. `adp_rank` is derived here — the
    page's own RK column is FantasyPros' overall rank, which is a different
    ordering than the Sleeper column and would be misleading stored beside it.
    """
    index = _match_index(session)
    if not index.valid:
        raise RuntimeError("Player pool is empty. Run `ffd sync-players` before sync-adp.")

    now = utcnow()
    rows: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []

    for entry in entries:
        adp = entry.get("sleeper_adp")
        if adp is None:
            continue

        player_id = _resolve_player(entry, index)
        if player_id is None:
            unmatched.append(f"{entry.get('player_name')} ({entry.get('player_position_id')})")
            continue

        # Two scraped rows can resolve to one Sleeper player, the same way two
        # ranked ones can. The earlier ADP is the one the league will see him
        # go at, so the lower number wins rather than the later row.
        existing = rows.get(player_id)
        if existing is not None:
            if existing["adp"] <= adp:
                continue
            log.warning(
                "Two ADP rows resolve to Sleeper %s: keeping %s (%s), dropping %s (%s)",
                player_id, entry.get("player_name"), adp, existing["fp_name"], existing["adp"],
            )

        rows[player_id] = {
            "player_id": player_id,
            "adp": float(adp),
            "adp_rank": None,
            "adp_min": None,
            "adp_max": None,
            "adp_std": None,
            "scoring": scoring,
            "sources": 1,
            "source": "sleeper-web",
            "fp_player_id": None,
            "fp_name": entry.get("player_name"),
            "published_at": None,
            "fetched_at": now,
        }

    # The ordinal, assigned after matching so it counts only players we can
    # actually join to. Ties keep their relative order and share no rank.
    for ordinal, row in enumerate(sorted(rows.values(), key=lambda r: r["adp"]), start=1):
        row["adp_rank"] = ordinal

    session.execute(delete(PlayerAdp))
    if rows:
        session.execute(insert(PlayerAdp), list(rows.values()))

    if unmatched:
        # Not an error: the page ranks free agents Sleeper's pool may not carry.
        log.warning(
            "No Sleeper match for %d ADP entries: %s",
            len(unmatched), ", ".join(unmatched[:10]),
        )
    log.info("Stored Sleeper ADP for %d players", len(rows))
    return len(rows)


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

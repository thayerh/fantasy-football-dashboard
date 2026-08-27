"""Sleeper -> SQLite ingestion.

Two plain functions, deliberately. A registry/plugin layer isn't warranted for
two jobs; extract one when there are several and the shape is actually known.

Both are pure `fetch -> upsert` given an injected session and client, which is
what makes them testable against recorded payloads with no network.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import League, LeagueUser
from app.sleeper import SleeperClient

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

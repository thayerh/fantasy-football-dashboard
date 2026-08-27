"""Database models.

Every table keeps a `raw` column holding the latest Sleeper payload verbatim.
When a future feature needs a field we didn't model yet, it can be backfilled
from stored data instead of re-fetching (which for historical data is often
impossible).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
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


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String, default="running")
    error: Mapped[str | None] = mapped_column(Text)
    records_written: Mapped[int] = mapped_column(Integer, default=0)

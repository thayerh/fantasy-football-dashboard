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

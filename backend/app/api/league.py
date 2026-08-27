"""League and member routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.db import DbSession
from app.models import League, LeagueUser
from app.schemas import LeagueOut, LeagueUserOut

router = APIRouter(tags=["league"])


@router.get("/api/league", response_model=LeagueOut)
def get_league(db: DbSession) -> League:
    league_id = get_settings().sleeper_league_id
    league = db.get(League, league_id)
    if league is None:
        raise HTTPException(
            status_code=503,
            detail="League not ingested yet. Run `ffd sync`.",
        )
    return league


@router.get("/api/users", response_model=list[LeagueUserOut])
def list_users(db: DbSession) -> list[LeagueUser]:
    league_id = get_settings().sleeper_league_id
    return list(
        db.scalars(
            select(LeagueUser)
            .where(LeagueUser.league_id == league_id)
            .order_by(LeagueUser.display_name)
        ).all()
    )

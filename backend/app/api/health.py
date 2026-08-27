"""Health and sync-observability routes."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import DbSession, utcnow
from app.models import SyncRun
from app.schemas import HealthOut, SyncRunOut

router = APIRouter(tags=["health"])


def _latest_completed_run(db: Session) -> SyncRun | None:
    return db.scalars(
        select(SyncRun).where(SyncRun.finished_at.is_not(None)).order_by(SyncRun.id.desc()).limit(1)
    ).first()


@router.get("/api/health", response_model=HealthOut)
def health(db: DbSession) -> HealthOut:
    settings = get_settings()

    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    run = _latest_completed_run(db) if db_ok else None
    last_sync_at = run.finished_at if run else None

    # Stale means "the data on screen may not be current" — either ingestion
    # hasn't succeeded recently, or the last attempt errored. The UI surfaces
    # this so a broken timer isn't invisible to users.
    age_exceeded = (
        last_sync_at is None
        or (utcnow() - last_sync_at).total_seconds() > settings.stale_after_seconds
    )
    stale = age_exceeded or (run is not None and run.status != "ok")

    return HealthOut(
        status="ok" if db_ok and not stale else "degraded",
        db_ok=db_ok,
        last_sync_at=last_sync_at,
        last_sync_status=run.status if run else None,
        stale=stale,
    )


@router.get("/api/sync/status", response_model=list[SyncRunOut])
def sync_status(db: DbSession, limit: int = 20) -> list[SyncRun]:
    limit = max(1, min(limit, 100))
    return list(db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)).all())

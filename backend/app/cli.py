"""Command-line entry points, driven by systemd timers.

`ffd sync` is also the *only* way to trigger ingestion by hand. There is no
web route for it: Cloudflare Access authenticates all ~15 leaguemates
identically, so an in-app admin endpoint behind it would be an admin action any
member could fire. Requiring shell access is the authorization boundary.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from app.backup import create_backup, prune_backups, restore_backup
from app.config import get_settings
from app.db import session_scope, utcnow
from app.ingest import sync_league, sync_users
from app.models import SyncRun
from app.sleeper import SleeperClient

app = typer.Typer(help="Fantasy football dashboard maintenance commands.", no_args_is_help=True)
log = logging.getLogger("ffd")


def _configure_logging(verbose: bool = False) -> None:
    # systemd captures stdout/stderr into the journal, so plain output is enough.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


@app.command()
def sync(verbose: bool = False) -> None:
    """Fetch the league and its users from Sleeper into SQLite.

    Records one sync_run row per invocation and exits non-zero on failure, so
    systemd marks ffd-sync.service failed rather than the app inventing its own
    alerting.
    """
    _configure_logging(verbose)
    settings = get_settings()

    if not settings.sleeper_league_id:
        typer.secho(
            "FFD_SLEEPER_LEAGUE_ID is not set. Copy .env.example to .env and fill it in.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    league_id = settings.sleeper_league_id

    with session_scope() as session:
        run = SyncRun(started_at=utcnow(), status="running")
        session.add(run)
        session.commit()

        try:
            with SleeperClient(settings) as client:
                written = sync_league(session, client, league_id)
                written += sync_users(session, client, league_id)
        except Exception as exc:
            session.rollback()
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = utcnow()
            session.add(run)
            session.commit()  # the failure record must survive
            log.error("Sync failed: %s", run.error)
            raise typer.Exit(code=1) from exc

        run.status = "ok"
        run.records_written = written
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
        typer.secho(f"Sync ok: {written} records written.", fg=typer.colors.GREEN)


@app.command()
def backup(verbose: bool = False) -> None:
    """Write a verified SQLite snapshot and prune old ones."""
    _configure_logging(verbose)
    path = create_backup()
    removed = prune_backups()
    typer.secho(f"Backup written: {path}", fg=typer.colors.GREEN)
    if removed:
        typer.echo(f"Pruned {len(removed)} old backup(s).")


@app.command()
def restore(
    source: Annotated[Path, typer.Argument(help="Path to a backup produced by `ffd backup`.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Replace the live database with a backup.

    Stop the API first: `systemctl --user stop ffd-api`.
    """
    _configure_logging()
    settings = get_settings()

    if not yes:
        typer.confirm(
            f"Replace {settings.resolved_db_path} with {source}?",
            abort=True,
        )

    target = restore_backup(source)
    typer.secho(f"Restored to {target}", fg=typer.colors.GREEN)
    typer.echo("Now run: uv run alembic upgrade head && systemctl --user start ffd-api")


@app.command()
def status() -> None:
    """Show the most recent sync runs."""
    _configure_logging()
    from sqlalchemy import select

    with session_scope() as session:
        runs = session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(10)).all()

    if not runs:
        typer.echo("No sync runs recorded yet.")
        return

    for run in runs:
        colour = typer.colors.GREEN if run.status == "ok" else typer.colors.RED
        typer.secho(
            f"#{run.id} {run.started_at.isoformat()} {run.status} "
            f"records={run.records_written} {run.error or ''}".strip(),
            fg=colour,
        )

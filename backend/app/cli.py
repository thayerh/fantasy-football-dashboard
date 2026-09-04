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
from app.fantasypros import FantasyProsClient
from app.fantasypros_adp import fetch_sleeper_adp
from app.fantasypros_web import fetch_cheatsheet
from app.ingest import (
    sync_adp,
    sync_draft,
    sync_league,
    sync_players,
    sync_rankings_api,
    sync_rankings_web,
    sync_sleeper_adp,
    sync_users,
    sync_vegas_projections,
)
from app.models import League, SyncRun
from app.sleeper import SleeperClient
from app.vegas import VegasParseError, parse_projections

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
                # Also here, not only on the fast timer: with ffd-draft.timer
                # disabled out of season the board still stays current.
                written += sync_draft(session, client, league_id)
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


@app.command(name="sync-draft")
def sync_draft_command(verbose: bool = False) -> None:
    """Fetch the draft board from Sleeper. Runs every 20s during the draft.

    Deliberately writes no sync_run row: at this cadence it would add ~4,300
    rows a day and drown the record of the real sync. The draft's own
    fetched_at is the freshness signal, and the draft page shows it. Failure
    still exits non-zero, so systemd reports it.
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

    try:
        with session_scope() as session, SleeperClient(settings) as client:
            written = sync_draft(session, client, settings.sleeper_league_id)
            # session_scope() does not commit — the caller owns the
            # transaction, same as `ffd sync`.
            session.commit()
    except Exception as exc:
        log.error("Draft sync failed: %s: %s", type(exc).__name__, exc)
        raise typer.Exit(code=1) from exc

    typer.secho(f"Draft sync ok: {written} records written.", fg=typer.colors.GREEN)


@app.command(name="sync-players")
def sync_players_command(verbose: bool = False) -> None:
    """Refresh the NFL player pool. Daily at most.

    The response is ~14 MB and Sleeper's docs ask for no more than one call a
    day, so this is deliberately not part of `ffd sync`. It needs no league id:
    the pool is the same for everyone.
    """
    _configure_logging(verbose)
    settings = get_settings()

    try:
        with session_scope() as session, SleeperClient(settings) as client:
            written = sync_players(session, client)
            session.commit()
    except Exception as exc:
        log.error("Player sync failed: %s: %s", type(exc).__name__, exc)
        raise typer.Exit(code=1) from exc

    typer.secho(f"Player sync ok: {written} players stored.", fg=typer.colors.GREEN)


@app.command(name="sync-rankings")
def sync_rankings_command(
    source: Annotated[
        str, typer.Option(help="'web' (cheat sheet, complete) or 'api' (top 10 per position).")
    ] = "web",
    season: Annotated[
        str | None, typer.Option(help="API source only. Defaults to the league's season.")
    ] = None,
    verbose: bool = False,
) -> None:
    """Refresh FantasyPros consensus rankings.

    `--source web` scrapes the cheat sheet: one request, the complete ranking
    set (~517 players) with the expert spread, and none of the JSON API's
    fifty-a-day budget. It is the source of truth and replaces the table
    outright.

    `--source api` is the fallback for when that page changes shape. It costs
    7 of 50 calls and returns only ~60 players, because the free tier caps
    every response at ten.

    Run `ffd sync-players` first either way: rankings are stored against
    Sleeper player ids.
    """
    _configure_logging(verbose)
    settings = get_settings()

    if source not in ("web", "api"):
        typer.secho(f"Unknown source {source!r}; expected 'web' or 'api'.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    with session_scope() as session:
        try:
            if source == "web":
                payload = fetch_cheatsheet(settings=settings)
                written = sync_rankings_web(session, payload)
            else:
                if season is None:
                    league = session.get(League, settings.sleeper_league_id)
                    season = league.season if league else None
                if not season:
                    typer.secho("No season known. Run `ffd sync` first, or pass --season.",
                                fg=typer.colors.RED, err=True)
                    raise typer.Exit(code=2)
                with FantasyProsClient(settings) as client:
                    written = sync_rankings_api(session, client, season)
            session.commit()
        except typer.Exit:
            raise
        except Exception as exc:
            log.error("Ranking sync failed: %s: %s", type(exc).__name__, exc)
            raise typer.Exit(code=1) from exc

    typer.secho(f"Ranking sync ok: {written} ranked players stored ({source}).",
                fg=typer.colors.GREEN)


@app.command(name="sync-adp")
def sync_adp_command(
    source: Annotated[
        str, typer.Option(help="'web' (Sleeper's own ADP, complete) or 'api' (cross-platform).")
    ] = "web",
    season: Annotated[
        str | None, typer.Option(help="API source only. Defaults to the ingested league's season.")
    ] = None,
    verbose: bool = False,
) -> None:
    """Refresh average draft position.

    `--source web` renders the real-time ADP page in a headless browser and
    reads its **Sleeper** column: ~270 players, the market this league actually
    drafts in, and none of the fifty-a-day API budget. It is the default.

    `--source api` is the fallback for when that page changes shape. It costs
    7 of 50 calls and returns FantasyPros' **cross-platform** average over five
    platforms — a different number, not a smaller sample of the same one, which
    is why `player_adp.source` records which one is in the table.

    Either way the table is replaced outright, so it never holds a mix. Run
    `ffd sync-players` first: ADP is stored against Sleeper player ids.
    """
    _configure_logging(verbose)
    settings = get_settings()

    if source not in ("web", "api"):
        typer.secho(f"Unknown source {source!r}; expected 'web' or 'api'.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    with session_scope() as session:
        try:
            if source == "web":
                entries = fetch_sleeper_adp(settings=settings)
                written = sync_sleeper_adp(session, entries, settings.fantasypros_scoring)
            else:
                if season is None:
                    league = session.get(League, settings.sleeper_league_id)
                    season = league.season if league else None
                if not season:
                    typer.secho("No season known. Run `ffd sync` first, or pass --season.",
                                fg=typer.colors.RED, err=True)
                    raise typer.Exit(code=2)
                with FantasyProsClient(settings) as client:
                    written = sync_adp(session, client, season)
            session.commit()
        except typer.Exit:
            raise
        except Exception as exc:
            log.error("ADP sync failed: %s: %s", type(exc).__name__, exc)
            raise typer.Exit(code=1) from exc

    typer.secho(f"ADP sync ok: {written} players stored ({source}).", fg=typer.colors.GREEN)



@app.command(name="sync-vegas")
def sync_vegas_command(
    path: Annotated[Path, typer.Argument(help="The exported projections file.")],
    verbose: bool = False,
) -> None:
    """Import Vegas season projections from a hand-exported file.

    The only sync that reads a file instead of a service, and the only one with
    no timer: the export is produced by hand, so nothing here should run on a
    schedule pretending the number is fresh. `player_projection.fetched_at`
    records when it was imported, not when the odds were priced.

    The table is replaced outright, so a re-import is the way to update it.
    Run `ffd sync-players` first: projections are stored against Sleeper ids.
    """
    _configure_logging(verbose)

    if not path.is_file():
        typer.secho(f"No such file: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    with session_scope() as session:
        try:
            rows = parse_projections(path.read_text(encoding="utf-8"))
            written = sync_vegas_projections(session, rows)
            session.commit()
        except VegasParseError as exc:
            log.error("Could not parse %s: %s", path, exc)
            raise typer.Exit(code=2) from exc
        except Exception as exc:
            log.error("Projection import failed: %s: %s", type(exc).__name__, exc)
            raise typer.Exit(code=1) from exc

    skipped = len(rows) - written
    typer.secho(
        f"Vegas import ok: {written} of {len(rows)} players stored, {skipped} unmatched.",
        fg=typer.colors.GREEN,
    )


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

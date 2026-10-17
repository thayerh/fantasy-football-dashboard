"""Command-line entry points, driven by systemd timers.

`ffd sync` is also the *only* way to trigger ingestion by hand. There is no
web route for it: Cloudflare Access authenticates all ~15 leaguemates
identically, so an in-app admin endpoint behind it would be an admin action any
member could fire. Requiring shell access is the authorization boundary.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from app.backup import create_backup, prune_backups, restore_backup
from app.config import REPO_ROOT, get_settings
from app.db import session_scope, utcnow
from app.dynasty import DynastyParseError, read_export
from app.ingest import (
    sync_draft,
    sync_dynasty_rankings,
    sync_league,
    sync_nfl_rosters,
    sync_players,
    sync_rosters,
    sync_traded_picks,
    sync_users,
    sync_vegas_projections,
)
from app.models import SyncRun
from app.nflverse import NflverseClient
from app.rosters import RosterReport, build_report, format_history, search_players
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
    """Fetch the league, its members, its rosters and its draft into SQLite.

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
                # One request for all twelve teams, and the only source of
                # records, points and who owns whom.
                written += sync_rosters(session, client, league_id)
                # The future-pick ledger moves when a roster does, and it is
                # the other half of what a dynasty team owns.
                written += sync_traded_picks(session, client, league_id)
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
    """Fetch the draft board from Sleeper. Runs every 2s during the draft.

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


@app.command(name="import-rankings")
def import_rankings_command(
    paths: Annotated[
        list[Path],
        typer.Argument(help="Export files, or directories of them. Defaults to data/."),
    ] = None,
    verbose: bool = False,
) -> None:
    """Import the dynasty ranking exports.

    Takes files or directories; a directory is scanned for *.csv, one level
    deep. Each file is identified by its **header**, not its name, so a
    download that landed as "rankings (1).csv" still imports as what it is, and
    a file whose format has changed under us fails loudly instead of importing
    as something else.

    Four boards are recognised — FantasyPros dynasty, FantasyCalc, TheTradeCalc
    and Sleeper ADP. Each replaces only its own rows, so re-importing one
    leaves the other three alone.

    Like `sync-vegas`, this reads files rather than a service: the exports are
    downloaded by hand, so there is no timer and nothing here pretends the
    numbers are fresher than the last import. Run `ffd sync-players` first —
    rankings are stored against Sleeper player ids.
    """
    _configure_logging(verbose)

    try:
        # Relative to the repo, not the cwd: systemd units and ad-hoc shell
        # invocations run from different directories, same as the database.
        files = _ranking_files(paths or [REPO_ROOT / "data"])
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    with session_scope() as session:
        results: list[tuple[str, Path, int, int]] = []
        try:
            for path in files:
                source, rows = read_export(path)
                written = sync_dynasty_rankings(session, source, rows)
                results.append((source, path, written, len(rows)))
            session.commit()
        except DynastyParseError as exc:
            log.error("Could not parse an export: %s", exc)
            raise typer.Exit(code=2) from exc
        except Exception as exc:
            log.error("Ranking import failed: %s: %s", type(exc).__name__, exc)
            raise typer.Exit(code=1) from exc

    for source, path, written, total in results:
        typer.secho(
            f"  {source:<13} {written:>4} of {total:>4} matched  ({path.name})",
            fg=typer.colors.GREEN if written else typer.colors.YELLOW,
        )
    typer.secho(
        f"Imported {len(results)} board(s), {sum(r[2] for r in results)} rows.",
        fg=typer.colors.GREEN,
    )


def _ranking_files(paths: list[Path]) -> list[Path]:
    """Expand the arguments into the export files to import.

    A missing path is an error rather than a skip: the usual way to get one is
    a typo, and silently importing three of four boards would leave the
    aggregate quietly averaging the wrong set.
    """
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.csv")))
        elif path.is_file():
            files.append(path)
        else:
            raise ValueError(f"No such file or directory: {path}")

    if not files:
        raise ValueError(f"No .csv exports found in {', '.join(str(p) for p in paths)}")
    return files


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


@app.command(name="sync-nfl-rosters")
def sync_nfl_rosters_command(
    seasons: Annotated[
        str | None,
        typer.Option(help="Seasons to load: '2024', '2017-2026', or '2019,2024'. "
                          "Defaults to the last FFD_NFLVERSE_HISTORY_SEASONS seasons."),
    ] = None,
    verbose: bool = False,
) -> None:
    """Load NFL active-roster history from nflverse.

    The full ten-season backfill downloads ~150 MB and takes a few minutes; it
    is a one-time cost. During the season only the current year changes, so the
    weekly refresh is `--seasons <this year>` and downloads one file.

    Only ACT rows are stored — players on an active roster. Practice squad
    (DEV), injured reserve (RES) and cut players are read and discarded, which
    is what makes the 2026 rows exactly the 53-man rosters.
    """
    _configure_logging(verbose)
    settings = get_settings()

    try:
        wanted = _parse_seasons(seasons) if seasons else _default_seasons(settings)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    with session_scope() as session:
        try:
            with NflverseClient(settings) as client:
                written = sync_nfl_rosters(session, client, wanted)
            session.commit()
        except Exception as exc:
            session.rollback()
            log.error("Roster sync failed: %s: %s", type(exc).__name__, exc)
            raise typer.Exit(code=1) from exc

    typer.secho(
        f"Roster sync ok: {written} stints across {len(wanted)} season(s) "
        f"({wanted[0]}-{wanted[-1]}).",
        fg=typer.colors.GREEN,
    )


def _default_seasons(settings) -> list[int]:
    """The last N seasons, counting back from the current one.

    An NFL season is named for the year it starts, so anything before September
    still belongs to the previous season's year.
    """
    now = utcnow()
    current = now.year if now.month >= 9 else now.year - 1
    span = max(1, settings.nflverse_history_seasons)
    return list(range(current - span + 1, current + 1))


def _parse_seasons(value: str) -> list[int]:
    """'2017-2026' / '2024' / '2019,2024' -> a sorted list of seasons."""
    seasons: set[int] = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            try:
                lo, hi = int(start), int(end)
            except ValueError as exc:
                raise ValueError(f"Not a season range: {chunk!r}") from exc
            if hi < lo:
                raise ValueError(f"Backwards season range: {chunk!r}")
            seasons.update(range(lo, hi + 1))
        else:
            try:
                seasons.add(int(chunk))
            except ValueError as exc:
                raise ValueError(f"Not a season: {chunk!r}") from exc
    if not seasons:
        raise ValueError("No seasons given.")
    return sorted(seasons)


@app.command(name="roster-check")
def roster_check_command(
    team: Annotated[str, typer.Argument(help="Team: 'LV', 'OAK', 'Raiders', 'Las Vegas Raiders'.")],
    players: Annotated[
        list[str] | None,
        typer.Argument(help="Player names. Omit to read them from --file or stdin."),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", "-f", help="List of players, one per line."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Split a list of players into those who did and did not play for a team.

    Names can be given three ways — as arguments, from a file, or on stdin:

        ffd roster-check LV "Derek Carr" "Patrick Mahomes"
        ffd roster-check LV --file players.txt
        pbpaste | ffd roster-check "Las Vegas Raiders"

    A file or stdin takes one player per line; blank lines and `#` comments are
    ignored, list markers ("1.", "- ") are stripped, and a trailing position
    ("Josh Allen, QB") disambiguates two players with one name. Relocations are
    handled, so OAK and LV are one franchise and either spans all ten seasons.

    Exits 0 whatever the counts. Unreadable input, an unknown team, or an
    unloaded database exit 2 — so a script can tell "nobody played for them"
    apart from "the question could not be answered".
    """
    _configure_logging()

    if players and file is not None:
        typer.secho(
            "Give player names either as arguments or with --file, not both.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if players:
        # One name per line is what the parser expects, and it is also what
        # lets a quoted argument carry a position hint: "Josh Allen, QB".
        text = "\n".join(players)
    elif file is not None:
        if not file.is_file():
            typer.secho(f"No such file: {file}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        text = file.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            typer.secho(
                "Reading players from stdin, one per line; end with Ctrl-D.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        text = sys.stdin.read()

    with session_scope() as session:
        try:
            report = build_report(session, team, text)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc

        if report.first_season is None:
            typer.secho(
                "No roster history loaded. Run `ffd sync-nfl-rosters` first.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

        if not report.lookups:
            typer.secho("No player names given.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)

        if as_json:
            typer.echo(json.dumps(_report_as_dict(report), indent=2))
        else:
            _print_report(report)


def _player_as_dict(lookup, franchise: str) -> dict:
    """One submitted name, with every candidate it resolved to."""
    return {
        "query": lookup.query,
        "played_for_team": lookup.played_for(franchise),
        "found": lookup.found,
        "ambiguous": lookup.ambiguous,
        "suggestions": lookup.suggestions,
        "matches": [
            {
                "gsis_id": c.gsis_id,
                "name": c.full_name,
                "position": c.position,
                "sleeper_id": c.sleeper_id,
                "with_team": format_history(c, franchise),
                "all_teams": format_history(c),
                "seasons_with_team": [s.season for s in c.stints_with(franchise)],
            }
            for c in lookup.candidates
        ],
    }


def _report_as_dict(report: RosterReport) -> dict:
    """The three buckets, pre-split, so a caller never has to re-derive them.

    `not_found` is kept apart from `did_not_play` rather than merged into it:
    the database cannot say a player it has never heard of did not play, and a
    script that treats the two the same will quietly report a typo as a fact.
    """
    franchise = report.franchise
    return {
        "team": franchise,
        "seasons": [report.first_season, report.last_season],
        "submitted": report.total,
        "played_count": report.count,
        "did_not_play_count": len(report.others),
        "not_found_count": len(report.unknown),
        "played": [_player_as_dict(lk, franchise) for lk in report.matched],
        "did_not_play": [_player_as_dict(lk, franchise) for lk in report.others],
        "not_found": [_player_as_dict(lk, franchise) for lk in report.unknown],
        # Every submitted name in the order it arrived, for callers that want
        # to line results up against their own input.
        "players": [_player_as_dict(lk, franchise) for lk in report.lookups],
    }


def _print_report(report: RosterReport) -> None:
    franchise = report.franchise
    width = min(max(max((len(lk.query) for lk in report.lookups), default=12), 12), 30)

    typer.secho(
        f"\n{franchise} — active rosters {report.first_season}-{report.last_season}",
        bold=True,
    )

    if report.matched:
        typer.echo("")
        typer.secho(f"PLAYED FOR {franchise} ({len(report.matched)})", fg=typer.colors.GREEN,
                    bold=True)
        for lk in report.matched:
            for c in lk.hits(franchise):
                flag = "  [ambiguous name]" if lk.ambiguous else ""
                typer.echo(
                    f"  {lk.query:<{width}}  {(c.position or '--'):<3} "
                    f"{format_history(c, franchise)}{flag}"
                )

    if report.others:
        typer.echo("")
        typer.secho(f"DID NOT PLAY FOR {franchise} ({len(report.others)})",
                    fg=typer.colors.YELLOW, bold=True)
        for lk in report.others:
            # Showing where they did play is what makes a "no" checkable.
            c = lk.candidates[0]
            typer.echo(
                f"  {lk.query:<{width}}  {(c.position or '--'):<3} {format_history(c)}"
            )

    if report.unknown:
        typer.echo("")
        typer.secho(
            f"NOT FOUND ({len(report.unknown)}) — no active-roster row in "
            f"{report.first_season}-{report.last_season}",
            fg=typer.colors.RED,
            bold=True,
        )
        for lk in report.unknown:
            hint = f"   did you mean: {', '.join(lk.suggestions)}?" if lk.suggestions else ""
            typer.echo(f"  {lk.query}{hint}")

    if report.uncertain:
        typer.echo("")
        typer.secho(
            f"CHECK THESE ({len(report.uncertain)}) — the name matches more than one player",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        for lk in report.uncertain:
            typer.echo(f"  {lk.query}")
            for c in lk.candidates:
                mark = "*" if c.stints_with(franchise) else " "
                typer.echo(
                    f"    {mark} {(c.position or '--'):<3} {c.full_name} "
                    f"({c.college or 'college unknown'}) — {format_history(c)}"
                )

    typer.echo("")
    summary = f"{report.count} played for {franchise}, {len(report.others)} did not"
    if report.unknown:
        summary += f", {len(report.unknown)} not found"
    typer.secho(f"{summary}  ({report.total} submitted).", bold=True)
    typer.echo("")


@app.command(name="roster-search")
def roster_search_command(
    name: Annotated[str | None, typer.Argument(help="Part of a player's name.")] = None,
    team: Annotated[str | None, typer.Option("--team", "-t", help="Franchise filter.")] = None,
    position: Annotated[str | None, typer.Option("--position", "-p")] = None,
    season: Annotated[int | None, typer.Option("--season", "-s")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
) -> None:
    """Search the roster history.

        ffd roster-search jefferson
        ffd roster-search --team LV --season 2019 --position QB
    """
    _configure_logging()

    with session_scope() as session:
        try:
            results = search_players(
                session, name=name, team=team, position=position, season=season, limit=limit
            )
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc

    if not results:
        typer.echo("No players matched.")
        return

    for c in results:
        typer.echo(
            f"{c.full_name:<26} {(c.position or '--'):<3} {format_history(c)}"
        )
    typer.echo(f"\n{len(results)} player(s).")

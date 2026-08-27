# Fantasy Football Dashboard

A private dashboard for a Sleeper fantasy football league, self-hosted and
exposed to leaguemates through a Cloudflare Tunnel.

**Current scope:** ingests the league and its members from the Sleeper API on a
5-minute timer and renders them. Standings, matchups, power rankings, and
transactions are intended to land additively on top of this.

## Stack

- **Backend** — FastAPI + SQLAlchemy 2.0 + SQLite (WAL), Alembic for migrations
- **Ingestion** — a `ffd sync` CLI run by a systemd timer, not an in-process scheduler
- **Frontend** — React 19 + Vite + TypeScript + TanStack Query + Tailwind
- **Ingress** — Cloudflare Tunnel, with Cloudflare Access enforcing an email allowlist
- **Runtime** — Python 3.12 via `uv`, systemd user services

## Quick start

```bash
cp .env.example .env      # set FFD_SLEEPER_LEAGUE_ID
uv sync
uv run alembic upgrade head
uv run ffd sync

npm --prefix frontend ci
npm --prefix frontend run build

uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>. API docs are at `/docs`.

For frontend development with hot reload, run the API as above and in another
terminal `npm --prefix frontend run dev` — Vite proxies `/api` to port 8000.

## Commands

| Command | Purpose |
|---|---|
| `uv run ffd sync` | Fetch the league and members into SQLite |
| `uv run ffd status` | Show recent sync runs |
| `uv run ffd backup` | Write a verified snapshot, prune old ones |
| `uv run ffd restore <file>` | Replace the database with a backup |
| `uv run pytest` | Run the test suite |
| `uv run ruff check .` | Lint |

## Design notes

**One writer, by construction.** `ffd sync` is a short-lived process launched by
a systemd timer; the API process only reads. A oneshot service cannot overlap
itself, so concurrent writes can't happen. Ingestion failures surface in
`systemctl --user status ffd-sync` rather than needing app-level alerting.

**No admin route.** Cloudflare Access authenticates all ~15 leaguemates
identically — it can't distinguish the commissioner from anyone else. An
in-app sync endpoint sitting behind it would therefore be an admin action any
member could fire, so the manual trigger is the CLI, gated by shell access.

**Every table keeps a `raw` payload.** When a later feature needs a Sleeper
field that isn't modelled yet, it can be backfilled from stored data rather than
re-fetched — which for historical data is often impossible. `raw` is never
exposed by the API; responses go through Pydantic models.

This paid for itself immediately. The live API differs from the published docs
in two ways worth knowing before touching the ingest code:

- `/league/<id>/users` does **not** return `username`, despite the docs example
  showing it. `display_name` is the real identifier.
- The top-level `avatar` id is frequently a shared Sleeper *default* — in this
  league, seven distinct ids covered twelve members. The member's actual image
  is `metadata.avatar`, a full URL, stored as `league_user.avatar_url` and
  preferred by the UI. Rendering from the id alone gives several people the same
  picture. Migration `6cdb67962daf` backfilled it from `raw` with no re-fetch.

**UTC everywhere.** SQLAlchemy's `DateTime(timezone=True)` silently returns
*naive* datetimes on SQLite, so all timestamp columns use a `UtcDateTime` type
that rejects naive input and re-attaches UTC on read. Ruff's `DTZ` rules catch
naive `datetime` calls at lint time. The browser is the only place local time
appears.

**Frontend refresh is decoupled from ingestion.** The page polls every 60s for
responsiveness; the ingest timer runs every 5m. They're separate knobs. The
header shows data age and turns amber when the API reports `stale`, so a broken
timer is visible instead of quietly serving old numbers as current.

## Deployment

See [deploy/README.md](deploy/README.md) for the tunnel, Cloudflare Access,
systemd units, and the backup/restore procedure.

## Layout

```
backend/app/       FastAPI app, models, CLI, Sleeper client, ingestion
backend/migrations Alembic
frontend/src/      React SPA
tests/             pytest + respx (no network)
deploy/            systemd units and deployment docs
```

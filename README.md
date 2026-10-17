# Fantasy Football Dashboard

A private dashboard for a **12-team superflex PPR dynasty** league on Sleeper,
self-hosted and exposed to leaguemates through a Cloudflare Tunnel. The league's
constitution lives in [`rulebook/`](rulebook/); the lineup is
`QB · RB · RB · WR · WR · WR · TE · FLEX · FLEX · SUPERFLEX`, with **no K and no
DEF** — which is why neither appears anywhere in the player pool.

**Current scope:** ingests the league, its members, its rosters, its future
pick ledger and its draft from the Sleeper API on a 5-minute timer and renders
them — standings, a draft board, a members directory, and a rosters page
showing each team's lineup, bench, taxi squad, IR and rookie picks against the
dynasty rankings. Matchups, power rankings and transactions are intended to
land additively on top of this.

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
| `uv run ffd sync` | Fetch the league, members, rosters, picks and draft into SQLite |
| `uv run ffd sync-players` | Refresh the Sleeper player pool (daily at most) |
| `uv run ffd fetch-rankings` | Fetch FantasyPros, FantasyCalc, TheTradeCalc and KTC boards, save dated CSVs, import (daily timer) |
| `uv run ffd import-rankings` | Import hand exports from `data/`, or a saved snapshot from `data/rankings/<date>/` |
| `uv run ffd sync-vegas <file>` | Import Vegas season projections |
| `uv run ffd status` | Show recent sync runs |
| `uv run ffd backup` | Write a verified snapshot, prune old ones |
| `uv run ffd restore <file>` | Replace the database with a backup |
| `uv run ffd sync-nfl-rosters` | Load NFL active-roster history from nflverse |
| `uv run ffd roster-check <team> <names...>` | Split a list of players into who did and didn't play for that team |
| `uv run ffd roster-search <name>` | Search the roster history |
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

**Roster history is active players only.** `nfl_stint` stores one row per
player, franchise and season, built from nflverse's *weekly* roster files with
only `ACT` rows kept — practice squad, IR and cut players are read and
discarded. Weekly rather than season-level because the season file collapses a
player to one team, so a mid-season trade would vanish from the other team's
history. Relocations are folded into a single `franchise` (OAK and LV are both
LV) while `team` keeps the abbreviation that season actually used, so a query
for the Raiders spans all ten years and a 2018 result still reads "OAK".

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
- `/league/<id>/traded_picks` is a ledger of *exceptions*, not holdings: a pick
  nobody has traded never appears, because every roster owns its own picks in
  every future draft. `/api/rosters` walks the tradeable window
  (`rulebook/08-trades.md` §8.3) applying the ledger over that default. Its ids
  are **roster** ids, not user ids, and `roster_id` is the pick's original
  owner while `owner_id` is whoever holds it now. A reversed trade *disappears*
  from the endpoint rather than reappearing with its old owner, which is why
  the sync deletes rows Sleeper no longer reports — an empty list included.
- `/league/<id>/rosters` returns four flat lists of player ids and **no bench**:
  the bench is `players` minus `starters`, `taxi` and `reserve`, and
  `/api/rosters` derives it. `starters` is *positional* against the league's
  `roster_positions`, with the string `"0"` for an empty lineup slot, so it must
  never be sorted. Points arrive split across two integers — `fpts` 1234 with
  `fpts_decimal` 56 means 1234.56 — and are recombined on the way in.

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
backend/app/dynasty.py   parsers for the four dynasty ranking exports
backend/app/nflverse.py  nflverse client, franchise aliases
backend/app/api/         the routes (api/rosters.py is the fantasy rosters)
backend/app/rosters.py   NFL roster-history queries (read-only)
backend/migrations Alembic
frontend/src/      React SPA
tests/             pytest + respx (no network)
data/              the database, and the ranking exports to import
rulebook/          the league constitution
deploy/            systemd units and deployment docs
```

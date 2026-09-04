# Deploying on the desktop

Everything runs as **systemd user services** — no Docker, no root daemon.

Paths below assume the repo lives at `~/devcode/fantasy/fantasy-football-dashboard`.
If you move it, update `WorkingDirectory=` in the `.service` files.

## Architecture

```
ffd-sync.timer  ──▶ ffd-sync.service  (oneshot: `ffd sync`)       ──┐
ffd-draft.timer ──▶ ffd-draft.service (oneshot: `ffd sync-draft`) ──┤
                                                                    ├─▶ data/ffd.db (WAL)
ffd-api.service (uvicorn, long-running) ─── reads only ─────────────┘
                                                    ▲
ffd-backup.timer  ──▶ ffd-backup.service            │
ffd-players.timer ──▶ ffd-players.service          │
ffd-rankings.timer ─▶ ffd-rankings.service         │
                                                    │
cloudflared (ffd-tunnel.service) ──▶ 127.0.0.1:8000 ┘
```

The sync processes are the **only** writers, and the API never writes. A
oneshot service cannot overlap itself, so neither timer can stack two runs of
its own job. The two write disjoint tables — `ffd sync` the league and members,
`ffd sync-draft` the draft and its picks — and SQLite's WAL plus a 5s
`busy_timeout` covers the moment they overlap each other.

## 1. First-time setup

```bash
cd ~/devcode/fantasy/fantasy-football-dashboard

cp .env.example .env
# Set FFD_SLEEPER_LEAGUE_ID — find it in the Sleeper web app URL:
#   sleeper.com/leagues/<league_id>/team
$EDITOR .env

uv sync
uv run alembic upgrade head
npm --prefix frontend ci
npm --prefix frontend run build

# Verify ingestion works before involving systemd.
uv run ffd sync
uv run ffd status
```

## 2. Install cloudflared

Not installed by default on this machine:

```bash
curl -L --output /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i /tmp/cloudflared.deb
cloudflared --version
```

## 3. Create the tunnel

```bash
cloudflared tunnel login          # opens a browser; pick your domain
cloudflared tunnel create ffd     # note the tunnel UUID it prints
cloudflared tunnel route dns ffd league.example.com
```

Then write `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /home/<you>/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: league.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

## 4. Lock it down with Cloudflare Access

The app itself has **no login screen**. Access is what keeps the league private,
so do not skip this step — without it the hostname is public.

1. Cloudflare dashboard → **Zero Trust** → **Access** → **Applications**
2. **Add an application** → *Self-hosted*
3. Application domain: `league.example.com`
4. Add a policy: Action **Allow**, Include → **Emails** → your ~15 leaguemates
   (or *Emails ending in* if they share a domain)
5. Save

Cloudflare then injects `Cf-Access-Authenticated-User-Email` into every
authenticated request. The app currently only logs it; it's the hook for a
future per-user view without building a login system.

## 5. Enable the services

```bash
mkdir -p ~/.config/systemd/user
cp deploy/ffd-*.service deploy/ffd-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload

systemctl --user enable --now ffd-api ffd-tunnel ffd-sync.timer ffd-backup.timer \
                             ffd-players.timer ffd-rankings.timer

# The draft board's fast poll. Deliberately NOT enabled by default — see below.
# systemctl --user start ffd-draft.timer

# Keep services running after logout and across reboots.
loginctl enable-linger "$USER"
```

Check it:

```bash
systemctl --user status ffd-api ffd-tunnel
systemctl --user list-timers 'ffd-*'
curl -s localhost:8000/api/health | jq
journalctl --user -u ffd-sync -f
```

## The player pool

`ffd sync-players` fetches Sleeper's whole player list — ~14 MB, ~12,000
players — and stores the ~1,900 that are draftable. It backs the draft page's
"best available" list, and it is the one endpoint Sleeper asks you to call no
more than once a day, so it has its own daily timer and is deliberately absent
from `ffd sync`.

Run it once before the draft if the timer has never fired:

```bash
uv run ffd sync-players
```

## FantasyPros rankings

`ffd sync-rankings` fills the "best available" ordering with FantasyPros
consensus (ECR). Run `ffd sync-players` first — rankings are stored against
Sleeper player ids — and the table is replaced outright each time, so the
newest fetch is the truth.

**Two sources, and the default is the scrape.**

| | `--source web` (default) | `--source api` |
|---|---|---|
| what | the public PPR cheat sheet | the JSON API |
| players | **~515**, the complete set | ~60 |
| extras | expert spread (min/max/ave/std) | — |
| cost | 1 HTTP request | 7 of 50 daily API calls |
| risk | breaks if the page changes | supported interface |

The cheat-sheet page renders its table from an embedded JSON blob with the same
shape the API returns, so the scraper reads the blob rather than the markup —
no dependence on class names or how many rows the page chose to draw. It fails
loudly if the blob is missing, because a silent empty result would look like
"nothing is ranked today".

The API path stays as the fallback. Its free tier caps every response at ten
players and ignores a `limit` parameter, so it needs one call per position and
still returns a fraction of the board.

```bash
uv run ffd sync-rankings                  # scrape, 1 request
uv run ffd sync-rankings --source api     # fallback, 7 API calls
```

Two things to know about the matching, since the two databases disagree:

* Names are reconciled by a ladder — name+position, then name alone
  (FantasyPros files fullbacks as RB), then last name+team (nicknames:
  "Hollywood Brown" is Sleeper's "Marquise Brown"), then team abbreviation for
  defenses (FantasyPros says JAC, Sleeper says JAX). Ambiguity always loses: a
  rung finding two candidates falls through rather than guessing.
* Misses are logged, not hidden. Today that is one free agent out of 517, plus
  one collision where two ranked players share a name and Sleeper lists one.

Scraping is a weaker footing than the API: FantasyPros' terms may not permit
it, and the page's shape is not a contract. That is why the request is made
once a day with a real User-Agent, and why the API path is kept working.

## ADP

`ffd sync-adp` stores average draft position, which the draft board uses to
show what each pick cost against the market. It runs from the same daily unit
as the rankings.

**It is Sleeper's own ADP** — the market this league actually drafts in — and it
comes from rendering FantasyPros' real-time ADP page in a headless browser:

```
uv run ffd sync-adp                # --source web, the default: ~270 players
uv run ffd sync-adp --source api   # fallback: 7 API calls, ~60 players
```

The two sources are **different numbers, not different sample sizes of the same
one**: `web` is Sleeper alone, `api` is FantasyPros' average across five
platforms. `player_adp.source` records which is in the table, and each sync
replaces the table outright so it never holds a mix.

*Why a browser is needed.* The cheat sheet embeds its rankings as JSON in the
HTML, so `httpx` is enough for `sync-rankings`. The ADP page does not — it ships
an empty table and fills it in from JavaScript, so fetching the HTML returns
nothing. Rendering it is the only way to read the Sleeper column.

*Why we render the page rather than calling the endpoint it calls.* That page
authenticates its own request to `api.fantasypros.com/v2/...` with a key
embedded in its JavaScript. The key is FantasyPros', not ours, and using it
would be borrowing their credential to get past the limits on ours. Loading the
page in a browser is what the page is served for; reading the table it draws is
not the same act.

*The browser.* Playwright drives the system Google Chrome if it is installed,
which is why `playwright install` is not a deployment step here. On a host
without Chrome, run `uv run playwright install chromium` once. A scrape takes
about three seconds.

*Coverage.* ~270 players carry a Sleeper ADP, against ~515 ranked. Every player
with an ADP also has a ranking, so a pick inside the top 270 shows both board
stats and one beyond it shows neither.

*Fragility.* The page's shape is not a contract, so the extractor finds the
Sleeper column by its **header text** rather than its position — FantasyPros can
reorder the table without breaking this. If the column disappears entirely the
sync fails loudly rather than silently storing an empty set, and
`--source api` is the way back until the extractor is fixed.

## Draft day

`ffd-draft.timer` polls the draft board every 20 seconds. It is the one timer
that is not enabled by default, because a process every 20 seconds all year to
watch a draft that happens once is waste, not resilience.

```bash
systemctl --user start ffd-draft.timer     # before the draft
journalctl --user -u ffd-draft -f          # watch it run
systemctl --user stop  ffd-draft.timer     # afterwards
```

With it off the board still updates — `ffd sync` carries the draft too — just
at five-minute resolution instead of twenty seconds. Costs, for reference: each
run makes three Sleeper requests (~9/minute against a documented ceiling near
1000), and a completed draft with every pick already stored skips the picks
request, so leaving the timer on after the draft is harmless if you forget.

The board's freshness comes from `draft.fetched_at`, shown in the page header —
`ffd sync-draft` writes no `sync_run` row, which at this cadence would add
~4,300 rows a day and bury the record of the real sync.

## Operations

| Task | Command |
|---|---|
| Sync now | `systemctl --user start ffd-sync` |
| Recent syncs | `uv run ffd status` |
| Back up now | `systemctl --user start ffd-backup` |
| Tail ingestion logs | `journalctl --user -u ffd-sync -n 50` |
| Restart the app | `systemctl --user restart ffd-api` |

After changing Python code: `systemctl --user restart ffd-api`.
After changing frontend code: `npm --prefix frontend run build` (no restart needed —
the files are read from disk per request).
After changing models: `uv run alembic revision --autogenerate -m "..."` then
`uv run alembic upgrade head`.

## Backups

`ffd backup` uses SQLite's `VACUUM INTO`, which takes a consistent snapshot
without stopping writers. **Do not** back up by copying `ffd.db` — with WAL
active that can produce a torn, unusable file.

Each backup is verified with `PRAGMA integrity_check` immediately after being
written; the command fails loudly if the result isn't `ok`. The newest
`FFD_BACKUP_KEEP` (default 14) are retained in `data/backups/`.

Because backups live on the same disk as the database, they protect against
accidental deletion and bad migrations — not drive failure. Copy
`data/backups/` somewhere else periodically if that matters to you.

### Restoring

```bash
systemctl --user stop ffd-api
uv run ffd restore data/backups/ffd-20260901T030000Z.db
uv run alembic upgrade head    # in case the backup predates a migration
systemctl --user start ffd-api
```

The existing database is moved aside to `ffd.db.pre-restore-<timestamp>` rather
than deleted, so a mistaken restore is itself recoverable.

**If the CLI is what's broken**, do it by hand:

```bash
systemctl --user stop ffd-api
cd ~/devcode/fantasy/fantasy-football-dashboard/data
mv ffd.db ffd.db.broken
rm -f ffd.db-wal ffd.db-shm     # sidecars belong to the replaced database
cp backups/ffd-20260901T030000Z.db ffd.db
sqlite3 ffd.db "PRAGMA integrity_check;"   # must print: ok
systemctl --user start ffd-api
```

Rehearse this once while nothing is wrong. A restore procedure that has never
been run is not a procedure.

## Troubleshooting

**The dashboard shows an amber "Data may be out of date" banner.**
Ingestion is failing or hasn't run. `systemctl --user status ffd-sync` and
`uv run ffd status` will say why.

**`ffd sync` exits 2.** `FFD_SLEEPER_LEAGUE_ID` is unset — check `.env`.

**Sync fails with a 404.** The league id is wrong, or the league was deleted.
404s are deliberately not retried; retrying a permanent error only burns rate
limit.

**The site 502s through the tunnel.** `ffd-api` is down, or bound to the wrong
port. `curl localhost:8000/api/health` locally to tell the two apart.

**"No frontend build ... serving API only" in the logs.** Run
`npm --prefix frontend run build`.

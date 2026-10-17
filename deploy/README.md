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
ffd-backup.timer   ──▶ ffd-backup.service           │
ffd-players.timer  ──▶ ffd-players.service          │
ffd-rankings.timer ──▶ ffd-rankings.service         │
                                                    │
cloudflared (ffd-tunnel.service) ──▶ 127.0.0.1:8000 ┘
```

The sync processes are the **only** writers, and the API never writes. A
oneshot service cannot overlap itself, so neither timer can stack two runs of
its own job. The two write disjoint tables — `ffd sync` the league, its members
and its rosters, `ffd sync-draft` the draft and its picks — and SQLite's WAL
plus a 5s `busy_timeout` covers the moment they overlap each other.

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

## Dynasty rankings

The "best available" ordering comes from the ranking boards, and four of them
fetch themselves. `ffd fetch-rankings` runs daily from `ffd-rankings.timer`
(07:30, after the player pool refresh) and pulls each board straight from the
site — no browser, no keys:

| source | where it comes from | rows |
|---|---|---|
| `fantasypros` | FantasyPros dynasty **superflex** page, its embedded `ecrData` | ~400 |
| `fantasycalc` | FantasyCalc's values API (12 teams, 2 QB, PPR) | ~400 |
| `thetradecalc` | TheTradeCalc's assets API, 12-team superflex PPR baseline | ~600 |
| `ktc` | KeepTradeCut's dynasty-rankings page, its embedded `ktc-players` JSON | ~460 |
| `fantasycalc-redraft` | FantasyCalc, redraft | ~200 |
| `thetradecalc-redraft` | TheTradeCalc, redraft value | ~450 |
| `ktc-redraft` | KeepTradeCut's fantasy-rankings page | ~300 |
| `sleeper-adp` | where Sleeper drafters actually take him — **hand export only** | ~280 |

```bash
uv run ffd fetch-rankings              # fetch, snapshot, import
uv run ffd fetch-rankings --no-import  # fetch and snapshot only
```

**History is kept.** Every run writes each board to
`data/rankings/<YYYY-MM-DD>/<source>.csv` — every column the site published,
not just the ones the app stores — plus a copy in `data/rankings/latest/`.
The dated folders are never pruned (about 300 KB a day). Each stored board is
also appended to the `dynasty_ranking_history` table under that date, one row
per (date, source, player) with rank, positional rank, tier and value, for
reading a player's trend; re-running on the same day replaces that day's rows
rather than duplicating them. Any day can be put back on the live board with
`uv run ffd import-rankings data/rankings/<date>`.

**One average of the four dynasty boards.** The dashboard's **Aggregate** tab
is the mean of FantasyPros, FantasyCalc, TheTradeCalc and KTC, computed per
request and never stored. Sleeper ADP is left out deliberately: it is what the
room *did*, and folding an outcome into an average of opinions would
double-count the very thing the rankings are read against. The redraft boards
are left out too — they are their own tabs, and a dynasty tab never borrows a
redraft value or tier for its detail columns.

*Failures.* A board that fails to fetch or parse — or comes back with fewer
than 50 players, which means a truncated page or a changed format — is skipped
and keeps yesterday's rows; the other boards still land, and the run exits 1
so `systemctl --user status ffd-rankings` shows it failed.

*Hand imports.* `ffd import-rankings` still reads the four export formats
(FantasyPros, FantasyCalc, TheTradeCalc, Sleeper ADP) from `data/`, and is the
only way to load Sleeper ADP. Each import replaces only its own source's rows.
Hand imports write no history, since a file imported today may say what a site
said last month. Run `ffd sync-players` first — rankings are stored against
Sleeper player ids.

*Identification.* Files are recognised by their **header**, not their name, so
a download that landed as `rankings (1).csv` still imports as what it is; a
snapshot names its board in its `source` column. A header matching none of them fails loudly rather than importing as
something else — which is what you want both for a stray file in `data/` and
for an export whose format has changed.

*Matching.* Names are reconciled by a ladder — Sleeper id where the export
carries one (FantasyCalc and TheTradeCalc do; KTC's MFL id is translated
through FantasyCalc's id map, so it nearly always does too), then name+position, then
name alone (several sources file fullbacks as RB while Sleeper says FB), then
last name+team, then last name+position. That last rung exists for the Sleeper
ADP export, which has no team column at all; without it, five players are lost
to first-name nicknames — Cam Ward, Cam Skattebo, Kenny Gainwell, Chig Okonkwo,
Tank Dell. Ambiguity always loses: a rung finding two candidates falls through
rather than guessing between two real players.

*Misses are logged, not hidden.* On the first fetch (2026-10-16): 2 of 404 on
FantasyPros, 0 of 397 on FantasyCalc, 2 of 464 on KTC and 10 of 609 on
TheTradeCalc — almost all of them undrafted rookies and deep free agents
Sleeper's pool does not carry, plus one collision where two ranked players
share a name.

## No kickers, no defenses

The league starts neither ([§2.1](../rulebook/02-rosters-and-lineups.md)), so
they are filtered out at ingestion in `ingest.FANTASY_POSITIONS` rather than in
every query. The `a7c1e9d40b52` migration cleared the 111 kickers, 32 defenses
and one punter already stored; the filter is what stops the next
`ffd sync-players` putting them back.

The filter reads `fantasy_positions`, not `position`, which is what keeps the
35 fullbacks — Sleeper files them as position FB with fantasy_positions `["RB"]`
and they are real flex bodies.

## Draft day

`ffd-draft.timer` polls the draft board every 2 seconds. It is the one timer
that is not enabled by default, because a process every 2 seconds all year to
watch a draft that happens once is waste, not resilience.

The browser's own refetch (`DRAFT_REFRESH_MS` in `frontend/src/queries.ts`) is
set to the same 2 seconds and has to stay that way. The page cannot see
Sleeper; it re-reads what this timer last wrote. Polling the API faster than
the ingest only re-reads identical rows, and polling it slower leaves picks
sitting in the database unshown.

```bash
systemctl --user start ffd-draft.timer     # before the draft
journalctl --user -u ffd-draft -f          # watch it run
systemctl --user stop  ffd-draft.timer     # afterwards
```

With it off the board still updates — `ffd sync` carries the draft too — just
at five-minute resolution instead of two seconds. Costs, for reference: each
run makes three Sleeper requests (~90/minute against a documented ceiling near
1000) and takes about 0.8s, so a run finishes well inside its own 2s window and
a oneshot cannot stack. A completed draft with every pick already stored skips
the picks request, so leaving the timer on after the draft is harmless if you
forget — but stop it anyway, since 2s all year is ~43,000 wakeups a day.

The board's freshness comes from `draft.fetched_at`, shown in the page header —
`ffd sync-draft` writes no `sync_run` row, which at this cadence would add
~4,300 rows a day and bury the record of the real sync.

## Operations

| Task | Command |
|---|---|
| Sync now | `systemctl --user start ffd-sync` |
| Re-import rankings | `uv run ffd import-rankings` |
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

## NFL roster history

`ffd sync-nfl-rosters` loads active-roster history from nflverse. The ten-season
backfill is a one-time ~150 MB download, run by hand:

```
uv run ffd sync-nfl-rosters --seasons 2017-2026
```

In season only the current year changes, so the timer is templated on it and
fetches one ~15 MB file a week:

```
systemctl --user enable --now ffd-rosters@2026.timer
```

Start the new year's timer each September and stop the old one; nothing else
needs re-running, because finished seasons do not change. If nflverse corrects
an old season, re-run just that one — `--seasons 2021` replaces its rows.

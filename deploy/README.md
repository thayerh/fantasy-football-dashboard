# Deploying on the desktop

Everything runs as **systemd user services** — no Docker, no root daemon.

Paths below assume the repo lives at `~/devcode/fantasy/fantasy-football-dashboard`.
If you move it, update `WorkingDirectory=` in the `.service` files.

## Architecture

```
ffd-sync.timer ──▶ ffd-sync.service (oneshot: `ffd sync`) ──┐
                                                            ├─▶ data/ffd.db (WAL)
ffd-api.service (uvicorn, long-running) ─── reads only ─────┘
                                                    ▲
ffd-backup.timer ──▶ ffd-backup.service             │
                                                    │
cloudflared (ffd-tunnel.service) ──▶ 127.0.0.1:8000 ┘
```

The sync process is the **only** writer. The API never writes, and a oneshot
service cannot overlap itself, so concurrent writes are impossible by
construction rather than by convention.

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

systemctl --user enable --now ffd-api ffd-tunnel ffd-sync.timer ffd-backup.timer

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

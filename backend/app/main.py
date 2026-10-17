"""FastAPI application.

This process is read-only with respect to the database. All writes happen in
`ffd sync`, run by a systemd timer, which makes the single-writer property
structural rather than a convention to remember.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import draft, health, league, rosters
from app.config import get_settings

log = logging.getLogger(__name__)

app = FastAPI(
    title="Fantasy Football Dashboard",
    version="0.1.0",
    description="Private Sleeper league dashboard.",
)

app.include_router(health.router)
app.include_router(league.router)
app.include_router(draft.router)
app.include_router(rosters.router)


@app.middleware("http")
async def log_access_identity(request: Request, call_next):
    """Record the Cloudflare Access identity, when present.

    Cloudflare injects this header on authenticated requests. Nothing depends on
    it yet; it's the hook that makes a future per-user view ("show me my team")
    possible without building a login system.
    """
    response = await call_next(request)
    email = request.headers.get("cf-access-authenticated-user-email")
    if email and request.url.path.startswith("/api/"):
        log.debug("%s %s by %s", request.method, request.url.path, email)
    return response


def resolve_static_file(root: Path, full_path: str) -> Path | None:
    """The file to serve for a SPA request, or None to fall back to index.html.

    `full_path` is attacker-controlled and reaches the route percent-decoded, so
    "/%2e%2e/%2e%2e/.env" arrives here as "../../.env". Joining that to the
    build directory escapes it and serves any file the process can read — .env
    among them. Resolve first, then require the result to still be inside the
    build before touching the filesystem.

    Split out from the route so it can be tested directly: an HTTP client
    normalises "../" out of the URL before it is ever sent, which makes a
    request-level test of this look like it passes when it does not.
    """
    if not full_path:
        return None

    candidate = (root / full_path).resolve()
    if not candidate.is_relative_to(root):
        log.warning("Refusing to serve %r: outside the static root", full_path)
        return None
    return candidate if candidate.is_file() else None


# How long each kind of file may be reused without asking.
#
# The two are opposites on purpose, and getting either wrong is what makes a
# deploy fail to reach anyone. Vite writes a content hash into every asset
# filename, so `index-DXL7iEhr.js` can never change meaning — it is safe to
# cache for a year and is replaced by being *renamed*. `index.html` is the one
# file with a stable name, and it is the file that names the others, so it must
# be revalidated on every load or a browser goes on requesting last week's
# bundle by name.
#
# Without these headers there is no Cache-Control at all, and browsers fall
# back to heuristic freshness — typically a tenth of the file's age — which
# silently pins clients to a stale index.html for an unpredictable stretch.
#
# `no-cache` means "revalidate before reusing", not "never store". Starlette's
# FileResponse sends an ETag but does not act on If-None-Match, so that
# revalidation is a 200 with a body rather than a 304 — which costs nothing
# here, because index.html is 725 bytes and the 259 KB bundle beside it is the
# thing that stays cached for a year.
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


class HashedAssets(StaticFiles):
    """/assets, whose filenames carry a content hash and so never go stale."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = IMMUTABLE
        return response


def _mount_spa() -> None:
    """Serve the built frontend, if it has been built.

    Absent a build, the API still runs — useful for `ffd sync` debugging and for
    first-run setup before npm has been touched.
    """
    static_dir = get_settings().resolved_static_dir
    index = static_dir / "index.html"
    if not index.exists():
        log.warning("No frontend build at %s; serving API only.", static_dir)
        return

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", HashedAssets(directory=assets), name="assets")

    root = static_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # Single-page app: unknown paths return index.html so client-side
        # routing works on a hard refresh. /api/* is already matched above.
        #
        # Everything reaching here revalidates, not only index.html: the rest
        # is public/ — the favicon and the touch icon — whose names are fixed
        # too, so they have the same staleness problem and the same cheap fix.
        candidate = resolve_static_file(root, full_path)
        return FileResponse(candidate or index, headers={"Cache-Control": REVALIDATE})


_mount_spa()

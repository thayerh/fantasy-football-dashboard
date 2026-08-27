"""FastAPI application.

This process is read-only with respect to the database. All writes happen in
`ffd sync`, run by a systemd timer, which makes the single-writer property
structural rather than a convention to remember.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import health, league
from app.config import get_settings

log = logging.getLogger(__name__)

app = FastAPI(
    title="Fantasy Football Dashboard",
    version="0.1.0",
    description="Private Sleeper league dashboard.",
)

app.include_router(health.router)
app.include_router(league.router)


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
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # Single-page app: unknown paths return index.html so client-side
        # routing works on a hard refresh. /api/* is already matched above.
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


_mount_spa()

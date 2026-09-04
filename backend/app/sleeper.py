"""Thin client for the Sleeper API.

Sleeper is public and unauthenticated. Rate limiting and transient-failure
handling live here so no caller has to re-solve them.

Docs: https://docs.sleeper.com/
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

# Sleeper documents a ceiling of ~1000 calls/minute before IP blocking.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class SleeperError(RuntimeError):
    """A Sleeper request failed after exhausting retries."""


def ms_to_utc(value: int | float | None) -> dt.datetime | None:
    """Convert Sleeper's epoch-milliseconds fields to aware UTC datetimes.

    Sleeper returns epoch ms on transactions, drafts, and similar objects.
    Converting at this boundary keeps raw epoch ints out of the rest of the app.
    """
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value / 1000, tz=dt.UTC)


class SleeperClient:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.settings.sleeper_base_url,
            timeout=self.settings.sleeper_timeout_seconds,
            headers={"User-Agent": "fantasy-football-dashboard/0.1"},
        )

    def __enter__(self) -> SleeperClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, path: str) -> Any:
        last_error: Exception | None = None

        for attempt in range(self.settings.sleeper_max_retries):
            try:
                response = self._client.get(path)
            except httpx.RequestError as exc:  # network/DNS/timeout
                last_error = exc
            else:
                if response.status_code == 404:
                    raise SleeperError(f"Sleeper returned 404 for {path}")
                if response.status_code not in RETRY_STATUS:
                    response.raise_for_status()
                    return response.json()
                last_error = SleeperError(f"Sleeper returned {response.status_code} for {path}")

            if attempt < self.settings.sleeper_max_retries - 1:
                backoff = 2**attempt
                log.warning("Sleeper %s failed (%s); retrying in %ss", path, last_error, backoff)
                time.sleep(backoff)

        raise SleeperError(
            f"Sleeper request to {path} failed after "
            f"{self.settings.sleeper_max_retries} attempts: {last_error}"
        )

    def get_state(self, sport: str = "nfl") -> dict[str, Any]:
        """Current season and week. Note season_type is 'pre' outside the season."""
        return self._get(f"/state/{sport}")

    def get_league(self, league_id: str) -> dict[str, Any]:
        return self._get(f"/league/{league_id}")

    def get_league_users(self, league_id: str) -> list[dict[str, Any]]:
        return self._get(f"/league/{league_id}/users")

    def get_players(self, sport: str = "nfl") -> dict[str, Any]:
        """The entire player pool — ~14 MB and ~12,000 players.

        Sleeper's docs say to call this **once a day at most**, which is why it
        has its own daily command and is not part of `ffd sync`.
        """
        return self._get(f"/players/{sport}")

    def get_league_drafts(self, league_id: str) -> list[dict[str, Any]]:
        """Every draft for the league, newest first."""
        return self._get(f"/league/{league_id}/drafts")

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return self._get(f"/draft/{draft_id}")

    def get_draft_picks(self, draft_id: str) -> list[dict[str, Any]]:
        """Picks made so far. Empty until the draft starts — except keepers,
        which Sleeper places on the board before it does."""
        return self._get(f"/draft/{draft_id}/picks")

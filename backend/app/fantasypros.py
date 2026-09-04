"""Thin client for the FantasyPros public API.

Two constraints shape everything here, both confirmed against the live API
rather than assumed:

  * **50 calls a day.** Every call is deliberate. Nothing calls this from a
    request path; ingestion is a daily CLI command like the others.
  * **The free tier caps every rankings response at 10 players**, regardless of
    what you ask for. The response says so itself — `"limit": 10,
    "public_api_limited": true, "tier": "free"` — and passing `limit=50` is
    ignored. `count` reports the full set (517 players today); you receive ten.

The consequence is the shape of `sync_rankings`: one call per position rather
than one big call, because the position filter is the only lever that changes
*which* ten come back. Note that `rank_ecr` is scoped to the filtered set — in
a position call it is the positional rank (Josh Allen is 1 among QBs), not the
overall one.

Docs: https://api.fantasypros.com/public/v2/docs
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# The positions worth a call. FantasyPros calls team defenses DST; Sleeper
# calls them DEF, and the two are reconciled at match time.
RANKED_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


class FantasyProsError(RuntimeError):
    """A FantasyPros request failed, or the key is missing."""


class FantasyProsClient:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        if not self.settings.fantasypros_key:
            raise FantasyProsError(
                "FFD_FANTASYPROS_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.settings.fantasypros_base_url,
            timeout=self.settings.fantasypros_timeout_seconds,
            headers={
                "x-api-key": self.settings.fantasypros_key,
                "User-Agent": "fantasy-football-dashboard/0.1",
            },
        )
        self.calls = 0

    def __enter__(self) -> FantasyProsClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None

        # Two attempts, not three: a retry against a 50-a-day budget is
        # expensive, and a 429 here means the budget is already gone.
        for attempt in range(2):
            self.calls += 1
            try:
                response = self._client.get(path, params=params)
            except httpx.RequestError as exc:
                last_error = exc
            else:
                if response.status_code == 429:
                    raise FantasyProsError(
                        "FantasyPros rate limit reached (50 calls/day on the free tier)."
                    )
                if response.status_code not in RETRY_STATUS:
                    response.raise_for_status()
                    return response.json()
                last_error = FantasyProsError(
                    f"FantasyPros returned {response.status_code} for {path}"
                )

            if attempt == 0:
                log.warning("FantasyPros %s failed (%s); retrying once", path, last_error)
                time.sleep(2)

        raise FantasyProsError(f"FantasyPros request to {path} failed: {last_error}")

    def get_consensus_rankings(
        self,
        season: str,
        position: str = "ALL",
        ranking_type: str = "DRAFT",
        scoring: str | None = None,
    ) -> dict[str, Any]:
        """One ranking set. Costs one call from the daily budget."""
        return self._get(
            f"/nfl/{season}/consensus-rankings",
            {
                "position": position,
                "type": ranking_type,
                "scoring": scoring or self.settings.fantasypros_scoring,
                "week": 0,
            },
        )

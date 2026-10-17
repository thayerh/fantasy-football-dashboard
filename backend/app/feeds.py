"""Fetch the ranking boards from the sites themselves.

The four dynasty boards used to be exports downloaded by hand, on different
days, which left the aggregate averaging a mix of fresh and stale numbers. All
four are public pages or APIs, so `ffd fetch-rankings` pulls them on a daily
timer instead:

- **FantasyCalc** — its own JSON API, the one fantasycalc.com/dynasty-rankings
  is drawn from.
- **KeepTradeCut** — no API, but each rankings page embeds its whole board as a
  ``<script id="ktc-players">`` JSON blob, so one GET of the page is the data.
- **FantasyPros** — likewise, the page carries its rankings as `ecrData`.
- **TheTradeCalc** — the JSON API its startup board fetches.

Every request is for this league's shape: 12 teams, superflex, PPR. The
redraft boards come from the same three requests or their redraft twins.

This module only fetches. Parsing is `dynasty.py`'s, so a site changing its
format is a parse error with a payload you can test against, not a network bug.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app import dynasty
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# This league: 12 teams, superflex (FantasyCalc counts a superflex as a second
# QB slot), full PPR. See rulebook/02-rosters-and-lineups.md.
_FANTASYCALC_PARAMS = {"numQbs": 2, "numTeams": 12, "ppr": 1, "includeAdp": "false"}

FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"
KTC_DYNASTY_URL = "https://keeptradecut.com/dynasty-rankings"
KTC_REDRAFT_URL = "https://keeptradecut.com/fantasy-rankings"
FANTASYPROS_URL = "https://www.fantasypros.com/nfl/rankings/dynasty-superflex.php"
THETRADECALC_URL = "https://thetradecalc.com/api/dynasty/assets"

# A board that comes back this short is a truncated page or a changed format,
# not a real board — each runs to hundreds of players. Refusing it keeps the
# previous day's rows in place rather than replacing a board with a fragment.
MIN_ROWS = 50

# KTC and FantasyPros serve pages to browsers; a bare client UA gets less.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 fantasy-football-dashboard/0.1"
)


class FeedError(RuntimeError):
    """A ranking request failed after exhausting retries."""


class FeedClient:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.settings.rankings_timeout_seconds,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )

    def __enter__(self) -> FeedClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last_error: Exception | None = None
        retries = self.settings.rankings_max_retries

        for attempt in range(retries):
            try:
                response = self._client.get(url, params=params)
            except httpx.RequestError as exc:  # network/DNS/timeout
                last_error = exc
            else:
                if response.status_code not in RETRY_STATUS:
                    response.raise_for_status()
                    return response
                last_error = FeedError(f"{url} returned {response.status_code}")

            if attempt < retries - 1:
                backoff = 2**attempt
                log.warning("%s failed (%s); retrying in %ss", url, last_error, backoff)
                time.sleep(backoff)

        raise FeedError(f"{url} failed after {retries} attempts: {last_error}")

    def fantasycalc(self, *, dynasty_values: bool = True) -> list[dict[str, Any]]:
        params = {**_FANTASYCALC_PARAMS, "isDynasty": str(dynasty_values).lower()}
        return self._get(FANTASYCALC_URL, params).json()

    def ktc_page(self, *, redraft: bool = False) -> str:
        return self._get(KTC_REDRAFT_URL if redraft else KTC_DYNASTY_URL).text

    def fantasypros_page(self) -> str:
        return self._get(FANTASYPROS_URL).text

    def thetradecalc(self) -> list[dict[str, Any]]:
        return self._get(THETRADECALC_URL).json()


def fetch_boards(
    client: FeedClient,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Exception]]:
    """Fetch and parse every board. Returns `(boards, failures)`, keyed by source.

    One board failing does not stop the rest: each is stored independently, so
    a KTC redesign should cost the KTC tab and nothing else. The failures come
    back beside the successes so the caller can still exit non-zero.

    FantasyCalc goes first because KTC's rows are matched through it — its
    MFL-to-Sleeper id map is what lets KTC skip the name ladder. If FantasyCalc
    is down, KTC still loads, by name.
    """
    boards: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, Exception] = {}
    mfl_to_sleeper: dict[str, str] = {}

    def attempt(source: str, load: Callable[[], list[dict[str, Any]]]) -> None:
        try:
            rows = load()
            if len(rows) < MIN_ROWS:
                raise dynasty.DynastyParseError(
                    f"only {len(rows)} players, fewer than the {MIN_ROWS} a real board has"
                )
        except Exception as exc:  # each board stands alone; see the docstring
            log.error("Could not fetch %s: %s: %s", source, type(exc).__name__, exc)
            failures[source] = exc
            return
        log.info("Fetched %d players from %s", len(rows), source)
        boards[source] = rows

    def fantasycalc(dynasty_values: bool) -> list[dict[str, Any]]:
        payload = client.fantasycalc(dynasty_values=dynasty_values)
        mfl_to_sleeper.update(dynasty.sleeper_ids_by_mfl(payload))
        return dynasty.parse_fantasycalc_api(payload)

    attempt(dynasty.FANTASYCALC, lambda: fantasycalc(True))
    attempt(dynasty.FANTASYCALC_REDRAFT, lambda: fantasycalc(False))

    # One request, two boards.
    ttc: list[dict[str, Any]] = []

    def thetradecalc(redraft: bool) -> list[dict[str, Any]]:
        if not ttc:
            ttc.extend(client.thetradecalc())
        return dynasty.parse_thetradecalc_api(ttc, redraft=redraft)

    attempt(dynasty.THETRADECALC, lambda: thetradecalc(False))
    attempt(dynasty.THETRADECALC_REDRAFT, lambda: thetradecalc(True))

    attempt(
        dynasty.KTC,
        lambda: dynasty.parse_ktc_page(client.ktc_page(), mfl_to_sleeper),
    )
    attempt(
        dynasty.KTC_REDRAFT,
        lambda: dynasty.parse_ktc_page(client.ktc_page(redraft=True), mfl_to_sleeper),
    )
    attempt(dynasty.FANTASYPROS, lambda: dynasty.parse_fantasypros_page(client.fantasypros_page()))

    return boards, failures

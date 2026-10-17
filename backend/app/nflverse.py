"""Historical NFL rosters from nflverse.

nflverse publishes one CSV per season of *weekly* rosters: a row per player,
per team, per week, going back further than this app needs. The files are
static release assets on GitHub, so there is no API key, no rate limit worth
modelling, and no scraping.

Weekly, not season-level, on purpose. The season-level file collapses a player
to a single team — 2025 has 3,138 rows for 3,134 distinct players — so a player
traded in October appears only under whichever team held him last, and the
other team's history silently loses him. The weekly file is ~15 MB a season
against ~1 MB, which is the price of getting trades right.

Source: https://github.com/nflverse/nflverse-data/releases/tag/weekly_rosters
"""

from __future__ import annotations

import csv
import io
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# The only roster status that counts as "played for this team". The file also
# carries DEV (practice squad), CUT, RES (injured/PUP), EXE and RET, none of
# which put a player on the field. For 2026 this makes ACT exactly the 53-man
# roster: 1,693 rows over 32 teams, 53 apiece.
ACTIVE_STATUS = "ACT"


class NflverseError(RuntimeError):
    """An nflverse download failed after exhausting retries."""


# Every abbreviation seen in the data or likely to be typed, mapped to one
# canonical franchise code. Relocations are the reason this exists: the Raiders
# are OAK for 2017-2019 and LV from 2020, so a query for either has to find
# both eras or it under-counts by three seasons. The Rams are "LA" in nflverse,
# never "LAR", which is what most other sources call them.
FRANCHISES: dict[str, str] = {
    "ARI": "ARI", "ARZ": "ARI",
    "ATL": "ATL",
    "BAL": "BAL", "BLT": "BAL",
    "BUF": "BUF",
    "CAR": "CAR",
    "CHI": "CHI",
    "CIN": "CIN",
    "CLE": "CLE", "CLV": "CLE",
    "DAL": "DAL",
    "DEN": "DEN",
    "DET": "DET",
    "GB": "GB", "GNB": "GB",
    "HOU": "HOU", "HST": "HOU",
    "IND": "IND",
    "JAX": "JAX", "JAC": "JAX",
    "KC": "KC", "KAN": "KC",
    "LA": "LA", "LAR": "LA", "RAM": "LA", "STL": "LA",
    "LAC": "LAC", "SD": "LAC", "SDG": "LAC",
    "LV": "LV", "OAK": "LV", "LVR": "LV", "RAI": "LV",
    "MIA": "MIA",
    "MIN": "MIN",
    "NE": "NE", "NWE": "NE",
    "NO": "NO", "NOR": "NO",
    "NYG": "NYG",
    "NYJ": "NYJ",
    "PHI": "PHI",
    "PIT": "PIT",
    "SEA": "SEA",
    "SF": "SF", "SFO": "SF",
    "TB": "TB", "TAM": "TB",
    "TEN": "TEN", "OTI": "TEN",
    "WAS": "WAS", "WSH": "WAS", "WFT": "WAS",
}

# So `ffd roster-check "Las Vegas Raiders"` works as well as `ffd roster-check LV`.
# Nicknames are keyed on the last word, which is unambiguous for all 32 clubs
# except the New York and Los Angeles pairs, spelled out in full below.
TEAM_NAMES: dict[str, str] = {
    "cardinals": "ARI", "arizona": "ARI",
    "falcons": "ATL", "atlanta": "ATL",
    "ravens": "BAL", "baltimore": "BAL",
    "bills": "BUF", "buffalo": "BUF",
    "panthers": "CAR", "carolina": "CAR",
    "bears": "CHI", "chicago": "CHI",
    "bengals": "CIN", "cincinnati": "CIN",
    "browns": "CLE", "cleveland": "CLE",
    "cowboys": "DAL", "dallas": "DAL",
    "broncos": "DEN", "denver": "DEN",
    "lions": "DET", "detroit": "DET",
    "packers": "GB", "green bay": "GB",
    "texans": "HOU", "houston": "HOU",
    "colts": "IND", "indianapolis": "IND",
    "jaguars": "JAX", "jags": "JAX", "jacksonville": "JAX",
    "chiefs": "KC", "kansas city": "KC",
    "rams": "LA",
    "chargers": "LAC",
    "raiders": "LV",
    "dolphins": "MIA", "miami": "MIA",
    "vikings": "MIN", "minnesota": "MIN",
    "patriots": "NE", "pats": "NE", "new england": "NE",
    "saints": "NO", "new orleans": "NO",
    "giants": "NYG",
    "jets": "NYJ",
    "eagles": "PHI", "philadelphia": "PHI",
    "steelers": "PIT", "pittsburgh": "PIT",
    "seahawks": "SEA", "seattle": "SEA",
    "49ers": "SF", "niners": "SF", "san francisco": "SF",
    "buccaneers": "TB", "bucs": "TB", "tampa bay": "TB",
    "titans": "TEN", "tennessee": "TEN",
    "commanders": "WAS", "washington": "WAS",
}


def franchise_of(team: str | None) -> str | None:
    """The canonical franchise code for a team abbreviation, or None.

    OAK and LV both answer LV; STL, LAR and LA all answer LA.
    """
    if not team:
        return None
    return FRANCHISES.get(team.strip().upper())


def resolve_team(value: str) -> str | None:
    """Franchise code for whatever the user typed: 'OAK', 'lv', 'Raiders'."""
    if not value:
        return None
    cleaned = value.strip()
    direct = FRANCHISES.get(cleaned.upper())
    if direct:
        return direct

    lowered = " ".join(cleaned.lower().split())
    if lowered in TEAM_NAMES:
        return TEAM_NAMES[lowered]
    # "Las Vegas Raiders" -> "raiders"; "New York Giants" -> "giants".
    tail = lowered.rsplit(" ", 1)[-1]
    return TEAM_NAMES.get(tail)


class NflverseClient:
    """Downloads one season's weekly roster CSV at a time.

    Each file is ~15 MB, so seasons are fetched and parsed one at a time rather
    than held together: the ten-season backfill peaks at one file in memory,
    not 150 MB of them.
    """

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.settings.nflverse_timeout_seconds,
            # GitHub redirects release assets to a signed storage URL.
            follow_redirects=True,
            headers={"User-Agent": "fantasy-football-dashboard/0.1"},
        )

    def __enter__(self) -> NflverseClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def season_url(self, season: int) -> str:
        return f"{self.settings.nflverse_base_url}/weekly_rosters/roster_weekly_{season}.csv"

    def fetch_season(self, season: int) -> str:
        """The raw CSV text for one season."""
        url = self.season_url(season)
        last_error: Exception | None = None

        for attempt in range(self.settings.nflverse_max_retries):
            try:
                response = self._client.get(url)
            except httpx.RequestError as exc:
                last_error = exc
            else:
                if response.status_code == 404:
                    # A season nflverse has not published yet. The caller
                    # decides whether that is fatal; mid-backfill it is not.
                    raise NflverseError(f"No nflverse roster file for {season} ({url})")
                if response.status_code not in RETRY_STATUS:
                    response.raise_for_status()
                    return response.text
                last_error = NflverseError(f"nflverse returned {response.status_code} for {url}")

            if attempt < self.settings.nflverse_max_retries - 1:
                backoff = 2**attempt
                log.warning("nflverse %s failed (%s); retrying in %ss", season, last_error, backoff)
                time.sleep(backoff)

        raise NflverseError(
            f"nflverse download for {season} failed after "
            f"{self.settings.nflverse_max_retries} attempts: {last_error}"
        )


def active_rows(text: str) -> Iterator[dict[str, Any]]:
    """Parse a weekly roster CSV, yielding only rows on an active roster.

    Playoff weeks (game_type WC/DIV/CON/SB, weeks 19-22) are kept: dressing for
    a playoff game is playing for the team as much as week 3 is.
    """
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("status") != ACTIVE_STATUS:
            continue
        # gsis_id is the join key for everything downstream. It is present on
        # every ACT row in 2017-2026, but a row without one cannot be stored
        # against a player, so it is dropped rather than given a synthetic id.
        if not row.get("gsis_id"):
            log.debug("Skipping ACT row with no gsis_id: %s", row.get("full_name"))
            continue
        yield row

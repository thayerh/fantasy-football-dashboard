"""FantasyPros rankings from the public cheat-sheet page.

The JSON API is the sanctioned interface, but its free tier caps every response
at ten players and ignores a limit parameter, which is not enough to rank a
draft board. The cheat-sheet page embeds the *complete* ranking set — the same
JSON structure, 517 players instead of 10, plus the expert spread
(rank_min/max/ave/std) the API does not return.

So this is the source of truth and `sync-rankings --source api` is the fallback
for when the page's markup changes.

Being a good guest about it: one request per daily sync, a real User-Agent, and
no crawling — a single URL, fetched no more often than the API would have been.
Worth knowing that scraping is a weaker footing than the API: FantasyPros'
terms may not permit it, and nothing about the page's shape is a contract, so
this can break without warning. That is why the extractor fails loudly and the
API path is kept working.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

BASE = "https://www.fantasypros.com/nfl/rankings"

# The scoring-specific cheat sheet. Standard scoring has no prefix.
CHEATSHEET_SLUGS = {
    "PPR": "ppr-cheatsheets.php",
    "HALF": "half-point-ppr-cheatsheets.php",
    "STD": "consensus-cheatsheets.php",
}

# The embedded blob opens a complete JSON object; raw_decode finds its end, so
# no regex has to balance braces. The pattern tolerates whitespace because the
# page's exact formatting is not a contract — matching '{"sport":"NFL"' byte for
# byte would break the day they pretty-print their output.
_MARKER = re.compile(r'\{\s*"sport"\s*:\s*"NFL"')

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class CheatsheetError(RuntimeError):
    """The page could not be fetched, or its embedded rankings were not found."""


def extract_rankings(html: str) -> dict[str, Any]:
    """Pull the rankings object out of the page's inline script.

    Deliberately not an HTML parse: the page renders its table from this blob,
    so reading the blob gets the same numbers without depending on markup,
    class names, or how many rows the page chose to render.
    """
    match = _MARKER.search(html)
    start = match.start() if match else -1
    if start < 0:
        raise CheatsheetError(
            "No embedded rankings found on the cheat sheet page. The page shape "
            "has probably changed; `ffd sync-rankings --source api` still works."
        )

    try:
        payload, _ = json.JSONDecoder().raw_decode(html[start:])
    except json.JSONDecodeError as exc:
        raise CheatsheetError(f"Embedded rankings are not valid JSON: {exc}") from exc

    players = payload.get("players")
    if not isinstance(players, list) or not players:
        raise CheatsheetError("Embedded rankings contained no players.")

    return payload


def fetch_cheatsheet(
    scoring: str | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """One request. Returns the same shape the JSON API returns, uncapped."""
    settings = settings or get_settings()
    scoring = (scoring or settings.fantasypros_scoring).upper()

    slug = CHEATSHEET_SLUGS.get(scoring)
    if slug is None:
        raise CheatsheetError(
            f"No cheat sheet for scoring {scoring!r}; expected one of "
            f"{', '.join(sorted(CHEATSHEET_SLUGS))}."
        )

    url = f"{BASE}/{slug}"
    owns = client is None
    client = client or httpx.Client(
        timeout=settings.fantasypros_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": _UA},
    )
    try:
        response = client.get(url)
        response.raise_for_status()
        payload = extract_rankings(response.text)
    except httpx.HTTPError as exc:
        raise CheatsheetError(f"Could not fetch {url}: {exc}") from exc
    finally:
        if owns:
            client.close()

    log.info(
        "Scraped %d ranked players (%s %s, updated %s)",
        len(payload["players"]),
        payload.get("scoring"),
        payload.get("ranking_type_name"),
        payload.get("last_updated"),
    )
    return payload

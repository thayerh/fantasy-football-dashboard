"""Parse the Vegas projections export.

The file is a hand-supplied export of Vegas-derived season fantasy projections.
Its one awkward property is that the first field is not a name but a run-on of
several fields with no delimiter between them:

    Jahmyr GibbsDETRB1High Floor,320.3
    Ja'Marr ChaseCINQWR1High Floor,300.9
    Zach ErtzTE52,33.3

The shape is ``{name}{TEAM?}{STATUS?}{POS}{posrank}{TAG?},{points}``, where team,
status and tag are all optional. Nothing here touches the network: the export is
a file the user supplies, so this module is a pure function over its text and is
testable without fixtures or mocks.

Every field is read from the **right**, because every field is unambiguous from
that end and none of them is from the left. A name can contain spaces, capitals,
apostrophes, periods and hyphens, so there is no prefix you can trust; but the
points, the tag, the position and the rank all terminate the string in a form
nothing else takes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# Sleeper's own abbreviations, so a parsed team needs no translation on the way
# in. `_TEAM_ALIASES` in ingest.py still covers anything that slips through.
TEAMS = (
    "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LV LAC LAR "
    "MIA MIN NE NO NYG NYJ PHI PIT SF SEA TB TEN WAS"
).split()

# Injury and availability flags, as the export writes them.
STATUSES = ["DNR", "PUP", "SUS", "IR", "NA", "Q", "D", "O"]

# The volatility label. Absent on most rows.
TAGS = ["High Floor", "Balanced", "Volatile"]

POSITIONS = ["QB", "RB", "WR", "TE"]

_POS_RANK = re.compile(rf"({'|'.join(POSITIONS)})(\d+)$")

# Team and status are matched **together**, in one anchored pass, and this is
# the whole reason the expression looks the way it does.
#
# Doing it in two steps — strip a status, then strip a team — mangles every
# Colt on the list: `Jonathan TaylorIND` ends in `D`, so the status comes off
# first and leaves the name as `Jonathan TaylorIN`, with no team. Jonathan
# Taylor is RB4, so this is not an edge case you would notice only in the tail
# of the file.
#
# One pattern fixes it twice over. `name` is non-greedy, so the *shortest* name
# that leaves a valid tail wins, and `team` is tried before `status`, so `IND`
# is claimed as a team before `D` can be claimed as a status. The `$` anchor
# caps the tail at five characters, which is what makes it impossible for a
# two-letter team like `KC` to be lifted out of the middle of a name.
_TAIL = re.compile(
    r"^(?P<name>.+?)"
    rf"(?P<team>{'|'.join(sorted(TEAMS, key=len, reverse=True))})?"
    rf"(?P<status>{'|'.join(STATUSES)})?$"
)


class VegasParseError(ValueError):
    """A line that does not have the documented shape."""


def parse_projections(text: str) -> list[dict[str, Any]]:
    """Parse the export into one dict per player.

    Returns rows shaped like FantasyPros' JSON entries (`player_name`,
    `player_team_id`, `player_position_id`) so the ingest can reuse the existing
    match ladder rather than growing a second one, plus the projection itself.

    Raises `VegasParseError` on a line that doesn't fit. Silently dropping one
    would be worse: the file is imported by hand and rarely, so a malformed row
    is far more likely to be a changed export format than a one-off typo, and
    that is worth stopping for.
    """
    rows: list[dict[str, Any]] = []

    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        blob, comma, points = line.rpartition(",")
        if not comma:
            raise VegasParseError(f"line {number}: no points column: {line!r}")
        try:
            projected = float(points)
        except ValueError as exc:
            raise VegasParseError(f"line {number}: {points!r} is not a number") from exc

        tag = next((t for t in TAGS if blob.endswith(t)), None)
        if tag:
            blob = blob[: -len(tag)]

        rank = _POS_RANK.search(blob)
        if rank is None:
            raise VegasParseError(f"line {number}: no position and rank in {blob!r}")
        position, pos_rank = rank.group(1), int(rank.group(2))

        # `.+?` needs at least one character, so a row that is nothing but a
        # position and rank has no name and fails here rather than silently
        # producing an empty one.
        tail = _TAIL.match(blob[: rank.start()])
        if tail is None or not tail["name"].strip():
            raise VegasParseError(f"line {number}: no player name in {blob!r}")

        rows.append(
            {
                "player_name": tail["name"].strip(),
                # None for a genuine free agent; the export leaves the team out
                # entirely rather than writing a placeholder.
                "player_team_id": tail["team"],
                "player_position_id": position,
                "status": tail["status"],
                "pos_rank": pos_rank,
                "tag": tag,
                "points": projected,
            }
        )

    log.info("Parsed %d Vegas projections", len(rows))
    return rows

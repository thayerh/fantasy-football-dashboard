"""Parse the dynasty ranking exports.

Four boards, four file formats, one shape out. Each is a file the user exports
by hand, so nothing here touches the network and every function is a pure
function over text — testable without fixtures or mocks, the same way
`vegas.py` is.

The four:

- **FantasyPros** dynasty consensus. A true ranking with the expert spread and
  tiers, and the only one of the four that says how much its experts disagree.
- **FantasyCalc** dynasty values. Carries a `sleeperId` outright, so its rows
  need no name matching at all — the one export that can be joined exactly.
- **TheTradeCalc** startup board, priced for *this* league: 12-team superflex
  PPR. The only source that knows the league's own shape.
- **Sleeper ADP**, dynasty. Not a ranking anyone published — it is what the
  market actually did, which is why it is kept beside the three and left out
  of the average of them.

Rows come out shaped like FantasyPros' JSON entries (`player_name`,
`player_team_id`, `player_position_id`) so the ingest can reuse the existing
match ladder rather than growing a second one.

Every parser drops kickers, defenses and anything else outside DYNASTY_POSITIONS.
This league starts none of them (`rulebook/02-rosters-and-lineups.md`), and a
board that ranks a kicker 281st is offering a pick nobody can make.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# The only positions this league starts. No K, no DEF — see the rulebook's
# §2.1 starting lineup: QB/RB/RB/WR/WR/WR/TE/FLEX/FLEX/SUPERFLEX.
DYNASTY_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})

# The source names stored in `dynasty_ranking.source`. Constants rather than
# string literals because the aggregate is defined by *exclusion* — everything
# but SLEEPER_ADP — and a typo in that list would silently change the average.
FANTASYPROS = "fantasypros"
FANTASYCALC = "fantasycalc"
THETRADECALC = "thetradecalc"
SLEEPER_ADP = "sleeper-adp"

#: The boards that make up the aggregate. Sleeper ADP is deliberately absent:
#: averaging a market price into a set of expert rankings would double-count
#: the market, which is already the thing the rankings are being read against.
CONSENSUS_SOURCES = (FANTASYPROS, FANTASYCALC, THETRADECALC)

ALL_SOURCES = (*CONSENSUS_SOURCES, SLEEPER_ADP)

# A label like "WR12" -> ("WR", 12). Every export writes the positional rank
# this way except FantasyCalc, which gives the two separately.
_POS_LABEL = re.compile(r"^([A-Za-z]+?)(\d+)$")


class DynastyParseError(ValueError):
    """A file that does not have the documented shape."""


def _number(value: Any, cast: Any) -> Any:
    """Exports write empty cells, "null" and "-" for the same missing number."""
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-", "null", "N/A"):
        return None
    try:
        return cast(text.lstrip("+"))
    except (TypeError, ValueError):
        return None


def _split_pos_label(label: str | None) -> tuple[str | None, int | None]:
    """'WR12' -> ('WR', 12); 'QB' -> ('QB', None)."""
    if not label:
        return None, None
    text = label.strip().upper()
    match = _POS_LABEL.match(text)
    if match:
        return match.group(1), int(match.group(2))
    return text or None, None


def _rows(text: str, delimiter: str = ",") -> list[dict[str, str | None]]:
    """Read a CSV, tolerating the BOM every one of these exports carries."""
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=delimiter))


def parse_fantasypros(text: str) -> list[dict[str, Any]]:
    """FantasyPros dynasty rankings.

    ``"RK",TIERS,"PLAYER NAME",TEAM,"POS","AGE","BEST","WORST","AVG.","STD.DEV"``

    The export interleaves **tier separator rows** — two cells, an empty rank
    and the tier number — among the real ones. They are skipped on the rank
    being non-numeric rather than on their width, so a future export that pads
    them out to full width does not start importing them as players.
    """
    out: list[dict[str, Any]] = []

    for row in _rows(text):
        rank = _number(row.get("RK"), int)
        if rank is None:
            continue  # a tier separator

        position, position_rank = _split_pos_label(row.get("POS"))
        if position not in DYNASTY_POSITIONS:
            continue

        out.append(
            {
                "player_name": (row.get("PLAYER NAME") or "").strip(),
                "player_team_id": (row.get("TEAM") or "").strip() or None,
                "player_position_id": position,
                "overall_rank": rank,
                "position_rank": position_rank,
                "pos_rank": (row.get("POS") or "").strip() or None,
                "tier": _number(row.get("TIERS"), int),
                "age": _number(row.get("AGE"), float),
                # The spread of expert opinion. FantasyPros is the only one of
                # the four that publishes it, and it is the difference between
                # a consensus and a coin flip.
                "rank_min": _number(row.get("BEST"), int),
                "rank_max": _number(row.get("WORST"), int),
                "rank_ave": _number(row.get("AVG."), float),
                "rank_std": _number(row.get("STD.DEV"), float),
            }
        )

    if not out:
        raise DynastyParseError("No ranked players found in the FantasyPros export.")
    return out


def parse_fantasycalc(text: str) -> list[dict[str, Any]]:
    """FantasyCalc dynasty values.

    Semicolon-delimited: ``name;team;position;age;fantasycalcId;sleeperId;...``

    Two kinds of row are dropped. Rows whose position is ``PICK`` are future
    rookie picks ("2027 1st (Early)") — real dynasty assets, and tradeable
    under §8, but not players and not draftable off this board. Anything else
    outside DYNASTY_POSITIONS goes for the usual reason.

    `sleeperId` is carried through because it makes this the one export that
    needs no name matching: it is Sleeper's own id, so the match is exact
    rather than a ladder of guesses.
    """
    out: list[dict[str, Any]] = []

    for row in _rows(text, delimiter=";"):
        position = (row.get("position") or "").strip().upper()
        if position not in DYNASTY_POSITIONS:
            continue

        rank = _number(row.get("overallRank"), int)
        if rank is None:
            continue

        position_rank = _number(row.get("positionRank"), int)
        team = (row.get("team") or "").strip()
        sleeper_id = (row.get("sleeperId") or "").strip()

        out.append(
            {
                "player_name": (row.get("name") or "").strip(),
                # The export writes the string "null" for a free agent's team,
                # which would otherwise be matched against as if it were one.
                "player_team_id": None if team in ("", "null") else team,
                "player_position_id": position,
                "sleeper_id": None if sleeper_id in ("", "null") else sleeper_id,
                "overall_rank": rank,
                "position_rank": position_rank,
                "pos_rank": f"{position}{position_rank}" if position_rank else None,
                "value": _number(row.get("value"), float),
                "age": _number(row.get("age"), float),
            }
        )

    if not out:
        raise DynastyParseError("No ranked players found in the FantasyCalc export.")
    return out


def parse_thetradecalc(text: str) -> list[dict[str, Any]]:
    """TheTradeCalc startup board.

    ``Rank,Player,Position,Pos Rank,Team,Value``

    Priced for a 12-team superflex PPR startup, which is this league exactly —
    so where it disagrees with the other two, it is the one that knows the
    format.
    """
    out: list[dict[str, Any]] = []

    for row in _rows(text):
        position = (row.get("Position") or "").strip().upper()
        if position not in DYNASTY_POSITIONS:
            continue

        rank = _number(row.get("Rank"), int)
        if rank is None:
            continue

        _, position_rank = _split_pos_label(row.get("Pos Rank"))

        out.append(
            {
                "player_name": (row.get("Player") or "").strip(),
                "player_team_id": (row.get("Team") or "").strip() or None,
                "player_position_id": position,
                "overall_rank": rank,
                "position_rank": position_rank,
                "pos_rank": (row.get("Pos Rank") or "").strip() or None,
                "value": _number(row.get("Value"), float),
            }
        )

    if not out:
        raise DynastyParseError("No ranked players found in the TheTradeCalc export.")
    return out


def parse_sleeper_adp(text: str) -> list[dict[str, Any]]:
    """Sleeper dynasty ADP.

    ``Sleeper ADP,Name,Position``

    The Position column is not uniform: skill players carry a plain "QB", but
    kickers arrive as "K1".."K9" — the position with its rank glued on — and
    defenses as a truncated "DE". Splitting the label rather than matching it
    whole is what makes all three fall out of DYNASTY_POSITIONS together
    instead of a "K1" slipping through as an unrecognised position.

    The ADP here is already an integer draft slot rather than an average across
    decimals, so it is both the ADP and its own rank; there is one number and
    it is stored once.
    """
    out: list[dict[str, Any]] = []

    for row in _rows(text):
        position, _ = _split_pos_label(row.get("Position"))
        if position not in DYNASTY_POSITIONS:
            continue

        rank = _number(row.get("Sleeper ADP"), int)
        if rank is None:
            continue

        out.append(
            {
                "player_name": (row.get("Name") or "").strip(),
                # The export carries no team at all, so the match ladder's
                # last rung (last name + team + position) cannot fire here.
                "player_team_id": None,
                "player_position_id": position,
                "overall_rank": rank,
            }
        )

    if not out:
        raise DynastyParseError("No ranked players found in the Sleeper ADP export.")
    return out


# Header fingerprint -> (source, parser). Files are identified by what is in
# them rather than by their name: these are downloads, and a download that
# lands as "rankings (1).csv" should still import as what it is.
_SIGNATURES: tuple[tuple[frozenset[str], str, Any], ...] = (
    (frozenset({"RK", "PLAYER NAME", "STD.DEV"}), FANTASYPROS, parse_fantasypros),
    (frozenset({"fantasycalcId", "overallRank", "sleeperId"}), FANTASYCALC, parse_fantasycalc),
    (frozenset({"Rank", "Player", "Pos Rank", "Value"}), THETRADECALC, parse_thetradecalc),
    (frozenset({"Sleeper ADP", "Name", "Position"}), SLEEPER_ADP, parse_sleeper_adp),
)


def identify(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Work out which export this is, and parse it.

    Returns `(source, rows)`. Raises `DynastyParseError` if the header matches
    none of the four — which is the right outcome for a file dropped in the
    directory by accident, and for an export whose format has changed under us.
    """
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    for delimiter in (",", ";"):
        header = {cell.strip().strip('"') for cell in first.split(delimiter)}
        for wanted, source, parser in _SIGNATURES:
            if wanted <= header:
                return source, parser(text)

    raise DynastyParseError(f"Unrecognised export header: {first[:120]!r}")


def read_export(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Identify and parse one export file."""
    source, rows = identify(path.read_text(encoding="utf-8-sig"))
    log.info("Parsed %d ranked players from %s (%s)", len(rows), path.name, source)
    return source, rows

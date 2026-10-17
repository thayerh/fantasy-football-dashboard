"""Parse the dynasty ranking boards, exported or fetched.

Four boards, several formats, one shape out. Each board arrives either as a
file the user exports by hand or as what the site itself serves, fetched daily
by `ffd fetch-rankings` (see `feeds.py`). Either way nothing here touches the
network: every function is a pure function over text — testable without
fixtures or mocks, the same way `vegas.py` is.

The fetched ones add KeepTradeCut, which has no export button at all, and a
redraft board from three of the sources, kept apart from the dynasty ones (see
REDRAFT_SOURCES).

The boards:

- **FantasyPros** dynasty consensus. A true ranking with the expert spread and
  tiers, and the only one that says how much its experts disagree. Fetched
  from the superflex board; the hand export was the 1QB one.
- **FantasyCalc** dynasty values. Carries a `sleeperId` outright, so its rows
  need no name matching at all.
- **TheTradeCalc** dynasty values, priced for *this* league: 12-team superflex
  PPR. Fetched with a `sleeper_id` too.
- **KeepTradeCut** dynasty values, crowd-sourced, superflex. No Sleeper id,
  but an MFL id that FantasyCalc can translate.
- **Sleeper ADP**, dynasty. Not a ranking anyone published — it is what the
  market actually did, which is why it is kept beside the others and left out
  of the average of them. Still a hand export: Sleeper serves no ADP feed.

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
import json
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
KTC = "ktc"
SLEEPER_ADP = "sleeper-adp"

# Redraft boards: this season only, no future. Kept as their own sources rather
# than folded into the dynasty ones, because "who scores this year" and "who is
# worth owning" are different questions and an average of the two answers
# neither.
FANTASYCALC_REDRAFT = "fantasycalc-redraft"
THETRADECALC_REDRAFT = "thetradecalc-redraft"
KTC_REDRAFT = "ktc-redraft"

#: The boards that make up the aggregate — the four dynasty boards that
#: `ffd fetch-rankings` refreshes daily, so the average is never a mix of
#: today's numbers and a month-old export. Sleeper ADP is deliberately absent:
#: averaging a market price into a set of expert rankings would double-count
#: the market, which is already the thing the rankings are being read against.
CONSENSUS_SOURCES = (FANTASYPROS, FANTASYCALC, THETRADECALC, KTC)

#: Never part of the aggregate, and never a fallback for a dynasty column.
REDRAFT_SOURCES = (FANTASYCALC_REDRAFT, THETRADECALC_REDRAFT, KTC_REDRAFT)

ALL_SOURCES = (*CONSENSUS_SOURCES, SLEEPER_ADP, *REDRAFT_SOURCES)

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


# --- The fetched boards --------------------------------------------------
#
# `ffd fetch-rankings` reads the same boards straight from the sites, so these
# parse what the sites serve rather than what their export buttons write. Still
# pure functions over text: the fetching lives in `feeds.py`, and these can be
# tested against a pasted payload.
#
# Each row carries more than the ingest stores. The extras ride along into the
# daily CSV snapshot, which is the record of everything the site said; the
# ingest picks out the columns `dynasty_ranking` has and ignores the rest.


def _positional(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Number rows by position, in the order given. For boards that publish none."""
    seen: dict[str, int] = {}
    for row in rows:
        position = row["player_position_id"]
        seen[position] = seen.get(position, 0) + 1
        row["position_rank"] = seen[position]
        row["pos_rank"] = f"{position}{seen[position]}"
    return rows


def parse_fantasycalc_api(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FantasyCalc's values API — the JSON its rankings page is drawn from.

    The same numbers as the CSV export, with more beside them: tier, the
    redraft value, and every id FantasyCalc knows, `mflId` among them. That
    last one is what lets KTC rows, which carry an MFL id but no Sleeper id,
    match exactly too (see `sleeper_ids_by_mfl`).

    Picks are dropped as in `parse_fantasycalc`. Their `overallRank` still
    counts them, so player ranks can skip a number where a pick sits; that is
    the board as FantasyCalc publishes it, and the same as the export.
    """
    out: list[dict[str, Any]] = []

    for entry in payload:
        player = entry.get("player") or {}
        position = (player.get("position") or "").upper()
        if position not in DYNASTY_POSITIONS:
            continue

        rank = _number(entry.get("overallRank"), int)
        if rank is None:
            continue

        position_rank = _number(entry.get("positionRank"), int)
        out.append(
            {
                "player_name": (player.get("name") or "").strip(),
                "player_team_id": player.get("maybeTeam") or None,
                "player_position_id": position,
                "sleeper_id": player.get("sleeperId") or None,
                "overall_rank": rank,
                "position_rank": position_rank,
                "pos_rank": f"{position}{position_rank}" if position_rank else None,
                "tier": _number(entry.get("maybeTier"), int),
                "value": _number(entry.get("value"), float),
                "age": _number(player.get("maybeAge"), float),
                "fantasycalc_id": player.get("id"),
                "mfl_id": player.get("mflId") or None,
                "trend_30_day": entry.get("trend30Day"),
                "redraft_value": entry.get("redraftValue"),
                "combined_value": entry.get("combinedValue"),
                "trade_frequency": entry.get("maybeTradeFrequency"),
                "roster_percent": entry.get("maybeRosterPercent"),
            }
        )

    if not out:
        raise DynastyParseError("No ranked players found in the FantasyCalc API response.")
    return out


def sleeper_ids_by_mfl(payload: list[dict[str, Any]]) -> dict[str, str]:
    """MFL id -> Sleeper id, from a FantasyCalc API response.

    KTC publishes an MFL id for every player and no Sleeper id; FantasyCalc
    publishes both. Joining through it turns most of KTC from a name guess into
    an exact match.
    """
    out: dict[str, str] = {}
    for entry in payload:
        player = entry.get("player") or {}
        mfl, sleeper = player.get("mflId"), player.get("sleeperId")
        if mfl and sleeper:
            out[str(mfl)] = str(sleeper)
    return out


_KTC_PLAYERS = re.compile(
    r'<script[^>]*\bid="ktc-players"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)


def parse_ktc_page(html: str, mfl_to_sleeper: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """KeepTradeCut's rankings page: the JSON it embeds for its own table.

    Both KTC pages — dynasty-rankings and fantasy-rankings (redraft) — carry
    the full board as a ``<script id="ktc-players">`` JSON blob, which is the
    "dynasty-rankings" document the browser's Sources tab shows. Each player
    has a 1QB and a superflex set of numbers; this league is superflex, so
    only `superflexValues` is read.

    Rookie draft picks (position "RDP") are dropped. KTC's `rank` already
    skips them — `pickRank` is the one that counts them.
    """
    match = _KTC_PLAYERS.search(html)
    if not match:
        raise DynastyParseError("No ktc-players JSON found in the KeepTradeCut page.")
    try:
        players = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise DynastyParseError(f"KeepTradeCut's ktc-players JSON is malformed: {exc}") from exc

    lookup = mfl_to_sleeper or {}
    out: list[dict[str, Any]] = []

    for player in players:
        position = (player.get("position") or "").upper()
        if position not in DYNASTY_POSITIONS:
            continue

        sf = player.get("superflexValues") or {}
        rank = _number(sf.get("rank"), int)
        if not rank:
            continue

        position_rank = _number(sf.get("positionalRank"), int)
        mfl = player.get("mflid")
        out.append(
            {
                "player_name": (player.get("playerName") or "").strip(),
                "player_team_id": player.get("team") or None,
                "player_position_id": position,
                "sleeper_id": lookup.get(str(mfl)) if mfl else None,
                "overall_rank": rank,
                "position_rank": position_rank,
                "pos_rank": f"{position}{position_rank}" if position_rank else None,
                "tier": _number(sf.get("overallTier"), int),
                "value": _number(sf.get("value"), float),
                "age": _number(player.get("age"), float),
                "ktc_id": player.get("playerID"),
                "mfl_id": mfl,
                "rookie": player.get("rookie"),
                "positional_tier": sf.get("positionalTier"),
                "trend_overall": sf.get("overallTrend"),
                "trend_7_day": sf.get("overall7DayTrend"),
                "startup_adp": sf.get("startupAdp"),
                "kept": sf.get("kept"),
                "traded": sf.get("traded"),
                "cut": sf.get("cut"),
                "oneqb_value": (player.get("oneQBValues") or {}).get("value"),
                "oneqb_rank": (player.get("oneQBValues") or {}).get("rank"),
            }
        )

    if not out:
        raise DynastyParseError("No ranked players found in the KeepTradeCut page.")
    out.sort(key=lambda row: row["overall_rank"])
    return out


_FP_ECR = re.compile(r"\bvar\s+ecrData\s*=\s*")


def parse_fantasypros_page(html: str) -> list[dict[str, Any]]:
    """FantasyPros' rankings page: the `ecrData` object it renders from.

    The same entries the CSV export is made of, under the field names the
    ingest already speaks (`player_name`, `rank_ecr`, `rank_std`...). Read
    with `raw_decode` from where the assignment starts, rather than a regex to
    the closing brace, so a "};" inside a player's data cannot cut it short.
    """
    match = _FP_ECR.search(html)
    if not match:
        raise DynastyParseError("No ecrData found in the FantasyPros page.")
    try:
        data, _ = json.JSONDecoder().raw_decode(html, match.end())
    except json.JSONDecodeError as exc:
        raise DynastyParseError(f"FantasyPros' ecrData is malformed: {exc}") from exc

    out: list[dict[str, Any]] = []
    for player in data.get("players") or []:
        position = (player.get("player_position_id") or "").upper()
        if position not in DYNASTY_POSITIONS:
            continue

        rank = _number(player.get("rank_ecr"), int)
        if rank is None:
            continue

        _, position_rank = _split_pos_label(player.get("pos_rank"))
        out.append(
            {
                "player_name": (player.get("player_name") or "").strip(),
                "player_team_id": player.get("player_team_id") or None,
                "player_position_id": position,
                "overall_rank": rank,
                "position_rank": position_rank,
                "pos_rank": player.get("pos_rank") or None,
                "tier": _number(player.get("tier"), int),
                "age": _number(player.get("player_age"), float),
                "rank_min": _number(player.get("rank_min"), int),
                "rank_max": _number(player.get("rank_max"), int),
                "rank_ave": _number(player.get("rank_ave"), float),
                "rank_std": _number(player.get("rank_std"), float),
                "fantasypros_id": player.get("player_id"),
                "owned_pct": player.get("player_owned_avg"),
            }
        )

    if not out:
        raise DynastyParseError("No ranked players found in the FantasyPros page.")
    return out


def parse_thetradecalc_api(
    payload: list[dict[str, Any]], *, redraft: bool = False
) -> list[dict[str, Any]]:
    """TheTradeCalc's assets API — every player and pick it prices.

    One response holds both boards: `final_value` is the dynasty value and
    `redraft_value` this season's, both already on TheTradeCalc's 12-team
    superflex PPR baseline. Neither comes with a rank, so the rank is the order
    of the value, players only — picks are not draftable off a startup board.

    A player valued at zero is one TheTradeCalc carries but does not rate, and
    is dropped rather than tied for last with a thousand others.

    Carries `sleeper_id` outright, so like FantasyCalc it needs no name
    matching.
    """
    key = "redraft_value" if redraft else "final_value"
    players = []
    for asset in payload:
        if asset.get("type") != "player":
            continue
        position = (asset.get("position") or "").upper()
        value = _number(asset.get(key), float)
        if position not in DYNASTY_POSITIONS or not value:
            continue
        players.append((value, asset, position))

    # Value descending, name as a stable tie-break so equal values don't
    # reshuffle from one day to the next.
    players.sort(key=lambda item: (-item[0], item[1].get("name") or ""))

    out: list[dict[str, Any]] = [
        {
            "player_name": (asset.get("name") or "").strip(),
            "player_team_id": asset.get("team") or None,
            "player_position_id": position,
            "sleeper_id": asset.get("sleeper_id") or None,
            "overall_rank": rank,
            "value": value,
            "thetradecalc_id": asset.get("id"),
            "rookie": bool(asset.get("is_rookie")),
            "dynasty_value": asset.get("final_value"),
            "trade_value": asset.get("trade_value"),
            "redraft_value": asset.get("redraft_value"),
            "redraft_adp": asset.get("redraft_adp"),
            "sleeper_adp": asset.get("sleeper_adp"),
            "updated_at": asset.get("updated_at"),
        }
        for rank, (value, asset, position) in enumerate(players, start=1)
    ]

    if not out:
        raise DynastyParseError("No valued players found in the TheTradeCalc API response.")
    return _positional(out)


# --- Snapshots -----------------------------------------------------------
#
# What `ffd fetch-rankings` writes to data/rankings/: one CSV per board, in the
# shape the parsers above return, with the source named in its first column.
# Reading one back is how a day's numbers are restored without the network —
# `ffd import-rankings data/rankings/2026-10-16` puts that day back.

#: Snapshot columns that are numbers. Everything else is read back as text.
_SNAPSHOT_INTS = ("overall_rank", "position_rank", "tier", "rank_min", "rank_max")
_SNAPSHOT_FLOATS = ("value", "age", "rank_ave", "rank_std")


def write_snapshot(source: str, rows: list[dict[str, Any]]) -> str:
    """One board as CSV text: `source` first, then every column any row has."""
    columns: dict[str, None] = {"source": None}
    for row in rows:
        columns.update(dict.fromkeys(row))

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({"source": source, **row})
    return buffer.getvalue()


def parse_snapshot(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Read a CSV that `write_snapshot` wrote. Returns `(source, rows)`."""
    rows = _rows(text)
    if not rows:
        raise DynastyParseError("The snapshot has no rows.")

    sources = {row.get("source") for row in rows}
    if len(sources) != 1:
        raise DynastyParseError(f"A snapshot holds one board; this one names {sorted(sources)}.")
    source = sources.pop() or ""
    if source not in ALL_SOURCES:
        raise DynastyParseError(f"The snapshot's source {source!r} is not a known board.")

    out: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {k: (v if v != "" else None) for k, v in row.items()}
        entry.pop("source", None)
        for key in _SNAPSHOT_INTS:
            entry[key] = _number(entry.get(key), int)
        for key in _SNAPSHOT_FLOATS:
            entry[key] = _number(entry.get(key), float)
        if entry["overall_rank"] is None:
            continue
        if entry.get("player_position_id") not in DYNASTY_POSITIONS:
            continue
        out.append(entry)

    if not out:
        raise DynastyParseError(f"No ranked players found in the {source} snapshot.")
    return source, out


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

    Returns `(source, rows)`. Recognises the four hand exports and the
    snapshots `ffd fetch-rankings` writes. Raises `DynastyParseError` if the
    header matches none of them — which is the right outcome for a file dropped in the
    directory by accident, and for an export whose format has changed under us.
    """
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    # A snapshot names its own board, so it is recognised before the exports.
    if {"source", "player_name", "overall_rank"} <= set(first.split(",")):
        return parse_snapshot(text)

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

"""Querying the historical roster tables.

Read-only, and separate from `ingest` for the reason the ingest docstring gives
about plain functions: loading and asking are different jobs with different
failure modes, and nothing here should be able to write.

The question this module exists to answer: given a list of player names, how
many of them were on a given franchise's active roster in the stored window.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingest import _normalize_name
from app.models import NflPlayer, NflStint
from app.nflverse import resolve_team

# "Josh Allen, QB" / "Josh Allen (QB)" / "Josh Allen - QB". A submitted list is
# typed by a person, so the position hint that disambiguates two same-named
# players may arrive in any of these shapes.
_POSITION_HINT = re.compile(r"\s*[,(\-|]\s*([A-Za-z]{1,4})\s*\)?\s*$")


@dataclass(frozen=True)
class Stint:
    season: int
    franchise: str
    team: str | None
    position: str | None
    first_week: int | None
    last_week: int | None
    weeks: int


@dataclass
class Candidate:
    """One real player who could be the submitted name."""

    gsis_id: str
    full_name: str
    position: str | None
    college: str | None
    birth_date: str | None
    draft_club: str | None
    draft_number: int | None
    first_season: int | None
    last_season: int | None
    sleeper_id: str | None
    stints: list[Stint] = field(default_factory=list)

    def stints_with(self, franchise: str) -> list[Stint]:
        return sorted(
            (s for s in self.stints if s.franchise == franchise), key=lambda s: s.season
        )

    def teams(self) -> list[str]:
        """Franchises played for, most recent first."""
        seen: dict[str, int] = {}
        for stint in self.stints:
            seen[stint.franchise] = max(seen.get(stint.franchise, 0), stint.season)
        return [f for f, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


@dataclass
class Lookup:
    """The result of resolving one submitted line."""

    query: str
    position_hint: str | None
    candidates: list[Candidate]
    suggestions: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.candidates)

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1

    def hits(self, franchise: str) -> list[Candidate]:
        return [c for c in self.candidates if c.stints_with(franchise)]

    def played_for(self, franchise: str) -> bool:
        return bool(self.hits(franchise))


@dataclass
class RosterReport:
    franchise: str
    first_season: int | None
    last_season: int | None
    lookups: list[Lookup]

    @property
    def matched(self) -> list[Lookup]:
        """Submitted names that played for the franchise."""
        return [lk for lk in self.lookups if lk.played_for(self.franchise)]

    @property
    def others(self) -> list[Lookup]:
        """Found in the database, but never on this franchise."""
        return [lk for lk in self.lookups if lk.found and not lk.played_for(self.franchise)]

    @property
    def unknown(self) -> list[Lookup]:
        return [lk for lk in self.lookups if not lk.found]

    @property
    def uncertain(self) -> list[Lookup]:
        """Counted, but on a name that belongs to more than one player.

        Surfaced separately rather than folded into the total, because the
        count is only as trustworthy as these are: 44 names in the window
        belong to two different players.
        """
        return [lk for lk in self.matched if lk.ambiguous]

    @property
    def count(self) -> int:
        return len(self.matched)

    @property
    def total(self) -> int:
        return len(self.lookups)


def parse_submission(text: str) -> list[tuple[str, str | None]]:
    """Split a submitted list into (name, position hint) pairs.

    One player per line. Blank lines and `#` comments are skipped so a list can
    be kept in a file with notes in it. A leading list marker or rank ("1.",
    "- ") is stripped, because that is how names arrive when they are pasted
    out of an article or a spreadsheet.
    """
    entries: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line)
        if not line:
            continue

        hint: str | None = None
        match = _POSITION_HINT.search(line)
        if match:
            candidate = match.group(1).upper()
            # Only treat the tail as a position if it looks like one. Otherwise
            # "Marvin Harrison, Jr" would lose its suffix to a phantom hint.
            if candidate in _KNOWN_POSITIONS:
                hint = candidate
                line = line[: match.start()].strip()
        if line:
            entries.append((line, hint))
    return entries


_KNOWN_POSITIONS = frozenset(
    {
        "QB", "RB", "FB", "WR", "TE", "OL", "T", "G", "C", "OT", "OG",
        "DL", "DE", "DT", "NT", "LB", "ILB", "OLB", "MLB", "EDGE",
        "DB", "CB", "S", "FS", "SS", "K", "P", "LS", "PK",
    }
)


def _load_candidates(session: Session, name_keys: set[str]) -> dict[str, list[Candidate]]:
    """Every player matching any of the normalised names, with their stints.

    Two queries regardless of how long the submitted list is: one for the
    players, one for their stints. A per-name query would be simpler and would
    turn a 300-name list into 600 round trips.
    """
    if not name_keys:
        return {}

    players = session.scalars(
        select(NflPlayer).where(NflPlayer.name_key.in_(name_keys))
    ).all()
    if not players:
        return {}

    by_id: dict[str, Candidate] = {
        p.gsis_id: Candidate(
            gsis_id=p.gsis_id,
            full_name=p.full_name or "",
            position=p.position,
            college=p.college,
            birth_date=p.birth_date,
            draft_club=p.draft_club,
            draft_number=p.draft_number,
            first_season=p.first_season,
            last_season=p.last_season,
            sleeper_id=p.sleeper_id,
        )
        for p in players
    }

    stints = session.scalars(
        select(NflStint).where(NflStint.gsis_id.in_(by_id.keys()))
    ).all()
    for s in stints:
        by_id[s.gsis_id].stints.append(
            Stint(
                season=s.season,
                franchise=s.franchise,
                team=s.team,
                position=s.position,
                first_week=s.first_week,
                last_week=s.last_week,
                weeks=s.weeks,
            )
        )

    grouped: dict[str, list[Candidate]] = {}
    for player in players:
        grouped.setdefault(player.name_key, []).append(by_id[player.gsis_id])
    return grouped


def _all_name_keys(session: Session) -> list[str]:
    return list(session.scalars(select(NflPlayer.name_key).distinct()).all())


def resolve_submission(
    session: Session, entries: list[tuple[str, str | None]]
) -> list[Lookup]:
    """Turn submitted names into candidate players.

    A position hint narrows an ambiguous name but never widens a miss: if the
    hint eliminates every candidate it is ignored, on the grounds that a person
    writing "Josh Allen, LB" about the only Josh Allen in the data meant that
    player and got the position wrong.
    """
    keys = {_normalize_name(name) for name, _ in entries}
    grouped = _load_candidates(session, keys)

    # Only paid for if something actually missed, and only once per call.
    corpus: list[str] | None = None
    lookups: list[Lookup] = []

    for name, hint in entries:
        key = _normalize_name(name)
        candidates = list(grouped.get(key, []))

        if hint and len(candidates) > 1:
            narrowed = [c for c in candidates if (c.position or "").upper() == hint]
            if narrowed:
                candidates = narrowed

        suggestions: list[str] = []
        if not candidates:
            if corpus is None:
                corpus = _all_name_keys(session)
            near = difflib.get_close_matches(key, corpus, n=3, cutoff=0.85)
            if near:
                for match in _load_candidates(session, set(near)).values():
                    suggestions.extend(c.full_name for c in match)

        lookups.append(Lookup(query=name, position_hint=hint, candidates=candidates,
                              suggestions=sorted(set(suggestions))))
    return lookups


def build_report(session: Session, team: str, text: str) -> RosterReport:
    """The whole question, end to end: a team plus a submitted list."""
    franchise = resolve_team(team)
    if franchise is None:
        raise ValueError(f"Unknown team {team!r}")

    entries = parse_submission(text)
    lookups = resolve_submission(session, entries)
    first, last = stored_window(session)
    return RosterReport(
        franchise=franchise, first_season=first, last_season=last, lookups=lookups
    )


def stored_window(session: Session) -> tuple[int | None, int | None]:
    """The seasons actually in the database, so no answer overstates its range."""
    row = session.execute(
        select(func.min(NflStint.season), func.max(NflStint.season))
    ).one()
    return row[0], row[1]


def search_players(
    session: Session,
    name: str | None = None,
    team: str | None = None,
    position: str | None = None,
    season: int | None = None,
    limit: int = 50,
) -> list[Candidate]:
    """Free-text player search with optional team, position and season filters.

    Substring, not prefix: someone searching "jefferson" should find Justin
    Jefferson, and at ~5,900 players a scan costs nothing worth indexing for.
    """
    stmt = select(NflPlayer)

    if name:
        stmt = stmt.where(NflPlayer.name_key.contains(_normalize_name(name)))
    if position:
        stmt = stmt.where(func.upper(NflPlayer.position) == position.upper())

    if team or season is not None:
        sub = select(NflStint.gsis_id)
        if team:
            franchise = resolve_team(team)
            if franchise is None:
                raise ValueError(f"Unknown team {team!r}")
            sub = sub.where(NflStint.franchise == franchise)
        if season is not None:
            sub = sub.where(NflStint.season == season)
        stmt = stmt.where(NflPlayer.gsis_id.in_(sub))

    stmt = stmt.order_by(NflPlayer.last_season.desc(), NflPlayer.name_key).limit(limit)
    players = session.scalars(stmt).all()
    if not players:
        return []

    found = {p.gsis_id: p for p in players}
    by_id = {
        gid: Candidate(
            gsis_id=p.gsis_id,
            full_name=p.full_name or "",
            position=p.position,
            college=p.college,
            birth_date=p.birth_date,
            draft_club=p.draft_club,
            draft_number=p.draft_number,
            first_season=p.first_season,
            last_season=p.last_season,
            sleeper_id=p.sleeper_id,
        )
        for gid, p in found.items()
    }
    for s in session.scalars(select(NflStint).where(NflStint.gsis_id.in_(by_id))).all():
        by_id[s.gsis_id].stints.append(
            Stint(
                season=s.season,
                franchise=s.franchise,
                team=s.team,
                position=s.position,
                first_week=s.first_week,
                last_week=s.last_week,
                weeks=s.weeks,
            )
        )
    return [by_id[p.gsis_id] for p in players]


def format_history(candidate: Candidate, franchise: str | None = None) -> str:
    """'OAK 2017-2019, LV 2020-2021' — consecutive seasons collapsed to a range.

    Grouped by the *era* abbreviation rather than the franchise, so a Raiders
    career reads as the two names it was actually played under.
    """
    stints = candidate.stints
    if franchise:
        stints = [s for s in stints if s.franchise == franchise]
    if not stints:
        return "-"

    parts: list[tuple[str, int, int]] = []
    # Ordered by week within a season, not alphabetically: a player traded in
    # October should read "DAL 2018, OAK 2018" in the order it happened.
    for stint in sorted(stints, key=lambda s: (s.season, s.first_week or 0)):
        label = stint.team or stint.franchise
        if parts and parts[-1][0] == label and parts[-1][2] == stint.season - 1:
            parts[-1] = (label, parts[-1][1], stint.season)
        else:
            parts.append((label, stint.season, stint.season))

    return ", ".join(
        f"{label} {start}" if start == end else f"{label} {start}-{end}"
        for label, start, end in parts
    )

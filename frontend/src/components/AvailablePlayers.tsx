import { useMemo, useState } from "react";

import type { AvailableSort, Draft, Player } from "../api/client";
import { signedGap } from "../format";
import { useAvailable, useDraft } from "../queries";
import { positionClass } from "./DraftBoard";

/**
 * Best available: the player pool minus everyone already drafted.
 *
 * Rankable two ways — by FantasyPros consensus or by Sleeper ADP — because
 * "best" has two defensible answers on draft night, and the gap between them is
 * the interesting part. Either ordering can be filtered by position.
 *
 * Whichever measure is doing the ordering leads the table; the other keeps its
 * column on the right. Both are always on screen, so the ECR column between
 * them can be checked against its own operands rather than taken on trust.
 *
 * Tell it which slot is yours and it rules the list where your turns are
 * projected to land, which is the question the list is really being read to
 * answer: not "who is best" but "who will still be here when it's my turn".
 *
 * Proj is the Vegas-derived season projection, the one column here that is a
 * quantity rather than a rank: the others say where a player sits in a queue,
 * this one says how far ahead of the next man he is.
 *
 * The overall list stops at 150. A position tab shows however many of that
 * position can be ranked the chosen way, which is always fewer.
 */

const FILTERS: (string | null)[] = [null, "QB", "RB", "WR", "TE", "K", "DEF"];

/** The two defensible answers to "who is best available". */
const SORTS: { value: AvailableSort; label: string; meta: string }[] = [
  { value: "ecr", label: "FantasyPros", meta: "FantasyPros consensus" },
  { value: "adp", label: "Sleeper ADP", meta: "Sleeper ADP" },
];

/**
 * The two measures the table shows, by name and by where to read them.
 *
 * `ecr_overall` rather than `ecr`: in a position-filtered view the latter is
 * the positional rank, which is already on screen in the Pos column and is not
 * comparable with an ADP.
 */
const MEASURES = {
  ecr: { label: "FP ", of: (p: Player) => p.ecr_overall },
  adp: { label: "ADP", of: (p: Player) => p.adp },
} as const;

/** Whichever sort is active leads; the other trails. */
function columns(sort: AvailableSort) {
  return sort === "adp"
    ? { lead: MEASURES.adp, trail: MEASURES.ecr }
    : { lead: MEASURES.ecr, trail: MEASURES.adp };
}

/** One of your turns: the round it falls in, and its overall pick number. */
interface Turn {
  round: number;
  pick: number;
}

/**
 * Your remaining turns, in order.
 *
 * A snake reverses on even rounds, so slot 1 picks first and then last around
 * the turn, while the slot at the end of the order picks twice in a row. That
 * is why two markers so often land on adjacent rows. Linear drafts don't
 * reverse; auctions have no pick order at all and are filtered out by the
 * caller.
 *
 * A turn drops off once its pick number is on the board — which covers picks
 * already made and, just as importantly, your own keepers: a keeper is written
 * in at the pick it cost, and you don't get to draft there. Since the server
 * skips those same numbers when projecting, both sides step over exactly the
 * same picks, and the markers walk down the list on their own as the draft
 * runs.
 */
function turnsFor(draft: Draft, slot: number): Turn[] {
  const teams = draft.teams ?? 0;
  const rounds = draft.rounds ?? 0;
  if (!teams || !rounds || slot < 1 || slot > teams) return [];

  const snake = draft.type !== "linear";
  const spent = new Set(draft.picks.map((pick) => pick.pick_no));

  const turns: Turn[] = [];
  for (let round = 1; round <= rounds; round++) {
    const inRound = snake && round % 2 === 0 ? teams - slot + 1 : slot;
    const pick = (round - 1) * teams + inRound;
    if (!spent.has(pick)) turns.push({ round, pick });
  }
  return turns;
}

/**
 * Which rows your turns land on, keyed by player.
 *
 * A turn lands on the first player still projected to be on the board when it
 * comes round: everyone above him is expected to be gone by then. Because
 * `expected_pick` counts the whole pool, this works identically on a filtered
 * tab — where the projections skip, and several turns can therefore share one
 * row. That reads as "he is the best one left at any of these picks", which is
 * the honest answer rather than a marker invented per position.
 */
function markersFor(players: Player[], turns: Turn[]): Map<string, Turn[]> {
  const marks = new Map<string, Turn[]>();
  let next = 0;
  for (const player of players) {
    if (player.expected_pick === null) continue;
    const here: Turn[] = [];
    while (next < turns.length && turns[next].pick <= player.expected_pick) {
      here.push(turns[next]);
      next += 1;
    }
    if (here.length) marks.set(player.player_id, here);
  }
  return marks;
}

/** Remembered across reloads: you set your slot once, then draft for hours. */
const SLOT_KEY = "ffd.draft-slot";

function storedSlot(): number | null {
  try {
    const raw = window.localStorage.getItem(SLOT_KEY);
    const slot = raw === null ? NaN : Number(raw);
    return Number.isInteger(slot) && slot > 0 ? slot : null;
  } catch {
    // Private windows and blocked site data both throw on access.
    return null;
  }
}

function rememberSlot(slot: number | null): void {
  try {
    if (slot === null) window.localStorage.removeItem(SLOT_KEY);
    else window.localStorage.setItem(SLOT_KEY, String(slot));
  } catch {
    // Not remembering it is a smaller problem than not setting it.
  }
}

function PlayerRow({
  player,
  sort,
  turns,
}: {
  player: Player;
  sort: AvailableSort;
  turns?: Turn[];
}) {
  // Sleeper's market against FantasyPros' consensus, on the same convention as
  // the board: negative means he goes *earlier* than the experts rank him.
  //
  // Always the *overall* rank. In a position-filtered view `player.ecr` is the
  // positional one, and "an ADP of 22 minus QB1" is not a number that means
  // anything.
  const ecr = signedGap(player.adp, player.ecr_overall);
  const { lead, trail } = columns(sort);

  return (
    <tr className={turns ? "is-my-pick" : undefined}>
      <td className="num col-lead">
        {lead.of(player) ?? <span className="pending">—</span>}
      </td>
      <td>
        <span className="player-name">{player.full_name ?? player.player_id}</span>
        {player.injury_status && <span className="tag tag--inline injury">{player.injury_status}</span>}
        {turns && (
          <span className="pick-note">
            {turns.length > 1 ? "Your picks" : "Your pick"}:{" "}
            {turns.map((turn) => `Round ${turn.round} · #${turn.pick}`).join(", ")}
          </span>
        )}
      </td>
      <td>
        <span className={`pos-chip${positionClass(player.position)}`}>{player.position}</span>
        {/* FantasyPros' published positional rank, not a recount of who is
            left: it answers "how good is he", not "how many remain". */}
        {player.pos_rank && <span className="pos-rank">{player.pos_rank}</span>}
      </td>
      <td className="num">{player.team ?? "FA"}</td>
      <td className="num">{player.bye_week ?? <span className="pending">—</span>}</td>
      <td className="num">{player.tier ?? <span className="pending">—</span>}</td>
      <td className="num">
        {player.vegas_points !== null ? (
          <span title={`${player.vegas_points} projected points`}>
            {Math.round(player.vegas_points)}
          </span>
        ) : (
          <span className="pending">—</span>
        )}
      </td>
      <td className="num">
        {trail.of(player) ?? <span className="pending">—</span>}
      </td>
      <td className="num">
        {ecr !== null ? (
          <span title={`Sleeper ADP ${player.adp} against FantasyPros rank ${player.ecr_overall}`}>
            {ecr}
          </span>
        ) : (
          <span className="pending">—</span>
        )}
      </td>
    </tr>
  );
}

export function AvailablePlayers() {
  const [position, setPosition] = useState<string | null>(null);
  const [sort, setSort] = useState<AvailableSort>("ecr");
  const [slot, setSlot] = useState<number | null>(storedSlot);

  const available = useAvailable(position, sort);
  // The same cache entry the page's board already holds, so this costs no
  // extra request and the two never disagree about how many picks are in.
  const draft = useDraft();

  const active = SORTS.find((s) => s.value === sort) ?? SORTS[0];
  const { lead, trail } = columns(sort);

  const board = draft.data;
  // An auction has no pick order to project onto, and an unset draft order
  // leaves the slots unnamed but still numbered — that is enough.
  const canProject = Boolean(board && board.type !== "auction" && board.teams && board.rounds);

  const turns = useMemo(
    () => (board && canProject && slot ? turnsFor(board, slot) : []),
    [board, canProject, slot],
  );
  const marks = useMemo(
    () => markersFor(available.data ?? [], turns),
    [available.data, turns],
  );

  const chooseSlot = (value: string) => {
    const chosen = value === "" ? null : Number(value);
    setSlot(chosen);
    rememberSlot(chosen);
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="argyle-mark" aria-hidden />
        <div className="sec-head">
          <h2>Best available</h2>
          <span className="spacer" />
          <span className="meta">{active.meta}</span>
        </div>
      </div>

      <div className="filter-bar" role="group" aria-label="Rank by">
        <span className="filter-label">Rank by</span>
        {SORTS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === sort ? "chip is-on" : "chip"}
            aria-pressed={option.value === sort}
            onClick={() => setSort(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="filter-bar" role="group" aria-label="Filter by position">
        <span className="filter-label">Position</span>
        {FILTERS.map((value) => (
          <button
            key={value ?? "all"}
            type="button"
            className={value === position ? "chip is-on" : "chip"}
            aria-pressed={value === position}
            onClick={() => setPosition(value)}
          >
            {value ?? "All"}
          </button>
        ))}
      </div>

      {canProject && board && (
        <div className="filter-bar">
          <label className="filter-label" htmlFor="draft-slot">
            My slot
          </label>
          <select
            id="draft-slot"
            className="slot-select"
            value={slot ?? ""}
            onChange={(event) => chooseSlot(event.target.value)}
          >
            <option value="">Not set</option>
            {board.slots.map((entry) => (
              <option key={entry.slot} value={entry.slot}>
                {entry.slot}. {entry.team_name || entry.display_name || "Unclaimed"}
              </option>
            ))}
          </select>
          {/* Nothing about the next turn: the first rule in the table says
              where it lands, and says it against the player who will be there,
              which is the part worth knowing. */}
          <span className="meta">
            {!slot
              ? "Pick a slot to see where your turns land"
              : turns.length
                ? ""
                : "No picks left"}
          </span>
        </div>
      )}

      {available.isPending ? (
        <div className="state">
          <h3>Loading players</h3>
          <p>Reading the pool from the database.</p>
        </div>
      ) : available.isError ? (
        <div className="state state--error">
          <h3>Couldn&rsquo;t load players</h3>
          <p>{(available.error as Error).message}</p>
        </div>
      ) : available.data.length === 0 ? (
        <div className="state">
          <h3>No players to rank</h3>
          <p>
            Nothing has been ingested yet. Run <code>ffd sync-players</code> on the host,
            then <code>ffd sync-rankings</code> and <code>ffd sync-adp</code> — all three run
            daily once their timers are enabled.
          </p>
        </div>
      ) : (
        <div className="table-scroll">
          <table className="stat-table available-table">
            <thead>
              <tr>
                {/* The active measure leads, so the column the list is sorted
                    by is the first thing read. */}
                <th className="num col-lead">{lead.label}</th>
                <th>Player</th>
                <th className="col-pos">Pos</th>
                <th className="num col-team">Team</th>
                <th className="num col-bye">Bye</th>
                {/* FantasyPros' overall tier: where the consensus sees a real
                    drop-off, which is the cue for trading down. */}
                <th className="num col-tier">Tier</th>
                {/* Beside Tier rather than beside the market columns: both
                    answer "how good is he", where ADP and ECR answer "what
                    will the room do about it". */}
                <th className="num col-proj">Proj</th>
                <th className="num col-adp">{trail.label}</th>
                {/* Not FantasyPros' rank itself — the gap between the Sleeper
                    market and that rank. Negative means the room drafts him
                    ahead of where the experts have him. */}
                <th className="num col-ecr">ECR</th>
              </tr>
            </thead>
            <tbody>
              {available.data.map((player) => (
                <PlayerRow
                  key={player.player_id}
                  player={player}
                  sort={sort}
                  turns={marks.get(player.player_id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel-foot">
        <p className="note">
          Both orderings refresh daily: FantasyPros ranks about 515 players, of whom
          about 270 carry a Sleeper ADP &mdash; so the ADP list is the shorter of the two.
          ECR is the Sleeper market against the consensus under either:{" "}
          <strong>negative means the room drafts him ahead of where the experts rank
          him</strong>, positive means he is still there later than they say he should be.
          Drafted players drop off within twenty seconds of the pick, and the rules
          marking your turns move up the list with them.
        </p>
      </div>
    </section>
  );
}

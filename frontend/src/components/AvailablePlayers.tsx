import { useMemo, useState } from "react";

import {
  BOARDS,
  CONSENSUS_BOARDS,
  type Board,
  type Draft,
  type Player,
} from "../api/client";
import { useAvailable, useDraft } from "../queries";
import { boardRounds, positionClass } from "./DraftBoard";

/**
 * Best available: the player pool minus everyone already drafted.
 *
 * A tab per board, because in dynasty there is no single answer to "who is
 * best". Four published boards disagree about it on purpose — they are pricing
 * different things — the Sleeper market disagrees with all of them, and the
 * aggregate is the mean of the four that are opinions rather than outcomes.
 * The redraft tabs order by this season alone and sit outside the aggregate.
 *
 * **Every row shows every board whichever tab is active.** That is the page:
 * the tab only decides the ordering, and the value is in reading across. A
 * player the boards average 14th who Sleeper drafters take 40th is the actual
 * find, and no single-number table can show him to you.
 *
 * Positions are filtered by lineup slot as well as by position, because FLEX
 * and SUPERFLEX are what this league actually starts (§2.1) and "who can I put
 * in my superflex" is the question being asked, not "who plays QB".
 *
 * Tell it which slot is yours and it rules the list where your turns are
 * projected to land — not "who is best" but "who will still be here when it's
 * my turn".
 */

/**
 * The position filters, as lineup slots.
 *
 * SUPERFLEX admits every position this league starts, so it returns what All
 * does. It is here anyway and not as a duplicate: the label is the league's
 * own slot, and reading it as "everyone is superflex-eligible" is a true and
 * useful thing to notice about a format with no K and no DEF.
 */
const FILTERS: (string | null)[] = [null, "QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX"];

/** The rank columns, in reading order: the average first, then its parts. */
const RANK_COLUMNS: { board: Board; label: string; title: string }[] = [
  { board: "aggregate", label: "AGG", title: "Average of the four dynasty boards" },
  { board: "fantasypros", label: "FP", title: "FantasyPros dynasty superflex consensus" },
  { board: "fantasycalc", label: "FC", title: "FantasyCalc dynasty" },
  { board: "thetradecalc", label: "TTC", title: "TheTradeCalc 12-team superflex PPR" },
  { board: "ktc", label: "KTC", title: "KeepTradeCut dynasty superflex" },
  { board: "sleeper-adp", label: "ADP", title: "Where Sleeper drafters actually take him" },
];

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
  const rounds = boardRounds(draft);
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
/** Likewise the tab — an owner who trusts one board will want it every time. */
const BOARD_KEY = "ffd.draft-board";

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

function storedBoard(): Board {
  try {
    const raw = window.localStorage.getItem(BOARD_KEY);
    // Validated against the list rather than cast: a board removed in a later
    // release would otherwise come back out of storage and 422 every request.
    const found = BOARDS.find((option) => option.value === raw);
    return found ? found.value : "aggregate";
  } catch {
    return "aggregate";
  }
}

function rememberBoard(board: Board): void {
  try {
    window.localStorage.setItem(BOARD_KEY, board);
  } catch {
    /* not remembering it is a smaller problem than not setting it */
  }
}

/** A rank cell, or an em dash where that board has no opinion of him. */
function Rank({ value, sorted }: { value: number | undefined; sorted: boolean }) {
  return (
    <td className={sorted ? "num is-sorted" : "num"}>
      {value ?? <span className="pending">—</span>}
    </td>
  );
}

function PlayerRow({
  player,
  board,
  turns,
}: {
  player: Player;
  board: Board;
  turns?: Turn[];
}) {
  // The aggregate is not in `ranks` — it is computed per request, and it is a
  // mean rather than a rank, so it rounds to one decimal rather than pretending
  // to the precision of a whole placing.
  const rankOf = (column: Board): number | undefined =>
    column === "aggregate" ? undefined : player.ranks[column];

  return (
    <tr className={turns ? "is-my-pick" : undefined}>
      <td>
        <span className="player-name">{player.full_name ?? player.player_id}</span>
        {/* A rookie, from Sleeper's own accrued-seasons count. In a startup
            this is the whole incoming class on the same board as everyone
            else, and it is the one thing about a player that a rank cannot
            tell you: two players the boards agree on are not the same asset
            when one of them has six more years of it left.

            role="img" with a label, because a bare ★ is announced as "black
            star" — which is the glyph, not the meaning. */}
        {player.years_exp === 0 && (
          <span className="rookie" role="img" aria-label="Rookie" title="Rookie">
            ★
          </span>
        )}
        {player.injury_status && (
          <span className="tag tag--inline injury">{player.injury_status}</span>
        )}
        {turns && (
          <span className="pick-note">
            {turns.length > 1 ? "Your picks" : "Your pick"}:{" "}
            {turns.map((turn) => `Round ${turn.round} · #${turn.pick}`).join(", ")}
          </span>
        )}
      </td>
      <td>
        <span className={`pos-chip${positionClass(player.position)}`}>{player.position}</span>
        {/* The published positional rank, not a recount of who is left: it
            answers "how good is he", not "how many remain". */}
        {player.pos_rank && <span className="pos-rank">{player.pos_rank}</span>}
      </td>
      <td className="num">{player.team ?? "FA"}</td>
      {/* The single most load-bearing number on a dynasty board, which is why
          it sits with the player rather than off with the projections. */}
      <td className="num col-age">
        {player.ranked_age ?? player.age ?? <span className="pending">—</span>}
      </td>

      <td className={board === "aggregate" ? "num is-sorted" : "num"}>
        {player.consensus !== null ? (
          <span
            title={`Mean of ${player.consensus_sources} of ${CONSENSUS_BOARDS.length} dynasty boards`}
          >
            {player.consensus}
            {/* Ranked by fewer than all four: the mean is over a smaller and
                more flattering sample, and the row says so rather than letting
                a one-board favourite pass for a consensus pick. */}
            {player.consensus_sources < CONSENSUS_BOARDS.length && (
              <span className="of-sources">/{player.consensus_sources}</span>
            )}
          </span>
        ) : (
          <span className="pending">—</span>
        )}
      </td>
      {RANK_COLUMNS.slice(1).map((column) => (
        <Rank key={column.board} value={rankOf(column.board)} sorted={board === column.board} />
      ))}

      <td className="num col-tier">{player.tier ?? <span className="pending">—</span>}</td>
      <td className="num col-value">
        {player.value !== null ? (
          Math.round(player.value).toLocaleString()
        ) : (
          <span className="pending">—</span>
        )}
      </td>
      <td className="num col-proj">
        {player.vegas_points !== null ? (
          <span title={`${player.vegas_points} projected points`}>
            {Math.round(player.vegas_points)}
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
  const [board, setBoard] = useState<Board>(storedBoard);
  const [slot, setSlot] = useState<number | null>(storedSlot);

  const available = useAvailable(position, board);
  // The same cache entry the page's board already holds, so this costs no
  // extra request and the two never disagree about how many picks are in.
  const draft = useDraft();

  const active = BOARDS.find((option) => option.value === board) ?? BOARDS[0];

  const boardData = draft.data;
  // An auction has no pick order to project onto, and an unset draft order
  // leaves the slots unnamed but still numbered — that is enough.
  const canProject = Boolean(
    boardData && boardData.type !== "auction" && boardData.teams && boardData.rounds,
  );

  const turns = useMemo(
    () => (boardData && canProject && slot ? turnsFor(boardData, slot) : []),
    [boardData, canProject, slot],
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

  const chooseBoard = (value: Board) => {
    setBoard(value);
    rememberBoard(value);
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
        <span className="filter-label">Ranking</span>
        {BOARDS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === board ? "chip is-on" : "chip"}
            aria-pressed={option.value === board}
            onClick={() => chooseBoard(option.value)}
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

      {canProject && boardData && (
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
            {boardData.slots.map((entry) => (
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
            then <code>ffd import-rankings</code> to load the dynasty exports from{" "}
            <code>data/</code>.
          </p>
        </div>
      ) : (
        <div className="table-scroll">
          <table className="stat-table available-table">
            <thead>
              <tr>
                <th>Player</th>
                <th className="col-pos">Pos</th>
                <th className="num col-team">Team</th>
                <th className="num col-age" title="Age, as the ranking published it">
                  Age
                </th>
                {/* Five boards, always all five. The sorted one is marked
                    rather than moved: a column that jumps to the front when
                    you select it makes the tabs harder to compare, which is
                    the one thing this table exists to do. */}
                {RANK_COLUMNS.map((column) => (
                  <th
                    key={column.board}
                    className={board === column.board ? "num is-sorted" : "num"}
                    title={column.title}
                    aria-sort={board === column.board ? "ascending" : undefined}
                  >
                    {column.label}
                  </th>
                ))}
                {/* FantasyPros' tier: where the consensus sees a real
                    drop-off, which is the cue for trading down. */}
                <th className="num col-tier">Tier</th>
                {/* Trade value on the active board's scale where it has one.
                    A rank says who is better; this says by how much, which is
                    what a trade is actually priced on. */}
                <th className="num col-value">Value</th>
                {/* The one column that is a quantity rather than a placing:
                    Vegas-derived points for *this* season, which is the thing
                    every dynasty board deliberately looks past. */}
                <th className="num col-proj">Proj</th>
              </tr>
            </thead>
            <tbody>
              {available.data.map((player) => (
                <PlayerRow
                  key={player.player_id}
                  player={player}
                  board={board}
                  turns={marks.get(player.player_id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel-foot">
        <p className="note">
          <strong>AGG</strong> is the mean of FantasyPros, FantasyCalc, TheTradeCalc and
          KTC, refreshed daily; Sleeper ADP is left out of it deliberately, since it is
          what the room <em> did</em> rather than what anyone thinks, and so are the
          redraft boards. A <code>/2</code> beside an aggregate means only two of the
          four ranked him, so it is the more
          flattering average of a smaller sample. The gap between{" "}
          <strong>AGG</strong> and <strong>ADP</strong> is where a startup is won: a
          player the boards rate well above where Sleeper takes him is the one to wait
          on. <span className="rookie" aria-hidden>★</span> marks a rookie — Sleeper
          has no accrued season for him, so in this startup that is the whole
          incoming class. Boards are imported by hand with{" "}
          <code>ffd import-rankings</code>, so they change only when you refresh the
          exports.
        </p>
      </div>
    </section>
  );
}

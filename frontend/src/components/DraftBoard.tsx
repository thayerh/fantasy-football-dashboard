import type { Draft, DraftPick, DraftSlot } from "../api/client";
import { signedGap } from "../format";

/**
 * The board: rounds down, draft slots across.
 *
 * Placed picks carry their own round and slot, so nothing is inferred for
 * them. Only *empty* cells need arithmetic — the pick number a cell will
 * eventually hold — and for a snake draft that means reversing even rounds.
 */

/** Positions the theme has a colour for. Anything else (IDP leagues) stays
 *  white rather than being lumped in with a position it isn't. */
const COLOURED = new Set(["QB", "RB", "WR", "TE", "K", "DEF"]);

export function positionClass(position: string | null): string {
  const pos = position?.toUpperCase() ?? "";
  return COLOURED.has(pos) ? ` pos-${pos.toLowerCase()}` : "";
}

function fullName(slot: DraftSlot): string {
  const handle = slot.display_name ?? "unclaimed";
  return slot.team_name ? `${slot.team_name} (${handle})` : handle;
}

function pickNumber(draft: Draft, round: number, slot: number): number {
  const teams = draft.teams ?? 0;
  const snake = draft.type === "snake" && round % 2 === 0;
  return (round - 1) * teams + (snake ? teams - slot + 1 : slot);
}

function Cell({ draft, round, slot, pick }: {
  draft: Draft;
  round: number;
  slot: number;
  pick: DraftPick | undefined;
}) {
  if (!pick) {
    return (
      <td className="board-cell is-empty">
        <span className="pick-no">{pickNumber(draft, round, slot)}</span>
      </td>
    );
  }

  // Split across two lines rather than left to wrap. Wrapping is a function of
  // how long the name happens to be, so "Bijan Robinson" broke over two lines
  // while "James Cook" stayed on one, and the board's rows never lined up.
  const name = [pick.first_name, pick.last_name].filter(Boolean) as string[];
  const classes = `board-cell${positionClass(pick.position)}${pick.is_keeper ? " is-keeper" : ""}`;
  return (
    <td className={classes}>
      <span className="pick-no">
        {pick.pick_no}
        {pick.is_keeper && " · Keeper"}
      </span>
      <span className="player">
        {(name.length ? name : ["Unknown player"]).map((part) => (
          <span key={part}>{part}</span>
        ))}
      </span>
      <span className="pos">
        {[pick.position, pick.nfl_team].filter(Boolean).join(" · ")}
      </span>
      <PickDelta pick={pick} />
    </td>
  );
}

/**
 * Where this pick landed against two different references, on one convention:
 * **negative means taken earlier than the reference, positive means later.**
 *
 * **ADP** compares the pick to the Sleeper market — negative is a reach past
 * where the room usually takes him, positive is a player who lasted (value).
 *
 * **ECR** compares the same pick to FantasyPros' consensus rank. A pick at 28
 * of a player ranked 68 reads −40: taken forty slots ahead of where the experts
 * have him.
 */
function PickDelta({ pick }: { pick: DraftPick }) {
  const adp = signedGap(pick.pick_no, pick.adp);
  const ecr = signedGap(pick.pick_no, pick.ecr);
  if (adp === null && ecr === null) return null;

  return (
    <span className="pick-delta">
      {adp !== null && (
        <span title={`Taken at ${pick.pick_no}; Sleeper ADP ${pick.adp}`}>ADP {adp}</span>
      )}
      {ecr !== null && (
        <span title={`Taken at ${pick.pick_no}; FantasyPros rank ${pick.ecr}`}>
          ECR {ecr}
        </span>
      )}
    </span>
  );
}

export function DraftBoard({ draft }: { draft: Draft }) {
  const rounds = draft.rounds ?? 0;
  const teams = draft.teams ?? 0;

  // One lookup keyed by round:slot beats scanning the pick list per cell —
  // 15 x 12 cells against 180 picks is 32,400 comparisons otherwise.
  const byCell = new Map<string, DraftPick>();
  for (const pick of draft.picks) {
    if (pick.round && pick.draft_slot) byCell.set(`${pick.round}:${pick.draft_slot}`, pick);
  }

  return (
    <div className="table-scroll">
      <table className="stat-table draft-board">
        <thead>
          <tr>
            <th className="round-head">Rd</th>
            {draft.slots.map((slot) => (
              <th className="slot-head" key={slot.slot}>
                <span className="slot-no">{slot.slot}</span>
                {/* Columns are too narrow for most team names, so the full
                    name lives in the tooltip rather than being lost. */}
                <span className="slot-name" title={fullName(slot)}>
                  {slot.team_name ?? slot.display_name ?? "—"}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rounds }, (_, i) => i + 1).map((round) => (
            <tr key={round}>
              <td className="round-cell">{round}</td>
              {Array.from({ length: teams }, (_, i) => i + 1).map((slot) => (
                <Cell
                  key={slot}
                  draft={draft}
                  round={round}
                  slot={slot}
                  pick={byCell.get(`${round}:${slot}`)}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

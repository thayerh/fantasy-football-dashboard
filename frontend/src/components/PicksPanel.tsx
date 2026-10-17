import type { Roster, RosterPick } from "../api/client";
import { teamName } from "./RosterRail";

/**
 * A team's future rookie picks — the other half of what a dynasty roster owns.
 *
 * Sleeper's own roster view puts picks behind a separate tab, which is how a
 * league ends up trading them without ever quite looking at them. They belong
 * beside the players: a rebuilding roster with nine 2027 picks and a
 * contending one with none are not the same team, and nothing on the two
 * tables above would tell you which is which.
 *
 * Picks a team has traded away are listed too, muted, under the ones it holds.
 * Absence is the thing you cannot see otherwise — a first-rounder that is
 * gone leaves no row of its own — and it is what makes the panel readable as
 * "what this team has done" rather than only "what it has".
 */

/** 1st, 2nd, 3rd, 4th — the way a pick is actually said out loud. */
const ORDINALS = ["", "1st", "2nd", "3rd", "4th"];

function ordinal(round: number): string {
  return ORDINALS[round] ?? `${round}th`;
}

function PickRow({
  pick,
  team,
  label,
  counterparty,
}: {
  pick: RosterPick;
  team: Roster;
  label: string;
  counterparty: string;
}) {
  return (
    <tr>
      <td>
        <span className="slot-label">{label}</span>
      </td>
      <td>
        <span className="player-name">
          {pick.season} {ordinal(pick.round)}
        </span>
      </td>
      {/* "Own" is the default case and recedes; a team name is the thing on
          the row worth reading. */}
      <td className={pick.roster_id === team.roster_id ? "pick-from is-own" : "pick-from"}>
        {counterparty}
      </td>
    </tr>
  );
}

export function PicksPanel({
  team,
  teams,
  seasons,
  rounds,
}: {
  team: Roster;
  teams: Roster[];
  seasons: string[];
  rounds: number;
}) {
  // Defaulted, though the types say they cannot be missing, because the types
  // describe *this* version of the API and the browser may be talking to
  // another one: an API older than the bundle sends no picks at all. The panel
  // then has nothing to show and says nothing, which is the truth, rather than
  // throwing and taking the roster tables down with it.
  const windows = seasons ?? [];
  const held = team.picks ?? [];
  const gone = team.picks_traded ?? [];
  if (windows.length === 0) return null;

  // The payload carries every team once; resolving names here rather than on
  // each of a dozen picks is why the API sends roster ids.
  const names = new Map(teams.map((other) => [other.roster_id, teamName(other)]));
  const nameOf = (rosterId: number) => names.get(rosterId) ?? `Roster ${rosterId}`;

  const own = held.filter((pick) => pick.roster_id === team.roster_id).length;
  const acquired = held.length - own;

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="argyle-mark" aria-hidden />
        <div className="sec-head">
          <h2>Draft picks</h2>
          <span className="spacer" />
          <span className="meta">
            {held.length} held
            {acquired > 0 && ` · ${acquired} acquired`}
            {gone.length > 0 && ` · ${gone.length} traded away`}
          </span>
        </div>
      </div>

      <div className="table-scroll">
        <table className="stat-table roster-table">
          <thead>
            <tr>
              <th className="col-slot">Status</th>
              <th>Pick</th>
              <th>Team</th>
            </tr>
          </thead>
          {/* Held first, then the ones that are gone — the same grouped-tbody
              rule the depth table uses, so the divider means the same thing on
              both. */}
          <tbody>
            {held.map((pick) => (
              <PickRow
                key={`${pick.season}-${pick.round}-${pick.roster_id}`}
                pick={pick}
                team={team}
                label="Held"
                counterparty={
                  pick.roster_id === team.roster_id ? "Own" : `From ${nameOf(pick.roster_id)}`
                }
              />
            ))}
          </tbody>
          {gone.length > 0 && (
            <tbody className="picks-gone">
              {gone.map((pick) => (
                <PickRow
                  key={`gone-${pick.season}-${pick.round}`}
                  pick={pick}
                  team={team}
                  label="Traded"
                  counterparty={`To ${nameOf(pick.roster_id)}`}
                />
              ))}
            </tbody>
          )}
        </table>
      </div>

      <div className="panel-foot">
        <p className="note">
          Rookie drafts run {rounds} linear rounds (rulebook §6.1). Picks are tradeable in
          the current league year plus the next two (§8.3), so the window is{" "}
          {windows[0]}–{windows[windows.length - 1]}; anything further out belongs to
          whoever it was issued to and is not shown.
        </p>
      </div>
    </section>
  );
}

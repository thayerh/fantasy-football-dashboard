import type { Roster, RosterLimits } from "../api/client";
import { Avatar } from "./Avatar";

/**
 * The team switcher, in the rail.
 *
 * Twelve teams is too many for a filter row and too few for a search box, and
 * the rail already exists to hold exactly this kind of secondary navigation
 * (GAMEDAY.md §4). Buttons rather than links: the page has one URL, because
 * the router deliberately has no parameterised routes yet — see router.tsx.
 */

/** What the switcher calls a team. Most owners never set a team name. */
export function teamName(team: Roster): string {
  return team.team_name ?? team.display_name ?? `Roster ${team.roster_id}`;
}

/**
 * Who owns a team, on a line already headed by that team's name.
 *
 * Null where the two would be the same word: most owners never set a team
 * name, so the header is already their handle, and repeating it fills the line
 * with nothing. A team nobody owns says so — that is real, and not the same as
 * an owner whose handle we simply do not need twice.
 */
export function ownerLabel(team: Roster): string | null {
  if (!team.owner_id) return "Unclaimed";
  return team.team_name ? team.display_name : null;
}

/** A record, once anybody has played. */
export function recordOf(team: Roster): string | null {
  if (team.wins + team.losses + team.ties === 0) return null;
  return `${team.wins}–${team.losses}${team.ties ? `–${team.ties}` : ""}`;
}

/** Players against the 23-man limit: taxi and IR sit outside it (§2.2). */
export function activeCount(team: Roster): number {
  return team.starters.filter((player) => player !== null).length + team.bench.length;
}

export function RosterRail({
  teams,
  limits,
  selected,
  onSelect,
}: {
  teams: Roster[];
  limits: RosterLimits;
  selected: Roster;
  onSelect: (rosterId: number) => void;
}) {
  // Nobody has a record before Week 1, and twelve rows of "0–0" is furniture
  // rather than information. It appears the week the first game does.
  const played = teams.some((team) => recordOf(team) !== null);

  return (
    <aside className="rail">
      <div className="rail-card rail-card--feature">
        <p className="rail-kicker">Viewing</p>
        <p className="rail-title">{teamName(selected)}</p>
        <p className="rail-sub">
          {[
            ownerLabel(selected),
            selected.is_owner ? "Commissioner" : null,
            `${activeCount(selected)}/${limits.active} active`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>

      <div className="rail-card">
        <div className="rail-head">
          <span className="argyle-mark" aria-hidden />
          <h3>Teams</h3>
        </div>
        <div className="rail-nav" role="group" aria-label="Teams">
          {teams.map((team) => (
            <button
              type="button"
              key={team.roster_id}
              className="rail-link"
              // "true" rather than "page": this moves the panel beside it, it
              // does not navigate. The theme keys off the attribute, not its
              // value, so the selected row still gets its Carolina edge.
              aria-current={team.roster_id === selected.roster_id ? "true" : undefined}
              onClick={() => onSelect(team.roster_id)}
            >
              <span className="rail-team">
                <Avatar user={team} name={team.display_name} />
                <span className="rail-team-name" title={teamName(team)}>
                  {teamName(team)}
                </span>
              </span>
              {played && <span className="rail-record">{recordOf(team) ?? "0–0"}</span>}
            </button>
          ))}
        </div>
      </div>

      <div className="rail-card">
        <div className="rail-head">
          <span className="argyle-mark" aria-hidden />
          <h3>Roster limits</h3>
        </div>
        {/* Sleeper's own settings, not the rulebook's table: if the two ever
            disagree, the platform is what actually blocks the waiver claim. */}
        <dl className="rail-list">
          <div>
            <dt>Starters</dt>
            <dd>{limits.starters}</dd>
          </div>
          <div>
            <dt>Bench</dt>
            <dd>{limits.bench}</dd>
          </div>
          <div>
            <dt>Active</dt>
            <dd>{limits.active}</dd>
          </div>
          <div>
            <dt>Taxi</dt>
            <dd>{limits.taxi}</dd>
          </div>
          <div>
            <dt>IR</dt>
            <dd>{limits.reserve}</dd>
          </div>
        </dl>
      </div>
    </aside>
  );
}

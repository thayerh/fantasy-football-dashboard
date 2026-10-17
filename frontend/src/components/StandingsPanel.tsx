import { type League, type LeagueUser, type Roster } from "../api/client";
import { Avatar } from "./Avatar";
import { humanStatus } from "../format";

/**
 * The homepage's primary box.
 *
 * Records and points come from the roster sync, which is also what the rosters
 * page reads — one number, one source. Before the first game they are all
 * zero, and a column of 0.0 is furniture rather than information, so until
 * somebody has played the columns stay empty and the footnote says why.
 * Presenting a league-wide 0–0 as a standing would be presenting nothing as
 * data.
 *
 * Rows are ordered by member name, not by record. Sorting a standings table by
 * standing is the right thing and is not done here yet — it wants the tiebreak
 * rules from the rulebook (§4), not just a descending sort.
 */

function TeamCell({ user }: { user: LeagueUser }) {
  const handle = user.display_name ?? user.username ?? user.user_id;
  return (
    <div className="cell-team">
      <Avatar user={user} name={user.display_name ?? user.username} />
      <div className="who">
        <p className={user.team_name ? "name" : "name unset"}>
          {user.team_name ?? "Team name not set"}
        </p>
        <p className="sub">
          {handle}
          {user.is_owner && " · Commissioner"}
        </p>
      </div>
    </div>
  );
}

/** Placeholder for a figure that exists but says nothing yet. */
function Pending() {
  return (
    <span className="pending" title="Arrives with the season's first games">
      —
    </span>
  );
}

/** A team's row of figures, once there is a season to report. */
function Figures({ roster }: { roster: Roster | undefined }) {
  if (!roster) {
    return (
      <>
        <td className="num">
          <Pending />
        </td>
        <td className="num">
          <Pending />
        </td>
        <td className="num">
          <Pending />
        </td>
      </>
    );
  }
  return (
    <>
      <td className="num">
        {roster.wins}–{roster.losses}
        {roster.ties > 0 && `–${roster.ties}`}
      </td>
      <td className="num">{roster.fpts?.toFixed(1) ?? <Pending />}</td>
      <td className="num">{roster.fpts_against?.toFixed(1) ?? <Pending />}</td>
    </>
  );
}

function SkeletonRows() {
  return (
    <>
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <tr key={i}>
          <td>
            <div className="cell-team">
              <span className="skeleton skeleton--avatar skeleton--avatar-sm" />
              <div className="who">
                <p className="skeleton skeleton--line" />
                <p className="skeleton skeleton--line" />
              </div>
            </div>
          </td>
          <td className="num">
            <Pending />
          </td>
          <td className="num">
            <Pending />
          </td>
          <td className="num">
            <Pending />
          </td>
        </tr>
      ))}
    </>
  );
}

export function StandingsPanel({
  league,
  users,
  rosters,
  isPending,
  error,
}: {
  league: League | undefined;
  users: LeagueUser[] | undefined;
  rosters: Roster[] | undefined;
  isPending: boolean;
  error: Error | null;
}) {
  const meta = league ? `${league.season} · ${humanStatus(league.status)}` : "";
  // Until somebody has played, every roster reads 0–0 and 0.0, which is true
  // and worth nothing. The columns fill themselves the week that changes.
  const played = (rosters ?? []).some(
    (roster) => roster.wins + roster.losses + roster.ties > 0,
  );
  const byOwner = new Map((rosters ?? []).map((roster) => [roster.owner_id, roster]));

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="argyle-mark" aria-hidden />
        <div className="sec-head">
          <h2>Standings</h2>
          <span className="spacer" />
          <span className="meta">{meta}</span>
        </div>
      </div>

      {!isPending && !error && (
        <div className="stat-strip">
          <div className="stat">
            <span className="figure">{league?.total_rosters ?? "—"}</span>
            <span className="label">Teams</span>
          </div>
          <div className="stat">
            <span className="figure">{users?.length ?? "—"}</span>
            <span className="label">Members</span>
          </div>
          <div className="stat">
            <span className="figure">{league?.season ?? "—"}</span>
            <span className="label">Season</span>
          </div>
        </div>
      )}

      {error ? (
        <div className="state state--error">
          <h3>Couldn&rsquo;t load the league</h3>
          <p>{error.message}</p>
        </div>
      ) : !isPending && users && users.length === 0 ? (
        <div className="state">
          <h3>No members yet</h3>
          <p>
            Nothing has been ingested. Run <code>ffd sync</code> on the host, or wait for
            the next run of <code>ffd-sync.timer</code>.
          </p>
        </div>
      ) : (
        <div className="table-scroll">
          <table className="stat-table">
            <thead>
              <tr>
                <th>Team</th>
                <th className="num">Record</th>
                <th className="num">PF</th>
                <th className="num">PA</th>
              </tr>
            </thead>
            <tbody>
              {isPending || !users ? (
                <SkeletonRows />
              ) : (
                users.map((user) => (
                  <tr key={user.user_id}>
                    <td>
                      <TeamCell user={user} />
                    </td>
                    <Figures roster={played ? byOwner.get(user.user_id) : undefined} />
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* The footnote explains empty columns, so it only belongs where there
          are empty columns to explain. */}
      {!error && !played && (
        <div className="panel-foot">
          <p className="note">
            Records and points are empty because nobody has played a game yet. They fill in
            from the first week of the season
            {league?.status === "pre_draft" && ", and this league is still pre-draft"}.
          </p>
        </div>
      )}
    </section>
  );
}

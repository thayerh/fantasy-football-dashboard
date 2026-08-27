import { avatarUrl, type League, type LeagueUser } from "../api/client";
import { humanStatus } from "../format";

/**
 * The homepage's primary box.
 *
 * Records and points are NOT in the database: v1 ingests the league and its
 * members, and the Sleeper roster endpoint — which is where wins, losses and
 * points live — isn't synced yet. The columns are rendered anyway, empty, so
 * the page shows what it will be rather than hiding it; the footnote says why
 * they're empty. Inventing 0–0 would be presenting a guess as data.
 */

function Avatar({ user }: { user: LeagueUser }) {
  const url = avatarUrl(user);
  if (!url) {
    // Sleeper members without an uploaded image get their initial, not a
    // stock silhouette that makes twelve people look identical.
    const initial = (user.display_name ?? user.username ?? "?").charAt(0).toUpperCase();
    return (
      <span className="avatar avatar--sm avatar--initial" aria-hidden>
        {initial}
      </span>
    );
  }
  return <img className="avatar avatar--sm" src={url} alt="" loading="lazy" />;
}

function TeamCell({ user }: { user: LeagueUser }) {
  const handle = user.display_name ?? user.username ?? user.user_id;
  return (
    <div className="cell-team">
      <Avatar user={user} />
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

/** Placeholder for a figure the database doesn't have yet. */
function Pending() {
  return (
    <span className="pending" title="Arrives with roster sync, once the season starts">
      —
    </span>
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
  isPending,
  error,
}: {
  league: League | undefined;
  users: LeagueUser[] | undefined;
  isPending: boolean;
  error: Error | null;
}) {
  const meta = league ? `${league.season} · ${humanStatus(league.status)}` : "";

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
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* The footnote explains empty columns, so it only belongs where there
          are columns to explain. */}
      {!error && (
        <div className="panel-foot">
          <p className="note">
            Records and points are empty because the roster endpoint isn&rsquo;t synced yet
            {league?.status === "pre_draft" && " — and the league is still pre-draft, so nobody has played a game"}
            .
          </p>
        </div>
      )}
    </section>
  );
}

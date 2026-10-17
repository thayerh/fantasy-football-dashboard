import type { League, LeagueUser, Roster, Rosters } from "../api/client";
import { Avatar } from "./Avatar";
import { activeCount } from "./RosterRail";

/**
 * The league's people, as a card grid.
 *
 * This is the one page where the person is the subject rather than their team,
 * so it uses the card grid from `design/04-gameday.html` — the form the theme
 * has always reserved for people (GAMEDAY.md §8, "People / teams as cards")
 * and the first page to actually use it.
 *
 * Each card carries the two things Sleeper knows about a member (their team
 * name and their handle) and the two things about their season that are worth
 * knowing at a glance: how full their roster is, and how much of their FAAB is
 * left. Both come from the roster sync, so they cannot disagree with the
 * rosters page.
 */

/**
 * A member Sleeper no longer returns.
 *
 * Departed members are deliberately kept (see `LeagueUser`), which is what
 * makes the league's history readable — and also what would let a card quietly
 * present somebody who left as though they were still here. Every present
 * member is written in one transaction with one timestamp, so anyone whose
 * `last_seen_at` trails the newest one was not in the last sync. The minute of
 * slack is for the day that assumption stops being exactly true.
 */
const SLACK_MS = 60_000;

function departedAfter(users: LeagueUser[]): number {
  const newest = Math.max(...users.map((user) => new Date(user.last_seen_at).getTime()));
  return newest - SLACK_MS;
}

/**
 * The grid in the order a reader scans it: by the line each card leads with.
 *
 * `/api/users` sorts by handle, which is the card's *second* line — so the
 * grid would read as unsorted to anyone who has named their team. Sorted here
 * rather than in the API because the standings table sorts its own rows the
 * same way and there is no reason yet to make one endpoint serve two orders.
 * Case-insensitively, since SQLite's ORDER BY is not.
 */
function byCardTitle(users: LeagueUser[]): LeagueUser[] {
  const title = (user: LeagueUser) =>
    user.team_name ?? user.display_name ?? user.username ?? user.user_id;
  return [...users].sort((a, b) =>
    title(a).localeCompare(title(b), undefined, { sensitivity: "base" }),
  );
}

/** What a member's card says about their season, or nothing if there is none
 *  to say — a co-owner, or a member whose team has yet to be assigned. */
function facts(roster: Roster | undefined, budget: number | null, limit: number): string[] {
  if (!roster) return [];

  const line: string[] = [];
  if (roster.wins + roster.losses + roster.ties > 0) {
    line.push(`${roster.wins}–${roster.losses}${roster.ties ? `–${roster.ties}` : ""}`);
  }
  line.push(`${activeCount(roster)} of ${limit} active`);
  // Remaining, not spent: what you have left is what you can bid (§7.1).
  if (budget !== null && roster.waiver_budget_used !== null) {
    line.push(`$${budget - roster.waiver_budget_used} FAAB`);
  }
  return line;
}

function MemberCard({
  user,
  roster,
  budget,
  limit,
  departed,
}: {
  user: LeagueUser;
  roster: Roster | undefined;
  budget: number | null;
  limit: number;
  departed: boolean;
}) {
  const handle = user.display_name ?? user.username ?? user.user_id;
  const line = facts(roster, budget, limit);

  return (
    <article className="card">
      <Avatar user={user} name={handle} size="lg" />
      <div className="body">
        <p className={user.team_name ? "team" : "team unset"}>
          {user.team_name ?? "Team name not set"}
        </p>
        <p className="handle">{handle}</p>
        {line.length > 0 && (
          <p className="card-facts" title={budget !== null ? `FAAB budget $${budget}` : undefined}>
            {line.join(" · ")}
          </p>
        )}
        {user.is_owner && <span className="tag">Commissioner</span>}
        {/* Marked rather than hidden: they are part of the league's history,
            and the card would otherwise read as current. */}
        {departed && <span className="tag tag--quiet">Left the league</span>}
      </div>
    </article>
  );
}

function SkeletonCards() {
  return (
    <>
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <article className="card" key={i}>
          <span className="skeleton skeleton--avatar" />
          <div className="body">
            <p className="skeleton skeleton--line" />
            <p className="skeleton skeleton--line" />
          </div>
        </article>
      ))}
    </>
  );
}

export function MembersPanel({
  league,
  users,
  rosters,
  isPending,
  error,
}: {
  league: League | undefined;
  users: LeagueUser[] | undefined;
  rosters: Rosters | undefined;
  isPending: boolean;
  error: Error | null;
}) {
  const byOwner = new Map((rosters?.teams ?? []).map((team) => [team.owner_id, team]));
  const cutoff = users && users.length > 0 ? departedAfter(users) : 0;

  return (
    <section className="panel">
      <div className="panel-head">
        <span className="argyle-mark" aria-hidden />
        <div className="sec-head">
          <h2>{league?.name ?? "The League"}</h2>
          <span className="spacer" />
          <span className="meta">
            {users ? `${users.length} member${users.length === 1 ? "" : "s"}` : ""}
          </span>
        </div>
      </div>

      {error ? (
        <div className="state state--error">
          <h3>Couldn&rsquo;t load the members</h3>
          <p>{error.message}</p>
        </div>
      ) : !isPending && !users?.length ? (
        // Covers both an empty league and a 503: the database is fine, it is
        // simply empty, and the command that fills it is the useful thing to
        // say rather than a red box.
        <div className="state">
          <h3>No members yet</h3>
          <p>
            Nothing has been ingested. Run <code>ffd sync</code> on the host, or wait for
            the next run of <code>ffd-sync.timer</code>.
          </p>
        </div>
      ) : (
        <div className="grid">
          {isPending || !users ? (
            <SkeletonCards />
          ) : (
            byCardTitle(users).map((user) => (
              <MemberCard
                key={user.user_id}
                user={user}
                roster={byOwner.get(user.user_id)}
                budget={rosters?.waiver_budget ?? null}
                limit={rosters?.limits.active ?? 0}
                departed={new Date(user.last_seen_at).getTime() < cutoff}
              />
            ))
          )}
        </div>
      )}

      {!error && (
        <div className="panel-foot">
          <p className="note">
            Team names are whatever each owner has set in Sleeper, and a member who never
            set one shows their handle instead. Members who leave the league keep their
            card, marked, so the league&rsquo;s history stays readable.
          </p>
        </div>
      )}
    </section>
  );
}

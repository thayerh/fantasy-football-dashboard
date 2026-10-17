import { ApiError } from "../api/client";
import { FieldHead } from "../components/FieldHead";
import { Freshness } from "../components/Freshness";
import { MembersPanel } from "../components/MembersPanel";
import { useHealth, useLeague, useRosters, useUsers } from "../queries";

export const MEMBERS = "Members";

/**
 * Who is in the league.
 *
 * No rail: the people *are* the page, which is how `design/04-gameday.html`
 * has it, and it keeps the narrower measure that mockup uses. The season's
 * context belongs on the standings page, where there is a second column to
 * put it in.
 */
export function MembersPage() {
  const league = useLeague();
  const users = useUsers();
  const health = useHealth();
  // Records and FAAB. The same cache entry the rosters page holds, so this
  // costs no extra request once you have been there — and the two pages cannot
  // disagree about how full a roster is.
  const rosters = useRosters();

  const notIngested =
    users.isError && users.error instanceof ApiError && users.error.status === 503;

  return (
    <>
      <FieldHead
        kicker={`Sleeper${league.data?.season ? ` · ${league.data.season} Season` : ""}`}
        title={MEMBERS}
        facts={
          users.isPending ? (
            <span className="skeleton skeleton--sub" />
          ) : users.data ? (
            <>
              <b>{users.data.length}</b> members · <b>{league.data?.total_rosters ?? "—"}</b>{" "}
              teams
            </>
          ) : (
            "Member details unavailable"
          )
        }
        aside={<Freshness health={health.data} />}
      />

      <main>
        <MembersPanel
          league={league.data}
          users={users.data}
          rosters={rosters.data}
          isPending={users.isPending}
          // A 503 is the database being empty rather than broken, and the panel
          // says which command fills it — so it is not an error box.
          error={users.isError && !notIngested ? (users.error as Error) : null}
        />
      </main>
    </>
  );
}

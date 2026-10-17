import { ApiError } from "../api/client";
import { FieldHead } from "../components/FieldHead";
import { Freshness } from "../components/Freshness";
import { Rail } from "../components/Rail";
import { StandingsPanel } from "../components/StandingsPanel";
import { humanStatus } from "../format";
import { useDraft, useHealth, useLeague, useRosters, useUsers } from "../queries";

export const STANDINGS = "Standings";

export function StandingsPage() {
  const health = useHealth();
  const league = useLeague();
  const users = useUsers();
  // The rail's "Up next" card wants the draft's schedule. Same query key as the
  // draft page, so React Query serves it from cache when you navigate.
  const draft = useDraft();
  // Where records and points live. The same cache entry the rosters page uses,
  // so the two pages cannot disagree about a team's record.
  const rosters = useRosters();

  // The database is fine, it is simply empty. Telling someone to run
  // `ffd sync` is more useful than a red box.
  const notIngested =
    league.isError && league.error instanceof ApiError && league.error.status === 503;

  return (
    <>
      <FieldHead
        kicker={`Sleeper${league.data?.season ? ` · ${league.data.season} Season` : ""}`}
        title={
          league.isPending ? (
            <span className="skeleton skeleton--title" />
          ) : (
            (league.data?.name ?? "League Dashboard")
          )
        }
        facts={
          league.isPending ? (
            <span className="skeleton skeleton--sub" />
          ) : league.data ? (
            <>
              <b>{league.data.total_rosters ?? "—"}</b> teams ·{" "}
              <b>{humanStatus(league.data.status)}</b>
            </>
          ) : (
            "League details unavailable"
          )
        }
        aside={<Freshness health={health.data} />}
      />

      {notIngested && (
        <section className="panel">
          <div className="state">
            <h3>Nothing ingested yet</h3>
            <p>{(league.error as ApiError).detail}</p>
          </div>
        </section>
      )}

      <div className="layout">
        <Rail
          league={league.data}
          draft={draft.data}
          health={health.data}
          memberCount={users.data?.length}
          current={STANDINGS}
        />
        <main>
          <StandingsPanel
            league={league.data}
            users={users.data}
            rosters={rosters.data?.teams}
            isPending={users.isPending}
            error={users.isError ? (users.error as Error) : null}
          />
        </main>
      </div>
    </>
  );
}

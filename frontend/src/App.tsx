import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "./api/client";
import { Freshness } from "./components/Freshness";
import { Masthead } from "./components/Masthead";
import { Rail } from "./components/Rail";
import { StandingsPanel } from "./components/StandingsPanel";
import { humanStatus } from "./format";

/**
 * How often the browser re-asks the API.
 *
 * Deliberately independent of the ingest cadence (ffd-sync.timer, every 5m):
 * this is a UI responsiveness knob, and reading from local SQLite is cheap.
 * The two are tuned separately and need not match.
 */
const REFRESH_MS = 60_000;

/** The page this build serves. Rail and masthead mark it aria-current. */
const CURRENT = "Standings";

export default function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: REFRESH_MS,
  });

  const league = useQuery({
    queryKey: ["league"],
    queryFn: api.league,
    refetchInterval: REFRESH_MS,
    // 503 means "not ingested yet" — retrying in a tight loop won't help.
    retry: (count, error) => !(error instanceof ApiError && error.status === 503) && count < 2,
  });

  const users = useQuery({
    queryKey: ["users"],
    queryFn: api.users,
    refetchInterval: REFRESH_MS,
  });

  const name = league.data?.name ?? null;
  useEffect(() => {
    document.title = name ? `${name} — Standings` : "League Dashboard";
  }, [name]);

  // The one error worth its own treatment: the database is fine, it is simply
  // empty. Telling someone to run `ffd sync` is more useful than a red box.
  const notIngested =
    league.isError && league.error instanceof ApiError && league.error.status === 503;

  return (
    <>
      <Masthead current={CURRENT} />

      <div className="field argyle-veil">
        <div className="wrap wrap--wide">
          <div className="field-head">
            <div>
              <p className="kicker">
                Sleeper{league.data?.season ? ` · ${league.data.season} Season` : ""}
              </p>
              {league.isPending ? (
                <h1>
                  <span className="skeleton skeleton--title" />
                </h1>
              ) : (
                <h1>{name ?? "League Dashboard"}</h1>
              )}
              <p className="subline">
                {league.isPending ? (
                  <span className="skeleton skeleton--sub" />
                ) : league.data ? (
                  <>
                    <b>{league.data.total_rosters ?? "—"}</b> teams ·{" "}
                    <b>{humanStatus(league.data.status)}</b>
                  </>
                ) : (
                  "League details unavailable"
                )}
              </p>
            </div>
            <Freshness health={health.data} />
          </div>

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
              health={health.data}
              memberCount={users.data?.length}
              current={CURRENT}
            />
            <main>
              <StandingsPanel
                league={league.data}
                users={users.data}
                isPending={users.isPending}
                error={users.isError ? (users.error as Error) : null}
              />
            </main>
          </div>
        </div>
      </div>
    </>
  );
}

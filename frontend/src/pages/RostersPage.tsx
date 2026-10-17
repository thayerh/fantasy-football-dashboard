import { useState } from "react";

import { ApiError } from "../api/client";
import { FieldHead } from "../components/FieldHead";
import { Freshness } from "../components/Freshness";
import { PicksPanel } from "../components/PicksPanel";
import { RosterPanel } from "../components/RosterPanel";
import { RosterRail } from "../components/RosterRail";
import { useHealth, useRosters } from "../queries";

export const ROSTERS = "Rosters";

/** Remembered across reloads: you look at your own team far more than anyone
 *  else's, and re-picking it every visit is the kind of small tax that stops
 *  people opening a page at all. */
const TEAM_KEY = "ffd.roster-team";

function storedTeam(): number | null {
  try {
    const raw = window.localStorage.getItem(TEAM_KEY);
    const id = raw === null ? NaN : Number(raw);
    return Number.isInteger(id) ? id : null;
  } catch {
    // Private windows and blocked site data both throw on access.
    return null;
  }
}

function rememberTeam(rosterId: number): void {
  try {
    window.localStorage.setItem(TEAM_KEY, String(rosterId));
  } catch {
    /* not remembering it is a smaller problem than not selecting it */
  }
}

export function RostersPage() {
  const rosters = useRosters();
  const health = useHealth();
  const [rosterId, setRosterId] = useState<number | null>(storedTeam);

  // The database is fine, it is simply empty — say which command fills it.
  const notIngested =
    rosters.isError && rosters.error instanceof ApiError && rosters.error.status === 503;

  const teams = rosters.data?.teams ?? [];
  // A remembered team that no longer exists — a league someone left, or a
  // different league in the same browser — falls back to the first one rather
  // than to an empty page.
  const selected = teams.find((team) => team.roster_id === rosterId) ?? teams[0];

  const choose = (id: number) => {
    setRosterId(id);
    rememberTeam(id);
  };

  return (
    <>
      <FieldHead
        kicker={`Sleeper${rosters.data?.season ? ` · ${rosters.data.season} Season` : ""}`}
        title={ROSTERS}
        facts={
          rosters.isPending ? (
            <span className="skeleton skeleton--sub" />
          ) : rosters.data ? (
            <>
              <b>{teams.length}</b> teams · <b>{rosters.data.limits.active}</b>-man active
              rosters · <b>{rosters.data.limits.starters}</b> starters
            </>
          ) : (
            "Roster details unavailable"
          )
        }
        aside={<Freshness health={health.data} />}
      />

      {notIngested || rosters.isError ? (
        <section className="panel">
          <div className={notIngested ? "state" : "state state--error"}>
            <h3>{notIngested ? "Nothing ingested yet" : "Couldn’t load the rosters"}</h3>
            <p>{(rosters.error as Error).message}</p>
          </div>
        </section>
      ) : rosters.isPending || !rosters.data || !selected ? (
        <section className="panel">
          <div className="state">
            <h3>{rosters.isPending ? "Loading the rosters" : "No teams yet"}</h3>
            <p>
              {rosters.isPending
                ? "Reading the last sync from the database."
                : "The league has no teams in it. They arrive with the next ffd sync."}
            </p>
          </div>
        </section>
      ) : (
        <div className="layout">
          <RosterRail
            teams={teams}
            limits={rosters.data.limits}
            selected={selected}
            onSelect={choose}
          />
          <main>
            <RosterPanel
              team={selected}
              lineup={rosters.data.lineup}
              limits={rosters.data.limits}
            />
            {/* Below the players, because a lineup is what a team is now and
                picks are what it will be. */}
            <PicksPanel
              team={selected}
              teams={teams}
              seasons={rosters.data.pick_seasons}
              rounds={rosters.data.pick_rounds}
            />
          </main>
        </div>
      )}
    </>
  );
}

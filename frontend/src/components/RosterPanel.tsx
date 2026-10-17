import { CONSENSUS_BOARDS, type Roster, type RosterLimits, type RosterPlayer } from "../api/client";
import { positionClass } from "./DraftBoard";
import { activeCount, ownerLabel, recordOf, teamName } from "./RosterRail";

/**
 * One team, in two panels: the ten slots it starts, then everything behind
 * them.
 *
 * The split is the league's own (rulebook §2.1 and §2.2) and it is the split
 * that matters: a lineup is a claim about this week, while the bench, the taxi
 * squad and IR are claims about the next three years. Reading them in one
 * undifferentiated list of twenty-four names is what Sleeper's own roster page
 * gets wrong.
 *
 * Columns are the draft table's, minus four of the five boards. On a roster
 * the question is "what do I have", not "where do the boards disagree", so one
 * market number is enough — and it is the same aggregate the board shows, so
 * the two pages cannot tell different stories about the same player.
 */

/** Sleeper's slot names, shortened to fit a column. */
const SLOT_LABELS: Record<string, string> = {
  SUPER_FLEX: "SF",
  BN: "BN",
  IR: "IR",
  TAXI: "TAXI",
};

/** What a slot is called, and what it means if the short form is cryptic. */
const SLOT_TITLES: Record<string, string> = {
  SUPER_FLEX: "Superflex — QB, RB, WR or TE",
  FLEX: "Flex — RB, WR or TE",
  BN: "Bench",
  IR: "Injured reserve",
  TAXI: "Taxi squad — rookies only",
};

function Slot({ slot }: { slot: string }) {
  return (
    <span className="slot-label" title={SLOT_TITLES[slot]}>
      {SLOT_LABELS[slot] ?? slot}
    </span>
  );
}

/** An em dash where a number would be, for a figure nothing has. */
function Missing() {
  return <span className="pending">—</span>;
}

function PlayerRow({
  slot,
  player,
  vacant = "Empty",
}: {
  slot: string;
  player: RosterPlayer | null;
  vacant?: string;
}) {
  if (!player) {
    // A spot with nobody in it, rendered as a row rather than skipped: an
    // unfilled slot is the thing worth seeing, and dropping it would make a
    // nine-man lineup look legal and a full bench look identical to one with
    // room on it.
    //
    // Two different words for two different facts. A hole in the lineup is a
    // slot that scores zero this week (§2.1); a hole behind it is capacity —
    // room to make a waiver claim without dropping anybody (§2.2).
    return (
      <tr>
        <td>
          <Slot slot={slot} />
        </td>
        <td>
          <span className="unset">{vacant}</span>
        </td>
        <td colSpan={5} />
      </tr>
    );
  }

  return (
    <tr>
      <td>
        <Slot slot={slot} />
      </td>
      <td>
        <span className="player-name">{player.full_name ?? player.player_id}</span>
        {/* Same two marks as the draft table, for the same reasons: a rookie
            is a different asset from a veteran the boards rank alongside him,
            and an injury designation is what puts a player on IR. */}
        {player.years_exp === 0 && (
          <span className="rookie" role="img" aria-label="Rookie" title="Rookie">
            ★
          </span>
        )}
        {player.injury_status && (
          <span className="tag tag--inline injury">{player.injury_status}</span>
        )}
      </td>
      <td>
        <span className={`pos-chip${positionClass(player.position)}`}>
          {player.position ?? "—"}
        </span>
      </td>
      <td className="num">{player.team ?? "FA"}</td>
      <td className="num">{player.age ?? <Missing />}</td>
      <td className="num">
        {player.consensus !== null ? (
          <span
            title={`Mean of ${player.consensus_sources} of ${CONSENSUS_BOARDS.length} dynasty boards`}
          >
            {player.consensus}
            {player.consensus_sources < CONSENSUS_BOARDS.length && (
              <span className="of-sources">/{player.consensus_sources}</span>
            )}
          </span>
        ) : (
          <Missing />
        )}
      </td>
      <td className="num">
        {player.vegas_points !== null ? (
          <span title={`${player.vegas_points} projected points`}>
            {Math.round(player.vegas_points)}
          </span>
        ) : (
          <Missing />
        )}
      </td>
    </tr>
  );
}

function Columns() {
  return (
    <thead>
      <tr>
        <th className="col-slot">Slot</th>
        <th>Player</th>
        <th className="col-pos">Pos</th>
        <th className="num col-team">NFL</th>
        <th className="num col-age">Age</th>
        <th className="num col-agg" title="Mean rank across the three dynasty boards">
          AGG
        </th>
        <th className="num col-proj" title="Vegas-derived projected points for the season">
          Proj
        </th>
      </tr>
    </thead>
  );
}

/**
 * The three groups behind the lineup, each with the rule that sizes it.
 *
 * The sizes are the league's own — thirteen bench, three taxi, three IR, and
 * a 23-man active roster that the first of those completes
 * (`rulebook/02-rosters-and-lineups.md` §2.2). They arrive from Sleeper's
 * settings rather than as constants here, because the platform is what
 * actually blocks the illegal claim; as of this writing the two agree exactly.
 */
function depthGroups(team: Roster, limits: RosterLimits) {
  return [
    { slot: "BN", label: "bench", players: team.bench, limit: limits.bench },
    { slot: "TAXI", label: "taxi", players: team.taxi, limit: limits.taxi },
    { slot: "IR", label: "IR", players: team.reserve, limit: limits.reserve },
  ];
}

/** Spots nobody is in. Never negative: a roster may legally run over the limit
 *  between the rookie draft and Week 1 kickoff (§2.2), and an over-full group
 *  has no room rather than negative room. */
function openSpots(filled: number, limit: number): number {
  return Math.max(0, limit - filled);
}

/** The mean age of everyone under contract, which in dynasty is half the
 *  scouting report. Taxi and IR are included: they are assets too. */
function averageAge(team: Roster): string {
  const ages = [...team.starters, ...team.bench, ...team.taxi, ...team.reserve]
    .filter((player): player is RosterPlayer => player !== null)
    .map((player) => player.age)
    .filter((age): age is number => age !== null);
  if (!ages.length) return "—";
  return (ages.reduce((sum, age) => sum + age, 0) / ages.length).toFixed(1);
}

export function RosterPanel({ team, lineup, limits }: {
  team: Roster;
  lineup: string[];
  limits: RosterLimits;
}) {
  const active = activeCount(team);
  const empty = active === 0 && team.taxi.length === 0 && team.reserve.length === 0;
  const groups = depthGroups(team, limits);

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <span className="argyle-mark" aria-hidden />
          <div className="sec-head">
            <h2>{teamName(team)}</h2>
            <span className="spacer" />
            <span className="meta">
              {[ownerLabel(team), recordOf(team)].filter(Boolean).join(" · ")}
            </span>
          </div>
        </div>

        <div className="stat-strip">
          <div className="stat">
            <span className="figure">{active}</span>
            <span className="label">of {limits.active} active</span>
          </div>
          <div className="stat">
            <span className="figure">{team.taxi.length}</span>
            <span className="label">of {limits.taxi} taxi</span>
          </div>
          <div className="stat">
            <span className="figure">{team.reserve.length}</span>
            <span className="label">of {limits.reserve} IR</span>
          </div>
          <div className="stat">
            <span className="figure">{averageAge(team)}</span>
            <span className="label">Average age</span>
          </div>
        </div>

        {empty ? (
          <div className="state">
            <h3>No players yet</h3>
            <p>
              This team controls nobody. Rosters fill in as the draft runs, and from
              waivers and trades after it.
            </p>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="stat-table roster-table">
              <Columns />
              <tbody>
                {lineup.map((slot, index) => (
                  <PlayerRow
                    // Slot labels repeat — three WRs, two FLEXes — so the
                    // position in the lineup is the only stable key.
                    key={`${slot}-${index}`}
                    slot={slot}
                    player={team.starters[index] ?? null}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {!empty && (
        <section className="panel">
          <div className="panel-head">
            <span className="argyle-mark" aria-hidden />
            <div className="sec-head">
              <h2>Depth</h2>
              <span className="spacer" />
              <span className="meta">
                {groups
                  .map(({ label, players, limit }) => `${players.length} of ${limit} ${label}`)
                  .join(" · ")}
              </span>
            </div>
          </div>

          <div className="table-scroll">
            <table className="stat-table roster-table">
              <Columns />
              {/* One tbody per group: the slot column says which is which, and
                  the rule between them is what makes that readable at a
                  glance. Each is ordered best-first by the aggregate, which is
                  the order nobody can get from Sleeper, and each runs to its
                  full size — a taxi squad with one rookie on it has two spots
                  free, and that is a thing you act on. */}
              {groups.map(({ slot, players, limit }) => (
                <tbody key={slot}>
                  {players.map((player) => (
                    <PlayerRow key={player.player_id} slot={slot} player={player} />
                  ))}
                  {Array.from({ length: openSpots(players.length, limit) }, (_, index) => (
                    <PlayerRow
                      key={`open-${index}`}
                      slot={slot}
                      player={null}
                      vacant="Open"
                    />
                  ))}
                </tbody>
              ))}
            </table>
          </div>

          <div className="panel-foot">
            <p className="note">
              Open rows are spots nobody is in: {limits.bench} bench behind{" "}
              {limits.starters} starters make the {limits.active}-man active roster, and the{" "}
              {limits.taxi} taxi and {limits.reserve} IR spots sit outside it (rulebook §2.2).
              AGG is the mean rank across the three dynasty boards; Proj is Vegas-derived
              points for the season, and stops before the deep end of a bench.
            </p>
          </div>
        </section>
      )}
    </>
  );
}

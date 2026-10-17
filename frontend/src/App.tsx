import { ErrorBoundary } from "./components/ErrorBoundary";
import { Masthead } from "./components/Masthead";
import { DRAFT, DraftPage } from "./pages/DraftPage";
import { MEMBERS, MembersPage } from "./pages/MembersPage";
import { ROSTERS, RostersPage } from "./pages/RostersPage";
import { STANDINGS, StandingsPage } from "./pages/StandingsPage";
import { usePath } from "./router";

/**
 * The shell every page shares: the athletics masthead, then the blue field.
 * Pages own their own header and their own arrangement below it — see
 * design/GAMEDAY.md for the structure they follow.
 */

/** Which page a path is, and how wide it wants to be.
 *
 * Three measures, each earned: the board wants every pixel it can get, the
 * pages with a second column take the wide one, and the members grid keeps the
 * plain 66rem measure its mockup uses. Still a chain of prefixes rather than a
 * route table — see router.tsx for where that stops being enough. */
function pageFor(path: string) {
  if (path.startsWith("/draft")) {
    return { current: DRAFT, measure: "wrap wrap--board", body: <DraftPage /> };
  }
  if (path.startsWith("/rosters")) {
    return { current: ROSTERS, measure: "wrap wrap--wide", body: <RostersPage /> };
  }
  if (path.startsWith("/members")) {
    return { current: MEMBERS, measure: "wrap", body: <MembersPage /> };
  }
  return { current: STANDINGS, measure: "wrap wrap--wide", body: <StandingsPage /> };
}

export default function App() {
  const path = usePath();
  const page = pageFor(path);

  return (
    <>
      <Masthead current={page.current} />
      <div className="field argyle-veil">
        <div className={page.measure}>
          {/* Keyed by route: a boundary that has caught stays caught, so
              without this a single bad page would hold the whole app in its
              error state even after you navigated away from it. The masthead
              is outside, so navigation always survives. */}
          <ErrorBoundary key={path}>{page.body}</ErrorBoundary>
        </div>
      </div>
    </>
  );
}

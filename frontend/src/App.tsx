import { Masthead } from "./components/Masthead";
import { DRAFT, DraftPage } from "./pages/DraftPage";
import { STANDINGS, StandingsPage } from "./pages/StandingsPage";
import { usePath } from "./router";

/**
 * The shell every page shares: the athletics masthead, then the blue field.
 * Pages own their own header and their own arrangement below it — see
 * design/GAMEDAY.md for the structure they follow.
 */
export default function App() {
  const path = usePath();
  const onDraft = path.startsWith("/draft");

  return (
    <>
      <Masthead current={onDraft ? DRAFT : STANDINGS} />
      <div className="field argyle-veil">
        {/* The board wants every pixel it can get; the standings page keeps the
            narrower measure it shares with the design mockups. */}
        <div className={onDraft ? "wrap wrap--board" : "wrap wrap--wide"}>
          {onDraft ? <DraftPage /> : <StandingsPage />}
        </div>
      </div>
    </>
  );
}

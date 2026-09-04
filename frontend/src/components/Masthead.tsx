import helmet from "../../../design/helmet.png";
import { ROUTES, Link } from "../router";

/**
 * The two-tier athletics chrome. Identical on every route by design — see
 * design/GAMEDAY.md §2: the masthead is not a per-page decision.
 *
 * Nothing here is a real destination yet. Links that don't go anywhere are
 * rendered as plain text rather than as anchors that lie about being
 * clickable; the one exception is the current page, which is marked
 * aria-current so the underline means something.
 */

const UTILITY = ["League", "Trades", "Keepers", "History"];
const SECTIONS = ["Standings", "Draft", "Members", "Rosters"];

/** A destination if we have built it, otherwise plain text. */
function NavItem({ label, current }: { label: string; current: string }) {
  const href = ROUTES[label];
  if (!href) return <span>{label}</span>;
  return (
    <Link href={href} aria-current={label === current ? "page" : undefined}>
      {label}
    </Link>
  );
}

export function Masthead({ current }: { current: string }) {
  return (
    <header className="masthead">
      <div className="masthead-utility">
        <div className="chevron-plate">
          {/* Decorative: the league is named by the <h1> directly below, so
              announcing it here too would just be a duplicate. */}
          <img className="plate-logo" src={helmet} alt="" width={153} height={120} />
        </div>
        <nav aria-label="Secondary">
          {UTILITY.map((item) => (
            <NavItem key={item} label={item} current={current} />
          ))}
        </nav>
      </div>
      <div className="masthead-section">
        <span className="wordmark">Fantasy</span>
        <nav aria-label="Primary">
          {SECTIONS.map((item) => (
            <NavItem key={item} label={item} current={current} />
          ))}
        </nav>
      </div>
    </header>
  );
}

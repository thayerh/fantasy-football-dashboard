import type { Draft, Health, League } from "../api/client";
import { humanStatus, localDateTime, relativeAge } from "../format";
import { ROUTES, Link } from "../router";

/**
 * The left rail — Direction 6's structure, kept because it is what lets new
 * sections arrive as a rail card instead of another block stacked down the
 * middle of the page.
 *
 * The categories below are placeholders for routes that don't exist yet. They
 * are rendered as text with a "soon" tag rather than as anchors, because a
 * link that goes nowhere is worse than an obvious gap.
 */

const GROUPS: { title: string; items: string[] }[] = [
  { title: "League", items: ["Standings", "Members", "Rosters", "Draft"] },
  { title: "Season", items: ["Matchups", "Transactions", "Waivers", "History"] },
];

/** What the league is waiting on. The draft knows its own schedule, so prefer
 *  it; the league's status is the fallback before a draft has been ingested. */
function upNext(
  league: League | undefined,
  draft: Draft | undefined,
): { title: string; sub: string } {
  if (draft?.status === "drafting") return { title: "Draft", sub: "On the clock" };
  if (draft && draft.status !== "complete") {
    return {
      title: "Draft",
      sub: draft.start_time
        ? localDateTime(draft.start_time)
        : `Not yet scheduled · ${draft.teams ?? "—"} teams entered`,
    };
  }

  switch (league?.status) {
    case "in_season":
      return { title: "Season", sub: "Underway" };
    case "complete":
      return { title: "Offseason", sub: "Season complete" };
    default:
      return { title: "Season", sub: "Draft complete" };
  }
}

export function Rail({
  league,
  draft,
  health,
  memberCount,
  current,
}: {
  league: League | undefined;
  draft: Draft | undefined;
  health: Health | undefined;
  memberCount: number | undefined;
  current: string;
}) {
  const next = upNext(league, draft);

  return (
    <aside className="rail">
      <div className="rail-card rail-card--feature">
        <p className="rail-kicker">Up next</p>
        <p className="rail-title">{next.title}</p>
        <p className="rail-sub">{next.sub}</p>
      </div>

      {GROUPS.map((group) => (
        <div className="rail-card" key={group.title}>
          <div className="rail-head">
            <span className="argyle-mark" aria-hidden />
            <h3>{group.title}</h3>
          </div>
          <nav className="rail-nav" aria-label={group.title}>
            {group.items.map((item) =>
              ROUTES[item] ? (
                <Link
                  className="rail-link"
                  href={ROUTES[item]}
                  aria-current={item === current ? "page" : undefined}
                  key={item}
                >
                  {item}
                </Link>
              ) : (
                <span className="rail-link is-soon" key={item}>
                  {item}
                  <span className="tag tag--quiet tag--inline">Soon</span>
                </span>
              ),
            )}
          </nav>
        </div>
      ))}

      <div className="rail-card">
        <div className="rail-head">
          <span className="argyle-mark" aria-hidden />
          <h3>At a glance</h3>
        </div>
        <dl className="rail-list">
          <div>
            <dt>Season</dt>
            <dd>{league?.season ?? "—"}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{humanStatus(league?.status)}</dd>
          </div>
          <div>
            <dt>Teams</dt>
            <dd>{league?.total_rosters ?? "—"}</dd>
          </div>
          <div>
            <dt>Members</dt>
            <dd>{memberCount ?? "—"}</dd>
          </div>
          <div>
            <dt>Last sync</dt>
            <dd>{relativeAge(health?.last_sync_at ?? null)}</dd>
          </div>
        </dl>
      </div>
    </aside>
  );
}

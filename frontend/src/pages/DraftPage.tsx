import { useEffect, useState } from "react";

import { ApiError, type Draft } from "../api/client";
import { AvailablePlayers } from "../components/AvailablePlayers";
import { DraftBoard } from "../components/DraftBoard";
import { FieldHead } from "../components/FieldHead";
import { liveAge, localDateTime } from "../format";
import { useDraft } from "../queries";

export const DRAFT = "Draft";

/** The legend, driven by the same tokens the cells use. */
const POSITIONS: [string, string][] = [
  ["QB", "k-qb"],
  ["RB", "k-rb"],
  ["WR", "k-wr"],
  ["TE", "k-te"],
  ["K", "k-k"],
  ["DEF", "k-def"],
];

/** Re-render on a clock so "18s ago" counts up between the 20s refetches. */
function useTick(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

/** What the board is doing, in one line, from Sleeper's own status. */
function statusLine(draft: Draft, now: number): string {
  switch (draft.status) {
    case "drafting":
      return draft.last_picked_at
        ? `On the clock · last pick ${liveAge(draft.last_picked_at, now)}`
        : "On the clock";
    case "paused":
      return "Paused";
    case "complete":
      return "Complete";
    default:
      return `Starts ${localDateTime(draft.start_time)}`;
  }
}

export function DraftPage() {
  const draft = useDraft();
  const now = useTick();

  const notIngested =
    draft.isError && draft.error instanceof ApiError && draft.error.status === 503;
  const made = draft.data?.picks.length ?? 0;
  const total = (draft.data?.rounds ?? 0) * (draft.data?.teams ?? 0);
  const live = draft.data?.status === "drafting";

  return (
    <>
      <FieldHead
        kicker={`Sleeper${draft.data?.season ? ` · ${draft.data.season} Draft` : ""}`}
        title={DRAFT}
        facts={
          draft.isPending ? (
            <span className="skeleton skeleton--sub" />
          ) : draft.data ? (
            <>
              <b>{draft.data.teams ?? "—"}</b> teams · <b>{draft.data.rounds ?? "—"}</b> rounds ·{" "}
              {draft.data.type ?? "unknown format"}
            </>
          ) : (
            "Draft details unavailable"
          )
        }
        aside={
          draft.data && (
            // Not the sync_run-based <Freshness>: `ffd sync-draft` records no
            // sync_run row, so the board's own fetched_at is the signal.
            <span className={live ? "freshness" : "freshness is-quiet"}>
              <span className="dot" aria-hidden />
              Board updated {liveAge(draft.data.fetched_at, now)}
            </span>
          )
        }
      />

      {/* No rail here. The board is twelve columns wide and is the only thing
          on the page worth looking at; the rail's navigation is duplicated in
          the masthead anyway. */}
      <main>
        <section className="panel">
          <div className="panel-head">
            <span className="argyle-mark" aria-hidden />
            <div className="sec-head">
              <h2>Board</h2>
              <span className="spacer" />
              <span className="meta">
                {draft.data ? statusLine(draft.data, now) : ""}
              </span>
            </div>
          </div>

          {draft.data && (
            <div className="stat-strip">
              <div className="stat">
                <span className="figure">{made}</span>
                <span className="label">Picks made</span>
              </div>
              <div className="stat">
                <span className="figure">{total || "—"}</span>
                <span className="label">Total picks</span>
              </div>
              <div className="stat">
                <span className="figure">
                  {draft.data.picks.filter((pick) => pick.is_keeper).length}
                </span>
                <span className="label">Keepers</span>
              </div>
            </div>
          )}

          {notIngested ? (
            <div className="state">
              <h3>No draft yet</h3>
              <p>{(draft.error as ApiError).detail}</p>
            </div>
          ) : draft.isError ? (
            <div className="state state--error">
              <h3>Couldn&rsquo;t load the board</h3>
              <p>{(draft.error as Error).message}</p>
            </div>
          ) : draft.isPending ? (
            <div className="state">
              <h3>Loading the board</h3>
              <p>Reading the last sync from the database.</p>
            </div>
          ) : (
            <DraftBoard draft={draft.data} />
          )}

          <div className="panel-foot">
          <p className="pos-key" aria-hidden>
            {POSITIONS.map(([label, swatch]) => (
              <span key={label}>
                <i className={swatch} />
                {label}
              </span>
            ))}
          </p>
          <p className="note">
            {live
              ? "Refreshing every 20 seconds while the draft is live."
              : "Keepers are marked with a navy edge; the rest fills in on draft day."}
          </p>
          </div>
        </section>

        <AvailablePlayers />
      </main>
    </>
  );
}

import type { Health } from "../api/client";
import { localTimestamp, relativeAge } from "../format";

/**
 * Data age, in the header where the fresh timestamp would otherwise sit.
 *
 * A failed ingest timer is invisible without this: the page would go on
 * serving old numbers as though they were current. GAMEDAY.md §6 — stale data
 * is never presented as current.
 */
export function Freshness({ health }: { health: Health | undefined }) {
  if (!health) return null;

  const { stale, last_sync_at, last_sync_status } = health;

  return (
    <span
      className={stale ? "freshness is-stale" : "freshness"}
      title={`Last successful sync: ${localTimestamp(last_sync_at)}`}
    >
      <span className="dot" aria-hidden />
      {stale
        ? `Data may be stale — synced ${relativeAge(last_sync_at)}`
        : `Updated ${relativeAge(last_sync_at)}`}
      {last_sync_status === "error" && " · last sync failed"}
    </span>
  );
}

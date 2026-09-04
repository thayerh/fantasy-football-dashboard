import { useQuery } from "@tanstack/react-query";

import { ApiError, api, type AvailableSort } from "./api/client";

/**
 * Every query the app makes, and its cadence, in one place.
 *
 * Browser refresh is deliberately independent of ingestion: these are UI
 * responsiveness knobs, and reading from local SQLite is cheap. They need not
 * match the systemd timers, and the draft's does not — see DRAFT_REFRESH_MS.
 */
const REFRESH_MS = 60_000;

/**
 * The board reloads three times a minute, matching ffd-draft.timer's 20s
 * poll. Worst case a pick is ~40s old on screen; average is ~20s. Going faster
 * here would only re-read the same rows, since the browser can't see Sleeper.
 */
const DRAFT_REFRESH_MS = 20_000;

/** 503 means "not ingested yet" — retrying in a tight loop won't help. */
const notIngested = (count: number, error: Error) =>
  !(error instanceof ApiError && error.status === 503) && count < 2;

export function useHealth() {
  return useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: REFRESH_MS });
}

export function useLeague() {
  return useQuery({
    queryKey: ["league"],
    queryFn: api.league,
    refetchInterval: REFRESH_MS,
    retry: notIngested,
  });
}

export function useUsers() {
  return useQuery({ queryKey: ["users"], queryFn: api.users, refetchInterval: REFRESH_MS });
}

export function useAvailable(
  position: string | null,
  sort: AvailableSort = "ecr",
  limit = 150,
) {
  return useQuery({
    // Keyed by both controls, so switching back to a combination you have
    // already looked at is instant and still refreshes on the board's cadence.
    queryKey: ["available", position, sort, limit],
    queryFn: () => api.available(position, sort, limit),
    refetchInterval: DRAFT_REFRESH_MS,
    retry: notIngested,
  });
}

export function useDraft() {
  return useQuery({
    queryKey: ["draft"],
    queryFn: api.draft,
    refetchInterval: DRAFT_REFRESH_MS,
    retry: notIngested,
  });
}

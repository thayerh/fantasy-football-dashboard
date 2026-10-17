import { useQuery } from "@tanstack/react-query";

import { ApiError, api, type Board } from "./api/client";

/**
 * Every query the app makes, and its cadence, in one place.
 *
 * Browser refresh is deliberately independent of ingestion: these are UI
 * responsiveness knobs, and reading from local SQLite is cheap. They need not
 * match the systemd timers, and the draft's does not — see DRAFT_REFRESH_MS.
 */
const REFRESH_MS = 60_000;

/**
 * The board reloads every two seconds, matching ffd-draft.timer's poll.
 * Worst case a pick is ~4s old on screen; average is ~2s.
 *
 * The two cadences have to move together. The browser cannot see Sleeper — it
 * re-reads what the timer last wrote — so polling faster than the ingest only
 * re-reads identical rows, and polling slower throws away picks already in the
 * database. Either number changed alone makes the board worse.
 */
const DRAFT_REFRESH_MS = 2_000;

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
  board: Board = "aggregate",
  limit = 150,
) {
  return useQuery({
    // Keyed by both controls, so switching back to a combination you have
    // already looked at is instant and still refreshes on the board's cadence.
    queryKey: ["available", position, board, limit],
    queryFn: () => api.available(position, board, limit),
    refetchInterval: DRAFT_REFRESH_MS,
    retry: notIngested,
  });
}

/**
 * The league's rosters. Written by `ffd sync`, so it moves on the ordinary
 * cadence — waivers and trades, not picks.
 */
export function useRosters() {
  return useQuery({
    queryKey: ["rosters"],
    queryFn: api.rosters,
    refetchInterval: REFRESH_MS,
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

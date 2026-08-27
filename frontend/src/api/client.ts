/**
 * Typed access to the dashboard API.
 *
 * All paths are relative: Vite proxies /api in dev, and FastAPI serves the
 * built SPA from the same origin in production, so neither needs a base URL.
 */

export interface League {
  league_id: string;
  name: string | null;
  season: string | null;
  season_type: string | null;
  status: string | null;
  total_rosters: number | null;
  avatar: string | null;
  fetched_at: string;
}

export interface LeagueUser {
  user_id: string;
  username: string | null;
  display_name: string | null;
  avatar: string | null;
  avatar_url: string | null;
  team_name: string | null;
  is_owner: boolean;
  first_seen_at: string;
  last_seen_at: string;
}

export interface Health {
  status: string;
  db_ok: boolean;
  last_sync_at: string | null;
  last_sync_status: string | null;
  stale: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });

  if (!response.ok) {
    // The API sends {"detail": "..."} on error; fall back if it didn't.
    let detail = `Request failed with ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => get<Health>("/api/health"),
  league: () => get<League>("/api/league"),
  users: () => get<LeagueUser[]>("/api/users"),
};

/**
 * Pick the best available avatar.
 *
 * `avatar_url` is the member's uploaded image. The top-level `avatar` id is
 * frequently a shared Sleeper default — in this league seven ids covered twelve
 * members — so falling back to it renders several people identically.
 */
export function avatarUrl(user: LeagueUser): string | null {
  if (user.avatar_url) return user.avatar_url;
  return user.avatar ? `https://sleepercdn.com/avatars/thumbs/${user.avatar}` : null;
}

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

export interface DraftSlot {
  slot: number;
  user_id: string | null;
  display_name: string | null;
  team_name: string | null;
  avatar_url: string | null;
  roster_id: number | null;
}

export interface DraftPick {
  pick_no: number;
  round: number | null;
  draft_slot: number | null;
  picked_by: string | null;
  player_id: string | null;
  first_name: string | null;
  last_name: string | null;
  position: string | null;
  nfl_team: string | null;
  is_keeper: boolean;
  /** What the market thought: Sleeper's own ADP, and FantasyPros' consensus
   *  rank. Null where coverage stops, around the top 270 players. */
  adp: number | null;
  ecr: number | null;
}

export interface Draft {
  draft_id: string;
  status: string | null;
  type: string | null;
  season: string | null;
  rounds: number | null;
  teams: number | null;
  start_time: string | null;
  last_picked_at: string | null;
  /** When the ingest last read Sleeper. The draft page's freshness signal —
   *  `ffd sync-draft` writes no sync_run row at its cadence. */
  fetched_at: string;
  /** One entry per column, already resolved to a league member by the API. */
  slots: DraftSlot[];
  picks: DraftPick[];
}

/** What "best available" is ranked by: expert consensus, or the Sleeper market. */
export type AvailableSort = "ecr" | "adp";

export interface Player {
  player_id: string;
  full_name: string | null;
  position: string | null;
  team: string | null;
  injury_status: string | null;
  years_exp: number | null;
  age: number | null;
  /** Sleeper's own overall rank. Null for team defenses, which sort last. */
  search_rank: number | null;
  /** FantasyPros consensus rank — overall in an unfiltered list, positional
   *  when filtered. Null for the ~1,900 players beyond their free-tier cap. */
  ecr: number | null;
  /** The overall rank, always — `ecr` above is positional in a filtered view. */
  ecr_overall: number | null;
  tier: number | null;
  bye_week: number | null;
  pos_rank: string | null;
  /** How far the experts disagree: the best and worst rank any of them gave. */
  rank_min: number | null;
  rank_max: number | null;
  /** Sleeper's own average draft position — the market this league drafts in. */
  adp: number | null;
  /** The overall pick number he is projected to go at: his place in the run
   *  order, mapped onto the picks still unspent — keepers are stepped over,
   *  since nobody spends a pick on them. Counted over the unfiltered pool, so
   *  it stays right on a position tab, which cannot see who else goes first.
   *  Null when nothing is ranked, or when he ranks past the last pick. */
  expected_pick: number | null;
  /** Vegas-derived projected fantasy points for the season, from the imported
   *  export. Null where it doesn't reach: every kicker and defense, and the
   *  deep end of the bench. */
  vegas_points: number | null;
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
  draft: () => get<Draft>("/api/draft"),
  available: (position: string | null, sort: AvailableSort = "ecr", limit = 150) => {
    const params = new URLSearchParams({ sort, limit: String(limit) });
    if (position) params.set("position", position);
    return get<Player[]>(`/api/draft/available?${params}`);
  },
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

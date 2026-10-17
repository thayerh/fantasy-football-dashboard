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

/** A player as he sits on somebody's roster. */
export interface RosterPlayer {
  player_id: string;
  full_name: string | null;
  position: string | null;
  team: string | null;
  injury_status: string | null;
  years_exp: number | null;
  age: number | null;
  /** The dynasty aggregate, on the same terms as the draft board's AGG. */
  consensus: number | null;
  consensus_sources: number;
  vegas_points: number | null;
}

/**
 * A future rookie pick, from one team's point of view.
 *
 * `roster_id` is the other end of it: for a pick a team holds, the roster the
 * pick was originally issued to (its own, when it has never been traded); for
 * one it has traded away, the roster holding it now.
 */
export interface RosterPick {
  season: string;
  round: number;
  roster_id: number;
}

export interface Roster {
  roster_id: number;
  owner_id: string | null;
  display_name: string | null;
  team_name: string | null;
  avatar_url: string | null;
  avatar: string | null;
  is_owner: boolean;
  wins: number;
  losses: number;
  ties: number;
  fpts: number | null;
  fpts_against: number | null;
  waiver_budget_used: number | null;
  waiver_position: number | null;
  /** Positional against `Rosters.lineup`: index i is lineup slot i, and a null
   *  is a slot nobody has filled. Never shorter than the lineup. */
  starters: (RosterPlayer | null)[];
  bench: RosterPlayer[];
  taxi: RosterPlayer[];
  reserve: RosterPlayer[];
  /** Future picks this team holds, and its own that are now somebody else's. */
  picks: RosterPick[];
  picks_traded: RosterPick[];
}

/** The league's own limits, as Sleeper enforces them. */
export interface RosterLimits {
  starters: number;
  bench: number;
  active: number;
  taxi: number;
  reserve: number;
}

export interface Rosters {
  league_id: string;
  season: string | null;
  /** The starting slots, in order — the labels down the lineup table. */
  lineup: string[];
  limits: RosterLimits;
  /** The rookie drafts whose picks are tradeable now (rulebook §8.3), earliest
   *  first, and how many rounds each of them runs (§6.1). */
  pick_seasons: string[];
  pick_rounds: number;
  /** What `waiver_budget_used` is spent against — $100 a year (rulebook §7.1). */
  waiver_budget: number | null;
  fetched_at: string;
  teams: Roster[];
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
  /** What the market thought: where Sleeper drafters actually took him, and
   *  the aggregate of the dynasty boards. Null where no board reaches him. */
  adp: number | null;
  consensus: number | null;
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

/**
 * Which ranking orders "best available".
 *
 * Four dynasty boards and their average, the Sleeper market, and three redraft
 * boards. `aggregate` is not a source — the server computes it per request as
 * the mean of the four dynasty boards, leaving `sleeper-adp` out (it is what the
 * market *did*, and folding an outcome into an average of opinions would
 * double-count it) and the redraft boards out (this season only is a different
 * question from who is worth owning).
 */
export const BOARDS = [
  { value: "aggregate", label: "Aggregate", meta: "Average of the four dynasty boards" },
  { value: "fantasypros", label: "FantasyPros", meta: "FantasyPros dynasty superflex consensus" },
  { value: "fantasycalc", label: "FantasyCalc", meta: "FantasyCalc dynasty trade values" },
  { value: "thetradecalc", label: "TheTradeCalc", meta: "TheTradeCalc 12-team superflex PPR" },
  { value: "ktc", label: "KTC", meta: "KeepTradeCut dynasty superflex values" },
  { value: "sleeper-adp", label: "Sleeper ADP", meta: "Where Sleeper drafters actually take him" },
  { value: "fantasycalc-redraft", label: "FC Redraft", meta: "FantasyCalc redraft values — this season only" },
  { value: "thetradecalc-redraft", label: "TTC Redraft", meta: "TheTradeCalc redraft values — this season only" },
  { value: "ktc-redraft", label: "KTC Redraft", meta: "KeepTradeCut redraft values — this season only" },
] as const;

export type Board = (typeof BOARDS)[number]["value"];

/** The four that make up the aggregate, in the order the table shows them. */
export const CONSENSUS_BOARDS = ["fantasypros", "fantasycalc", "thetradecalc", "ktc"] as const;

export interface Player {
  player_id: string;
  full_name: string | null;
  position: string | null;
  team: string | null;
  injury_status: string | null;
  years_exp: number | null;
  age: number | null;
  /** Sleeper's own overall rank, the fallback ordering when no board ranks. */
  search_rank: number | null;
  /** Every board's overall rank for him, keyed by source. A board with no
   *  opinion of him is absent rather than null, so the keys can be counted. */
  ranks: Partial<Record<Board, number>>;
  /** The mean of the consensus boards' ranks, and how many of the three
   *  produced it. "3rd on one board of three" is a different claim from "3rd
   *  on all three", which is why the count travels with the number. */
  consensus: number | null;
  consensus_sources: number;
  /** From whichever board is in play, so these change with the tab. Tier and
   *  the spread are FantasyPros'; value is FantasyCalc's or TheTradeCalc's,
   *  on their own scales and not comparable between the two. */
  tier: number | null;
  pos_rank: string | null;
  value: number | null;
  rank_min: number | null;
  rank_max: number | null;
  /** The board's own age for him. In dynasty this is most of the valuation. */
  ranked_age: number | null;
  /** The overall pick number he is projected to go at: his place in the run
   *  order, mapped onto the picks still unspent — keepers are stepped over,
   *  since nobody spends a pick on them. Counted over the unfiltered pool, so
   *  it stays right on a position tab, which cannot see who else goes first.
   *  Null when nothing is ranked, or when he ranks past the last pick. */
  expected_pick: number | null;
  /** Vegas-derived projected fantasy points for the season, from the imported
   *  export. Null where it doesn't reach, at the deep end of the bench. A
   *  season quantity rather than a dynasty valuation: it says how good he is
   *  this year, the one thing the dynasty boards deliberately do not. */
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
  rosters: () => get<Rosters>("/api/rosters"),
  available: (position: string | null, board: Board = "aggregate", limit = 150) => {
    const params = new URLSearchParams({ board, limit: String(limit) });
    if (position) params.set("position", position);
    return get<Player[]>(`/api/draft/available?${params}`);
  },
};

/**
 * Pick the best available avatar.
 *
 * Takes the two fields rather than a whole member, because a roster carries
 * its owner's avatar too and the fallback rule must not fork.
 *
 * `avatar_url` is the member's uploaded image. The top-level `avatar` id is
 * frequently a shared Sleeper default — in this league seven ids covered twelve
 * members — so falling back to it renders several people identically.
 */
export function avatarUrl(user: Pick<LeagueUser, "avatar_url" | "avatar">): string | null {
  if (user.avatar_url) return user.avatar_url;
  return user.avatar ? `https://sleepercdn.com/avatars/thumbs/${user.avatar}` : null;
}

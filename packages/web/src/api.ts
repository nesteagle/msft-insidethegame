/** Types and calls for the footballfoundry-agents API. Shapes mirror footballfoundry_schema. */

export type Audience = "broadcast" | "analyst" | "casual" | "player";

export interface MatchEvent {
  event_id: string;
  match_id: string;
  seq: number;
  period: number;
  elapsed_s: number;
  minute: number;
  second: number;
  clock: string;
  type: string;
  team_id: string;
  player_id: string | null;
  related_player_id: string | null;
  outcome: string | null;
  x: number | null;
  y: number | null;
  end_x: number | null;
  end_y: number | null;
  possession_id: number;
  set_piece: string;
  under_pressure: boolean;
  body_part: string | null;
  xg: number | null;
  home_score: number;
  away_score: number;
  qualifiers: Record<string, any>;
}

export interface Club {
  name: string;
  short_name: string;
  colors: string[];
  stadium: string;
  formation: string;
  manager: string;
}

export interface PlayerInfo {
  name: string;
  short_name: string;
  number: number;
  position: string;
  club_id: string;
  nationality: string;
}

export interface League {
  name: string;
  season: string;
  clubs: Record<string, Club>;
  players: Record<string, PlayerInfo>;
}

export interface MatchSummary {
  match_id: string;
  home_id: string | null;
  away_id: string | null;
  home_score: number;
  away_score: number;
  clock: string;
  period: number;
  last_seq: number;
  finished: boolean;
  live?: boolean; // still receiving events (from the relay; stored snapshots don't say)
}

export interface TeamSheet {
  formation: string;
  lineup: { player_id: string; role: string; x: number; y: number }[];
  bench: string[];
}

export interface MatchSession {
  summary: MatchSummary;
  team_sheets: Record<string, TeamSheet>;
  run: number;
  halftime_s: number;
}

export interface InsightView {
  audience: Audience;
  language: string;
  headline: string;
  text: string;
  translated_by?: "translator" | "agent" | null;
}

export interface Insight {
  insight_id: string;
  match_id: string;
  kind: "goal" | "red_card" | "penalty" | "big_chance" | "momentum" | "summary" | "half_time" | "full_time"
    | "tactical_change" | "spotlight" | "kick_off" | "players_to_watch";
  seq: number;
  period: number;
  minute: number;
  clock: string;
  team_id: string | null;
  player_ids: string[];
  event_ids: string[];
  headline: string;
  text: string;
  why_it_matters: string;
  stats: Record<string, string>;
  priority: 1 | 2 | 3;
  display_s: number;
  verified: boolean;
  language: string;
  created_at: string;
  visuals?: Visual[];
  trivia?: Trivia | null;
  view: InsightView;
}

/** A "Did you know?" for casual fans (see footballfoundry_schema.Trivia). */
export interface Trivia {
  trivia_id: string;
  key: string;
  subject_id: string;
  params: Record<string, string | number>;
  text: string; // English
  translations: Record<string, string>; // the storyteller's story in other languages
  source: "data" | "storyteller";
}

/** One action of an animated move. Coordinates are the acting team's (x 0-100 towards the opponent's goal). */
export interface MoveStep {
  seq: number;
  clock: string;
  type: string;
  team_id: string;
  player_id: string | null;
  outcome: string | null;
  x: number;
  y: number;
  end_x: number | null;
  end_y: number | null;
}

export interface ShotPoint {
  seq: number;
  period: number;
  minute: number;
  t: number; // minutes from kick-off, continuing through half-time (the clock restarts at 45')
  clock: string;
  team_id: string;
  player_id: string | null;
  x: number;
  y: number;
  xg: number;
  outcome: string | null;
}

/** A chart or animated replay, built from the events by the agents service (see footballfoundry_schema.Visual). */
export interface Visual {
  type: "move" | "shot_map" | "xg_race" | "momentum" | "compare";
  home_id: string;
  away_id: string;
  caption: string;
  clips: { label: string; steps: MoveStep[] }[];
  shots: ShotPoint[];
  threat: { minute: number; home: number; away: number }[];
  rows: { key: string; home: number; away: number }[];
  now: number | null; // xg_race: the minute it was drawn at, as ShotPoint.t
  half_time: number | null; // xg_race, momentum: where the second half starts, as ShotPoint.t
}

export interface Viewer {
  audience: Audience;
  language: string;
  clubId: string | null;
  playerId: string | null;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  league: () => json<League>("/api/league"),
  matches: () => json<MatchSummary[]>("/api/matches"),
  session: (matchId: string) => json<MatchSession>(`/api/matches/${encodeURIComponent(matchId)}/session`),
  saveProfile: (userId: string, v: Viewer) =>
    json(`/api/profiles/${encodeURIComponent(userId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audience: v.audience,
        language: v.language,
        favourite_club_ids: v.clubId ? [v.clubId] : [],
        focus_player_id: v.playerId,
      }),
    }),
};

export function viewerQuery(v: Viewer): string {
  const q = new URLSearchParams({ audience: v.audience, lang: v.language });
  if (v.clubId) q.set("club", v.clubId);
  if (v.playerId) q.set("player", v.playerId);
  return q.toString();
}

/** Club colour names (world.py) to kit swatches. */
export const KIT_HEX: Record<string, string> = {
  red: "#d62828", blue: "#1d4ed8", "sky blue": "#7cc4f0", claret: "#7a1f3d", white: "#f5f5f5",
  black: "#111111", amber: "#f2a900", green: "#1f8a4c", navy: "#1b2a4a", orange: "#f77f00",
  maroon: "#6d1a1a", yellow: "#f6d32d", purple: "#5b2a86", teal: "#0f8b8d", silver: "#b8bcc2",
};

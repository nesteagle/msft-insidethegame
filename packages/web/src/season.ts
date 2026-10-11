import { useEffect, useState } from "react";

/** /api/season: the simulated season aggregated up to a matchday (see packages/agents/.../season.py). */

export interface FormEntry {
  result: "W" | "D" | "L";
  match_id: string;
  matchday: number;
  opponent: string;
  home: boolean;
  score: string;
}

export interface TableRow {
  club_id: string;
  position: number;
  previous: number | null;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  gf: number;
  ga: number;
  gd: number;
  points: number;
  form: FormEntry[];
  next: { match_id: string; matchday: number; home: boolean; opponent: string } | null;
}

export interface ClubTotals {
  played: number;
  gf: number;
  ga: number;
  xg: number;
  xga: number;
  clean_sheets: number;
  possession: number;
  pass_accuracy: number;
  sh: number;
  sot: number;
  tkw: number;
  int: number;
  sav: number;
  yc: number;
  rc: number;
  km_per_match: number;
}

/** Season totals for one player (short keys as in the season book). */
export interface PlayerTotals {
  p: string;
  c: string;
  apps: number;
  st: number;
  min: number;
  g: number;
  a: number;
  xg: number;
  sh: number;
  sot: number;
  kp: number;
  pa: number;
  pc: number;
  tk: number;
  tkw: number;
  int: number;
  rec: number;
  clr: number;
  blk: number;
  sav: number;
  yc: number;
  rc: number;
  cs: number;
  km: number;
  spr: number;
  top: number;
}

export interface SeasonView {
  status: "ready";
  season: string;
  last_matchday: number;
  default_through: number;
  live_matchday: number | null;
  through: number;
  played: number;
  table: TableRow[];
  clubs: Record<string, ClubTotals>;
  players: PlayerTotals[];
}

export interface MatchLine extends Partial<Omit<PlayerTotals, "p" | "c">> {
  match_id: string;
  matchday: number;
  kickoff: string;
  home: boolean;
  opponent: string;
  gf: number;
  ga: number;
  min: number;
}

export type SeasonState =
  | { status: "loading" }
  | { status: "building" }
  | { status: "error"; error: string }
  | SeasonView;

/** The season view after `through` (null: the server's default, which hides a live matchday's results). */
export function useSeason(through: number | null): SeasonState {
  const [state, setState] = useState<SeasonState>({ status: "loading" });
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const res = await fetch(`/api/season${through === null ? "" : `?through=${through}`}`);
        const body = await res.json();
        if (!alive) return;
        if (res.status === 202) {
          if (body.status === "failed") setState({ status: "error", error: body.error ?? "failed" });
          else {
            setState({ status: "building" });
            timer = window.setTimeout(load, 3000);
          }
        } else if (!res.ok) setState({ status: "error", error: body.error ?? `${res.status}` });
        else setState(body as SeasonView);
      } catch (e) {
        if (alive) setState({ status: "error", error: (e as Error).message });
      }
    };
    void load();
    return () => { alive = false; clearTimeout(timer); };
  }, [through]);
  return state;
}

export async function playerLog(playerId: string, through: number): Promise<MatchLine[]> {
  const res = await fetch(`/api/season/players/${encodeURIComponent(playerId)}?through=${through}`);
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()).matches as MatchLine[];
}

export type PositionGroup = "GK" | "DEF" | "MID" | "FWD";

export function positionGroup(position: string): PositionGroup {
  if (position === "GK") return "GK";
  if (["RB", "CB", "LB"].includes(position)) return "DEF";
  if (["DM", "CM", "AM"].includes(position)) return "MID";
  return "FWD";
}

export const per90 = (v: number, min: number) => (min ? (v * 90) / min : 0);

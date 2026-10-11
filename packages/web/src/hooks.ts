import { useEffect, useState } from "react";
import { api, type League, type MatchSession, type MatchSummary } from "./api";

export function useLeague(): { league: League | null; error: string | null } {
  const [league, setLeague] = useState<League | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.league().then(setLeague).catch((e: Error) => setError(e.message));
  }, []);
  return { league, error };
}

/** Matches the agents have seen, refreshed every few seconds so new streams show up. */
export function useMatches(): MatchSummary[] | null {
  const [matches, setMatches] = useState<MatchSummary[] | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.matches().then((m) => alive && setMatches(m)).catch(() => alive && setMatches([]));
    load();
    const timer = setInterval(load, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  return matches;
}

/** A match's team sheets. Polls until kick-off has been seen (the sheets come with it). */
export function useSession(matchId: string | null): MatchSession | null {
  const [session, setSession] = useState<MatchSession | null>(null);
  useEffect(() => {
    setSession(null);
    if (!matchId) return;
    let alive = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const s = await api.session(matchId);
        if (!alive) return;
        if (s.summary.home_id && s.summary.away_id) setSession(s);
        else timer = window.setTimeout(load, 2000);
      } catch {
        if (alive) timer = window.setTimeout(load, 3000);
      }
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [matchId]);
  return session;
}

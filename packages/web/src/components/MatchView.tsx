import { useEffect, useMemo, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import type { League, MatchSession } from "../api";
import { MatchPlayer } from "../match/engine";
import { usePlayback, useViewer } from "../store";
import { Chat } from "./Chat";
import { Feed } from "./Feed";
import { Insights } from "./Insights";
import { Momentum } from "./Momentum";
import { PlaybackBar } from "./PlaybackBar";
import { Pitch } from "./Pitch";
import { Scoreboard } from "./Scoreboard";
import { StatsTable } from "./StatsTable";

export interface PageControls {
  seekSeq: (seq: number) => string | null;
  focusPlayer: (playerId: string | null) => void;
}

export function MatchView({ league, session, matchId }: { league: League; session: MatchSession; matchId: string }) {
  const { i18n } = useTranslation();
  const player = useMemo(() => new MatchPlayer(league, session, matchId), [league, session, matchId]);
  const view = useSyncExternalStore(player.subscribe, player.getView);
  const playerId = useViewer((s) => s.playerId);
  const setPlayer = useViewer((s) => s.setPlayer);
  const setPosition = usePlayback((s) => s.setPosition);
  useEffect(() => {
    player.goLive();
    return () => player.destroy();
  }, [player]);

  useEffect(() => { player.focusPlayer = playerId; }, [player, playerId]);
  useEffect(() => { player.relabel(); }, [player, i18n.language]);
  useEffect(() => { setPosition(view.appliedSeq, view.appliedT, view.clock); },
            [view.appliedSeq, view.appliedT, view.clock, setPosition]);

  const controls: PageControls = useMemo(() => ({
    seekSeq: (seq) => player.seekSeq(seq),
    focusPlayer: (pid) => setPlayer(pid),
  }), [player, setPlayer]);

  return (
    <main className="layout">
      <section className="stage">
        <Scoreboard league={league} view={view} />
        <PlaybackBar player={player} view={view} />
        <Pitch player={player} league={league} view={view} />
        <Momentum player={player} league={league} view={view} />
        <div className="two-col">
          <StatsTable league={league} view={view} />
          <Feed view={view} />
        </div>
      </section>
      <aside className="side">
        <Insights matchId={matchId} league={league} controls={controls} />
        <Chat matchId={matchId} league={league} controls={controls} />
      </aside>
    </main>
  );
}

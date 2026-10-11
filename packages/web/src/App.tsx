import { Spinner, Text } from "@fluentui/react-components";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import { Header } from "./components/Header";
import { MatchView } from "./components/MatchView";
import { useLeague, useMatches, useSession } from "./hooks";
import i18n, { LANGUAGES } from "./i18n";
import { useViewer } from "./store";

export default function App() {
  const { t } = useTranslation();
  const { league, error } = useLeague();
  const matches = useMatches();
  const viewer = useViewer();
  const session = useSession(viewer.matchId);

  // The interface follows the chosen language when we have it; insights and chat follow it regardless.
  useEffect(() => {
    const ui = LANGUAGES.some((l) => l.code === viewer.language) ? viewer.language : "en";
    if (i18n.language !== ui) void i18n.changeLanguage(ui);
    document.documentElement.lang = viewer.language;
  }, [viewer.language]);

  // Pick a match on arrival: the remembered one if it's still live (or nothing is), else a live one. After that,
  // the viewer's choice stands, even a finished match they want to replay.
  const picked = useRef(false);
  useEffect(() => {
    if (!matches?.length) return;
    const remembered = matches.find((m) => m.match_id === viewer.matchId);
    const live = matches.find((m) => m.live);
    if (!picked.current) {
      picked.current = true;
      if (remembered && (remembered.live || !live)) return;
      viewer.setMatch((live ?? matches.find((m) => !m.finished) ?? matches[0]).match_id);
    } else if (!remembered) {
      viewer.setMatch((live ?? matches[0]).match_id);
    }
  }, [matches, viewer]);

  // Keep the stored profile in step, so the agents' personalisation follows this viewer across devices.
  useEffect(() => {
    const timer = setTimeout(() => {
      api.saveProfile(viewer.userId, viewer).catch(() => undefined);
    }, 800);
    return () => clearTimeout(timer);
  }, [viewer.userId, viewer.audience, viewer.language, viewer.clubId, viewer.playerId, viewer]);

  return (
    <div className="app">
      <Header league={league} matches={matches ?? []} session={session} />
      {error ? (
        <div className="empty"><Text weight="semibold">{error}</Text></div>
      ) : !league || matches === null ? (
        <div className="empty"><Spinner label={t("conn.connecting")} /></div>
      ) : !matches.length ? (
        <div className="empty">
          <Text size={500} weight="semibold">{t("noMatches")}</Text>
          <code>{t("noMatchesHint")}</code>
          <a href="/control">{t("controlRoom")}</a>
        </div>
      ) : session && viewer.matchId ? (
        <MatchView key={`${viewer.matchId}:${session.run}`} league={league} session={session} matchId={viewer.matchId} />
      ) : (
        <div className="empty"><Spinner label={t("conn.connecting")} /></div>
      )}
    </div>
  );
}

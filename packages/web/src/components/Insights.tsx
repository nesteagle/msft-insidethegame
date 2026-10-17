import { Badge, Button, Text } from "@fluentui/react-components";
import { ArrowCounterclockwiseRegular, CheckmarkCircleRegular } from "@fluentui/react-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { viewerQuery, type Insight, type League } from "../api";
import { usePlayback, useViewer } from "../store";
import type { PageControls } from "./MatchView";
import { TriviaCard } from "./Trivia";
import { Visuals } from "./Visual";

const KIND_COLOR: Record<Insight["kind"], "danger" | "important" | "brand" | "informative" | "subtle" | "success"> = {
  goal: "success", red_card: "danger", penalty: "important", big_chance: "brand", momentum: "informative",
  summary: "subtle", half_time: "informative", full_time: "informative", tactical_change: "important",
  spotlight: "brand", kick_off: "informative", players_to_watch: "informative",
};

/**
 * The analyst agents' insights, written for this viewer (audience, language, club, player). Held back until
 * playback reaches them, so a replay never spoils what comes next; each new one also shows on the pitch as a
 * lower third.
 */
export function Insights({ matchId, league, controls }: { matchId: string; league: League; controls: PageControls }) {
  const { t } = useTranslation();
  const audience = useViewer((s) => s.audience);
  const language = useViewer((s) => s.language);
  const clubId = useViewer((s) => s.clubId);
  const playerId = useViewer((s) => s.playerId);
  const appliedSeq = usePlayback((s) => s.appliedSeq);
  const showOverlay = usePlayback((s) => s.showOverlay);
  const [items, setItems] = useState<Map<string, Insight>>(new Map());

  useEffect(() => {
    const q = viewerQuery({ audience, language, clubId, playerId });
    const es = new EventSource(`/api/matches/${encodeURIComponent(matchId)}/stream?${q}`);
    es.addEventListener("insight", (msg) => {
      const i = JSON.parse((msg as MessageEvent).data) as Insight;
      setItems((prev) => new Map(prev).set(i.insight_id, i));
    });
    return () => es.close();
  }, [matchId, audience, language, clubId, playerId]);

  const visible = useMemo(
    () => [...items.values()].filter((i) => i.seq <= appliedSeq).sort((a, b) => b.seq - a.seq),
    [items, appliedSeq],
  );

  // A lower third for an insight that appears during playback (not for a backlog arriving all at once).
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    const ids = new Set(visible.map((i) => i.insight_id));
    const fresh = seen.current ? visible.filter((i) => !seen.current!.has(i.insight_id)) : [];
    seen.current = ids;
    const pick = fresh.length && fresh.length <= 2 ? fresh.filter((i) => i.priority <= 2)[0] : undefined;
    if (!pick) return;
    showOverlay(pick);
    const timer = setTimeout(() => usePlayback.getState().overlay?.insight_id === pick.insight_id && showOverlay(null),
                             pick.display_s * 1000);
    return () => clearTimeout(timer);
  }, [visible, showOverlay]);
  useEffect(() => () => showOverlay(null), [showOverlay]);

  const english = language.startsWith("en");
  return (
    <div className="card insights-card">
      <div className="card-head">
        <h2>{t("insights.title")} <span className="sub">{t("insights.sub")}</span></h2>
      </div>
      {!visible.length && <Text className="muted">{t("insights.empty")}</Text>}
      <ol className="insights">
        {visible.map((i) => (
          <li key={i.insight_id} className={`insight p${i.priority}${playerId && i.player_ids.includes(playerId) ? " mine" : ""}`}>
            <div className="insight-meta">
              <span className="clock-chip">{i.clock}</span>
              <Badge appearance="tint" color={KIND_COLOR[i.kind]} size="small">{t(`insights.kind.${i.kind}`)}</Badge>
              {/* Native tooltips (title): no floating layer added over the live pitch on hover. */}
              {i.verified && (
                <span className="verified" title={t("insights.verified")}>
                  <CheckmarkCircleRegular aria-label={t("insights.verified")} />
                </span>
              )}
              <Button size="small" appearance="subtle" icon={<ArrowCounterclockwiseRegular />}
                      aria-label={t("insights.replay")} title={t("insights.replay")}
                      onClick={() => controls.seekSeq(i.seq)} />
            </div>
            <h3 className="insight-headline">{i.view.headline}</h3>
            <p className="insight-text">{i.view.text}</p>
            {i.view.translated_by === "translator" && (
              <p className="insight-translated">{t("insights.translatedBy")}</p>
            )}
            {english && i.view.audience !== "analyst" && i.why_it_matters && (
              <p className="insight-why"><b>{t("insights.why")}:</b> {i.why_it_matters}</p>
            )}
            {Object.keys(i.stats).length > 0 && (
              <div className="insight-stats">
                {Object.entries(i.stats).map(([k, v]) => <span key={k}>{k} <b>{v}</b></span>)}
              </div>
            )}
            {/* Casual fans get a "Did you know?" about a player or club in the moment. */}
            {audience === "casual" && i.trivia && <TriviaCard trivia={i.trivia} />}
            {/* Captions are written in English; other languages get the chart without one. */}
            <Visuals visuals={i.visuals} league={league} showCaptions={english} />
            {i.player_ids.length > 0 && (
              <div className="insight-players">
                {i.player_ids.map((p) => (
                  <button key={p} type="button" className={`player-chip${p === playerId ? " on" : ""}`}
                          onClick={() => controls.focusPlayer(p === playerId ? null : p)}>
                    {league.players[p]?.short_name ?? p}
                  </button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

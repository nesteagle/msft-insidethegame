import { useTranslation } from "react-i18next";
import { KIT_HEX, type League } from "../api";
import type { View } from "../match/engine";

function Kit({ colors }: { colors: string[] }) {
  const [a, b] = [KIT_HEX[colors[0]] ?? "#888", KIT_HEX[colors[1]] ?? KIT_HEX[colors[0]] ?? "#888"];
  return <span className="kit" aria-hidden="true" style={{ background: `linear-gradient(90deg, ${a} 50%, ${b} 50%)` }} />;
}

export function Scoreboard({ league, view }: { league: League; view: View }) {
  const { t } = useTranslation();
  const home = league.clubs[view.home], away = league.clubs[view.away];
  return (
    <header className="scorebar card">
      <div className="team home">
        {home && <Kit colors={home.colors} />}
        <span className="team-dot home" aria-hidden="true" />
        <span className="team-name">{home?.name ?? view.home}</span>
      </div>
      <div className="score" aria-live="polite" key={view.scoreFlash} data-flash={view.scoreFlash > 0}>
        <span>{view.score[0]}</span><span className="dash">–</span><span>{view.score[1]}</span>
      </div>
      <div className="team away">
        <span className="team-name">{away?.name ?? view.away}</span>
        <span className="team-dot away" aria-hidden="true" />
        {away && <Kit colors={away.colors} />}
      </div>
      <div className="clock">
        <span className="clock-time">{view.clock}</span>
        <span className="status">{t(`status.${view.status}`)}</span>
      </div>
    </header>
  );
}

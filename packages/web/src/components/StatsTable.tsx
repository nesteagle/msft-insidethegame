import { useTranslation } from "react-i18next";
import type { League } from "../api";
import type { View } from "../match/engine";

export function StatsTable({ league, view }: { league: League; view: View }) {
  const { t } = useTranslation();
  const h = view.stats.home, a = view.stats.away;
  const total = h.passes + a.passes;
  const pos = total ? Math.round((h.passes / total) * 100) : 50;
  const acc = (p: number, c: number) => (p ? `${Math.round((c / p) * 100)}%` : "–");
  // [label, home shown, away shown, home value, away value]; 0/0 means "more isn't better", no highlight
  const rows: [string, string | number, string | number, number, number][] = [
    ["possession", `${pos}%`, `${100 - pos}%`, pos, 100 - pos],
    ["shots", h.shots, a.shots, h.shots, a.shots],
    ["onTarget", h.onTarget, a.onTarget, h.onTarget, a.onTarget],
    ["xg", h.xg.toFixed(2), a.xg.toFixed(2), h.xg, a.xg],
    ["passes", h.passes, a.passes, h.passes, a.passes],
    ["passAccuracy", acc(h.passes, h.completed), acc(a.passes, a.completed),
     h.completed / (h.passes || 1), a.completed / (a.passes || 1)],
    ["corners", h.corners, a.corners, h.corners, a.corners],
    ["fouls", h.fouls, a.fouls, 0, 0],
    ["offsides", h.offsides, a.offsides, 0, 0],
    ["yellows", h.yellows, a.yellows, 0, 0],
    ["reds", h.reds, a.reds, 0, 0],
  ];
  return (
    <div className="card">
      <h2>{t("stats.title")}</h2>
      <table className="stats">
        <thead>
          <tr>
            <th scope="col">{league.clubs[view.home]?.short_name}</th>
            <th scope="col" />
            <th scope="col">{league.clubs[view.away]?.short_name}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([key, hv, av, hn, an]) => (
            <tr key={key}>
              <td className={hn > an ? "lead" : ""}>{hv}</td>
              <td className="label">
                {t(`stats.${key}`)}
                {key === "possession" && (
                  <div className="possession">
                    <div className="home" style={{ flexGrow: pos }} />
                    <div className="away" style={{ flexGrow: 100 - pos }} />
                  </div>
                )}
              </td>
              <td className={an > hn ? "lead" : ""}>{av}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

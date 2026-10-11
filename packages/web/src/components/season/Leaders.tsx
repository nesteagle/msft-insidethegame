import { Button, Text } from "@fluentui/react-components";
import { ChevronRight16Regular } from "@fluentui/react-icons";
import { useTranslation } from "react-i18next";
import type { League } from "../../api";
import { positionGroup, type ClubTotals, type PlayerTotals, type SeasonView } from "../../season";
import { ClubName } from "../Nav";

export type PlayerStat = "g" | "a" | "xg" | "kp" | "sot" | "cs" | "sav" | "tkw" | "int" | "km" | "top";
type ClubStat = "gf" | "ga" | "clean_sheets" | "xg" | "possession" | "pass_accuracy";

const PLAYER_CATS: { key: PlayerStat; gk?: boolean; min?: number }[] = [
  { key: "g" }, { key: "a" }, { key: "xg" }, { key: "kp" }, { key: "sot" }, { key: "tkw" }, { key: "int" },
  { key: "cs", gk: true }, { key: "sav", gk: true }, { key: "km" }, { key: "top", min: 270 },
];
const CLUB_CATS: { key: ClubStat; ascending?: boolean }[] = [
  { key: "gf" }, { key: "ga", ascending: true }, { key: "clean_sheets" }, { key: "xg" }, { key: "possession" },
  { key: "pass_accuracy" },
];

function format(key: string, v: number, t: (k: string) => string): string {
  if (key === "possession" || key === "pass_accuracy") return `${Math.round(v * 100)}%`;
  if (key === "xg") return v.toFixed(1);
  if (key === "km") return `${Math.round(v)} ${t("season.unit.km")}`;
  if (key === "top") return `${v.toFixed(1)} ${t("season.unit.kmh")}`;
  return String(v);
}

export function Leaders({ league, view, onPlayer, onSeeAll }: {
  league: League; view: SeasonView; onPlayer: (id: string) => void; onSeeAll: (stat: PlayerStat) => void;
}) {
  const { t } = useTranslation();
  if (view.through === 0) return <div className="card empty-sm"><Text>{t("season.preseason")}</Text></div>;

  const top = (cat: (typeof PLAYER_CATS)[number]): PlayerTotals[] =>
    view.players
      .filter((p) => (!cat.gk || positionGroup(league.players[p.p]?.position ?? "") === "GK") && p.min >= (cat.min ?? 1))
      .filter((p) => p[cat.key] > 0)
      .sort((a, b) => b[cat.key] - a[cat.key] || a.min - b.min)
      .slice(0, 5);

  const clubTop = (cat: (typeof CLUB_CATS)[number]): [string, ClubTotals][] =>
    Object.entries(view.clubs)
      .sort(([, a], [, b]) => (cat.ascending ? a[cat.key] - b[cat.key] : b[cat.key] - a[cat.key]))
      .slice(0, 5);

  return (
    <div className="leaders-wrap">
      <Text as="h2" weight="semibold" size={400} className="section-title">{t("season.stats.players")}</Text>
      <div className="leaders">
        {PLAYER_CATS.map((cat) => {
          const rows = top(cat);
          const [first, ...rest] = rows;
          return (
            <section key={cat.key} className="card leader-card">
              <div className="card-head">
                <Text weight="semibold">{t(`season.stats.cat.${cat.key}`)}</Text>
                <Button size="small" appearance="transparent" icon={<ChevronRight16Regular />} iconPosition="after"
                  onClick={() => onSeeAll(cat.key)}>{t("season.stats.seeAll")}</Button>
              </div>
              {!first ? <Text className="muted">–</Text> : (
                <>
                  <button type="button" className="leader-first" onClick={() => onPlayer(first.p)}>
                    <span className="leader-value">{format(cat.key, first[cat.key], t)}</span>
                    <span className="leader-who">
                      <span className="leader-name">{league.players[first.p]?.name ?? first.p}</span>
                      <ClubName league={league} id={first.c} />
                    </span>
                  </button>
                  <ol className="leader-list" start={2}>
                    {rest.map((p) => (
                      <li key={p.p}>
                        <button type="button" className="link-btn leader-row" onClick={() => onPlayer(p.p)}>
                          <span className="leader-row-name">{league.players[p.p]?.name ?? p.p}</span>
                          <span className="muted club-tag">{league.clubs[p.c]?.short_name}</span>
                          <span className="leader-row-value">{format(cat.key, p[cat.key], t)}</span>
                        </button>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </section>
          );
        })}
      </div>

      <Text as="h2" weight="semibold" size={400} className="section-title">{t("season.stats.clubs")}</Text>
      <div className="leaders">
        {CLUB_CATS.map((cat) => {
          const [[firstId, first], ...rest] = clubTop(cat);
          return (
            <section key={cat.key} className="card leader-card">
              <div className="card-head"><Text weight="semibold">{t(`season.stats.club.${cat.key}`)}</Text></div>
              <div className="leader-first static">
                <span className="leader-value">{format(cat.key, first[cat.key], t)}</span>
                <span className="leader-who"><ClubName league={league} id={firstId} bold /></span>
              </div>
              <ol className="leader-list" start={2}>
                {rest.map(([id, c]) => (
                  <li key={id} className="leader-row">
                    <span className="leader-row-name">{league.clubs[id]?.name ?? id}</span>
                    <span className="leader-row-value">{format(cat.key, c[cat.key], t)}</span>
                  </li>
                ))}
              </ol>
            </section>
          );
        })}
      </div>
    </div>
  );
}

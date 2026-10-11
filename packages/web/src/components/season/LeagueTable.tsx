import { Text } from "@fluentui/react-components";
import { ArrowDown12Filled, ArrowUp12Filled } from "@fluentui/react-icons";
import { useTranslation } from "react-i18next";
import type { League } from "../../api";
import type { SeasonView, TableRow } from "../../season";
import { useViewer } from "../../store";
import { ClubName } from "../Nav";

type Zone = "ucl" | "uel" | "uecl" | "rel" | null;

function zone(position: number, clubs: number): Zone {
  if (position <= 4) return "ucl";
  if (position === 5) return "uel";
  if (position === 6) return "uecl";
  if (position > clubs - 3) return "rel";
  return null;
}

function Movement({ row }: { row: TableRow }) {
  const { t } = useTranslation();
  if (row.previous === null || row.previous === row.position) {
    return <span className="move same" title={t("season.table.same")}>–</span>;
  }
  const up = row.previous > row.position;
  const n = Math.abs(row.previous - row.position);
  return (
    <span className={`move ${up ? "up" : "down"}`} title={t(up ? "season.table.up" : "season.table.down", { n })}>
      {up ? <ArrowUp12Filled /> : <ArrowDown12Filled />}
    </span>
  );
}

export function LeagueTable({ league, view, onClub }: {
  league: League; view: SeasonView; onClub: (clubId: string) => void;
}) {
  const { t } = useTranslation();
  const favourite = useViewer((v) => v.clubId);
  const club = (id: string) => league.clubs[id]?.short_name ?? id;
  const n = view.table.length;

  if (view.through === 0) {
    return <div className="card empty-sm"><Text>{t("season.preseason")}</Text></div>;
  }

  return (
    <div className="card table-card">
      <div className="table-scroll">
        <table className="league-table">
          <thead>
            <tr>
              <th className="num" scope="col"><abbr title={t("season.table.position")}>{t("season.table.pos")}</abbr></th>
              <th scope="col" className="left">{t("season.table.club")}</th>
              <th className="num" scope="col"><abbr title={t("season.table.played")}>{t("season.table.p")}</abbr></th>
              <th className="num hide-sm" scope="col"><abbr title={t("season.table.won")}>{t("season.table.w")}</abbr></th>
              <th className="num hide-sm" scope="col"><abbr title={t("season.table.drawn")}>{t("season.table.d")}</abbr></th>
              <th className="num hide-sm" scope="col"><abbr title={t("season.table.lost")}>{t("season.table.l")}</abbr></th>
              <th className="num hide-md" scope="col"><abbr title={t("season.table.goalsFor")}>{t("season.table.gf")}</abbr></th>
              <th className="num hide-md" scope="col"><abbr title={t("season.table.goalsAgainst")}>{t("season.table.ga")}</abbr></th>
              <th className="num" scope="col"><abbr title={t("season.table.goalDifference")}>{t("season.table.gd")}</abbr></th>
              <th className="num" scope="col"><abbr title={t("season.table.points")}>{t("season.table.pts")}</abbr></th>
              <th scope="col" className="hide-sm">{t("season.table.form")}</th>
              <th scope="col" className="hide-md">{t("season.table.next")}</th>
            </tr>
          </thead>
          <tbody>
            {view.table.map((row) => {
              const z = zone(row.position, n);
              return (
                <tr key={row.club_id} className={`${z ? `zone-${z}` : ""}${row.club_id === favourite ? " mine" : ""}`}>
                  <td className="num pos">
                    <span className="pos-n">{row.position}</span>
                    <Movement row={row} />
                  </td>
                  <td className="left">
                    <button type="button" className="link-btn" onClick={() => onClub(row.club_id)}
                      title={t("season.table.seePlayers", { club: league.clubs[row.club_id]?.name })}>
                      <ClubName league={league} id={row.club_id} />
                    </button>
                  </td>
                  <td className="num">{row.played}</td>
                  <td className="num hide-sm">{row.won}</td>
                  <td className="num hide-sm">{row.drawn}</td>
                  <td className="num hide-sm">{row.lost}</td>
                  <td className="num hide-md">{row.gf}</td>
                  <td className="num hide-md">{row.ga}</td>
                  <td className="num">{row.gd > 0 ? `+${row.gd}` : row.gd}</td>
                  <td className="num pts">{row.points}</td>
                  <td className="hide-sm">
                    <span className="form">
                      {row.form.map((f) => (
                        <span key={f.match_id} className={`form-pill ${f.result}`}
                          title={`${t("season.matchday", { md: f.matchday })} · ${f.home ? t("season.vs") : t("season.at")} ${club(f.opponent)} ${f.score}`}>
                          {t(`season.form.${f.result}`)}
                        </span>
                      ))}
                    </span>
                  </td>
                  <td className="hide-md next">
                    {row.next ? (
                      <span title={t("season.matchday", { md: row.next.matchday })}>
                        <span className="muted">{row.next.home ? t("season.vs") : t("season.at")}</span>{" "}
                        {club(row.next.opponent)}
                      </span>
                    ) : "–"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="legend zones">
        {(["ucl", "uel", "uecl", "rel"] as const).map((z) => (
          <span key={z}><span className={`zone-dot zone-${z}`} aria-hidden="true" />{t(`season.table.zones.${z}`)}</span>
        ))}
      </div>
    </div>
  );
}

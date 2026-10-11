import {
  Button,
  DrawerBody,
  DrawerHeader,
  DrawerHeaderTitle,
  OverlayDrawer,
  Spinner,
  Text,
} from "@fluentui/react-components";
import { Dismiss24Regular } from "@fluentui/react-icons";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { League } from "../../api";
import { per90, playerLog, positionGroup, type MatchLine, type SeasonView } from "../../season";
import { ClubName } from "../Nav";

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
      {sub && <span className="stat-sub muted">{sub}</span>}
    </div>
  );
}

export function PlayerDrawer({ league, view, playerId, onClose }: {
  league: League; view: SeasonView; playerId: string | null; onClose: () => void;
}) {
  const { t } = useTranslation();
  const [log, setLog] = useState<MatchLine[] | null>(null);
  const info = playerId ? league.players[playerId] : undefined;
  const totals = view.players.find((p) => p.p === playerId);

  useEffect(() => {
    setLog(null);
    if (!playerId) return;
    let alive = true;
    playerLog(playerId, view.through).then((l) => alive && setLog(l), () => alive && setLog([]));
    return () => { alive = false; };
  }, [playerId, view.through]);

  const gk = info ? positionGroup(info.position) === "GK" : false;
  const club = (id: string) => league.clubs[id]?.short_name ?? id;

  return (
    <OverlayDrawer open={!!playerId} position="end" size="medium" onOpenChange={(_, d) => !d.open && onClose()}>
      <DrawerHeader>
        <DrawerHeaderTitle action={
          <Button appearance="subtle" aria-label={t("season.player.close")} icon={<Dismiss24Regular />} onClick={onClose} />
        }>
          {info?.name ?? playerId}
        </DrawerHeaderTitle>
        {info && (
          <div className="player-meta">
            <span className="shirt big" aria-hidden="true">{info.number}</span>
            <span>{t(`season.position.${info.position}`)}</span>
            <span className="muted">·</span>
            <ClubName league={league} id={info.club_id} />
            <span className="muted">·</span>
            <span>{info.nationality}</span>
          </div>
        )}
      </DrawerHeader>
      <DrawerBody>
        <Text weight="semibold" block className="drawer-section">
          {t("season.player.season", { md: view.through })}
        </Text>
        {!totals ? <Text className="muted">{t("season.player.noMatches")}</Text> : (
          <>
            <div className="stat-tiles">
              <Stat label={t("season.player.apps")} value={totals.apps} sub={t("season.player.starts", { n: totals.st })} />
              <Stat label={t("season.player.minutes")} value={totals.min.toLocaleString()} />
              {gk ? (
                <>
                  <Stat label={t("season.stats.cat.cs")} value={totals.cs} />
                  <Stat label={t("season.stats.cat.sav")} value={totals.sav}
                    sub={`${per90(totals.sav, totals.min).toFixed(1)} ${t("season.player.per90")}`} />
                </>
              ) : (
                <>
                  <Stat label={t("season.stats.cat.g")} value={totals.g}
                    sub={`${per90(totals.g, totals.min).toFixed(2)} ${t("season.player.per90")}`} />
                  <Stat label={t("season.stats.cat.a")} value={totals.a}
                    sub={`${per90(totals.a, totals.min).toFixed(2)} ${t("season.player.per90")}`} />
                  <Stat label={t("season.stats.cat.xg")} value={totals.xg.toFixed(1)}
                    sub={totals.g - totals.xg >= 0 ? `+${(totals.g - totals.xg).toFixed(1)} ${t("season.player.vsXg")}`
                      : `${(totals.g - totals.xg).toFixed(1)} ${t("season.player.vsXg")}`} />
                  <Stat label={t("season.stats.cat.kp")} value={totals.kp} />
                </>
              )}
            </div>
            <div className="stat-grid">
              {[
                [t("season.players.colTitle.sh"), totals.sh],
                [t("season.players.colTitle.sot"), totals.sot],
                [t("season.players.colTitle.pass"), totals.pa ? `${Math.round((totals.pc / totals.pa) * 100)}%` : "–"],
                [t("season.players.colTitle.tkw"), totals.tkw],
                [t("season.players.colTitle.int"), totals.int],
                [t("season.player.recoveries"), totals.rec],
                [t("season.player.cards"), `${totals.yc} / ${totals.rc}`],
              ].map(([label, v]) => (
                <div key={label as string} className="stat-row"><span className="muted">{label}</span><span>{v}</span></div>
              ))}
            </div>
            <Text weight="semibold" block className="drawer-section">{t("season.player.physical")}</Text>
            <div className="stat-tiles">
              <Stat label={t("season.player.distance")} value={`${per90(totals.km, totals.min).toFixed(1)} ${t("season.unit.km")}`} />
              <Stat label={t("season.player.sprints")} value={per90(totals.spr, totals.min).toFixed(0)} />
              <Stat label={t("season.player.topSpeed")} value={`${totals.top.toFixed(1)} ${t("season.unit.kmh")}`} />
            </div>
          </>
        )}
        <Text weight="semibold" block className="drawer-section">{t("season.player.matches")}</Text>
        {log === null ? <Spinner size="tiny" /> : !log.length ? <Text className="muted">{t("season.player.noMatches")}</Text> : (
          <table className="log-table">
            <thead>
              <tr>
                <th className="num" scope="col"><abbr title={t("season.table.matchdayLong")}>{t("season.table.md")}</abbr></th>
                <th className="left" scope="col">{t("season.player.opponent")}</th>
                <th className="num" scope="col">{t("season.player.result")}</th>
                <th className="num" scope="col">{t("season.players.col.min")}</th>
                {gk ? <th className="num" scope="col">{t("season.players.col.sav")}</th> : (
                  <>
                    <th className="num" scope="col">{t("season.players.col.g")}</th>
                    <th className="num" scope="col">{t("season.players.col.a")}</th>
                    <th className="num" scope="col">{t("season.players.col.xg")}</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {[...log].reverse().map((m) => {
                const res = m.gf > m.ga ? "W" : m.gf === m.ga ? "D" : "L";
                return (
                  <tr key={m.match_id}>
                    <td className="num muted">{m.matchday}</td>
                    <td className="left"><span className="muted">{m.home ? t("season.vs") : t("season.at")}</span> {club(m.opponent)}</td>
                    <td className="num"><span className={`form-pill ${res}`}>{t(`season.form.${res}`)}</span> {m.gf}-{m.ga}</td>
                    <td className="num">{m.min}{m.st ? "" : "*"}</td>
                    {gk ? <td className="num">{m.sav ?? 0}</td> : (
                      <>
                        <td className="num">{m.g || "–"}</td>
                        <td className="num">{m.a || "–"}</td>
                        <td className="num">{m.xg ? m.xg.toFixed(2) : "–"}</td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {log && log.some((m) => !m.st) && <Text size={200} className="muted">{t("season.player.subNote")}</Text>}
      </DrawerBody>
    </OverlayDrawer>
  );
}

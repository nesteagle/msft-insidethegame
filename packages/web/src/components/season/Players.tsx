import { Button, SearchBox, Select, Switch, Text, ToggleButton } from "@fluentui/react-components";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { League } from "../../api";
import { setQuery } from "../../router";
import { per90, positionGroup, type PlayerTotals, type PositionGroup, type SeasonView } from "../../season";
import { ClubName } from "../Nav";

type Col = "apps" | "min" | "g" | "a" | "xg" | "sh" | "sot" | "kp" | "pass" | "tkw" | "int" | "sav" | "cs";

const COLS: { key: Col; rate?: boolean; hide?: "sm" | "md" }[] = [
  { key: "apps" }, { key: "min", hide: "sm" }, { key: "g", rate: true }, { key: "a", rate: true },
  { key: "xg", rate: true, hide: "sm" }, { key: "sh", rate: true, hide: "md" }, { key: "sot", rate: true, hide: "md" },
  { key: "kp", rate: true, hide: "md" }, { key: "pass", hide: "md" }, { key: "tkw", rate: true, hide: "md" },
  { key: "int", rate: true, hide: "md" }, { key: "sav", rate: true, hide: "md" }, { key: "cs", hide: "sm" },
];
const GROUPS: PositionGroup[] = ["GK", "DEF", "MID", "FWD"];
const PAGE = 50;
const PER90_MIN = 450;

export const PLAYER_SORTS: string[] = COLS.map((c) => c.key);

function value(p: PlayerTotals, col: Col, rate: boolean): number {
  if (col === "pass") return p.pa ? p.pc / p.pa : 0;
  const v = p[col];
  return rate && COLS.find((c) => c.key === col)?.rate ? per90(v, p.min) : v;
}

function shown(col: Col, v: number, rate: boolean, group: PositionGroup): string {
  if (col === "pass") return v ? `${Math.round(v * 100)}%` : "–";
  if (col === "cs" && (group === "MID" || group === "FWD")) return "–";  // clean sheets count for keepers and defenders
  if (rate && COLS.find((c) => c.key === col)?.rate) return v.toFixed(2);
  if (col === "xg") return v.toFixed(1);
  return v.toLocaleString();
}

export function Players({ league, view, club, group, sort, rate, onPlayer }: {
  league: League; view: SeasonView; club: string; group: PositionGroup | ""; sort: Col; rate: boolean;
  onPlayer: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(PAGE);

  const rows = useMemo(() => {
    const q = search.trim().toLocaleLowerCase();
    return view.players
      .filter((p) => {
        const info = league.players[p.p];
        if (!info) return false;
        if (club && p.c !== club) return false;
        if (group && positionGroup(info.position) !== group) return false;
        if (rate && p.min < PER90_MIN) return false;
        return !q || info.name.toLocaleLowerCase().includes(q) || info.short_name.toLocaleLowerCase().includes(q);
      })
      .filter((p) => sort !== "cs" || ["GK", "DEF"].includes(positionGroup(league.players[p.p].position)))
      .sort((a, b) => value(b, sort, rate) - value(a, sort, rate) || b.min - a.min);
  }, [view, league, club, group, sort, rate, search]);

  const header = (col: (typeof COLS)[number]) => {
    const active = col.key === sort;
    return (
      <th key={col.key} scope="col" className={`num${col.hide ? ` hide-${col.hide}` : ""}`}
        aria-sort={active ? "descending" : "none"}>
        <button type="button" className={`sort-btn${active ? " active" : ""}`} title={t(`season.players.colTitle.${col.key}`)}
          onClick={() => setQuery({ sort: col.key === "g" ? null : col.key })}>
          {t(`season.players.col.${col.key}`)}{active && <span aria-hidden="true"> ↓</span>}
        </button>
      </th>
    );
  };

  return (
    <div className="card players-card">
      <div className="players-filters">
        <SearchBox placeholder={t("season.players.search")} value={search} size="medium"
          onChange={(_, d) => { setSearch(d.value); setLimit(PAGE); }} className="players-search" />
        <Select value={club} onChange={(_, d) => setQuery({ club: d.value || null })} aria-label={t("season.table.club")}>
          <option value="">{t("season.players.allClubs")}</option>
          {Object.entries(league.clubs).sort(([, a], [, b]) => a.name.localeCompare(b.name)).map(([id, c]) => (
            <option key={id} value={id}>{c.name}</option>
          ))}
        </Select>
        <div className="segmented" role="group" aria-label={t("season.players.position")}>
          <ToggleButton size="small" appearance="subtle" checked={!group} onClick={() => setQuery({ pos: null })}>
            {t("season.players.all")}
          </ToggleButton>
          {GROUPS.map((g) => (
            <ToggleButton key={g} size="small" appearance="subtle" checked={group === g}
              onClick={() => setQuery({ pos: g })}>{t(`season.players.pos.${g}`)}</ToggleButton>
          ))}
        </div>
        <Switch checked={rate} label={t("season.players.per90")} onChange={(_, d) => setQuery({ per90: d.checked ? "1" : null })} />
      </div>
      <Text size={200} className="muted players-count">
        {t("season.players.count", { count: rows.length })}{rate ? ` · ${t("season.players.per90Note", { min: PER90_MIN })}` : ""}
      </Text>
      {!rows.length ? <div className="empty-sm"><Text>{t("season.players.none")}</Text></div> : (
        <div className="table-scroll">
          <table className="players-table">
            <thead>
              <tr>
                <th className="num" scope="col">#</th>
                <th className="left" scope="col">{t("season.players.col.player")}</th>
                <th className="left hide-sm" scope="col">{t("season.table.club")}</th>
                {COLS.map(header)}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, limit).map((p, i) => {
                const info = league.players[p.p];
                return (
                  <tr key={p.p} onClick={() => onPlayer(p.p)} className="clickable">
                    <td className="num muted">{i + 1}</td>
                    <td className="left">
                      <button type="button" className="link-btn player-cell" onClick={(e) => { e.stopPropagation(); onPlayer(p.p); }}>
                        <span className="shirt" aria-hidden="true">{info.number}</span>
                        <span className="player-name">{info.name}</span>
                        <span className="pos-chip" title={t(`season.position.${info.position}`)}>{info.position}</span>
                      </button>
                    </td>
                    <td className="left hide-sm"><ClubName league={league} id={p.c} /></td>
                    {COLS.map((c) => (
                      <td key={c.key} className={`num${c.key === sort ? " sorted" : ""}${c.hide ? ` hide-${c.hide}` : ""}`}>
                        {shown(c.key, value(p, c.key, rate), rate, positionGroup(info.position))}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > limit && (
        <div className="show-more">
          <Button onClick={() => setLimit((l) => l + PAGE)}>
            {t("season.players.showMore", { n: Math.min(PAGE, rows.length - limit) })}
          </Button>
        </div>
      )}
    </div>
  );
}

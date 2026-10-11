import {
  Button,
  Field,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Select,
  Spinner,
  Tab,
  TabList,
  Text,
} from "@fluentui/react-components";
import { ChevronLeft20Regular, ChevronRight20Regular } from "@fluentui/react-icons";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useLeague } from "../../hooks";
import { LANGUAGES } from "../../i18n";
import { setQuery, useLocation } from "../../router";
import { useSeason, type PositionGroup, type SeasonView } from "../../season";
import { useViewer } from "../../store";
import { Nav } from "../Nav";
import { LeagueTable } from "./LeagueTable";
import { Leaders, type PlayerStat } from "./Leaders";
import { PlayerDrawer } from "./PlayerDrawer";
import { PLAYER_SORTS, Players } from "./Players";

type SeasonTab = "table" | "stats" | "players";
const TABS: SeasonTab[] = ["table", "stats", "players"];

function MatchdayPicker({ view }: { view: SeasonView }) {
  const { t } = useTranslation();
  const go = (md: number) => setQuery({ md: md === view.default_through ? null : String(md) });
  return (
    <div className="md-picker">
      <Button appearance="subtle" icon={<ChevronLeft20Regular />} aria-label={t("season.prev")}
        disabled={view.through <= 0} onClick={() => go(view.through - 1)} />
      <Field label={t("season.standingsAfter")} size="small" className="md-field">
        <Select size="small" value={String(view.through)} onChange={(_, d) => go(Number(d.value))}>
          <option value="0">{t("season.beforeSeason")}</option>
          {Array.from({ length: view.last_matchday }, (_, i) => i + 1).map((md) => (
            <option key={md} value={md}>
              {t("season.matchday", { md })}{md === view.live_matchday ? ` · ${t("season.liveTag")}` : ""}
            </option>
          ))}
        </Select>
      </Field>
      <Button appearance="subtle" icon={<ChevronRight20Regular />} aria-label={t("season.next")}
        disabled={view.through >= view.last_matchday} onClick={() => go(view.through + 1)} />
    </div>
  );
}

export function SeasonPage() {
  const { t } = useTranslation();
  const { league, error: leagueError } = useLeague();
  const { query } = useLocation();
  const v = useViewer();
  const tab = (TABS.includes(query.get("tab") as SeasonTab) ? query.get("tab") : "table") as SeasonTab;
  const md = query.get("md");
  const state = useSeason(md === null ? null : Number(md));
  const sortParam = query.get("sort") ?? "g";
  const sort = (PLAYER_SORTS.includes(sortParam) ? sortParam : "g") as Parameters<typeof Players>[0]["sort"];
  const playerId = query.get("player");

  useEffect(() => {
    document.title = `${t("nav.season")} · ${t("appTitle")}`;
    return () => { document.title = t("appTitle"); };
  }, [t]);

  const header = (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <div>
          <h1>{t("appTitle")}</h1>
          <Nav />
        </div>
      </div>
      <div className="controls">
        <Field label={t("language")} size="small">
          <Select value={v.language} onChange={(_, d) => v.setLanguage(d.value)} size="small">
            {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </Select>
        </Field>
      </div>
    </header>
  );

  if (leagueError || state.status === "error") {
    return (
      <div className="app">{header}
        <div className="empty"><Text weight="semibold">
          {t("season.error", { error: leagueError ?? (state.status === "error" ? state.error : "") })}
        </Text></div>
      </div>
    );
  }
  if (!league || state.status === "loading" || state.status === "building") {
    return (
      <div className="app">{header}
        <div className="empty"><Spinner label={state.status === "building" ? t("season.building") : t("conn.connecting")} /></div>
      </div>
    );
  }
  const view = state;
  const hiding = view.live_matchday !== null && view.through < view.live_matchday && md === null;
  const showingLive = view.live_matchday !== null && view.through >= view.live_matchday;
  const openPlayer = (id: string | null) => setQuery({ player: id });

  return (
    <div className="app season">
      {header}
      <div className="season-bar">
        <div>
          <Text as="h2" size={600} weight="semibold" block>{t("season.title", { season: view.season })}</Text>
          <Text className="muted" size={200}>
            {view.through === 0 ? t("season.beforeSeason")
              : t("season.subtitle", { md: view.through, played: view.played })}
          </Text>
        </div>
        <MatchdayPicker view={view} />
      </div>
      {hiding && (
        <MessageBar intent="info" className="season-note">
          <MessageBarBody>{t("season.liveHidden", { md: view.live_matchday })}</MessageBarBody>
          <MessageBarActions>
            <Button size="small" onClick={() => setQuery({ md: String(view.last_matchday) })}>{t("season.showAll")}</Button>
          </MessageBarActions>
        </MessageBar>
      )}
      {showingLive && (
        <MessageBar intent="warning" className="season-note">
          <MessageBarBody>{t("season.liveShown", { md: view.live_matchday })}</MessageBarBody>
          <MessageBarActions>
            <Button size="small" onClick={() => setQuery({ md: null })}>{t("season.hideLive")}</Button>
          </MessageBarActions>
        </MessageBar>
      )}
      <TabList selectedValue={tab} onTabSelect={(_, d) => setQuery({ tab: d.value === "table" ? null : String(d.value) })}
        className="season-tabs">
        {TABS.map((x) => <Tab key={x} value={x}>{t(`season.tabs.${x}`)}</Tab>)}
      </TabList>
      {tab === "table" && (
        <LeagueTable league={league} view={view} onClub={(club) => setQuery({ tab: "players", club })} />
      )}
      {tab === "stats" && (
        <Leaders league={league} view={view} onPlayer={openPlayer}
          onSeeAll={(stat: PlayerStat) => {
            const sortKey = PLAYER_SORTS.includes(stat) ? stat : "g";
            setQuery({ tab: "players", sort: sortKey === "g" ? null : sortKey,
                       pos: stat === "cs" || stat === "sav" ? "GK" : null });
          }} />
      )}
      {tab === "players" && (
        <Players league={league} view={view} club={query.get("club") ?? ""} rate={query.get("per90") === "1"}
          group={(query.get("pos") ?? "") as PositionGroup | ""} sort={sort} onPlayer={openPlayer} />
      )}
      <PlayerDrawer league={league} view={view} playerId={playerId} onClose={() => openPlayer(null)} />
    </div>
  );
}

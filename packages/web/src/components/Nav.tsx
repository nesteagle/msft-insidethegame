import { Tab, TabList } from "@fluentui/react-components";
import { useTranslation } from "react-i18next";
import { KIT_HEX, type League } from "../api";
import { navigate, useLocation } from "../router";

/** The app's two pages: the live match and the season (table, stats, players). */
export function Nav() {
  const { t } = useTranslation();
  const { path } = useLocation();
  const page = path.startsWith("/season") ? "season" : "match";
  return (
    <TabList size="small" selectedValue={page} onTabSelect={(_, d) => navigate(d.value === "season" ? "/season" : "/")}
      aria-label={t("nav.label")} className="nav">
      <Tab value="match">{t("nav.match")}</Tab>
      <Tab value="season">{t("nav.season")}</Tab>
    </TabList>
  );
}

/** A club with its kit colours; the short name on narrow screens. */
export function ClubName({ league, id, bold }: { league: League; id: string; bold?: boolean }) {
  const club = league.clubs[id];
  if (!club) return <span>{id}</span>;
  const [main, trim] = club.colors;
  return (
    <span className={`club-name${bold ? " strong" : ""}`}>
      <span className="kit-sm" aria-hidden="true"
        style={{ background: KIT_HEX[main] ?? "#888", borderColor: KIT_HEX[trim] ?? "var(--border)" }} />
      <span className="club-full">{club.name}</span>
      <span className="club-short">{club.short_name}</span>
    </span>
  );
}

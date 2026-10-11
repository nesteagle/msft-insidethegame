import { Tab, TabList } from "@fluentui/react-components";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { View } from "../match/engine";

const LIMIT = 300;

export function Feed({ view }: { view: View }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<"key" | "all">("key");
  const items = view.feed.filter((i) => filter === "all" || i.key).slice(-LIMIT).reverse();
  return (
    <div className="card feed-card">
      <div className="card-head">
        <h2>{t("feed.title")}</h2>
        <TabList size="small" selectedValue={filter} onTabSelect={(_, d) => setFilter(d.value as "key" | "all")}>
          <Tab value="key">{t("feed.key")}</Tab>
          <Tab value="all">{t("feed.all")}</Tab>
        </TabList>
      </div>
      <ol className="feed">
        {items.map(({ e, side, text }) => (
          <li key={e.event_id}>
            <span className="t">{e.clock}</span>
            <span className={`d ${side}`} />
            <span className={e.type === "shot" && e.outcome === "goal" ? "goal" : ""}>
              {e.type === "card" && <i className={`chip ${e.outcome === "yellow" ? "yellow" : "red"}`} />}
              {e.type === "substitution" && <span className="tag">SUB</span>}
              {text}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

import { LightbulbRegular } from "@fluentui/react-icons";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { Trivia } from "../api";

/** The league's FIFA-style codes as ISO regions, for the browser's own country names (England has no region). */
const REGION: Record<string, string> = {
  IRL: "IE", ESP: "ES", MEX: "MX", FRA: "FR", BRA: "BR", POR: "PT", GER: "DE", ITA: "IT", NED: "NL", DEN: "DK",
  NOR: "NO", SWE: "SE", POL: "PL", CRO: "HR", CZE: "CZ", TUR: "TR", USA: "US", GHA: "GH", NGA: "NG",
};

function country(code: string, lang: string, t: TFunction): string {
  if (code === "ENG") return t("trivia.england");
  try {
    return new Intl.DisplayNames([lang], { type: "region" }).of(REGION[code] ?? code) ?? code;
  } catch {
    return code;
  }
}

/**
 * The fact in the viewer's language: the storyteller's own translation when it wrote one, else the fact worded
 * from its key and values, else the English text.
 */
export function triviaText(tr: Trivia, lang: string, t: TFunction): string {
  const base = lang.split("-")[0];
  if (base === "en") return tr.text;
  if (tr.translations[base]) return tr.translations[base];
  const p: Record<string, string | number> = { ...tr.params };
  if (typeof p.nationality === "string") p.country = country(p.nationality, base, t);
  if (typeof p.position_code === "string") {
    const pos = t(`season.position.${p.position_code}`);
    p.position = base === "de" ? pos : pos.toLowerCase(); // German capitalises nouns
  }
  if (typeof p.foot === "string") p.foot = t(`trivia.foot.${p.foot}`);
  if (typeof p.capacity === "number") p.capacity = new Intl.NumberFormat(base).format(p.capacity);
  return t(`trivia.${tr.key}`, { ...p, defaultValue: tr.text });
}

export function TriviaCard({ trivia }: { trivia: Trivia }) {
  const { t, i18n } = useTranslation();
  return (
    <aside className="trivia">
      <LightbulbRegular className="trivia-icon" aria-hidden />
      <div>
        <b className="trivia-title">{t("trivia.title")}</b>
        <p>{triviaText(trivia, i18n.language, t)}</p>
        {trivia.source === "storyteller" && <span className="trivia-by">{t("trivia.byStoryteller")}</span>}
      </div>
    </aside>
  );
}

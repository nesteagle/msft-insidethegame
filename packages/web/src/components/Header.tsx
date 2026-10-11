import { Field, Select } from "@fluentui/react-components";
import { useTranslation } from "react-i18next";
import type { Audience, League, MatchSession, MatchSummary } from "../api";
import { LANGUAGES } from "../i18n";
import { useViewer } from "../store";
import { Nav } from "./Nav";

const AUDIENCES: Audience[] = ["casual", "broadcast", "analyst", "player"];

export function Header({ league, matches, session }: {
  league: League | null; matches: MatchSummary[]; session: MatchSession | null;
}) {
  const { t } = useTranslation();
  const v = useViewer();
  const club = (id: string | null) => (id && league?.clubs[id]?.name) || id || "?";

  // Favourite club and followed player only make sense for the two teams on the pitch.
  const teams = session ? [session.summary.home_id, session.summary.away_id].filter(Boolean) as string[] : [];
  const players = teams.flatMap((clubId) =>
    (session?.team_sheets[clubId]?.lineup.map((l) => l.player_id) ?? [])
      .concat(session?.team_sheets[clubId]?.bench ?? [])
      .map((pid) => ({ pid, clubId })));

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <div>
          <h1>{t("appTitle")}</h1>
          <p className="tagline">{t("tagline")}</p>
          <Nav />
        </div>
      </div>
      <div className="controls">
        <Field label={t("match")} size="small">
          <Select value={v.matchId ?? ""} onChange={(_, d) => v.setMatch(d.value)} size="small">
            {matches.map((m) => (
              <option key={m.match_id} value={m.match_id}>
                {m.match_id} · {club(m.home_id)} {m.home_score}–{m.away_score} {club(m.away_id)}
                {m.finished ? ` · ${t("matchState.ft")}` : m.live ? ` · ${m.clock} ${t("matchState.live")}`
                  : m.live === false ? ` · ${m.clock} ${t("matchState.stalled")}` : ` · ${m.clock}`}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("viewFor")} size="small">
          <Select value={v.audience} onChange={(_, d) => v.setAudience(d.value as Audience)} size="small">
            {AUDIENCES.map((a) => <option key={a} value={a}>{t(`audience.${a}`)}</option>)}
          </Select>
        </Field>
        <Field label={t("language")} size="small">
          <Select value={v.language} onChange={(_, d) => v.setLanguage(d.value)} size="small">
            {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
            {!LANGUAGES.some((l) => l.code === v.language) && <option value={v.language}>{v.language}</option>}
          </Select>
        </Field>
        <Field label={t("club")} size="small">
          <Select value={v.clubId ?? ""} onChange={(_, d) => v.setClub(d.value || null)} size="small"
                  disabled={!teams.length}>
            <option value="">{t("noClub")}</option>
            {teams.map((c) => <option key={c} value={c}>{club(c)}</option>)}
          </Select>
        </Field>
        <Field label={t("player")} size="small">
          <Select value={v.playerId ?? ""} onChange={(_, d) => v.setPlayer(d.value || null)} size="small"
                  disabled={!players.length}>
            <option value="">{t("noPlayer")}</option>
            {teams.map((c) => (
              <optgroup key={c} label={club(c)}>
                {players.filter((p) => p.clubId === c).map(({ pid }) => (
                  <option key={pid} value={pid}>
                    {league?.players[pid]?.number}. {league?.players[pid]?.name ?? pid}
                  </option>
                ))}
              </optgroup>
            ))}
          </Select>
        </Field>
      </div>
    </header>
  );
}

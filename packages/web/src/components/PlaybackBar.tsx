import { Button, ToggleButton } from "@fluentui/react-components";
import { LiveRegular, PauseRegular, PlayRegular } from "@fluentui/react-icons";
import { useTranslation } from "react-i18next";
import type { MatchPlayer, View } from "../match/engine";

const SPEEDS = [1, 5, 10, 30, 60, 120];

export function PlaybackBar({ player, view }: { player: MatchPlayer; view: View }) {
  const { t } = useTranslation();
  const connText = view.conn === "replay" ? t("conn.replay", { speed: view.speed }) : t(`conn.${view.conn}`);
  return (
    <div className="toolbar">
      <Button appearance="primary" icon={view.playing ? <PauseRegular /> : <PlayRegular />}
              onClick={() => (view.playing ? player.pause() : player.play())}>
        {view.playing ? t("pause") : t("play")}
      </Button>
      <ToggleButton icon={<LiveRegular />} checked={view.speed === "live" && view.playing}
                    onClick={() => player.goLive()}>
        {t("goLive")}
      </ToggleButton>
      <div className="segmented" role="group" aria-label={t("speed")}>
        {SPEEDS.map((s) => (
          <Button key={s} size="small" appearance={view.speed === s ? "primary" : "subtle"}
                  aria-pressed={view.speed === s} onClick={() => player.setSpeed(s)}>{s}×</Button>
        ))}
      </div>
      <span className={`conn ${view.conn === "live" ? "live" : ""}`} role="status">
        <span className="conn-dot" />{connText}
      </span>
    </div>
  );
}

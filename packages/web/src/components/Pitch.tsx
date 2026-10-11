import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { League } from "../api";
import type { Banner, MatchPlayer, View } from "../match/engine";
import { BALL } from "../match/sprite";
import { usePlayback } from "../store";

export function Pitch({ player, league, view }: { player: MatchPlayer; league: League; view: View }) {
  const { t } = useTranslation();
  const players = useRef<SVGGElement>(null);
  const ball = useRef<SVGGElement>(null);
  const trails = useRef<SVGGElement>(null);
  const overlay = usePlayback((s) => s.overlay);
  const [banner, setBanner] = useState<Banner | null>(null);

  useEffect(() => {
    if (players.current && ball.current && trails.current) {
      player.attach({ players: players.current, ball: ball.current, trails: trails.current });
    }
  }, [player]);

  useEffect(() => {
    if (!view.banner) return;
    setBanner(view.banner);
    const timer = setTimeout(() => setBanner(null), 2600);
    return () => clearTimeout(timer);
  }, [view.banner]);

  const short = (id: string) => league.clubs[id]?.short_name ?? id;
  const [left, right] = view.homeAttacksRight ? [view.home, view.away] : [view.away, view.home];

  return (
    <div className="card pitch-card">
      <svg className="pitch" viewBox="-4 -3 113 74" role="img" aria-label="Pitch">
        <defs>
          <pattern id="stripes" width="10.5" height="68" patternUnits="userSpaceOnUse">
            <rect width="5.25" height="68" className="stripe" />
          </pattern>
        </defs>
        <rect x="-4" y="-3" width="113" height="74" className="pitch-bg" />
        <rect x="0" y="0" width="105" height="68" fill="url(#stripes)" />
        <g className="lines">
          <rect x="0" y="0" width="105" height="68" />
          <line x1="52.5" y1="0" x2="52.5" y2="68" />
          <circle cx="52.5" cy="34" r="9.15" />
          <circle cx="52.5" cy="34" r="0.35" className="spot" />
          <rect x="0" y="13.84" width="16.5" height="40.32" />
          <rect x="88.5" y="13.84" width="16.5" height="40.32" />
          <rect x="0" y="24.84" width="5.5" height="18.32" />
          <rect x="99.5" y="24.84" width="5.5" height="18.32" />
          <circle cx="11" cy="34" r="0.35" className="spot" />
          <circle cx="94" cy="34" r="0.35" className="spot" />
          <path d="M 16.5 26.69 A 9.15 9.15 0 0 1 16.5 41.31" />
          <path d="M 88.5 26.69 A 9.15 9.15 0 0 0 88.5 41.31" />
          <rect x="-1.8" y="30.34" width="1.8" height="7.32" className="goal" />
          <rect x="105" y="30.34" width="1.8" height="7.32" className="goal" />
        </g>
        <g ref={trails} />
        <g ref={players} />
        <g ref={ball} className="ball">
          <clipPath id="ball-clip"><circle r={BALL.r} /></clipPath>
          <circle r={BALL.r} className="ball-body" />
          <path d={BALL.patches} clipPath="url(#ball-clip)" className="ball-patch" />
          <circle r={BALL.r} className="ball-edge" />
        </g>
      </svg>
      <div className="direction">
        <span>{t("attacks", { team: short(left) })} →</span>
        <span>← {t("attacks", { team: short(right) })}</span>
      </div>
      {banner && (
        <div className="banner" key={banner.id}>
          <strong>{banner.red && <i className="card-chip" />}{banner.title}</strong>
          <span>{banner.sub}</span>
        </div>
      )}
      {overlay && (
        <div className={`lower-third kind-${overlay.kind}`} key={overlay.insight_id} role="status">
          <span className="lt-clock">{overlay.clock}</span>
          <div>
            <strong>{overlay.view.headline}</strong>
            {Object.entries(overlay.stats).length > 0 && (
              <span className="lt-stats">
                {Object.entries(overlay.stats).map(([k, v]) => <span key={k}>{k} <b>{v}</b></span>)}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

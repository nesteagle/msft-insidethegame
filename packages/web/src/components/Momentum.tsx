import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { League } from "../api";
import type { MatchPlayer, View } from "../match/engine";

const H = 140, TOP = 14, BOTTOM = 18;

function roundedBar(x: number, y: number, w: number, h: number, r: number, up: boolean): string {
  r = Math.min(r, h);
  return up
    ? `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`
    : `M${x},${y} V${y + h - r} Q${x},${y + h} ${x + r},${y + h} H${x + w - r} Q${x + w},${y + h} ${x + w},${y + h - r} V${y} Z`;
}

function label(b: number, p1: number | null): string {
  if (p1 === null || b < p1) return b < 45 ? `${b + 1}'` : `45+${b - 44}'`;
  const m2 = b - p1 - 1;
  return m2 < 45 ? `${46 + m2}'` : `90+${m2 - 44}'`;
}

/** Attacking threat per minute for each side. Hover for detail, click to replay from that minute. */
export function Momentum({ player, league, view }: { player: MatchPlayer; league: League; view: View }) {
  const { t } = useTranslation();
  const wrap = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth || 600));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { buckets, p1Buckets } = view;
  const mid = TOP + (H - TOP - BOTTOM) / 2, half = (H - TOP - BOTTOM) / 2;
  const n = Math.max(100, buckets.length + 1);
  const step = W / n, bw = Math.max(1, step - 2);
  const max = Math.max(8, ...buckets.map((b) => Math.max(b.home, b.away)));
  const at = (clientX: number) => {
    const rect = wrap.current!.getBoundingClientRect();
    const i = Math.floor((clientX - rect.left) / step);
    return i >= 0 && i < buckets.length ? i : null;
  };
  const short = (id: string) => league.clubs[id]?.short_name ?? id;

  return (
    <div className="card">
      <div className="card-head">
        <h2>{t("momentum.title")} <span className="sub">{t("momentum.sub")}</span></h2>
        <div className="legend">
          <span><i className="home" />{short(view.home)}</span>
          <span><i className="away" />{short(view.away)}</span>
        </div>
      </div>
      <div className="momentum-wrap" ref={wrap}>
        <svg className="momentum" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("momentum.title")}
             onMouseMove={(e) => setHover(at(e.clientX))} onMouseLeave={() => setHover(null)}
             onClick={(e) => { const i = at(e.clientX); if (i !== null) player.seekT(player.bucketStartT(i)); }}>
          {hover !== null && <rect x={hover * step} y={0} width={step} height={H} className="m-hover" />}
          {p1Buckets !== null && (
            <>
              <line x1={p1Buckets * step + step / 2} x2={p1Buckets * step + step / 2} y1={TOP - 6} y2={H - BOTTOM}
                    className="m-half" />
              <text x={p1Buckets * step + step / 2 + 4} y={TOP} className="m-label">HT</text>
            </>
          )}
          {buckets.map((b, i) => {
            const x = i * step + 1, r = Math.min(2, bw / 2);
            return (
              <g key={i}>
                {b.home > 0 && <path className="m-bar home" d={roundedBar(x, mid - (b.home / max) * half, bw,
                                                                       (b.home / max) * half, r, true)} />}
                {b.away > 0 && <path className="m-bar away" d={roundedBar(x, mid, bw, (b.away / max) * half, r, false)} />}
                {b.goals.map((g, j) => {
                  const cx = x + bw / 2, cy = g.side === "home" ? TOP - 6 : H - BOTTOM + 6;
                  return (
                    <g key={j}>
                      <line x1={cx} x2={cx} y1={mid} y2={cy} className="m-goal" />
                      <circle cx={cx} cy={cy} r={4} className="m-goal-dot" />
                    </g>
                  );
                })}
              </g>
            );
          })}
          <line x1={0} x2={W} y1={mid} y2={mid} className="m-base" />
          <text x={1} y={H - 2} className="m-label">1'</text>
          <text x={45 * step + 1} y={H - 2} className="m-label">45'</text>
          {p1Buckets !== null && <text x={(p1Buckets + 1) * step + 1} y={H - 2} className="m-label">46'</text>}
        </svg>
        {hover !== null && buckets[hover] && (
          <div className="tooltip" style={{ left: Math.min(Math.max(hover * step + step / 2, 90), W - 90), top: 10 }}>
            <div className="tt-head">{t("momentum.replayFrom", { m: label(hover, p1Buckets) })}</div>
            <div className="row"><i className="home" />{short(view.home)}: {t("momentum.threat", { v: buckets[hover].home })}</div>
            <div className="row"><i className="away" />{short(view.away)}: {t("momentum.threat", { v: buckets[hover].away })}</div>
            {buckets[hover].goals.map((g, j) => <div key={j}>{t("momentum.goal", { p: g.scorer, c: g.clock })}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}

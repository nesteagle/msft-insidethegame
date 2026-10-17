import { Button } from "@fluentui/react-components";
import { NextRegular, PauseRegular, PlayRegular } from "@fluentui/react-icons";
import { useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { League, MoveStep, ShotPoint, Visual } from "../api";

/**
 * Charts and animated move replays that the agents attach to insights and chat answers. The data comes from the
 * agents service, computed from the events (never written by a model); this only draws it.
 */
export function Visuals({ visuals, league, showCaptions = true }: {
  visuals: Visual[] | undefined; league: League; showCaptions?: boolean;
}) {
  if (!visuals?.length) return null;
  return (
    <div className="visuals">
      {visuals.map((v, i) => <VisualFigure key={`${v.type}-${i}`} v={v} league={league} showCaption={showCaptions} />)}
    </div>
  );
}

function VisualFigure({ v, league, showCaption }: { v: Visual; league: League; showCaption: boolean }) {
  const { t } = useTranslation();
  const title = v.type === "move" && v.clips.length > 1 ? t("viz.reel") : t(`viz.${v.type}`);
  return (
    <figure className={`viz viz-${v.type}`}>
      <figcaption>
        <span className="viz-title">{title}</span>
        {showCaption && v.caption && <span className="viz-caption">{v.caption}</span>}
      </figcaption>
      {v.type === "move" && <MoveReplay v={v} league={league} />}
      {v.type === "shot_map" && <ShotMap v={v} league={league} />}
      {v.type === "xg_race" && <XgRace v={v} league={league} />}
      {v.type === "momentum" && <MomentumBars v={v} league={league} />}
      {v.type === "compare" && <Compare v={v} league={league} />}
    </figure>
  );
}

// -- shared ----------------------------------------------------------------------------------------------------

type Pt = [number, number];

/** Team-relative coordinates (x 0-100 towards the opponent's goal) to a 105 x 68 pitch. */
const attackingRight = (x: number, y: number): Pt => [x * 1.05, (100 - y) * 0.68];
const attackingLeft = (x: number, y: number): Pt => [(100 - x) * 1.05, y * 0.68];

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
const side = (v: Visual, teamId: string) => (teamId === v.home_id ? "home" : "away");
const clubShort = (league: League, id: string) => league.clubs[id]?.short_name ?? id;
const playerShort = (league: League, id: string | null) => (id ? league.players[id]?.short_name ?? id : "");

function useWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth || fallback));
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, w] as const;
}

function useVisible<T extends Element>() {
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return setVisible(true);
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return [ref, visible] as const;
}

function PitchLines() {
  return (
    <>
      <rect x="-3" y="-3" width="111" height="74" className="pitch-bg" />
      <g className="lines">
        <rect x="0" y="0" width="105" height="68" />
        <line x1="52.5" y1="0" x2="52.5" y2="68" />
        <circle cx="52.5" cy="34" r="9.15" />
        <rect x="0" y="13.84" width="16.5" height="40.32" />
        <rect x="88.5" y="13.84" width="16.5" height="40.32" />
        <rect x="0" y="24.84" width="5.5" height="18.32" />
        <rect x="99.5" y="24.84" width="5.5" height="18.32" />
        <path d="M 16.5 26.69 A 9.15 9.15 0 0 1 16.5 41.31" />
        <path d="M 88.5 26.69 A 9.15 9.15 0 0 0 88.5 41.31" />
        <rect x="-1.8" y="30.34" width="1.8" height="7.32" className="goal" />
        <rect x="105" y="30.34" width="1.8" height="7.32" className="goal" />
      </g>
    </>
  );
}

function Legend({ v, league, extra }: { v: Visual; league: League; extra?: (teamId: string) => string }) {
  return (
    <div className="legend viz-legend">
      {[v.home_id, v.away_id].map((id) => (
        <span key={id}><i className={side(v, id)} />{clubShort(league, id)}{extra ? ` · ${extra(id)}` : ""}</span>
      ))}
    </div>
  );
}

// -- move: the replay, looping like a GIF ----------------------------------------------------------------------

type Seg = { from: Pt; to: Pt; ms: number; step: number | null; kind: string };
const HOLD_MS = 1800; // the finished move stays on screen before the next clip or the loop

function segments(steps: MoveStep[]): Seg[] {
  const out: Seg[] = [];
  let prev: Pt | null = null;
  steps.forEach((s, i) => {
    const a = attackingRight(s.x, s.y);
    const shot = s.type === "shot";
    const ex = s.end_x ?? (shot ? (s.outcome === "blocked" ? Math.min(100, s.x + 4) : 100) : s.x);
    const ey = s.end_y ?? (shot && s.outcome !== "blocked" ? 50 : s.y);
    const b = attackingRight(ex, ey);
    if (prev && Math.hypot(prev[0] - a[0], prev[1] - a[1]) > 0.5) {
      out.push({ from: prev, to: a, ms: 140, step: null, kind: "gap" }); // the receiver's first touch
    }
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const ms = shot ? 520 : s.type === "carry" ? 420 + len * 14 : s.type === "take_on" ? 380 : 320 + len * 9;
    out.push({ from: a, to: b, ms, step: i, kind: s.type });
    prev = b;
  });
  return out;
}

const lerp = (a: Pt, b: Pt, f: number): Pt => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
const ease = (f: number) => (f < 0.5 ? 2 * f * f : 1 - (-2 * f + 2) ** 2 / 2);

function MoveReplay({ v, league }: { v: Visual; league: League }) {
  const { t } = useTranslation();
  const clips = v.clips;
  const still = useMemo(reducedMotion, []);
  const [wrap, visible] = useVisible<HTMLDivElement>();
  const [playing, setPlaying] = useState(!still);
  const clock = useRef({ clip: 0, t: still ? Number.POSITIVE_INFINITY : 0 });
  const [, redraw] = useReducer((n: number) => n + 1, 0);
  const all = useMemo(() => clips.map((c) => segments(c.steps)), [clips]);
  const totals = useMemo(() => all.map((s) => s.reduce((a, x) => a + x.ms, 0)), [all]);

  useEffect(() => {
    if (!playing || !visible) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const c = clock.current;
      if (c.clip >= clips.length) c.clip = 0; // the visual was replaced by one with fewer clips
      c.t = (Number.isFinite(c.t) ? c.t : 0) + (now - last);
      last = now;
      if (c.t >= totals[c.clip] + HOLD_MS) {
        c.clip = (c.clip + 1) % clips.length;
        c.t = 0;
      }
      redraw();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, visible, totals, clips.length]);

  const ci = Math.min(clock.current.clip, clips.length - 1);
  const segs = all[ci], steps = clips[ci].steps;
  const elapsed = Math.min(clock.current.t, totals[ci]);
  let k = segs.length, f = 1, acc = 0;
  for (let i = 0; i < segs.length; i++) {
    if (elapsed < acc + segs[i].ms) { k = i; f = (elapsed - acc) / segs[i].ms; break; }
    acc += segs[i].ms;
  }
  const cur = segs[k];
  const ball = cur ? lerp(cur.from, cur.to, cur.kind === "gap" ? f : ease(f)) : segs[segs.length - 1].to;
  const activeStep = cur?.step ?? segs.slice(0, k + 1).reverse().find((s) => s.step !== null)?.step ?? 0;
  const done = !cur;
  const last = steps[steps.length - 1];
  const team = side(v, last.team_id);
  const outcome = last.type === "shot" && last.outcome ? t(`viz.outcome.${last.outcome}`, { defaultValue: "" }) : "";
  const active = steps[activeStep];
  const [nx, ny] = attackingRight(active.x, active.y);

  const next = () => { clock.current = { clip: (ci + 1) % clips.length, t: 0 }; redraw(); };
  const toggle = () => {
    if (!playing && done) clock.current.t = 0; // play again from the start of the move
    setPlaying(!playing);
  };

  return (
    <div className="viz-pitch" ref={wrap}>
      <svg viewBox="-3 -3 111 74" role="img"
           aria-label={`${clips[ci].label}: ${steps.map((s) => `${playerShort(league, s.player_id)} ${s.type}`).join(", ")}`}>
        <PitchLines />
        <g className={`mv ${team}`}>
          {segs.slice(0, k).map((s, i) => s.step !== null && (
            <line key={i} x1={s.from[0]} y1={s.from[1]} x2={s.to[0]} y2={s.to[1]} className={`mv-trail mv-${s.kind}`} />
          ))}
          {cur && cur.step !== null && (
            <line x1={cur.from[0]} y1={cur.from[1]} x2={ball[0]} y2={ball[1]} className={`mv-trail mv-${cur.kind}`} />
          )}
          {steps.slice(0, activeStep + 1).map((s, i) => {
            const [x, y] = attackingRight(s.x, s.y);
            return <circle key={i} cx={x} cy={y} r={i === activeStep ? 1.9 : 1.2} className="mv-player" />;
          })}
          <text x={nx} y={ny - 3} className="mv-name" textAnchor="middle">{playerShort(league, active.player_id)}</text>
          <circle cx={ball[0]} cy={ball[1]} r={1} className="mv-ball" />
          {done && last.outcome === "goal" && <rect x="104.2" y="29.5" width="2.6" height="9" className="mv-goal-flash" />}
        </g>
      </svg>
      <div className="viz-pitch-bar">
        <span className="viz-clip">
          <b>{clips[ci].label}</b>
          {clips.length > 1 && <span className="muted"> · {ci + 1}/{clips.length}</span>}
          {done && outcome && <span className={`viz-outcome${last.outcome === "goal" ? " goal" : ""}`}>{outcome}</span>}
        </span>
        <span className="viz-buttons">
          {clips.length > 1 && (
            <Button size="small" appearance="subtle" icon={<NextRegular />} onClick={next}
                    aria-label={t("viz.next")} title={t("viz.next")} />
          )}
          <Button size="small" appearance="subtle" icon={playing ? <PauseRegular /> : <PlayRegular />} onClick={toggle}
                  aria-label={playing ? t("viz.pause") : t("viz.play")} title={playing ? t("viz.pause") : t("viz.play")} />
        </span>
      </div>
    </div>
  );
}

// -- shot map --------------------------------------------------------------------------------------------------

function shotTitle(s: ShotPoint, league: League, outcome: string) {
  return `${s.clock} ${playerShort(league, s.player_id)} · xG ${s.xg.toFixed(2)}${outcome ? ` · ${outcome}` : ""}`;
}

function ShotMap({ v, league }: { v: Visual; league: League }) {
  const { t } = useTranslation();
  const sorted = [...v.shots].sort((a, b) => b.xg - a.xg); // big circles first, small ones drawn on top
  const totals = (id: string) => {
    const mine = v.shots.filter((s) => s.team_id === id);
    return t("viz.shots", { n: mine.length, xg: mine.reduce((a, s) => a + s.xg, 0).toFixed(2) });
  };
  return (
    <>
      <div className="viz-pitch">
        <svg viewBox="-3 -3 111 74" role="img" aria-label={`${t("viz.shot_map")}: ${totals(v.home_id)}; ${totals(v.away_id)}`}>
          <PitchLines />
          {sorted.map((s) => {
            const home = s.team_id === v.home_id;
            const [x, y] = home ? attackingRight(s.x, s.y) : attackingLeft(s.x, s.y);
            const goal = s.outcome === "goal";
            return (
              <circle key={s.seq} cx={x} cy={y} r={0.9 + Math.sqrt(s.xg) * 3.2}
                      className={`shot ${home ? "home" : "away"}${goal ? " goal" : ""}`}>
                <title>{shotTitle(s, league, s.outcome ? t(`viz.outcome.${s.outcome}`, { defaultValue: "" }) : "")}</title>
              </circle>
            );
          })}
        </svg>
      </div>
      <Legend v={v} league={league} extra={totals} />
      <div className="viz-note">{t("viz.size")} · {t("viz.filled")}</div>
    </>
  );
}

/** A continuous minute (ShotPoint.t, MinuteThreat.minute) as the match clock shows it: 45+2', 46', 90+3'. */
function minuteLabel(m: number, halfTime: number | null): string {
  if (halfTime === null || m < halfTime) {
    const i = Math.floor(m);
    return i < 45 ? `${i + 1}'` : `45+${i - 44}'`;
  }
  const j = Math.floor(m - halfTime);
  return j < 45 ? `${46 + j}'` : `90+${j - 44}'`;
}

// -- xG race ---------------------------------------------------------------------------------------------------

const XH = 150, XL = 30, XR = 40, XT = 12, XB = 20;

function XgRace({ v, league }: { v: Visual; league: League }) {
  const { t } = useTranslation();
  const [wrap, W] = useWidth(320);
  const [hover, setHover] = useState<number | null>(null);
  const shots = useMemo(() => [...v.shots].sort((a, b) => a.t - b.t), [v.shots]);
  const now = Math.max(v.now ?? 0, ...shots.map((s) => s.t), 1);
  const end = Math.max(now, v.half_time !== null ? v.half_time + 45 : 90);
  const total = (id: string) => shots.filter((s) => s.team_id === id).reduce((a, s) => a + s.xg, 0);
  const top = Math.max(0.5, total(v.home_id), total(v.away_id));
  const tick = top <= 2 ? 0.5 : 1;
  const maxY = Math.ceil(top / tick) * tick; // the top gridline is the top of the plot
  const sx = (m: number) => XL + (m / end) * (W - XL - XR);
  const sy = (val: number) => XT + (1 - val / maxY) * (XH - XT - XB);
  const cum = (id: string, upTo: number) => shots.filter((s) => s.team_id === id && s.t <= upTo).reduce((a, s) => a + s.xg, 0);
  const label = (m: number) => minuteLabel(m, v.half_time);

  function path(id: string) {
    let y = 0;
    let d = `M${sx(0)},${sy(0)}`;
    for (const s of shots.filter((x) => x.team_id === id)) {
      d += ` H${sx(s.t)}`;
      y += s.xg;
      d += ` V${sy(y)}`;
    }
    return `${d} H${sx(now)}`;
  }

  const ends = [v.home_id, v.away_id].map((id) => ({ id, y: sy(total(id)) }));
  if (Math.abs(ends[0].y - ends[1].y) < 12) { // keep the two end labels apart, each on its own side of the other
    const [hi, lo] = ends[0].y <= ends[1].y ? [ends[0], ends[1]] : [ends[1], ends[0]];
    const mid = (hi.y + lo.y) / 2;
    hi.y = mid - 6;
    lo.y = mid + 6;
  }
  const at = (clientX: number) => {
    const r = wrap.current!.getBoundingClientRect();
    const m = ((clientX - r.left - XL) / (W - XL - XR)) * end;
    return m >= 0 && m <= now ? m : null;
  };

  return (
    <>
      <Legend v={v} league={league} />
      <div className="viz-chart" ref={wrap}>
        <svg viewBox={`0 0 ${W} ${XH}`} role="img"
             aria-label={`${t("viz.xg_race")}: ${clubShort(league, v.home_id)} ${total(v.home_id).toFixed(2)}, ${clubShort(league, v.away_id)} ${total(v.away_id).toFixed(2)}`}
             onMouseMove={(e) => setHover(at(e.clientX))} onMouseLeave={() => setHover(null)}>
          {Array.from({ length: Math.round(maxY / tick) + 1 }, (_, i) => i * tick).map((y) => (
            <g key={y}>
              <line x1={XL} x2={W - XR} y1={sy(y)} y2={sy(y)} className="viz-grid" />
              <text x={XL - 6} y={sy(y) + 3} className="viz-tick" textAnchor="end">{y.toFixed(1)}</text>
            </g>
          ))}
          {v.half_time !== null && (
            <>
              <line x1={sx(v.half_time)} x2={sx(v.half_time)} y1={XT - 4} y2={XH - XB} className="m-half" />
              <text x={sx(v.half_time) + 3} y={XT} className="viz-tick">HT</text>
            </>
          )}
          <text x={XL} y={XH - 4} className="viz-tick">1'</text>
          <text x={W - XR} y={XH - 4} className="viz-tick" textAnchor="end">{label(now)}</text>
          {hover !== null && <line x1={sx(hover)} x2={sx(hover)} y1={XT} y2={XH - XB} className="viz-cross" />}
          {[v.home_id, v.away_id].map((id) => <path key={id} d={path(id)} className={`xr-line ${side(v, id)}`} />)}
          {shots.filter((s) => s.outcome === "goal").map((s) => (
            <circle key={s.seq} cx={sx(s.t)} cy={sy(cum(s.team_id, s.t))} r={4} className={`xr-goal ${side(v, s.team_id)}`}>
              <title>{`${t("viz.outcome.goal")} ${s.clock} ${playerShort(league, s.player_id)}`}</title>
            </circle>
          ))}
          {ends.map(({ id, y }) => (
            <text key={id} x={sx(now) + 5} y={y + 4} className="viz-end">{total(id).toFixed(2)}</text>
          ))}
        </svg>
        {hover !== null && (
          <div className="tooltip" style={{ left: Math.min(Math.max(sx(hover), 80), W - 80), top: 4 }}>
            <div className="tt-head">{label(hover)}</div>
            {[v.home_id, v.away_id].map((id) => (
              <div key={id} className="row"><i className={side(v, id)} />{clubShort(league, id)}: {cum(id, hover).toFixed(2)} xG</div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

// -- momentum --------------------------------------------------------------------------------------------------

const MH = 84;

function MomentumBars({ v, league }: { v: Visual; league: League }) {
  const { t } = useTranslation();
  const [wrap, W] = useWidth(320);
  const n = Math.max(v.threat.length, 30);
  const step = W / n, bw = Math.max(1, step - 2), mid = MH / 2, half = MH / 2 - 4;
  const max = Math.max(4, ...v.threat.map((m) => Math.max(m.home, m.away)));
  return (
    <>
      <Legend v={v} league={league} />
      <div className="viz-chart" ref={wrap}>
        <svg viewBox={`0 0 ${W} ${MH}`} role="img" aria-label={t("viz.momentum")}>
          {v.threat.map((m, i) => (
            <g key={m.minute}>
              <title>{`${minuteLabel(m.minute, v.half_time)} · ${clubShort(league, v.home_id)} ${m.home} · ${clubShort(league, v.away_id)} ${m.away}`}</title>
              <rect x={i * step} y={0} width={step} height={MH} className="viz-hit" />
              {m.home > 0 && <rect x={i * step + 1} y={mid - (m.home / max) * half} width={bw} height={(m.home / max) * half}
                                   rx={Math.min(2, bw / 2)} className="m-bar home" />}
              {m.away > 0 && <rect x={i * step + 1} y={mid} width={bw} height={(m.away / max) * half}
                                   rx={Math.min(2, bw / 2)} className="m-bar away" />}
            </g>
          ))}
          {v.half_time !== null && (
            <line x1={v.half_time * step} x2={v.half_time * step} y1={0} y2={MH} className="m-half" />
          )}
          <line x1={0} x2={W} y1={mid} y2={mid} className="m-base" />
        </svg>
      </div>
    </>
  );
}

// -- compare ---------------------------------------------------------------------------------------------------

function fmt(key: string, value: number) {
  if (key === "possession" || key === "pass_accuracy") return `${Math.round(value * 100)}%`;
  if (key === "xg") return value.toFixed(2);
  return `${Math.round(value)}`;
}

function Compare({ v, league }: { v: Visual; league: League }) {
  const { t } = useTranslation();
  const rows = v.rows.filter((r) => r.home || r.away);
  return (
    <>
      <Legend v={v} league={league} />
      <table className="cmp">
        <tbody>
          {rows.map((r) => {
            const sum = r.home + r.away || 1;
            return (
              <tr key={r.key}>
                <td className="cmp-v">{fmt(r.key, r.home)}</td>
                <td className="cmp-bar">
                  <div className="cmp-label">{t(`viz.rows.${r.key}`, { defaultValue: r.key })}</div>
                  <div className="cmp-track">
                    <span className="cmp-half home"><i className="home" style={{ width: `${(r.home / sum) * 100}%` }} /></span>
                    <span className="cmp-half away"><i className="away" style={{ width: `${(r.away / sum) * 100}%` }} /></span>
                  </div>
                </td>
                <td className="cmp-v">{fmt(r.key, r.away)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

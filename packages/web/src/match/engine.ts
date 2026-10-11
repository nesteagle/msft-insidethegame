/*
 * The match player: follows the agents' event relay and draws the pitch.
 *
 * Ported from the original viewer (packages/generator/.../viewer.js). Events arrive over Server-Sent Events from
 * /api/matches/{id}/events. Each connection starts with a `reset`, then everything before the requested point
 * arrives as catch-up (applied instantly, no animation), then events stream at the chosen speed, or as they
 * happen when following live. Live events wait in a short buffer and are played by a requestAnimationFrame loop
 * on a continuous match clock, so the ball travels each action and players glide instead of jumping.
 *
 * React never drives the 60 fps loop: the pitch is drawn straight into SVG layers, and React components read a
 * throttled snapshot (`view`) for the scoreboard, stats, momentum and feed.
 *
 * Coordinates: every event is in the acting team's frame (x 0->100 towards the opponent's goal, y 0->100 from
 * that team's right touchline to its left). The home team attacks right in the first half.
 */

import type { League, MatchEvent, MatchSession } from "../api";
import i18n from "../i18n";
import { BALL, SHIRT_Y, playerSprite } from "./sprite";

const SVGNS = "http://www.w3.org/2000/svg";
const KEY_TYPES = new Set(["shot", "card", "substitution", "period_start", "period_end", "tactical_change"]);
// Off-ball context (pressing, who has the ball): for analysis, not for the feed or the ball's path.
const CONTEXT_TYPES = new Set(["pressure", "possession_change"]);
const FEED_LIMIT = 400;

const BUFFER_MATCH_S = 4; // playout delay in match seconds
const BUFFER_MS: [number, number] = [250, 4000]; // ...clamped to this many real milliseconds
const PLAYER_SMOOTH_S = 0.9; // player spring time in match seconds
const PLAYER_SMOOTH_MIN_REAL = 0.1; // never snappier than this in real seconds
const PLAYER_MAX_SPEED = 9; // metres per match second (a sprint)
const BALL_TAU_REAL = 0.035; // light smoothing on the ball
const RESTART_MOVE_S = 2.5; // during a stoppage the ball moves to the restart in its last seconds

export type Side = "home" | "away";
export type Speed = number | "live";
export type Conn = "connecting" | "live" | "replay" | "paused" | "reconnecting" | "ended";
export type Status = "prematch" | "h1" | "ht" | "h2" | "ft";

export interface Stats {
  passes: number; completed: number; shots: number; onTarget: number; xg: number; corners: number;
  fouls: number; yellows: number; reds: number; offsides: number;
}
export interface FeedItem { e: MatchEvent; key: boolean; side: Side | "none"; text: string }
export interface Bucket { home: number; away: number; goals: { side: Side; scorer: string; clock: string }[] }
export interface Banner { id: number; title: string; sub: string; red: boolean }

export interface View {
  home: string;
  away: string;
  score: [number, number];
  scoreFlash: number;
  clock: string;
  status: Status;
  period: number;
  stats: { home: Stats; away: Stats };
  feed: FeedItem[];
  buckets: Bucket[];
  p1Buckets: number | null;
  appliedSeq: number;
  appliedT: number;
  playing: boolean;
  conn: Conn;
  speed: Speed;
  finished: boolean;
  banner: Banner | null;
  homeAttacksRight: boolean;
  halftimeS: number;
}

interface OnPitch { role: string; bx: number; by: number; booked: boolean }
interface Team { side: Side; clubId: string; onPitch: Map<string, OnPitch>; stats: Stats }
interface BallEvent { t: number; type: string; start: [number, number]; end: [number, number]; travel: number }
interface MatchState {
  teams: Record<string, Team>;
  home: string;
  away: string;
  score: [number, number];
  clock: string;
  period: number;
  status: Status;
  feed: FeedItem[];
  buckets: Bucket[];
  p1Buckets: number | null;
  ballEvent: BallEvent | null;
  ballRest: [number, number];
  overrides: Map<string, "ball" | [number, number]>;
  actor: string | null;
}

export interface Layers { players: SVGGElement; ball: SVGGElement; trails: SVGGElement }

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lerp = (a: [number, number], b: [number, number], k: number): [number, number] =>
  [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
const easeOut = (k: number) => 1 - (1 - k) * (1 - k);
const svg = <K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}) => {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
};
/* The frame loop runs 60 times a second. Writing an unchanged value still makes the browser repaint the whole SVG,
 * which wastes power and, with a floating layer such as a tooltip on screen, can show as a blank frame. */
function setIfChanged(el: Element, name: string, value: string): void {
  if (el.getAttribute(name) !== value) el.setAttribute(name, value);
}
function toggleIfChanged(el: Element, cls: string, on: boolean): void {
  if (el.classList.contains(cls) !== on) el.classList.toggle(cls, on);
}

const emptyStats = (): Stats => ({ passes: 0, completed: 0, shots: 0, onTarget: 0, xg: 0, corners: 0, fouls: 0,
                                   yellows: 0, reds: 0, offsides: 0 });

/** Simple attacking-threat score per event, summed per minute for the momentum strip. */
function threat(e: MatchEvent): number {
  const inBox = (x: number | null, y: number | null) => x != null && y != null && x >= 84 && y >= 20 && y <= 80;
  if (e.type === "shot") return 4;
  if (e.type === "pass" && e.outcome === "complete" && (e.end_x ?? 0) >= 66) return inBox(e.end_x, e.end_y) ? 3 : 1;
  if (e.type === "carry" && inBox(e.end_x, e.end_y)) return 2;
  if (e.type === "take_on" && e.outcome === "complete" && (e.x ?? 0) >= 60) return 1;
  return 0;
}

/** Critically damped spring towards `goal`: eases in and out, never overshoots, caps speed. */
function smoothDamp(pos: [number, number], goal: [number, number], vel: [number, number], smoothTime: number,
                    dt: number, maxSpeed: number): [number, number] {
  if (dt <= 0) return pos;
  const omega = 2 / smoothTime;
  const x = omega * dt;
  const decay = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
  let cx = pos[0] - goal[0], cy = pos[1] - goal[1];
  const maxChange = maxSpeed * smoothTime;
  const len = Math.hypot(cx, cy);
  if (len > maxChange) { cx *= maxChange / len; cy *= maxChange / len; }
  const tx = (vel[0] + omega * cx) * dt, ty = (vel[1] + omega * cy) * dt;
  vel[0] = (vel[0] - omega * tx) * decay;
  vel[1] = (vel[1] - omega * ty) * decay;
  let ox = pos[0] - cx + (cx + tx) * decay, oy = pos[1] - cy + (cy + ty) * decay;
  if ((goal[0] - pos[0]) * (ox - goal[0]) + (goal[1] - pos[1]) * (oy - goal[1]) > 0) {
    ox = goal[0]; oy = goal[1];
    vel[0] = vel[1] = 0;
  }
  return [ox, oy];
}

/** Match seconds the ball takes to complete an action. */
function travelTime(e: MatchEvent, start: [number, number], end: [number, number]): number {
  const d = Math.hypot(end[0] - start[0], end[1] - start[1]);
  switch (e.type) {
    case "shot": return 0.15 + d / 28;
    case "pass":
      if (e.qualifiers.cross) return 1.3;
      return e.qualifiers.long_ball ? 0.3 + d / 20 : 0.2 + d / 15;
    case "clearance": return 0.2 + d / 22;
    case "carry": return Math.max(0.5, d / 5);
    default: return 0.6;
  }
}

export class MatchPlayer {
  private league: League;
  private session: MatchSession;
  private matchId: string;
  private layers: Layers | null = null;
  private m: MatchState;
  private source: EventSource | null = null;
  private speed: Speed = "live";
  private playing = false;
  private finished = false;
  private catchingUp = false;
  private conn: Conn = "connecting";
  private pending: { t: number; e: MatchEvent }[] = [];
  private anchor: { t: number; now: number } | null = null;
  private playT = 0;
  private appliedT = 0;
  private appliedSeq = 0;
  private streamEnded = false;
  private snap = true;
  private disp = new Map<string, { pos: [number, number]; vel: [number, number] }>();
  private playerEls = new Map<string, SVGGElement>();
  private ballDisp: [number, number] | null = null;
  private ballSpin = 0;
  private liveRate = 10; // estimated upstream speed while following live
  private lastArrival: { t: number; now: number } | null = null;
  private seqT = new Map<number, number>();
  private banner: Banner | null = null;
  private bannerId = 0;
  private scoreFlash = 0;
  private raf = 0;
  private lastFrame = 0;
  private notifyTimer: number | null = null;
  private snapshot: View;
  private listeners = new Set<() => void>();
  private reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  focusPlayer: string | null = null;

  constructor(league: League, session: MatchSession, matchId: string) {
    this.league = league;
    this.session = session;
    this.matchId = matchId;
    this.m = this.fresh();
    this.snapshot = this.buildView();
  }

  /* ---------------------------------------------------------------- public API */

  attach(layers: Layers): void {
    this.layers = layers;
    this.syncPlayers();
    this.snap = true;
    if (!this.raf) this.raf = requestAnimationFrame(this.frame);
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getView = (): View => this.snapshot;

  /** Follow the live edge: everything so far as catch-up, then events as they happen. */
  goLive(): void {
    this.connect("live", "live");
  }

  play(speed?: Speed): void {
    const s = speed ?? this.speed;
    if (s === "live") return this.goLive();
    this.connect(this.finished ? 0 : this.appliedT, s);
  }

  pause(): void {
    this.disconnect();
    this.playing = false;
    this.conn = "paused";
    this.notify(true);
  }

  setSpeed(speed: Speed): void {
    this.speed = speed;
    if (speed === "live") this.goLive();
    else if (this.playing) this.connect(this.appliedT, speed);
    else this.notify(true);
  }

  /** Replay from match-clock time `t` (seconds, half-time included). */
  seekT(t: number): void {
    this.connect(Math.max(0, t), this.speed === "live" ? 10 : this.speed);
  }

  /** Replay from a few seconds before an event, to show the build-up. Returns the clock label, or null. */
  seekSeq(seq: number, leadS = 10): string | null {
    const t = this.seqT.get(seq);
    if (t === undefined) return null;
    this.seekT(t - leadS);
    const e = this.m.feed.find((f) => f.e.seq === seq)?.e;
    return e?.clock ?? null;
  }

  bucketStartT(b: number): number {
    const p1 = this.m.p1Buckets;
    if (p1 === null || b < p1) return b * 60;
    return p1 * 60 + this.session.halftime_s + (b - p1 - 1) * 60;
  }

  destroy(): void {
    this.disconnect();
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.notifyTimer) clearTimeout(this.notifyTimer);
    for (const el of this.playerEls.values()) el.remove();
    this.playerEls.clear();
    this.layers?.trails.replaceChildren();
    this.listeners.clear();
  }

  /* ---------------------------------------------------------------- state */

  private fresh(): MatchState {
    const s = this.session.summary;
    const home = s.home_id ?? "", away = s.away_id ?? "";
    const teams: Record<string, Team> = {};
    for (const [side, clubId] of [["home", home], ["away", away]] as [Side, string][]) {
      const team: Team = { side, clubId, onPitch: new Map(), stats: emptyStats() };
      for (const l of this.session.team_sheets[clubId]?.lineup ?? []) {
        team.onPitch.set(l.player_id, { role: l.role, bx: l.x, by: l.y, booked: false });
      }
      teams[clubId] = team;
    }
    return {
      teams, home, away, score: [0, 0], clock: "0'", period: 1, status: "prematch", feed: [], buckets: [],
      p1Buckets: null, ballEvent: null, ballRest: [52.5, 34], overrides: new Map(), actor: null,
    };
  }

  private name(id: string | null): string {
    return (id && this.league.players[id]?.short_name) || "—";
  }

  private clubName(id: string): string {
    return this.league.clubs[id]?.name ?? id;
  }

  private attacksRight(clubId: string, period: number): boolean {
    return (clubId === this.m.home) !== (period === 2);
  }

  private toScreen(x: number, y: number, right: boolean): [number, number] {
    return right ? [x * 1.05, (100 - y) * 0.68] : [(100 - x) * 1.05, y * 0.68];
  }

  private bucketOf(e: MatchEvent): number {
    const minute = Math.floor(e.elapsed_s / 60);
    return e.period === 1 ? minute : (this.m.p1Buckets ?? 46) + 1 + minute;
  }

  private describe(e: MatchEvent): string {
    const t = i18n.t.bind(i18n);
    const p = this.name(e.player_id), r = this.name(e.related_player_id);
    const speed = Number(e.qualifiers.speed_kmh ?? 0);
    const xg = e.xg != null ? ` (xG ${e.xg.toFixed(2)}${speed >= 95 && e.body_part !== "head" ? ` · ${Math.round(speed)} km/h` : ""})` : "";
    switch (e.type) {
      case "pass":
        if (e.qualifiers.cross) return t(e.outcome === "complete" ? "ev.crossTo" : "ev.crossCut", { p, r });
        if (e.outcome === "offside") return t("ev.offside", { r });
        if (e.outcome === "complete") {
          return e.set_piece !== "open_play"
            ? t("ev.setPieceTo", { p, r, sp: t(`ev.setPiece.${e.set_piece}`) })
            : t("ev.finds", { p, r });
        }
        return t("ev.astray", { p });
      case "carry": return t("ev.carries", { p });
      case "take_on":
        if (e.outcome === "complete") return t("ev.beats", { p, r });
        if (e.outcome === "fouled") return t("ev.fouled", { p });
        return t("ev.stopped", { p, r });
      case "shot": {
        const how = e.set_piece === "penalty" ? t("ev.pen") : e.body_part === "head" ? t("ev.header") : "";
        const key = { goal: "goal", saved: "saved", blocked: "blocked", post: "post", off_target: "wide" }[
          e.outcome ?? ""] ?? "shoots";
        return t(`ev.${key}`, { p, how, xg });
      }
      case "save": return t(e.outcome === "claimed" ? "ev.claims" : "ev.save", { p });
      case "block": return t("ev.blocks", { p, r });
      case "tackle": return t(e.outcome === "won" ? "ev.wins" : "ev.misses", { p, r });
      case "interception": return t("ev.intercepts", { p });
      case "clearance": return t("ev.clears", { p });
      case "ball_recovery": return t("ev.recovers", { p });
      case "foul": return r !== "—" ? t("ev.foulOn", { p, r }) : t("ev.foul", { p });
      case "card":
        return t(e.outcome === "yellow" ? "ev.yellow" : e.outcome === "second_yellow" ? "ev.secondYellow" : "ev.red",
                 { p });
      case "substitution": return t("ev.sub", { p, r });
      case "tactical_change":
        return e.qualifiers.kind === "formation"
          ? t("ev.formation", { team: this.clubName(e.team_id), f: e.qualifiers.formation })
          : t("ev.role", { p, role: e.qualifiers.role });
      case "period_start": return t(e.period === 1 ? "ev.kickoff" : "ev.secondHalf");
      case "period_end": return t(e.period === 1 ? "ev.halfTime" : "ev.fullTime");
      default: return e.type;
    }
  }

  /** Re-render feed text in the current interface language. */
  relabel(): void {
    for (const item of this.m.feed) item.text = this.describe(item.e);
    this.notify(true);
  }

  private apply(e: MatchEvent, live: boolean, t: number): void {
    const m = this.m;
    this.seqT.set(e.seq, t);
    this.appliedSeq = e.seq;
    this.appliedT = t;
    // period markers arrive once per team; keep only the home copy
    if ((e.type === "period_start" || e.type === "period_end") && e.team_id !== m.home) return;
    if (CONTEXT_TYPES.has(e.type)) return;
    m.period = e.period;
    m.clock = e.clock;
    const scored = e.home_score + e.away_score > m.score[0] + m.score[1];
    m.score = [e.home_score, e.away_score];
    const team = m.teams[e.team_id];
    if (!team) return;
    const s = team.stats;

    switch (e.type) {
      case "pass":
        s.passes++;
        if (e.outcome === "complete") s.completed++;
        if (e.outcome === "offside") s.offsides++;
        if (e.set_piece === "corner") s.corners++;
        break;
      case "shot":
        s.shots++;
        s.xg += e.xg || 0;
        if (e.outcome === "goal" || e.outcome === "saved") s.onTarget++;
        break;
      case "foul": s.fouls++; break;
      case "card": {
        const p = e.player_id ? team.onPitch.get(e.player_id) : undefined;
        if (e.outcome === "yellow") { s.yellows++; if (p) p.booked = true; }
        else { s.reds++; if (e.player_id) team.onPitch.delete(e.player_id); }
        break;
      }
      case "substitution": {
        const off = e.player_id ? team.onPitch.get(e.player_id) : undefined;
        if (e.player_id) team.onPitch.delete(e.player_id);
        if (off && e.related_player_id) team.onPitch.set(e.related_player_id, { ...off, booked: false });
        break;
      }
      case "tactical_change": {
        // New shape: everyone keeps their booking, but takes the role and base position from the team sheet.
        for (const l of (e.qualifiers.lineup ?? []) as { player_id: string; role: string; x: number; y: number }[]) {
          const p = team.onPitch.get(l.player_id);
          if (p) team.onPitch.set(l.player_id, { ...p, role: l.role, bx: l.x, by: l.y });
        }
        break;
      }
      case "period_start": m.status = e.period === 1 ? "h1" : "h2"; break;
      case "period_end":
        m.status = e.period === 1 ? "ht" : "ft";
        if (e.period === 1) m.p1Buckets = Math.floor(e.elapsed_s / 60) + 1;
        break;
    }

    const b = this.bucketOf(e);
    while (m.buckets.length <= b) m.buckets.push({ home: 0, away: 0, goals: [] });
    m.buckets[b][team.side] += threat(e);
    if (e.type === "shot" && e.outcome === "goal") {
      m.buckets[b].goals.push({ side: team.side, scorer: this.name(e.player_id), clock: e.clock });
    }

    const key = KEY_TYPES.has(e.type) || (e.type === "pass" && e.outcome === "offside");
    m.feed.push({ e, key, side: e.type.startsWith("period") ? "none" : team.side, text: this.describe(e) });
    if (m.feed.length > FEED_LIMIT * 4) m.feed.splice(0, m.feed.length - FEED_LIMIT * 4);

    if (e.x != null && e.y != null) {
      const right = this.attacksRight(e.team_id, e.period);
      const start = this.toScreen(e.x, e.y, right);
      const end = e.end_x != null && e.end_y != null ? this.toScreen(e.end_x, e.end_y, right) : start;
      m.ballEvent = { t, type: e.type, start, end, travel: travelTime(e, start, end) };
      m.overrides = new Map();
      m.actor = e.player_id;
      const dribbling = e.type === "carry" || (e.type === "take_on" && e.outcome === "complete");
      if (e.player_id) m.overrides.set(e.player_id, dribbling ? "ball" : [e.x, e.y]);
      if (e.related_player_id) {
        if (e.type === "pass" && e.outcome === "complete" && e.end_x != null && e.end_y != null) {
          m.overrides.set(e.related_player_id, [e.end_x, e.end_y]); // receiver runs onto the pass
        } else if (["take_on", "tackle", "foul", "block"].includes(e.type)) {
          m.overrides.set(e.related_player_id, [100 - e.x, 100 - e.y]); // the opponent in the duel
        }
      }
      if (live && (e.type === "shot" || (e.type === "pass" && e.qualifiers.long_ball))) {
        this.drawTrail(start, end, team.side, e.type === "shot");
      }
    }
    if (e.type === "period_start" || e.type === "period_end") {
      m.ballEvent = null;
      m.ballRest = [52.5, 34];
      m.overrides = new Map();
      m.actor = null;
    }

    if (!live) return;
    if (scored) this.scoreFlash++;
    this.syncPlayers();
    const t_ = i18n.t.bind(i18n);
    if (e.type === "shot" && e.outcome === "goal") {
      this.showBanner(t_("banner.goal"), `${this.name(e.player_id)} · ${this.clubName(e.team_id)} · ${e.clock}`);
    } else if (e.type === "card" && e.outcome !== "yellow") {
      this.showBanner(t_("banner.red"), `${this.name(e.player_id)} · ${this.clubName(e.team_id)}`, true);
    } else if (e.type === "period_end") {
      this.showBanner(t_(e.period === 1 ? "banner.ht" : "banner.ft"),
                      `${this.clubName(m.home)} ${m.score[0]}–${m.score[1]} ${this.clubName(m.away)}`);
    } else if (e.type === "shot" && e.set_piece === "penalty") {
      this.showBanner(t_("banner.pen"), `${this.name(e.player_id)} · ${(e.outcome ?? "").replace("_", " ")}`);
    }
    this.notify();
  }

  private showBanner(title: string, sub: string, red = false): void {
    this.banner = { id: ++this.bannerId, title, sub, red };
  }

  /* ---------------------------------------------------------------- pitch */

  private syncPlayers(): void {
    const layer = this.layers?.players;
    if (!layer) return;
    const wanted = new Set<string>();
    for (const team of Object.values(this.m.teams)) {
      for (const [pid, p] of team.onPitch) {
        wanted.add(pid);
        let el = this.playerEls.get(pid);
        if (!el) {
          el = svg("g", { class: `player ${team.side}${p.role === "GK" ? " gk" : ""}` });
          const info = this.league.players[pid];
          const title = svg("title");
          title.textContent = `${info?.name ?? pid} (${p.role}) · ${this.clubName(team.clubId)}`;
          const num = svg("text", { y: SHIRT_Y });
          num.textContent = String(info?.number ?? "");
          el.append(svg("circle", { class: "focus-ring", r: 2.9 }), ...playerSprite(pid, svg), num,
                    svg("rect", { class: "card", x: 1.1, y: -2.3, width: 0.7, height: 0.95, rx: 0.1 }), title);
          layer.append(el);
          this.playerEls.set(pid, el);
        }
        el.classList.toggle("booked", p.booked);
      }
    }
    for (const [pid, el] of this.playerEls) {
      if (!wanted.has(pid)) { el.remove(); this.playerEls.delete(pid); this.disp.delete(pid); }
    }
  }

  private drawTrail(start: [number, number], end: [number, number], side: Side, isShot: boolean): void {
    const trails = this.layers?.trails;
    if (!trails) return;
    const line = svg("line", { x1: start[0], y1: start[1], x2: end[0], y2: end[1],
                               class: `trail ${side}${isShot ? " shot" : ""}` });
    trails.append(line);
    setTimeout(() => line.classList.add("fade"), 400);
    setTimeout(() => line.remove(), 3200);
  }

  private effectiveSpeed(): number {
    return this.speed === "live" ? this.liveRate : this.speed;
  }

  private bufferMs(): number {
    return clamp((BUFFER_MATCH_S / this.effectiveSpeed()) * 1000, ...BUFFER_MS);
  }

  private nextBallEvent(): { t: number; start: [number, number] } | null {
    for (const { t, e } of this.pending) {
      if (e.type === "period_start" || e.type === "period_end") return null;
      if (CONTEXT_TYPES.has(e.type)) continue;
      if (e.x != null && e.y != null) return { t, start: this.toScreen(e.x, e.y, this.attacksRight(e.team_id, e.period)) };
    }
    return null;
  }

  private ballTarget(T: number): [number, number] {
    const be = this.m.ballEvent;
    if (!be) return this.m.ballRest;
    const next = this.nextBallEvent();
    let travel = be.travel;
    if (next) {
      const gap = Math.max(next.t - be.t, 0.05);
      travel = be.type === "carry" ? gap : Math.min(travel, gap * 0.9);
    }
    if (T < be.t + travel) {
      const k = clamp((T - be.t) / travel, 0, 1);
      return lerp(be.start, be.end, be.type === "carry" ? k : easeOut(k));
    }
    if (!next) return be.end;
    const t0 = Math.max(be.t + travel, next.t - RESTART_MOVE_S);
    if (T <= t0) return be.end;
    return lerp(be.end, next.start, clamp((T - t0) / (next.t - t0), 0, 1));
  }

  private playerTarget(team: Team, pid: string, p: OnPitch, ball: [number, number]): [number, number] {
    const m = this.m;
    const right = this.attacksRight(team.clubId, m.period);
    const o = m.overrides.get(pid);
    if (o === "ball") return [ball[0] - (right ? 0.9 : -0.9), ball[1]];
    if (o) return this.toScreen(o[0], o[1], right);
    const bx = right ? ball[0] / 1.05 : 100 - ball[0] / 1.05;
    const by = right ? 100 - ball[1] / 0.68 : ball[1] / 0.68;
    const shift = (bx - 50) * 0.35;
    let x: number, y: number;
    if (p.role === "GK") {
      x = clamp(p.bx + shift * 0.15, 1.5, 14);
      y = clamp(50 + (by - 50) * 0.2, 40, 60);
    } else {
      x = clamp(p.bx + shift, 2, 97);
      y = p.by;
      const d = Math.hypot(bx - x, by - y);
      if (d < 24) {
        const k = 0.35 * (1 - d / 24);
        x += (bx - x) * k;
        y += (by - y) * k;
      }
    }
    return this.toScreen(x, y, right);
  }

  private stepPitch(dtReal: number): void {
    const layers = this.layers;
    if (!layers) return;
    const m = this.m;
    const snap = this.snap || this.reducedMotion.matches;
    const target = this.ballTarget(this.playT);
    if (!this.ballDisp || snap) this.ballDisp = [...target];
    else {
      const next = lerp(this.ballDisp, target, 1 - Math.exp(-dtReal / BALL_TAU_REAL));
      // Roll the ball: one turn per circumference travelled, spinning the way it is heading.
      const dx = next[0] - this.ballDisp[0];
      this.ballSpin = (this.ballSpin + (Math.sign(dx || 1) * Math.hypot(dx, next[1] - this.ballDisp[1]) * 180)
        / (Math.PI * BALL.r)) % 360;
      this.ballDisp = next;
    }
    setIfChanged(layers.ball, "transform",
      `translate(${this.ballDisp[0].toFixed(2)} ${this.ballDisp[1].toFixed(2)}) rotate(${this.ballSpin.toFixed(0)})`);

    const speed = this.effectiveSpeed();
    const smoothReal = Math.max(PLAYER_SMOOTH_S / speed, PLAYER_SMOOTH_MIN_REAL);
    const maxSpeedReal = PLAYER_MAX_SPEED * speed;
    for (const team of Object.values(m.teams)) {
      for (const [pid, p] of team.onPitch) {
        const el = this.playerEls.get(pid);
        if (!el) continue;
        const goal = this.playerTarget(team, pid, p, this.ballDisp);
        let d = this.disp.get(pid);
        if (!d) d = { pos: snap ? goal : [52.5, 69.5], vel: [0, 0] }; // substitutes jog on from the touchline
        if (snap) { d.pos = goal; d.vel = [0, 0]; }
        const cap = pid === m.actor ? maxSpeedReal * 4 : maxSpeedReal;
        d.pos = smoothDamp(d.pos, goal, d.vel, pid === m.actor ? smoothReal / 2 : smoothReal, dtReal, cap);
        this.disp.set(pid, d);
        setIfChanged(el, "transform", `translate(${d.pos[0].toFixed(2)} ${d.pos[1].toFixed(2)})`);
        toggleIfChanged(el, "actor", pid === m.actor);
        toggleIfChanged(el, "focus", pid === this.focusPlayer);
      }
    }
    this.snap = false;
  }

  private frame = (now: number): void => {
    const dt = this.lastFrame ? Math.min((now - this.lastFrame) / 1000, 0.1) : 0;
    this.lastFrame = now;
    if (this.anchor && this.playing) {
      this.playT = this.anchor.t + (Math.max(0, now - this.anchor.now) / 1000) * this.effectiveSpeed();
      // Events reach us one buffer ahead of the clock. One further ahead means the server cut a stoppage short
      // (DEAD_BALL_S in api.py): skip the rest of the dead ball instead of waiting it out. The ball still rolls to
      // the restart spot (RESTART_MOVE_S) inside the remaining buffer.
      const lead = (this.bufferMs() / 1000) * this.effectiveSpeed();
      if (this.speed !== "live" && this.pending.length && this.pending[0].t - this.playT > lead + 1) {
        this.anchor = { t: this.pending[0].t - lead, now };
        this.playT = this.anchor.t;
      }
      let applied = false;
      while (this.pending.length && this.pending[0].t <= this.playT) {
        const { t, e } = this.pending.shift()!;
        this.apply(e, true, t);
        applied = true;
      }
      if (applied) this.notify();
    }
    if (this.streamEnded && !this.pending.length && !this.finished) this.finishPlayback();
    this.stepPitch(dt);
    this.raf = requestAnimationFrame(this.frame);
  };

  /* ---------------------------------------------------------------- streaming */

  private disconnect(): void {
    this.source?.close();
    this.source = null;
  }

  private finishPlayback(): void {
    this.finished = true;
    this.anchor = null;
    this.playing = false;
    this.conn = "ended";
    this.notify(true);
  }

  private connect(from: number | "live", speed: Speed): void {
    this.disconnect();
    this.speed = speed;
    this.finished = false;
    this.pending = [];
    this.anchor = null;
    this.streamEnded = false;
    this.lastArrival = null;
    this.playing = true;
    this.conn = "connecting";
    this.notify(true);

    const q = new URLSearchParams({ from: String(from), speed: String(speed) });
    const es = new EventSource(`/api/matches/${encodeURIComponent(this.matchId)}/events?${q}`);
    this.source = es;

    es.addEventListener("reset", (msg) => {
      const { from: frm } = JSON.parse((msg as MessageEvent).data) as { from: number };
      this.m = this.fresh();
      this.seqT.clear();
      this.pending = [];
      this.anchor = null;
      this.streamEnded = false;
      this.playT = this.appliedT = frm;
      this.appliedSeq = 0;
      this.catchingUp = true;
      this.layers?.trails.replaceChildren();
      this.syncPlayers();
      this.snap = true;
      this.conn = speed === "live" ? "live" : "replay";
      this.notify(true);
    });
    es.addEventListener("event", (msg) => {
      const { t, catchup, e } = JSON.parse((msg as MessageEvent).data) as { t: number; catchup: boolean; e: MatchEvent };
      if (catchup) {
        this.apply(e, false, t);
        this.playT = t;
        return;
      }
      if (this.catchingUp) this.finishCatchup();
      const now = performance.now();
      if (speed === "live") this.trackLiveRate(t, now);
      if (!this.anchor) this.anchor = { t, now: now + this.bufferMs() };
      this.pending.push({ t, e });
    });
    es.addEventListener("end", () => {
      if (this.catchingUp) this.finishCatchup();
      this.streamEnded = true; // buffered events still play out before full-time
      this.disconnect();
    });
    es.onerror = () => {
      if (!this.streamEnded) { this.conn = "reconnecting"; this.notify(true); }
    };
  }

  private finishCatchup(): void {
    this.catchingUp = false;
    this.syncPlayers();
    this.snap = true;
    this.notify(true);
  }

  /** Following live, the producer sets the pace; estimate it so the ball and players move at the right speed. */
  private trackLiveRate(t: number, now: number): void {
    const last = this.lastArrival;
    if (last && now - last.now > 50 && t > last.t) {
      const rate = clamp((t - last.t) / ((now - last.now) / 1000), 0.5, 600);
      this.liveRate = this.liveRate * 0.8 + rate * 0.2;
    }
    this.lastArrival = { t, now };
    // Drifted too far behind the live edge (tab in the background, rate change): jump closer.
    if (this.anchor) {
      const behind = t - this.playT;
      const buffer = (this.bufferMs() / 1000) * this.liveRate;
      if (behind > buffer * 3) this.anchor = { t: t - buffer, now };
    }
  }

  /* ---------------------------------------------------------------- snapshot for React */

  private buildView(): View {
    const m = this.m;
    const st = (club: string) => ({ ...(m.teams[club]?.stats ?? emptyStats()) });
    return {
      home: m.home,
      away: m.away,
      score: [...m.score] as [number, number],
      scoreFlash: this.scoreFlash,
      clock: m.status === "prematch" ? "0'" : m.clock,
      status: m.status,
      period: m.period,
      stats: { home: st(m.home), away: st(m.away) },
      feed: m.feed.slice(-FEED_LIMIT * 4),
      buckets: m.buckets.map((b) => ({ ...b, goals: [...b.goals] })),
      p1Buckets: m.p1Buckets,
      appliedSeq: this.appliedSeq,
      appliedT: this.appliedT,
      playing: this.playing,
      conn: this.conn,
      speed: this.speed,
      finished: this.finished,
      banner: this.banner,
      homeAttacksRight: this.attacksRight(m.home, m.period),
      halftimeS: this.session.halftime_s,
    };
  }

  /** Rebuild the snapshot and tell React, at most ~6 times a second unless `now`. */
  private notify(now = false): void {
    const run = () => {
      this.notifyTimer = null;
      this.snapshot = this.buildView();
      for (const fn of this.listeners) fn();
    };
    if (now) {
      if (this.notifyTimer) clearTimeout(this.notifyTimer);
      run();
    } else if (!this.notifyTimer) {
      this.notifyTimer = window.setTimeout(run, 160);
    }
  }
}

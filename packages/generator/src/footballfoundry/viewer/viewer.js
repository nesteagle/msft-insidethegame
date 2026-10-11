"use strict";
/*
 * footballfoundry match viewer.
 *
 * Events arrive over Server-Sent Events from `footballfoundry serve` at the chosen speed. Each connection starts
 * with a `reset`, then replays everything before the requested point instantly (catch-up, no animation),
 * then streams live. Live events are buffered briefly and played by a requestAnimationFrame loop on a
 * continuous match clock (see "motion"). Pause, speed changes and "replay from minute" reconnect with a new `from`.
 *
 * Coordinates: every event is in the acting team's frame (x 0->100 towards the opponent's goal,
 * y 0->100 from that team's right touchline to its left). The home team attacks right in the first half.
 */

const SVGNS = "http://www.w3.org/2000/svg";
const SPEEDS = [1, 5, 10, 30, 60, 120];
const FEED_LIMIT = 300;
const KEY_TYPES = new Set(["shot", "card", "substitution", "period_start", "period_end"]);
// Off-ball context (pressing, who has the ball): for analysis, not for the feed or the ball's path.
const CONTEXT_TYPES = new Set(["pressure", "possession_change"]);

const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const svg = (tag, attrs = {}) => {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

const app = {
  session: null,
  match: null,
  speed: Number(new URLSearchParams(location.search).get("speed")) || 10,
  source: null,
  playing: false,
  finished: false,
  catchingUp: false,
  feedFilter: "key",
  m: null,            // per-connection match state
  playerEls: new Map(),
  // playout: live events wait in `pending` and are applied when the match clock `playT` reaches them
  pending: [],
  anchor: null,       // { t, now }: match time t is shown at performance.now() === now
  playT: 0,
  appliedT: 0,        // match time of the last applied event (where pause/speed changes resume from)
  streamEnded: false,
  snap: true,         // next frame jumps straight to target positions (after a reset / catch-up)
  disp: new Map(),    // player id -> { pos: [X, Y] screen metres, vel } as displayed
  ballDisp: null,
};

/* ------------------------------------------------------------------ state */

function emptyStats() {
  return { passes: 0, completed: 0, shots: 0, onTarget: 0, xg: 0, corners: 0, fouls: 0,
           yellows: 0, reds: 0, offsides: 0 };
}

function freshMatchState(match) {
  const teams = {};
  for (const side of ["home", "away"]) {
    const clubId = match[`${side}_club_id`];
    teams[clubId] = { side, clubId, onPitch: new Map(), stats: emptyStats() };
  }
  for (const l of match.lineups) {
    if (l.starter) teams[l.club_id].onPitch.set(l.player_id, { role: l.role, bx: l.base_x, by: l.base_y, booked: false });
  }
  return {
    teams, home: match.home_club_id, away: match.away_club_id,
    score: [0, 0], clock: "0'", period: 1, status: "Pre-match",
    feed: [], buckets: [], p1Buckets: null, goals: [],
    ballEvent: null,            // the on-ball action the ball is currently following
    ballRest: [52.5, 34],       // where the ball sits when no action is in play
    overrides: new Map(), actor: null,
  };
}

const playerName = (id) => (id && app.session.players[id] ? app.session.players[id].short_name : "—");
const clubName = (id) => app.session.clubs[id].name;

/** Does this club attack towards the right of the screen in this period? */
const attacksRight = (clubId, period) => (clubId === app.m.home) !== (period === 2);

/** Team-frame (0-100) to screen metres (105 x 68). */
function toScreen(x, y, right) {
  return right ? [x * 1.05, (100 - y) * 0.68] : [(100 - x) * 1.05, y * 0.68];
}

function bucketOf(e) {
  const minute = Math.floor(e.elapsed_s / 60);
  return e.period === 1 ? minute : (app.m.p1Buckets ?? 46) + 1 + minute;
}

/** Simple attacking-threat score per event, summed per minute for the momentum strip. */
function threat(e) {
  const inBox = (x, y) => x >= 84 && y >= 20 && y <= 80;
  if (e.type === "shot") return 4;
  if (e.type === "pass" && e.outcome === "complete" && e.end_x >= 66) return inBox(e.end_x, e.end_y) ? 3 : 1;
  if (e.type === "carry" && inBox(e.end_x, e.end_y)) return 2;
  if (e.type === "take_on" && e.outcome === "complete" && e.x >= 60) return 1;
  return 0;
}

/* ------------------------------------------------------------------ event text */

function describe(e) {
  const p = playerName(e.player_id), r = playerName(e.related_player_id);
  switch (e.type) {
    case "pass":
      if (e.qualifiers.cross) return e.outcome === "complete" ? `${p} crosses to ${r}` : `${p}'s cross is cut out`;
      if (e.outcome === "offside") return `${r} is caught offside`;
      if (e.outcome === "complete") return e.set_piece !== "open_play" ? `${p} takes the ${e.set_piece.replace("_", " ")} to ${r}` : `${p} finds ${r}`;
      return `${p}'s pass goes astray`;
    case "carry": return `${p} carries forward`;
    case "take_on":
      if (e.outcome === "complete") return `${p} beats ${r}`;
      if (e.outcome === "fouled") return `${p} is fouled`;
      return `${p} is stopped by ${r}`;
    case "shot": {
      const xg = e.xg != null ? ` (xG ${e.xg.toFixed(2)})` : "";
      const pen = e.set_piece === "penalty" ? " from the spot" : e.body_part === "head" ? " with a header" : "";
      return {
        goal: `GOAL! ${p} scores${pen}${xg}`,
        saved: `${p}'s effort${pen} is saved${xg}`,
        blocked: `${p}'s shot is blocked${xg}`,
        post: `${p} hits the woodwork${pen}!${xg}`,
        off_target: `${p} shoots wide${pen}${xg}`,
      }[e.outcome] || `${p} shoots${xg}`;
    }
    case "save": return e.outcome === "claimed" ? `${p} claims the cross` : `${p} makes the save`;
    case "block": return `${p} blocks ${r}'s shot`;
    case "tackle": return e.outcome === "won" ? `${p} wins the ball from ${r}` : `${p} misses a tackle on ${r}`;
    case "interception": return `${p} intercepts`;
    case "clearance": return `${p} clears`;
    case "ball_recovery": return `${p} picks up the loose ball`;
    case "foul": return r !== "—" ? `Foul by ${p} on ${r}` : `Foul by ${p}`;
    case "card":
      return e.outcome === "yellow" ? `Yellow card for ${p}`
        : e.outcome === "second_yellow" ? `Second yellow — ${p} is sent off` : `Straight red — ${p} is sent off`;
    case "substitution": return `${r} replaces ${p}`;
    case "period_start": return e.period === 1 ? "Kick-off" : "Second half underway";
    case "period_end": return e.period === 1 ? "Half-time" : "Full-time";
    default: return e.type;
  }
}

/* ------------------------------------------------------------------ applying events */

function apply(e, live, t) {
  const m = app.m;
  // period markers arrive once per team; keep only the home copy
  if ((e.type === "period_start" || e.type === "period_end") && e.team_id !== m.home) return;
  if (CONTEXT_TYPES.has(e.type)) return;

  m.period = e.period;
  m.clock = e.clock;
  const scored = e.home_score + e.away_score > m.score[0] + m.score[1];
  m.score = [e.home_score, e.away_score];
  const team = m.teams[e.team_id];
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
      const p = team.onPitch.get(e.player_id);
      if (e.outcome === "yellow") { s.yellows++; if (p) p.booked = true; }
      else { s.reds++; team.onPitch.delete(e.player_id); }
      break;
    }
    case "substitution": {
      const off = team.onPitch.get(e.player_id);
      team.onPitch.delete(e.player_id);
      if (off) team.onPitch.set(e.related_player_id, { ...off, booked: false });
      break;
    }
    case "period_start": m.status = e.period === 1 ? "1st half" : "2nd half"; break;
    case "period_end":
      m.status = e.period === 1 ? "Half-time" : "Full-time";
      if (e.period === 1) m.p1Buckets = Math.floor(e.elapsed_s / 60) + 1;
      break;
  }

  // momentum
  const b = bucketOf(e);
  while (m.buckets.length <= b) m.buckets.push({ home: 0, away: 0, goals: [] });
  m.buckets[b][team.side] += threat(e);
  if (e.type === "shot" && e.outcome === "goal") {
    m.buckets[b].goals.push({ side: team.side, scorer: playerName(e.player_id), clock: e.clock });
  }

  // feed
  const key = KEY_TYPES.has(e.type) || (e.type === "pass" && e.outcome === "offside");
  const item = { e, key, side: e.type.startsWith("period") ? "none" : team.side, text: describe(e) };
  m.feed.push(item);

  // pitch: what the ball does next and which players are pulled to the action
  if (e.x != null) {
    const right = attacksRight(e.team_id, e.period);
    const start = toScreen(e.x, e.y, right);
    const end = e.end_x != null ? toScreen(e.end_x, e.end_y, right) : start;
    m.ballEvent = { t, type: e.type, start, end, travel: travelTime(e, start, end) };
    m.overrides = new Map();
    m.actor = e.player_id;
    const dribbling = e.type === "carry" || (e.type === "take_on" && e.outcome === "complete");
    m.overrides.set(e.player_id, dribbling ? "ball" : [e.x, e.y]);
    if (e.related_player_id) {
      if (e.type === "pass" && e.outcome === "complete") {
        m.overrides.set(e.related_player_id, [e.end_x, e.end_y]);   // receiver runs onto the pass
      } else if (["take_on", "tackle", "foul", "block"].includes(e.type)) {
        m.overrides.set(e.related_player_id, [100 - e.x, 100 - e.y]);  // opponent in the duel, in their own frame
      }
    }
    if (live && (e.type === "shot" || (e.type === "pass" && e.qualifiers.long_ball))) drawTrail(start, end, team.side, e.type === "shot");
  }
  if (e.type === "period_start" || e.type === "period_end") {
    m.ballEvent = null;
    m.ballRest = [52.5, 34];
    m.overrides = new Map();
    m.actor = null;
  }

  if (!live) return;
  renderScore(scored);
  renderStats();
  scheduleMomentum();
  prependFeed(item);
  syncPlayers();
  if (e.type === "shot" && e.outcome === "goal") {
    showBanner("GOAL!", `${playerName(e.player_id)} · ${clubName(e.team_id)} · ${e.clock}`);
  } else if (e.type === "card" && e.outcome !== "yellow") {
    showBanner("RED CARD", `${playerName(e.player_id)} · ${clubName(e.team_id)}`, "red");
  } else if (e.type === "period_end") {
    showBanner(e.period === 1 ? "HALF-TIME" : "FULL-TIME", `${clubName(m.home)} ${m.score[0]}–${m.score[1]} ${clubName(m.away)}`);
  } else if (e.type === "shot" && e.set_piece === "penalty") {
    showBanner("PENALTY", `${playerName(e.player_id)} · ${e.outcome.replace("_", " ")}`);
  }
}

/* ------------------------------------------------------------------ pitch rendering */

function syncPlayers() {
  const layer = $("players");
  const wanted = new Set();
  for (const team of Object.values(app.m.teams)) {
    for (const [pid, p] of team.onPitch) {
      wanted.add(pid);
      let el = app.playerEls.get(pid);
      if (!el) {
        el = svg("g", { class: `player ${team.side}${p.role === "GK" ? " gk" : ""}` });
        const title = svg("title");
        const info = app.session.players[pid];
        title.textContent = `${info.name} (${p.role}) · ${clubName(team.clubId)}`;
        el.append(svg("circle", { r: "1.55" }), Object.assign(svg("text"), { textContent: info.number }), title);
        layer.append(el);
        app.playerEls.set(pid, el);
      }
      el.classList.toggle("booked", p.booked);
    }
  }
  for (const [pid, el] of app.playerEls) {
    if (!wanted.has(pid)) { el.remove(); app.playerEls.delete(pid); app.disp.delete(pid); }
  }
}

/* ------------------------------------------------------------------ motion (frame loop)
 * Live events are buffered a little behind the stream and applied on a continuous match clock, so the
 * ball can travel each action at a believable speed and then move on to where the next action starts,
 * and players glide towards their positions every frame instead of jumping once per event.
 */

const BUFFER_MATCH_S = 4;          // playout delay in match seconds (covers the gap to the next action)
const BUFFER_MS = [250, 4000];     // ...clamped to this many real milliseconds
const PLAYER_SMOOTH_S = 0.9;       // player spring time in match seconds
const PLAYER_SMOOTH_MIN_REAL = 0.1;  // never snappier than this in real seconds, even at 120x
const PLAYER_MAX_SPEED = 9;        // metres per match second (a sprint)
const BALL_TAU_REAL = 0.035;       // light smoothing on the ball to hide frame-to-frame steps
const RESTART_MOVE_S = 2.5;        // during a stoppage the ball is moved to the restart in its last seconds
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const bufferMs = () => clamp((BUFFER_MATCH_S / app.speed) * 1000, ...BUFFER_MS);
const lerp = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
const easeOut = (k) => 1 - (1 - k) * (1 - k);

/**
 * Critically damped spring towards `goal` (2D "smooth damp"): eases in and out, never overshoots,
 * is stable for any frame time, and caps speed. Returns the new position; updates `vel` in place.
 */
function smoothDamp(pos, goal, vel, smoothTime, dt, maxSpeed) {
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
  // don't overshoot the goal
  if ((goal[0] - pos[0]) * (ox - goal[0]) + (goal[1] - pos[1]) * (oy - goal[1]) > 0) {
    ox = goal[0]; oy = goal[1];
    vel[0] = vel[1] = 0;
  }
  return [ox, oy];
}

/** Match seconds the ball takes to complete an action. */
function travelTime(e, start, end) {
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

/** The next buffered action in this half that has a location, or null. */
function nextBallEvent() {
  for (const { t, e } of app.pending) {
    if (e.type === "period_start" || e.type === "period_end") return null;
    if (CONTEXT_TYPES.has(e.type)) continue;
    if (e.x != null) return { t, start: toScreen(e.x, e.y, attacksRight(e.team_id, e.period)) };
  }
  return null;
}

/** Where the ball should be at match time T. */
function ballTarget(T) {
  const be = app.m.ballEvent;
  if (!be) return app.m.ballRest;
  const next = nextBallEvent();
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
  // ball comes to rest, then moves to where the next action begins (short gaps: all the way; stoppages: at the end)
  const t0 = Math.max(be.t + travel, next.t - RESTART_MOVE_S);
  if (T <= t0) return be.end;
  return lerp(be.end, next.start, clamp((T - t0) / (next.t - t0), 0, 1));
}

/** Where a player should be, given the ball's current screen position. */
function playerTarget(team, pid, p, ball) {
  const m = app.m;
  const right = attacksRight(team.clubId, m.period);
  const o = m.overrides.get(pid);
  if (o === "ball") return [ball[0] - (right ? 0.9 : -0.9), ball[1]];   // dribbling: just behind the ball
  if (o) return toScreen(o[0], o[1], right);
  // ball in this team's frame; the block shifts with it and nearby players close in
  const bx = right ? ball[0] / 1.05 : 100 - ball[0] / 1.05;
  const by = right ? 100 - ball[1] / 0.68 : ball[1] / 0.68;
  const shift = (bx - 50) * 0.35;
  let x, y;
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
  return toScreen(x, y, right);
}

function stepPitch(dtReal) {
  const m = app.m;
  const snap = app.snap || reducedMotion.matches;
  const target = ballTarget(app.playT);
  if (!app.ballDisp || snap) {
    app.ballDisp = [...target];
  } else {
    const a = 1 - Math.exp(-dtReal / BALL_TAU_REAL);
    app.ballDisp = lerp(app.ballDisp, target, a);
  }
  $("ball").setAttribute("transform", `translate(${app.ballDisp[0].toFixed(2)} ${app.ballDisp[1].toFixed(2)})`);

  const smoothReal = Math.max(PLAYER_SMOOTH_S / app.speed, PLAYER_SMOOTH_MIN_REAL);
  const maxSpeedReal = PLAYER_MAX_SPEED * app.speed;
  for (const team of Object.values(m.teams)) {
    for (const [pid, p] of team.onPitch) {
      const el = app.playerEls.get(pid);
      if (!el) continue;
      const goal = playerTarget(team, pid, p, app.ballDisp);
      let d = app.disp.get(pid);
      if (!d) d = { pos: snap ? goal : [52.5, 69.5], vel: [0, 0] };   // substitutes jog on from the touchline
      if (snap) { d.pos = goal; d.vel = [0, 0]; }
      // the player on the ball keeps up with it; everyone else runs at most at sprint speed
      const cap = pid === m.actor ? maxSpeedReal * 4 : maxSpeedReal;
      d.pos = smoothDamp(d.pos, goal, d.vel, pid === m.actor ? smoothReal / 2 : smoothReal, dtReal, cap);
      app.disp.set(pid, d);
      const pos = d.pos;
      el.setAttribute("transform", `translate(${pos[0].toFixed(2)} ${pos[1].toFixed(2)})`);
      el.classList.toggle("actor", pid === m.actor);
    }
  }
  app.snap = false;
}

let lastFrame = 0;
function frame(now) {
  const dt = lastFrame ? Math.min((now - lastFrame) / 1000, 0.1) : 0;
  lastFrame = now;
  if (app.m) {
    if (app.anchor && app.playing) {
      app.playT = app.anchor.t + (Math.max(0, now - app.anchor.now) / 1000) * app.speed;
      while (app.pending.length && app.pending[0].t <= app.playT) {
        const { t, e } = app.pending.shift();
        apply(e, true, t);
        app.appliedT = t;
      }
    }
    if (app.streamEnded && !app.pending.length && !app.finished) finishPlayback();
    stepPitch(dt);
  }
  requestAnimationFrame(frame);
}

function drawTrail(start, end, side, isShot) {
  const line = svg("line", { x1: start[0], y1: start[1], x2: end[0], y2: end[1], class: `trail ${side}${isShot ? " shot" : ""}` });
  $("trails").append(line);
  setTimeout(() => line.classList.add("fade"), 400);
  setTimeout(() => line.remove(), 3200);
}

function updateDirection() {
  const m = app.m;
  const homeRight = attacksRight(m.home, m.period);
  const h = app.session.clubs[m.home].short_name, a = app.session.clubs[m.away].short_name;
  $("dir-left").textContent = homeRight ? `${h} attack →` : `${a} attack →`;
  $("dir-right").textContent = homeRight ? `← ${a} attack` : `← ${h} attack`;
}

let bannerTimer = null;
function showBanner(title, sub, chip) {
  const el = $("banner");
  el.replaceChildren();
  const strong = document.createElement("strong");
  if (chip === "red") {
    const c = document.createElement("i");
    c.className = "card-chip";
    c.style.background = "var(--red-card)";
    strong.append(c);
  }
  strong.append(title);
  const span = document.createElement("span");
  span.textContent = sub;
  el.append(strong, span);
  el.hidden = false;
  el.style.animation = "none";
  void el.offsetWidth;
  el.style.animation = "";
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

/* ------------------------------------------------------------------ scoreboard, stats */

function renderScore(flash) {
  const m = app.m;
  $("home-score").textContent = m.score[0];
  $("away-score").textContent = m.score[1];
  $("clock").textContent = m.status === "Pre-match" ? "0'" : m.clock;
  $("status").textContent = m.status;
  if (flash) {
    const s = document.querySelector(".score");
    s.classList.remove("flash");
    void s.offsetWidth;
    s.classList.add("flash");
  }
  updateDirection();
}

function renderStats() {
  const h = app.m.teams[app.m.home].stats, a = app.m.teams[app.m.away].stats;
  const total = h.passes + a.passes;
  const pos = total ? Math.round((h.passes / total) * 100) : 50;
  const acc = (s) => (s.passes ? `${Math.round((s.completed / s.passes) * 100)}%` : "–");
  const rows = [
    ["Possession", `${pos}%`, `${100 - pos}%`, pos, 100 - pos],
    ["Shots", h.shots, a.shots],
    ["On target", h.onTarget, a.onTarget],
    ["Expected goals (xG)", h.xg.toFixed(2), a.xg.toFixed(2), h.xg, a.xg],
    ["Passes", h.passes, a.passes],
    ["Pass accuracy", acc(h), acc(a), h.completed / (h.passes || 1), a.completed / (a.passes || 1)],
    ["Corners", h.corners, a.corners],
    // no "leader" highlight for counts where more isn't better
    ["Fouls", h.fouls, a.fouls, 0, 0],
    ["Offsides", h.offsides, a.offsides, 0, 0],
    ["Yellow cards", h.yellows, a.yellows, 0, 0],
    ["Red cards", h.reds, a.reds, 0, 0],
  ];
  const body = document.querySelector("#stats tbody");
  body.replaceChildren();
  for (const [label, hv, av, hn = Number(hv), an = Number(av)] of rows) {
    const tr = document.createElement("tr");
    const cells = [hv, label, av].map((v, i) => {
      const td = document.createElement("td");
      td.textContent = v;
      if (i === 1) td.className = "label";
      if ((i === 0 && hn > an) || (i === 2 && an > hn)) td.className = "lead";
      return td;
    });
    if (label === "Possession") {
      const bar = document.createElement("div");
      bar.className = "possession";
      bar.innerHTML = `<div class="home" style="flex-grow:${pos}"></div><div class="away" style="flex-grow:${100 - pos}"></div>`;
      cells[1].append(bar);
    }
    tr.append(...cells);
    body.append(tr);
  }
}

/* ------------------------------------------------------------------ momentum strip */

function bucketStartT(b) {
  const m = app.m;
  const p1 = m.p1Buckets;
  if (p1 === null || b < p1) return b * 60;
  return p1 * 60 + app.session.halftime_s + (b - p1 - 1) * 60;  // approximate start of the minute
}

function bucketLabel(b) {
  const p1 = app.m.p1Buckets;
  if (p1 === null || b < p1) return b < 45 ? `${b + 1}'` : `45+${b - 44}'`;
  const m2 = b - p1 - 1;
  return m2 < 45 ? `${46 + m2}'` : `90+${m2 - 44}'`;
}

function renderMomentum() {
  const m = app.m;
  const el = $("momentum");
  const W = el.clientWidth || 600, H = 140, top = 14, bottom = 18;
  const mid = top + (H - top - bottom) / 2, half = (H - top - bottom) / 2;
  const n = Math.max(100, m.buckets.length + 1);
  const step = W / n, bw = Math.max(1, step - 2);
  const max = Math.max(8, ...m.buckets.map((b) => Math.max(b.home, b.away)));
  el.setAttribute("viewBox", `0 0 ${W} ${H}`);
  el.replaceChildren();

  if (m.p1Buckets !== null) {
    const x = m.p1Buckets * step + step / 2;
    el.append(svg("line", { x1: x, x2: x, y1: top - 6, y2: H - bottom, class: "m-half" }));
    el.append(Object.assign(svg("text", { x: x + 4, y: top, class: "m-label" }), { textContent: "HT" }));
  }
  m.buckets.forEach((b, i) => {
    const x = i * step + 1;
    const r = Math.min(2, bw / 2);
    if (b.home) {
      const h = (b.home / max) * half;
      el.append(svg("path", { class: "m-bar home", d: roundedBar(x, mid - h, bw, h, r, "up") }));
    }
    if (b.away) {
      const h = (b.away / max) * half;
      el.append(svg("path", { class: "m-bar away", d: roundedBar(x, mid, bw, h, r, "down") }));
    }
    for (const g of b.goals) {
      const cx = x + bw / 2, cy = g.side === "home" ? top - 6 : H - bottom + 6;
      el.append(svg("line", { x1: cx, x2: cx, y1: mid, y2: cy, class: "m-goal" }));
      el.append(svg("circle", { cx, cy, r: 4, class: "m-goal-dot" }));
    }
  });
  el.append(svg("line", { x1: 0, x2: W, y1: mid, y2: mid, class: "m-base" }));
  for (const [label, b] of [["1'", 0], ["45'", 45]]) {
    el.append(Object.assign(svg("text", { x: b * step + 1, y: H - 2, class: "m-label" }), { textContent: label }));
  }
  if (m.p1Buckets !== null) {
    el.append(Object.assign(svg("text", { x: (m.p1Buckets + 1) * step + 1, y: H - 2, class: "m-label" }), { textContent: "46'" }));
  }

  app.momentumStep = step;
  const hover = app.hoverBucket;
  if (hover != null && hover < m.buckets.length) {
    el.prepend(svg("rect", { x: hover * step, y: 0, width: step, height: H, class: "m-hover" }));
    showTooltip(hover, m.buckets[hover], hover * step + step / 2);
  }
}

/** The strip is redrawn as events arrive, so pointer handling lives on the stable <svg>, not on its children. */
function bucketAt(clientX) {
  const rect = $("momentum").getBoundingClientRect();
  const i = Math.floor((clientX - rect.left) / (app.momentumStep || 1));
  return app.m && i >= 0 && i < app.m.buckets.length ? i : null;
}

let momentumQueued = false;
function scheduleMomentum() {
  if (momentumQueued) return;
  momentumQueued = true;
  setTimeout(() => { momentumQueued = false; renderMomentum(); }, 200);
}

function roundedBar(x, y, w, h, r, dir) {
  r = Math.min(r, h);
  if (dir === "up") {   // rounded top, flat on the baseline
    return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
  }
  return `M${x},${y} V${y + h - r} Q${x},${y + h} ${x + r},${y + h} H${x + w - r} Q${x + w},${y + h} ${x + w},${y + h - r} V${y} Z`;
}

function showTooltip(i, b, x) {
  const tip = $("tooltip");
  const m = app.m;
  tip.replaceChildren();
  const head = document.createElement("div");
  head.style.fontWeight = "600";
  head.textContent = `${bucketLabel(i)} · click to replay from here`;
  tip.append(head);
  for (const [side, club] of [["home", m.home], ["away", m.away]]) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<i style="background:var(--${side})"></i>`;
    row.append(`${app.session.clubs[club].short_name}: threat ${b[side]}`);
    tip.append(row);
  }
  for (const g of b.goals) {
    const row = document.createElement("div");
    row.textContent = `Goal: ${g.scorer} (${g.clock})`;
    tip.append(row);
  }
  tip.style.left = `${clamp(x, 80, $("momentum").clientWidth - 80)}px`;
  tip.style.top = "10px";
  tip.hidden = false;
}

/* ------------------------------------------------------------------ feed */

function feedLi(item, isNew) {
  const li = document.createElement("li");
  if (isNew) li.className = "new";
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = item.e.clock;
  const d = document.createElement("span");
  d.className = `d ${item.side}`;
  const text = document.createElement("span");
  const e = item.e;
  if (e.type === "card") {
    const chip = document.createElement("i");
    chip.className = "chip";
    chip.style.background = e.outcome === "yellow" ? "var(--yellow-card)" : "var(--red-card)";
    text.append(chip);
  }
  if (e.type === "shot" && e.outcome === "goal") text.className = "goal";
  if (e.type === "substitution") {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = "SUB";
    text.append(tag);
  }
  text.append(item.text);
  li.append(t, d, text);
  return li;
}

function renderFeed() {
  const list = $("feed");
  const items = app.m.feed.filter((i) => app.feedFilter === "all" || i.key);
  list.replaceChildren(...items.slice(-FEED_LIMIT).reverse().map((i) => feedLi(i, false)));
}

function prependFeed(item) {
  if (app.feedFilter === "key" && !item.key) return;
  const list = $("feed");
  list.prepend(feedLi(item, true));
  while (list.children.length > FEED_LIMIT) list.lastChild.remove();
}

/* ------------------------------------------------------------------ streaming */

function setConn(text, live = false) {
  $("conn-text").textContent = text;
  $("conn").classList.toggle("live", live);
}

function setPlaying(playing) {
  app.playing = playing;
  $("play").textContent = playing ? "Pause" : app.finished ? "Replay" : "Play";
}

function disconnect() {
  if (app.source) { app.source.close(); app.source = null; }
}

function renderAll() {
  renderScore(false);
  renderStats();
  renderMomentum();
  renderFeed();
  syncPlayers();
  app.snap = true;
}

function finishCatchup() {
  app.catchingUp = false;
  renderAll();
}

function finishPlayback() {
  app.finished = true;
  app.anchor = null;
  setPlaying(false);
  setConn("Full-time");
}

function connect(from = 0) {
  disconnect();
  app.finished = false;
  app.pending = [];
  app.anchor = null;
  app.streamEnded = false;
  const url = `/api/events?match=${encodeURIComponent(app.match.match_id)}&speed=${app.speed}&from=${from}`;
  const es = new EventSource(url);
  app.source = es;
  setPlaying(true);
  setConn("Connecting…");

  es.addEventListener("reset", () => {
    app.m = freshMatchState(app.match);
    app.pending = [];
    app.anchor = null;
    app.streamEnded = false;
    app.playT = app.appliedT = from;
    app.catchingUp = true;
    $("trails").replaceChildren();
    renderAll();
    setConn(`Live · ${app.speed}×`, true);
  });
  es.addEventListener("event", (msg) => {
    const { t, catchup, e } = JSON.parse(msg.data);
    if (catchup) {
      apply(e, false, t);
      app.playT = app.appliedT = t;
      return;
    }
    if (app.catchingUp) finishCatchup();
    // Start the playout clock a short buffer behind the first live event; the frame loop applies the rest.
    if (!app.anchor) app.anchor = { t, now: performance.now() + bufferMs() };
    app.pending.push({ t, e });
  });
  es.addEventListener("end", () => {
    if (app.catchingUp) finishCatchup();
    app.streamEnded = true;   // buffered events still play out before full-time
    disconnect();
  });
  es.onerror = () => { if (!app.streamEnded) setConn("Reconnecting…"); };
}

/* ------------------------------------------------------------------ setup */

function selectMatch(matchId) {
  app.match = app.session.matches.find((m) => m.match_id === matchId);
  const { home_club_id: h, away_club_id: a } = app.match;
  const clubs = app.session.clubs;
  $("home-name").textContent = clubs[h].name;
  $("away-name").textContent = clubs[a].name;
  $("stats-home").textContent = clubs[h].short_name;
  $("stats-away").textContent = clubs[a].short_name;
  $("home-kit").style.background = `linear-gradient(90deg, ${clubs[h].kit[0]} 50%, ${clubs[h].kit[1]} 50%)`;
  $("away-kit").style.background = `linear-gradient(90deg, ${clubs[a].kit[0]} 50%, ${clubs[a].kit[1]} 50%)`;
  $("meta").textContent = `${app.match.venue} · Referee ${app.match.referee} · ` +
    `${clubs[h].short_name} ${app.match.home_formation} v ${app.match.away_formation} ${clubs[a].short_name}`;
  $("momentum-legend").innerHTML = "";
  for (const [side, club] of [["home", h], ["away", a]]) {
    const s = document.createElement("span");
    s.innerHTML = `<i style="background:var(--${side})"></i>`;
    s.append(clubs[club].short_name);
    $("momentum-legend").append(s);
  }
  for (const el of app.playerEls.values()) el.remove();
  app.playerEls.clear();
  app.disp.clear();
  app.ballDisp = null;
  connect(0);
}

async function init() {
  const res = await fetch("/api/session");
  app.session = await res.json();

  const select = $("match-select");
  for (const m of app.session.matches) {
    const opt = document.createElement("option");
    opt.value = m.match_id;
    opt.textContent = `${m.match_id} · ${app.session.clubs[m.home_club_id].name} v ${app.session.clubs[m.away_club_id].name}`;
    select.append(opt);
  }
  select.addEventListener("change", () => selectMatch(select.value));

  const speeds = $("speeds");
  for (const s of SPEEDS) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${s}×`;
    b.classList.toggle("active", s === app.speed);
    b.addEventListener("click", () => {
      app.speed = s;
      for (const other of speeds.children) other.classList.toggle("active", other === b);
      if (app.playing) connect(app.appliedT);
    });
    speeds.append(b);
  }

  $("play").addEventListener("click", () => {
    if (app.playing) {
      disconnect();
      setPlaying(false);
      setConn("Paused");
    } else {
      connect(app.finished ? 0 : app.appliedT);
    }
  });
  $("restart").addEventListener("click", () => connect(0));

  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      app.feedFilter = tab.dataset.filter;
      for (const t of document.querySelectorAll(".tab")) {
        t.classList.toggle("active", t === tab);
        t.setAttribute("aria-selected", String(t === tab));
      }
      renderFeed();
    });
  }
  window.addEventListener("resize", () => app.m && renderMomentum());
  const strip = $("momentum");
  strip.addEventListener("mousemove", (ev) => {
    const i = bucketAt(ev.clientX);
    if (i === app.hoverBucket) return;
    app.hoverBucket = i;
    if (i === null) $("tooltip").hidden = true;
    renderMomentum();
  });
  strip.addEventListener("mouseleave", () => {
    app.hoverBucket = null;
    $("tooltip").hidden = true;
    renderMomentum();
  });
  strip.addEventListener("click", (ev) => {
    const i = bucketAt(ev.clientX);
    if (i !== null) connect(bucketStartT(i));
  });

  selectMatch(app.session.matches[0].match_id);
  requestAnimationFrame(frame);
}

init().catch((err) => {
  setConn("Could not reach footballfoundry serve");
  console.error(err);
});

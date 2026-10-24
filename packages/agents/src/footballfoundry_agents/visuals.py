"""Charts and animated replays for insights and chat answers.

Built in code from the match state, never by a model: the agents only choose which of the visuals on offer to show
and write a caption, which the fact-checker reads like any other claim. The web app draws them (see `Visual`).

    move       the move behind a goal or chance, replayed on a mini pitch; at full time, every goal in turn
    shot_map   every shot so far, sized by xG
    xg_race    cumulative xG per team by minute, goals marked
    momentum   attacking threat per minute for each side
    compare    the two teams' numbers side by side
    pass_network   who passes to whom, each player at their average passing spot
    touch_map      every touch of one player
    defensive_map  where each team wins the ball back
"""

from __future__ import annotations

from typing import get_args

from footballfoundry_schema import Clip, CompareRow, Event, InsightKind, MinuteThreat, MoveStep, NetworkLink, \
    NetworkNode, PitchSpot, ShotPoint, Visual, VisualType

from footballfoundry_agents.reference import Reference
from footballfoundry_agents.state import SPOT_FIELDS, MatchState, move_steps

VISUAL_TYPES: tuple[str, ...] = get_args(VisualType)

ABOUT = {
    "move": "the move replayed on a mini pitch, action by action",
    "shot_map": "every shot so far on a pitch, sized by xG, goals filled",
    "xg_race": "cumulative xG for each team minute by minute, goals marked",
    "momentum": "attacking threat per minute for each side",
    "compare": "the two teams' main numbers side by side",
    "pass_network": "each team's passing network: players at their average passing spot, linked by passes between "
                    "them",
    "touch_map": "every touch of one player on the pitch",
    "defensive_map": "where each team won the ball back: tackles, interceptions, blocks, clearances, recoveries",
}
DEFENSIVE_TYPES = ("tackle", "interception", "block", "clearance", "ball_recovery")

# What each kind of moment can show, and what it shows when no agent chose (offline, or the template fallback).
OFFER: dict[str, tuple[str, ...]] = {
    "goal": ("move", "xg_race", "shot_map"),
    "penalty": ("move", "xg_race", "shot_map"),
    "big_chance": ("move", "shot_map", "xg_race"),
    "red_card": ("compare", "momentum"),
    "momentum": ("momentum", "xg_race", "compare", "pass_network"),
    "tactical_change": ("momentum", "compare", "pass_network"),
    "summary": ("compare", "momentum", "xg_race", "shot_map", "pass_network", "defensive_map"),
    "half_time": ("xg_race", "compare", "shot_map", "momentum", "pass_network", "defensive_map"),
    "full_time": ("move", "xg_race", "shot_map", "compare", "momentum", "pass_network", "defensive_map"),
    "spotlight": ("compare", "momentum", "xg_race", "shot_map", "pass_network", "touch_map", "defensive_map"),
}
DEFAULTS: dict[str, tuple[str, ...]] = {
    "goal": ("move",), "penalty": ("move",), "big_chance": ("move",), "momentum": ("momentum",),
    "summary": ("compare",), "half_time": ("xg_race", "compare", "pass_network"),
    "full_time": ("move", "xg_race", "shot_map"),
}
COMPARE_KEYS = ("goals", "xg", "shots", "shots_on_target", "possession", "passes", "pass_accuracy", "corners",
                "fouls", "tackles_won")


def clip(steps: list[dict], label: str) -> Clip:
    return Clip(label=label, steps=[MoveStep.model_validate(s) for s in steps])


def _goal_label(g: dict, ref: Reference) -> str:
    return f"{g['clock']} {ref.player_short(g['player_id'])}"


def spot(row: list) -> PitchSpot:
    return PitchSpot.model_validate(dict(zip(SPOT_FIELDS, row)))


def min_link(nodes: dict[str, list]) -> int:
    """The fewest passes a pair needs to be drawn: 2 early on, rising to ~4 by full time, so the network stays
    readable (each completed pass counts once for each of its two players)."""
    return max(2, round(sum(n[2] for n in nodes.values()) / 200))


def build(state: MatchState, kind: str, ref: Reference, *, anchor: Event | None = None,
          evidence: list[Event] | None = None, team_id: str | None = None,
          player_id: str | None = None) -> Visual | None:
    """One visual of `kind` from what `state` has seen, or None when there's nothing to draw yet. `team_id` picks
    the team a pass network or defensive map shows first (default: home); `player_id` whose touch map."""
    h, a = state.home_id, state.away_id
    if not h or not a:
        return None
    v = Visual(type=kind, home_id=h, away_id=a)
    if kind == "pass_network":
        v.team_id = team_id if team_id in (h, a) else h
        for team in (h, a):
            net = state.passing.get(team, {"nodes": {}, "links": {}})
            least = min_link(net["nodes"])
            v.nodes += [NetworkNode(team_id=team, player_id=p, x=round(sx / n, 1), y=round(sy / n, 1), passes=n,
                                    on_pitch=p not in state.off) for p, (sx, sy, n) in net["nodes"].items()]
            v.links += [NetworkLink(team_id=team, a=pair.split()[0], b=pair.split()[1], passes=n)
                        for pair, n in net["links"].items() if n >= least]
        return v if v.links else None
    if kind == "touch_map":
        if player_id not in state.player_team:
            return None
        v.team_id, v.player_id = state.player_team[player_id], player_id
        v.spots = [spot(r) for r in state.spots if r[4] == player_id]
        return v if v.spots else None
    if kind == "defensive_map":
        v.team_id = team_id if team_id in (h, a) else h
        v.player_id = player_id if player_id and state.player_team.get(player_id) == v.team_id else None
        v.spots = [spot(r) for r in state.spots if r[2] in DEFENSIVE_TYPES]
        return v if v.spots else None
    if kind == "move":
        if anchor is not None:
            steps = move_steps(evidence or state.possession_chain(anchor.possession_id), anchor.team_id)
            who = ref.player_short(anchor.player_id) if anchor.player_id else ref.club_name(anchor.team_id)
            v.clips = [clip(steps, f"{anchor.clock} {who}")] if steps else []
        else:  # the reel: every goal so far
            v.clips = [clip(g["steps"], _goal_label(g, ref)) for g in state.goal_moves if g["steps"]]
        return v if v.clips else None
    if kind in ("shot_map", "xg_race"):
        v.shots = [ShotPoint.model_validate(s) for s in state.shots]
        if kind == "xg_race":
            v.now = state.match_minute()
            v.half_time = state.p1_end_minute + 1 if state.period == 2 else None
        return v if v.shots or kind == "xg_race" else None
    if kind == "momentum":
        last = int(state.match_minute())
        series = [MinuteThreat(minute=m, home=round(state.threat_by_t[h].get(m, 0.0), 2),
                               away=round(state.threat_by_t[a].get(m, 0.0), 2)) for m in range(last + 1)]
        v.threat = series
        v.half_time = state.p1_end_minute + 1 if state.period == 2 else None
        return v if any(t.home or t.away for t in series) else None
    if kind == "compare":
        hs, as_ = state.team_stats(h), state.team_stats(a)
        poss = state.possession()
        rows = []
        for k in COMPARE_KEYS:
            hv, av = (poss[0], poss[1]) if k == "possession" else (hs.get(k, 0.0), as_.get(k, 0.0))
            rows.append(CompareRow(key=k, home=round(hv, 3), away=round(av, 3)))
        v.rows = rows
        return v
    raise ValueError(f"unknown visual {kind}")


def offered(state: MatchState, kind: InsightKind, ref: Reference, *, anchor: Event | None = None,
            evidence: list[Event] | None = None, team_id: str | None = None,
            player_id: str | None = None) -> dict[str, Visual]:
    """The visuals a moment can show, built now so nothing after the moment gets in. The pitch maps focus on the
    moment's team and player."""
    out: dict[str, Visual] = {}
    for t in OFFER.get(kind, ()):
        # A goal, chance or penalty replays its own move; the full-time recap replays every goal.
        use_anchor = anchor if t == "move" and kind != "full_time" else None
        v = build(state, t, ref, anchor=use_anchor, evidence=evidence if use_anchor else None, team_id=team_id,
                  player_id=player_id)
        if v is not None:
            out[t] = v
    return out


def choose(offer: dict[str, Visual], kind: str, picks: list[tuple[str, str]] | None = None,
           default: str | None = None) -> list[Visual]:
    """The visuals to publish: the agent's picks (type, caption) that are on offer, else `default` (a spotlight's
    story says which visual shows it best), else the kind's defaults."""
    chosen = [offer[t].model_copy(update={"caption": c}) for t, c in picks or [] if t in offer]
    seen: set[str] = set()
    chosen = [v for v in chosen if not (v.type in seen or seen.add(v.type))][:3]
    defaults = (default,) if default else DEFAULTS.get(kind, ())
    return chosen or [offer[t] for t in defaults if t in offer]


def _teams_first(v: Visual) -> list[str]:
    return [v.team_id, v.away_id if v.team_id == v.home_id else v.home_id]


def _network(v: Visual, team: str, ref: Reference) -> str:
    nodes = [n for n in v.nodes if n.team_id == team]
    links = [link for link in v.links if link.team_id == team]
    if not nodes:
        return f"{ref.club_name(team)}: no completed passes"
    hub = max(nodes, key=lambda n: n.passes)
    out = (f"{ref.club_name(team)}: {sum(n.passes for n in nodes) // 2} completed passes, most involved "
           f"{ref.player_short(hub.player_id)} ({hub.passes} made or received)")
    if links:
        top = max(links, key=lambda link: link.passes)
        out += f", strongest link {ref.player_short(top.a)}-{ref.player_short(top.b)} ({top.passes} passes)"
    return out


def _touches(v: Visual, ref: Reference) -> str:
    n = len(v.spots)
    half = sum(s.x > 50 for s in v.spots)
    box = sum(s.x >= 83 and 21 <= s.y <= 79 for s in v.spots)
    return (f"{ref.player_short(v.player_id)}: {n} touches, {round(100 * half / n)}% in the opponent's half, "
            f"{box} in the opponent's box")


def _defending(v: Visual, team: str, ref: Reference) -> str:
    mine = [s for s in v.spots if s.team_id == team]
    if not mine:
        return f"{ref.club_name(team)}: none"
    counts = ", ".join(f"{t.replace('_', ' ')} {sum(s.type == t for s in mine)}" for t in DEFENSIVE_TYPES)
    high = sum(s.x > 50 for s in mine)
    return f"{ref.club_name(team)}: {counts}; {high} of {len(mine)} in the opponent's half"


def describe_offer(offer: dict[str, Visual], ref: Reference) -> str:
    """For the fact sheet: what the author can choose to show, with the numbers a caption may quote."""
    lines = []
    for t, v in offer.items():
        detail = ""
        if t == "move":
            detail = f" ({sum(len(c.steps) for c in v.clips)} actions" + (f", {len(v.clips)} goals" if len(v.clips) > 1 else "") + ")"
        elif t in ("shot_map", "xg_race"):
            detail = f" ({len(v.shots)} shots)"
        elif t == "pass_network":
            detail = " (" + "; ".join(_network(v, team, ref) for team in _teams_first(v)) + ")"
        elif t == "touch_map":
            detail = f" ({_touches(v, ref)})"
        elif t == "defensive_map":
            detail = " (" + "; ".join(_defending(v, team, ref) for team in _teams_first(v)) + ")"
        lines.append(f"  {t}: {ABOUT[t]}{detail}")
    return "\n".join(lines)

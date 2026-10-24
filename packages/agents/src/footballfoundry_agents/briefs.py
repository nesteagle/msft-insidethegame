"""Freeze what was known at the moment a trigger fired.

The workflow can run seconds after the trigger while the match keeps going, so everything an agent sees is copied
here at trigger time. Nothing after `seq` can leak into an explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from footballfoundry_schema import Event, InsightKind, Visual

from footballfoundry_agents.prematch import describe_context, players_to_watch, season_context
from footballfoundry_agents.reference import Reference
from footballfoundry_agents.spotlight import find_angles, write_up
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.triggers import Moment
from footballfoundry_agents.visuals import describe_offer, offered

KEY_STATS = ("goals", "xg", "shots", "shots_on_target", "passes", "pass_accuracy", "corners", "fouls",
             "yellow_cards", "red_cards", "tackles_won", "interceptions")


@dataclass
class Brief:
    insight_id: str
    match_id: str
    kind: InsightKind
    priority: int
    seq: int
    period: int
    minute: int
    clock: str
    team_id: str | None
    player_ids: list[str]
    event_ids: list[str]
    facts: dict[str, Any]
    text: str  # the fact sheet the agents read
    previous: list[str] = field(default_factory=list)  # earlier headlines, to avoid repeating them
    visuals: dict[str, Visual] = field(default_factory=dict)  # what this moment can show, built at trigger time
    run_id: int = 0  # which run of the match (MatchState.run_id)


def describe(e: Event, ref: Reference) -> str:
    """One compact line per event, readable by a model and traceable by id."""
    who = ref.player_name(e.player_id) if e.player_id else ref.club_name(e.team_id)
    bits = [f"[{e.event_id}] {e.clock} {ref.club(e.team_id).short_name if ref.club(e.team_id) else e.team_id}",
            who, e.type]
    if e.outcome:
        bits.append(e.outcome)
    if e.related_player_id:
        bits.append({"substitution": "for ", "pressure": "on "}.get(e.type, "to ") + ref.player_name(e.related_player_id))
    if e.x is not None and e.end_x is not None:
        bits.append(f"({e.x:.0f},{e.y:.0f})->({e.end_x:.0f},{e.end_y:.0f})")
    elif e.x is not None:
        bits.append(f"at ({e.x:.0f},{e.y:.0f})")
    if e.xg is not None:
        bits.append(f"xG {e.xg:.2f}")
    if e.set_piece != "open_play":
        bits.append(e.set_piece)
    if e.under_pressure:
        bits.append("under pressure")
    if e.body_part and e.type == "shot":
        bits.append(e.body_part.replace("_", " "))
    q = e.qualifiers
    flags = [k for k in ("progressive", "long_ball", "through_ball", "cross", "cutback", "from_cross", "aerial",
                         "one_on_one", "fast_break", "long_range", "sprint", "counterpress") if q.get(k)]
    if flags:
        bits.append("+".join(flags))
    if q.get("speed_kmh") and e.type in ("shot", "carry"):
        bits.append(f"{q['speed_kmh']:.0f} km/h")
    if q.get("difficulty") is not None and e.type == "save":
        bits.append(f"{q['difficulty']} save")
    if e.type == "tactical_change":
        bits.append(f"{q.get('kind')}: " + (q.get("formation") or f"{q.get('from_role')} -> {q.get('role')}"))
    return " ".join(bits)


def physical_leaders(state: MatchState, ref: Reference) -> list[str]:
    """Distance and top-speed leaders from the latest period_end physical totals of each team."""
    latest: dict[str, dict] = {}
    for e in state.recent:
        if e.type == "period_end" and e.qualifiers.get("physical"):
            latest[e.team_id] = e.qualifiers["physical"]
    rows = [(pid, v) for team in latest.values() for pid, v in team.items()]
    if not rows:
        return []
    far = max(rows, key=lambda r: r[1]["distance_km"])
    fast = max(rows, key=lambda r: r[1]["top_speed_kmh"])
    sprints = max(rows, key=lambda r: r[1]["sprints"])
    return [f"most distance: {ref.player_name(far[0])} {far[1]['distance_km']:.1f} km",
            f"top speed: {ref.player_name(fast[0])} {fast[1]['top_speed_kmh']:.1f} km/h",
            f"most sprints: {ref.player_name(sprints[0])} {sprints[1]['sprints']}"]


STYLE_WORDS = (("possession", "keep the ball"), ("directness", "play direct"), ("press_intensity", "press high"),
               ("width", "use the width"), ("tempo", "play at a high tempo"))


def style_phrase(club) -> str:
    """A club's two strongest style dials in words: "like to keep the ball and press high"."""
    if club is None:
        return "play a balanced game"
    dials = sorted(((getattr(club.style, k), words) for k, words in STYLE_WORDS if getattr(club.style, k) >= 0.6),
                   reverse=True)[:2]
    return "like to " + " and ".join(w for _, w in dials) if dials else "play a balanced game"


def preview(state: MatchState, ref: Reference) -> dict[str, Any]:
    """The two teams as they kick off: formation (from the team sheets), manager, style, and the ground."""
    formation = {e.team_id: e.qualifiers.get("formation") for e in state.recent
                 if e.type == "period_start" and e.period == 1}
    out: dict[str, Any] = {}
    for side, team in (("home", state.home_id), ("away", state.away_id)):
        club = ref.club(team)
        out[side] = {"club_id": team, "name": ref.club_name(team), "formation": formation.get(team) or
                     (club.formation if club else "?"), "manager": club.manager if club else "?",
                     "style": style_phrase(club)}
    home = ref.club(state.home_id)
    out["stadium"] = home.stadium if home else "?"
    return out


def _fmt(v: float, key: str) -> str:
    if key == "pass_accuracy":
        return f"{v:.0%}"
    if key == "xg":
        return f"{v:.2f}"
    return f"{v:.0f}"


def build(state: MatchState, moment: Moment, ref: Reference, *, lore: Any = None, season: Any = None) -> Brief:
    """`lore` and `season` (the season book) feed the opening minutes: the kick-off preview and players to watch."""
    h, a = state.home_id, state.away_id
    hs, as_ = state.team_stats(h), state.team_stats(a)
    poss = state.possession()
    mom = state.momentum()
    e = moment.anchor
    players = {pid: state.player_stats(pid) for pid in moment.player_ids}
    facts: dict[str, Any] = {
        "home": h, "away": a, "score": [state.home_score, state.away_score], "clock": e.clock,
        "possession": list(poss), "momentum_10min": list(mom),
        "home_stats": {k: hs.get(k, 0) for k in KEY_STATS}, "away_stats": {k: as_.get(k, 0) for k in KEY_STATS},
        "players": players,
        "anchor": e.model_dump(mode="json", exclude={"qualifiers"}) | {
            "qualifiers": {k: v for k, v in e.qualifiers.items() if k not in ("physical", "lineup")}},
        "keeper": state.keepers.get(state.opponent(e.team_id) or ""),
    }

    lines = [
        f"MATCH {state.match_id}: {ref.club_name(h)} ({h}, home) {state.home_score}-{state.away_score} "
        f"{ref.club_name(a)} ({a}, away), {e.clock}, period {e.period}.",
        f"MOMENT: {moment.kind} at event seq {e.seq}: {describe(e, ref)}",
        "",
        "TEAM STATS SO FAR (home | away):",
        f"  possession (pass share) {poss[0]:.0%} | {poss[1]:.0%}",
    ]
    lines += [f"  {k} {_fmt(hs.get(k, 0), k)} | {_fmt(as_.get(k, 0), k)}" for k in KEY_STATS]
    lines.append(f"  attacking threat, last 10 min: {mom[0]:.1f} | {mom[1]:.1f}")
    for pid, s in players.items():
        shown = ", ".join(f"{k} {_fmt(v, k)}" for k, v in s.items() if v and k in KEY_STATS + ("touches",))
        lines.append(f"PLAYER {ref.player_label(pid)}: {shown or 'no stats yet'}")
    if moment.evidence:
        lines += ["", "EVIDENCE (the move or incident, oldest first):"]
        lines += ["  " + describe(x, ref) for x in moment.evidence]
    if moment.kind in ("kick_off", "players_to_watch"):
        facts["season"] = season_context(season, state.match_id, h, a)
    if moment.kind == "kick_off":
        facts["preview"] = preview(state, ref)
        p = facts["preview"]
        lines += ["", "KICK-OFF: set the scene for the viewer before anything has happened. Introduce the two teams, "
                  "how they line up and like to play, and the story coming into the match. Use season_query for "
                  "their form, the table and head-to-head (it only sees matchdays before this one).",
                  f"  {p['home']['name']} (home, at {p['stadium']}): {p['home']['formation']}, manager "
                  f"{p['home']['manager']}, style: {p['home']['style']}",
                  f"  {p['away']['name']} (away): {p['away']['formation']}, manager {p['away']['manager']}, "
                  f"style: {p['away']['style']}"]
        if facts["season"]:
            lines.append("  Season so far (matchdays before this one): " + describe_context(facts["season"], ref, h, a))
    player_ids = list(moment.player_ids)
    if moment.kind == "players_to_watch":
        facts["watch"] = players_to_watch(state, lore, facts["season"], ref)
        player_ids = [w["player_id"] for w in facts["watch"]]
        lines += ["", "PLAYERS TO WATCH: introduce one player from each side the viewer should watch today, and why "
                  "(a scoring run, a standout ability, their role in how the team plays). Suggested, from the data: "
                  + " ".join(w["reason"] for w in facts["watch"]),
                  "Or pick others from the starting line-ups with the tools (season_query player_season for their "
                  "season so far). Don't repeat the kick-off preview."]
    if moment.kind == "spotlight" and moment.angle is not None:
        facts["angle"] = moment.angle.as_fact()
        others = find_angles(state, set(state.triggers.get("angles_used", [])))
        lines += ["", "SPOTLIGHT: nothing decisive has happened for a while, so take the initiative and tell the "
                  "viewer something interesting about this match that they may have missed. Suggested story (from "
                  "the data): " + write_up(facts["angle"], ref)[1]]
        if others:
            lines.append("Other stories you could tell instead: "
                         + " | ".join(write_up(a.as_fact(), ref)[1] for a in others[:3]))
        lines.append("Or find a better one with the tools. Don't repeat the earlier insights.")
    if moment.kind in ("summary", "half_time", "full_time", "momentum", "tactical_change", "spotlight"):
        recent_key = [x for x in state.recent if x.type in ("shot", "card", "substitution")][-8:]
        if recent_key:
            lines += ["", "RECENT KEY EVENTS:"] + ["  " + describe(x, ref) for x in recent_key]
    physical = physical_leaders(state, ref) if moment.kind in ("half_time", "full_time") else []
    if physical:
        lines += ["", "PHYSICAL (modelled from tracking-style workload):"] + ["  " + p for p in physical]
        facts["physical"] = physical
    visuals = offered(state, moment.kind, ref, anchor=e, evidence=moment.evidence, team_id=moment.team_id,
                      player_id=moment.player_ids[0] if moment.player_ids else None)
    if visuals:
        lines += ["", "VISUALS YOU CAN SHOW (drawn from the data; pick 0-2, each with a one-sentence caption):",
                  describe_offer(visuals, ref)]
    lines += ["", "Coordinates: x 0-100 from the acting team's own goal to the opponent's; the box starts at x=83."]

    return Brief(
        insight_id=moment.insight_id, match_id=state.match_id, kind=moment.kind, priority=moment.priority,
        seq=e.seq, period=e.period, minute=e.minute, clock=e.clock, team_id=moment.team_id,
        player_ids=player_ids, event_ids=[x.event_id for x in moment.evidence] or [e.event_id],
        facts=facts, text="\n".join(lines), visuals=visuals, run_id=state.run_id,
    )

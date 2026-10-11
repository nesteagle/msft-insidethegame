"""Scenarios: scripted tactical storylines with ground truth.

A scenario changes how a team plays from a given minute:

    style      press_intensity, defensive_line, directness, possession, width, tempo (deltas, -1..1). Not
               announced in the event stream, as in a real data feed: the agents have to notice it.
    role       a player (by id, or by role: "RW" = whoever plays right wing) moves to another role. Announced as
               a `tactical_change` event, because it's visible on the pitch.
    formation  the team switches formation. Announced as a `tactical_change` event.

`truth()` measures what each change did (shots, xG, pressing, territory, the moved player's touches, before and
after) so an explanation can be checked against what was really scripted.

From the command line:
    footballfoundry stream --match-id M0001 --scenario scenarios/high-press.json
    footballfoundry match --tactic "58:home:press_intensity=+0.35,defensive_line=+0.25" --move "61:home:RW:AM"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footballfoundry.engine import FormationChange, MatchResult, RoleChange, TacticChange
from footballfoundry.formations import FORMATIONS
from footballfoundry_schema import Event, TeamStyle
from footballfoundry_schema.stats import CONTEXT

STYLE_KEYS = set(TeamStyle.model_fields)
WINDOW_MIN = 20  # minutes either side of a change used to measure its effect


def _side(text: str) -> int:
    value = text.strip().lower()
    if value in ("home", "h", "0"):
        return 0
    if value in ("away", "a", "1"):
        return 1
    raise ValueError(f"side must be home or away, not {text!r}")


@dataclass
class Scenario:
    name: str = "custom"
    description: str = ""
    tactics: list[TacticChange] = field(default_factory=list)
    roles: list[RoleChange] = field(default_factory=list)
    formations: list[FormationChange] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.tactics or self.roles or self.formations)

    def engine_kwargs(self) -> dict[str, Any]:
        return {"tactic_changes": self.tactics, "role_changes": self.roles, "formation_changes": self.formations}

    def describe(self) -> list[dict[str, Any]]:
        side = ("home", "away")
        out = [{"minute": c.minute, "side": side[c.side], "type": "style", "changes": c.changes, "announced": False}
               for c in self.tactics]
        out += [{"minute": c.minute, "side": side[c.side], "type": "role", "player": c.player_id, "to": c.role,
                 "announced": True} for c in self.roles]
        out += [{"minute": c.minute, "side": side[c.side], "type": "formation", "formation": c.formation,
                 "announced": True} for c in self.formations]
        return sorted(out, key=lambda c: c["minute"])

    def merge(self, other: Scenario) -> Scenario:
        return Scenario(other.name if other.name != "custom" else self.name,
                        other.description or self.description, self.tactics + other.tactics,
                        self.roles + other.roles, self.formations + other.formations)

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        """JSON: {"name", "description", "changes": [{"minute", "side", "type": style|role|formation, ...}]}"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        s = cls(name=data.get("name", Path(path).stem), description=data.get("description", ""))
        for c in data.get("changes", []):
            minute, side, kind = float(c["minute"]), _side(c["side"]), c["type"]
            if kind == "style":
                s.tactics.append(TacticChange(minute, side, _style_deltas(c["changes"])))
            elif kind == "role":
                s.roles.append(RoleChange(minute, side, c["player"], _role(c["to"])))
            elif kind == "formation":
                s.formations.append(FormationChange(minute, side, _formation(c["formation"])))
            else:
                raise ValueError(f"unknown change type {kind!r} (style, role or formation)")
        return s

    @classmethod
    def from_flags(cls, tactic: list[str] | None = None, move: list[str] | None = None,
                   formation: list[str] | None = None) -> Scenario:
        """--tactic MIN:SIDE:key=+delta,...  --move MIN:SIDE:PLAYER_OR_ROLE:ROLE  --formation MIN:SIDE:FORMATION"""
        s = cls()
        for spec in tactic or []:
            minute, side, body = spec.split(":", 2)
            deltas = dict(pair.split("=", 1) for pair in body.split(","))
            s.tactics.append(TacticChange(float(minute), _side(side), _style_deltas(deltas)))
        for spec in move or []:
            minute, side, who, role = spec.split(":")
            s.roles.append(RoleChange(float(minute), _side(side), who, _role(role)))
        for spec in formation or []:
            minute, side, f = spec.split(":")
            s.formations.append(FormationChange(float(minute), _side(side), _formation(f)))
        return s


def _style_deltas(raw: dict[str, Any]) -> dict[str, float]:
    out = {}
    for k, v in raw.items():
        if k.strip() not in STYLE_KEYS:
            raise ValueError(f"unknown style dial {k!r}; use one of {sorted(STYLE_KEYS)}")
        out[k.strip()] = float(v)
    return out


def _role(role: str) -> str:
    role = role.strip().upper()
    known = {r for slots in FORMATIONS.values() for r, _, _ in slots} | {"AM", "RM", "LM"}
    if role not in known:
        raise ValueError(f"unknown role {role!r}; use one of {sorted(known)}")
    return role


def _formation(f: str) -> str:
    if f not in FORMATIONS:
        raise ValueError(f"unknown formation {f!r}; use one of {sorted(FORMATIONS)}")
    return f


def scenario_from_args(args) -> Scenario:
    s = Scenario.load(args.scenario) if getattr(args, "scenario", None) else Scenario()
    return s.merge(Scenario.from_flags(getattr(args, "tactic", None), getattr(args, "move", None),
                                       getattr(args, "formation", None)))


# -- ground truth ------------------------------------------------------------------------------------------------


def _window(events: list[Event], team_id: str, lo: float, hi: float) -> dict[str, float]:
    """What a team did between two match minutes, measured from the events."""
    inside = [e for e in events if lo <= e.minute + e.second / 60 < hi]
    own = [e for e in inside if e.team_id == team_id]
    opp = [e for e in inside if e.team_id != team_id and e.type not in ("period_start", "period_end")]
    shots = [e for e in own if e.type == "shot"]
    passes = [e for e in own if e.type == "pass"]
    defensive = [e for e in own if e.type in ("tackle", "interception", "ball_recovery", "foul", "block")]
    opp_passes = [e for e in opp if e.type == "pass"]
    # PPDA: opponent passes in their own 60% per defensive action of ours in that area (lower = more pressing).
    opp_build_up = [e for e in opp_passes if (e.x or 0) < 60]
    high_actions = [e for e in defensive if (e.x or 0) > 40]
    minutes = max(1e-6, hi - lo)
    return {
        "minutes": round(minutes, 1),
        "shots": len(shots),
        "xg": round(sum(e.xg or 0 for e in shots), 2),
        "goals": sum(e.outcome == "goal" for e in shots),
        "passes": len(passes),
        "pass_accuracy": round(sum(e.outcome == "complete" for e in passes) / len(passes), 3) if passes else 0.0,
        "final_third_entries": sum(1 for e in own if e.type in ("pass", "carry") and e.outcome in (None, "complete")
                                   and (e.end_x or 0) > 66.7 >= (e.x or 0)),
        "ppda": round(len(opp_build_up) / len(high_actions), 2) if high_actions else None,
        "defensive_action_height_m": round(sum((e.x or 0) for e in defensive) / len(defensive) * 1.05, 1)
        if defensive else None,
        "opponent_passes_under_pressure": round(sum(e.under_pressure for e in opp_passes) / len(opp_passes), 3)
        if opp_passes else 0.0,
    }


def _touches(events: list[Event], player_id: str, lo: float, hi: float) -> dict[str, Any]:
    t = [e for e in events if e.player_id == player_id and e.x is not None and e.type not in CONTEXT
         and lo <= e.minute + e.second / 60 < hi]
    if not t:
        return {"touches": 0}
    return {"touches": len(t), "avg_x": round(sum(e.x for e in t) / len(t), 1),
            "avg_y": round(sum(e.y for e in t) / len(t), 1),
            "central_share": round(sum(30 <= e.y <= 70 for e in t) / len(t), 2)}


def truth(result: MatchResult, scenario: Scenario) -> dict[str, Any]:
    """The scripted changes and their measured effect, for checking the agents' explanations."""
    events, info = result.events, result.info
    teams = (info.home_club_id, info.away_club_id)
    end = max(e.minute + e.second / 60 for e in events)
    changes = []
    for c in scenario.describe():
        side = 0 if c["side"] == "home" else 1
        m = c["minute"]
        lo, hi = max(0.0, m - WINDOW_MIN), min(end, m + WINDOW_MIN)
        entry = dict(c, team_id=teams[side])
        entry["effect"] = {"before": _window(events, teams[side], lo, m), "after": _window(events, teams[side], m, hi)}
        if c["type"] == "role":
            ev = next((e for e in events if e.type == "tactical_change" and e.team_id == teams[side]
                       and e.qualifiers.get("kind") == "role" and abs(e.minute - m) < 15), None)
            if ev is None:
                entry["applied"] = False  # the player had already gone off
            else:
                entry["applied"] = True
                entry["player_id"] = ev.player_id
                entry["at"] = ev.clock
                entry["effect"]["player_before"] = _touches(events, ev.player_id, lo, m)
                entry["effect"]["player_after"] = _touches(events, ev.player_id, m, hi)
        elif c["type"] == "formation":
            ev = next((e for e in events if e.type == "tactical_change" and e.team_id == teams[side]
                       and e.qualifiers.get("kind") == "formation"), None)
            entry["applied"] = ev is not None
            if ev is not None:
                entry["at"] = ev.clock
        changes.append(entry)
    return {
        "match_id": info.match_id,
        "seed": info.seed,
        "home_club_id": info.home_club_id,
        "away_club_id": info.away_club_id,
        "final_score": [info.home_score, info.away_score],
        "scenario": scenario.name,
        "description": scenario.description,
        "window_minutes": WINDOW_MIN,
        "changes": changes,
    }


def write_truth(result: MatchResult, scenario: Scenario, out_dir: str | Path) -> Path:
    path = Path(out_dir) / f"{result.info.match_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(truth(result, scenario), indent=2), encoding="utf-8")
    return path

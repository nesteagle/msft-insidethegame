"""Box-score statistics derived from events.

One definition shared by the generator, the agents and the MCP server, so a "shot on target" or "pass accuracy"
means the same thing on every screen.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from footballfoundry_schema import Event

BOX_X = 83.0  # x where the penalty area starts (16.5m of 105m, from the attacking end)
BOX_Y = (21.0, 79.0)
FINAL_THIRD_X = 66.7

COUNTED = {
    "interception": "interceptions",
    "clearance": "clearances",
    "save": "saves",
    "take_on": "take_ons",
    "ball_recovery": "ball_recoveries",
    "carry": "carries",
    "block": "blocks",
    "pressure": "pressures",
}

CONTEXT = ("pressure", "possession_change")  # off-ball events: not touches, not part of the move


def add_event(s: dict[str, float], e: Event) -> None:
    """Fold one event into a running stats dict (a defaultdict(float))."""
    t, o = e.type, e.outcome
    if t == "pass":
        s["passes"] += 1
        s["passes_completed"] += o == "complete"
        s["crosses"] += bool(e.qualifiers.get("cross"))
        s["offsides"] += o == "offside"
        if e.set_piece == "corner":
            s["corners"] += 1
    elif t == "shot":
        s["shots"] += 1
        s["xg"] += e.xg or 0.0
        s["goals"] += o == "goal"
        s["shots_on_target"] += o in ("goal", "saved")
    elif t == "foul":
        s["fouls"] += 1
    elif t == "card":
        s["yellow_cards"] += o == "yellow"
        s["red_cards"] += o in ("red", "second_yellow")
    elif t == "tackle":
        s["tackles"] += 1
        s["tackles_won"] += o == "won"
    elif t in COUNTED:
        s[COUNTED[t]] += 1


def finish(s: dict[str, float]) -> dict[str, float]:
    """Derived ratios and rounding; returns a plain dict copy."""
    s = defaultdict(float, s)
    s["pass_accuracy"] = s["passes_completed"] / s["passes"] if s["passes"] else 0.0
    s["xg"] = round(s["xg"], 2)
    return dict(s)


def team_stats(events: Iterable[Event], team_id: str) -> dict[str, float]:
    s: dict[str, float] = defaultdict(float)
    for e in events:
        if e.team_id == team_id:
            add_event(s, e)
    return finish(s)


def player_stats(events: Iterable[Event], player_id: str) -> dict[str, float]:
    s: dict[str, float] = defaultdict(float)
    for e in events:
        if e.player_id == player_id and e.type != "substitution":
            add_event(s, e)
            s["touches"] += e.type in ("pass", "carry", "take_on", "shot")
        elif e.type == "pass" and e.related_player_id == player_id and e.outcome == "complete":
            s["passes_received"] += 1
    return finish(s)


def possession_share(events: Iterable[Event], home_id: str, away_id: str) -> tuple[float, float]:
    """Share of passes, the same possession proxy the generator's box score uses."""
    home = away = 0
    for e in events:
        if e.type == "pass":
            home += e.team_id == home_id
            away += e.team_id == away_id
    total = home + away or 1
    return round(home / total, 3), round(away / total, 3)


def in_box(x: float | None, y: float | None) -> bool:
    return x is not None and y is not None and x >= BOX_X and BOX_Y[0] <= y <= BOX_Y[1]


def threat(e: Event) -> float:
    """Attacking threat of one event, from the same ingredients as the viewer's momentum strip: shots (weighted by
    xG), completed passes into the box or the final third, and carries into the box."""
    if e.type == "shot":
        return 1.0 + 2.0 * (e.xg or 0.0)
    if e.type == "pass" and e.outcome == "complete":
        if in_box(e.end_x, e.end_y):
            return 0.3
        if (e.end_x or 0) >= FINAL_THIRD_X > (e.x or 0):
            return 0.1
    if e.type == "carry" and in_box(e.end_x, e.end_y) and not in_box(e.x, e.y):
        return 0.3
    return 0.0

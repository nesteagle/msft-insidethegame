"""Spotlight: when the match goes quiet, the analysts find a story on their own initiative.

No goal, chance or momentum swing has produced an insight for a while (see `TriggerConfig.idle_after_s`), so the
trigger engine fires a `spotlight` moment. This module finds the angles worth talking about in the match so far,
most interesting first: the player running the game, the one winning everything back, possession without
threat, a keeper keeping his side in it... The analyst agent gets them as suggestions (it may dig for its own with
the data tools); offline, the template writes up the first one. No angle is used twice in a match.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from footballfoundry_agents.reference import Reference
from footballfoundry_agents.state import MatchState


@dataclass
class Angle:
    key: str
    team_id: str
    player_id: str | None = None
    numbers: dict[str, Any] = field(default_factory=dict)
    visual: str | None = None  # the visual that shows it best

    @property
    def angle_id(self) -> str:
        # Once per team: a second "keeps winning it back" for another player of the same side would feel repetitive.
        return f"{self.key}:{self.team_id}"

    def as_fact(self) -> dict[str, Any]:
        return asdict(self) | {"angle_id": self.angle_id}


def _best(state: MatchState, score, minimum: float) -> tuple[str, str, float] | None:
    """(player, team, value) of the player with the highest `score`, if it reaches `minimum`."""
    rows = [(pid, state.player_team.get(pid), score(s)) for pid, s in state.players.items()]
    rows = [r for r in rows if r[1] and r[2] >= minimum]
    return max(rows, key=lambda r: r[2]) if rows else None


def find_angles(state: MatchState, used: set[str] | frozenset[str] = frozenset()) -> list[Angle]:
    """Stories in the match so far that nobody has told yet, most interesting first."""
    h, a = state.home_id, state.away_id
    if not h or not a:
        return []
    out: list[Angle] = []
    hs, as_ = state.team_stats(h), state.team_stats(a)
    stats = {h: hs, a: as_}
    poss = dict(zip((h, a), state.possession()))
    score = {h: state.home_score, a: state.away_score}

    for team, opp in ((h, a), (a, h)):
        # Creating more without being ahead: the scoreline hides who is really on top.
        if stats[team].get("xg", 0) - stats[opp].get("xg", 0) >= 0.6 and score[team] <= score[opp]:
            out.append(Angle("xg_without_reward", team, numbers={
                "opponent": opp, "xg": stats[team]["xg"], "opp_xg": stats[opp]["xg"],
                "score": [state.home_score, state.away_score]}, visual="xg_race"))
        # Plenty of the ball, little to show for it.
        if poss[team] >= 0.58 and stats[team].get("xg", 0) < stats[opp].get("xg", 0):
            out.append(Angle("possession_without_threat", team, numbers={
                "opponent": opp, "possession": round(poss[team] * 100), "xg": stats[team].get("xg", 0),
                "opp_xg": stats[opp].get("xg", 0)}, visual="compare"))
        # Not a single shot on target yet.
        if state.minute >= 25 and stats[team].get("shots_on_target", 0) == 0:
            out.append(Angle("no_shots_on_target", team, numbers={
                "shots": int(stats[team].get("shots", 0)), "minute": state.minute}, visual="shot_map"))
        # Camped in the opponent's half: corners pile up.
        if stats[team].get("corners", 0) - stats[opp].get("corners", 0) >= 4:
            out.append(Angle("corner_pressure", team, numbers={
                "opponent": opp, "corners": int(stats[team].get("corners", 0)),
                "opp_corners": int(stats[opp].get("corners", 0))},
                visual="compare"))

    keeper = _best(state, lambda s: s.get("saves", 0), 3)
    if keeper:
        out.append(Angle("busy_keeper", keeper[1], keeper[0], {"saves": int(keeper[2])}, visual="shot_map"))
    passer = _best(state, lambda s: s.get("passes_completed", 0), 15)
    if passer:
        s = state.players[passer[0]]
        out.append(Angle("passing_hub", passer[1], passer[0], {
            "completed": int(s.get("passes_completed", 0)),
            "accuracy": round(100 * s.get("passes_completed", 0) / max(1, s.get("passes", 0)))}, visual="compare"))
    winner = _best(state, lambda s: s.get("tackles_won", 0) + s.get("interceptions", 0) + s.get("ball_recoveries", 0), 4)
    if winner:
        s = state.players[winner[0]]
        out.append(Angle("ball_winner", winner[1], winner[0], {
            "won": int(winner[2]), "tackles": int(s.get("tackles_won", 0)),
            "interceptions": int(s.get("interceptions", 0)), "recoveries": int(s.get("ball_recoveries", 0))},
            visual="momentum"))
    dribbler = _best(state, lambda s: s.get("take_ons", 0), 3)
    if dribbler:
        out.append(Angle("dribbler", dribbler[1], dribbler[0], {"take_ons": int(dribbler[2])}, visual="momentum"))
    return [x for x in out if x.angle_id not in used]


def write_up(angle: dict[str, Any], ref: Reference) -> tuple[str, str, str, dict[str, str]]:
    """Headline, text, why it matters and on-screen numbers for an angle (the offline spotlight)."""
    n, key = angle["numbers"], angle["key"]
    team, opp = ref.club_name(angle["team_id"]), ref.club_name(n.get("opponent"))
    who = ref.player_name(angle.get("player_id"))
    short = ref.player_short(angle.get("player_id"))
    if key == "xg_without_reward":
        hs, as_ = n["score"]
        return (f"{ref.club(angle['team_id']).short_name if ref.club(angle['team_id']) else team} deserve more",
                f"{team} have created far more ({n['xg']:.2f} xG to {n['opp_xg']:.2f}), yet the score is {hs}-{as_}.",
                "Chances like these usually turn into goals sooner or later.",
                {"xG": f"{n['xg']:.2f} - {n['opp_xg']:.2f}"})
    if key == "possession_without_threat":
        return (f"{team}: the ball, but not the chances",
                f"{team} have {n['possession']}% of the ball, but {opp} have the better chances "
                f"({n['opp_xg']:.2f} xG to {n['xg']:.2f}).",
                "Possession only matters if it turns into chances.",
                {"Possession": f"{n['possession']}%", "xG": f"{n['xg']:.2f} - {n['opp_xg']:.2f}"})
    if key == "no_shots_on_target":
        return (f"{team} still to test the keeper",
                f"{n['minute']} minutes in, {team} have had {n['shots']} shot{'s' if n['shots'] != 1 else ''} "
                "and none on target.",
                "Without a shot on target, the keeper has nothing to do.",
                {"Shots": str(n["shots"]), "On target": "0"})
    if key == "corner_pressure":
        return (f"Corner after corner for {team}",
                f"{team} have won {n['corners']} corners to {n['opp_corners']}: they're camped in {opp}'s half.",
                "Pressure like this often brings a set-piece goal.",
                {"Corners": f"{n['corners']} - {n['opp_corners']}"})
    if key == "busy_keeper":
        return (f"{short} keeping {team} in it",
                f"{who} has already made {n['saves']} saves.",
                "A goalkeeper this busy is a sign of who is pushing.",
                {"Saves": str(n["saves"])})
    if key == "passing_hub":
        return (f"{short} is running the game",
                f"{who} has completed {n['completed']} passes at {n['accuracy']}% for {team}.",
                "Everything goes through him: stop him and you stop them.",
                {"Passes completed": str(n["completed"]), "Accuracy": f"{n['accuracy']}%"})
    if key == "ball_winner":
        parts = ", ".join(f"{v} {label}{'s' if v != 1 else ''}" for v, label in (
            (n["tackles"], "tackle"), (n["interceptions"], "interception"), (n["recoveries"], "recovery"),
        ) if v).replace("recoverys", "recoveries")
        return (f"{short} keeps winning it back",
                f"{who} has won the ball back {n['won']} times for {team}: {parts}.",
                "Winning it back this often turns defence into attack.",
                {"Balls won": str(n["won"])})
    if key == "dribbler":
        return (f"{short} keeps taking them on",
                f"{who} has tried {n['take_ons']} take-ons for {team}.",
                "A player who runs at defenders draws fouls and opens space.",
                {"Take-ons": str(n["take_ons"])})
    raise ValueError(f"unknown angle {key}")

"""Live state of one match, folded event by event.

Kept compact on purpose: running counters plus the most recent events, so it can be snapshotted to Cosmos DB and
restored after a restart without replaying the whole match.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from footballfoundry_schema import Event
from footballfoundry_schema.stats import add_event, finish, threat

RECENT = 200  # events kept for evidence (a long possession is ~40)
MOVE_STEPS = 15  # actions kept per goal move, for the animated replays
MOVE_TYPES = ("pass", "carry", "take_on", "shot")


def move_steps(chain: list[Event], team_id: str | None, limit: int = MOVE_STEPS) -> list[dict[str, Any]]:
    """The attacking team's on-ball actions of a move, compact, for an animated replay."""
    steps = [{"seq": e.seq, "clock": e.clock, "type": e.type, "team_id": e.team_id, "player_id": e.player_id,
              "outcome": e.outcome, "x": e.x, "y": e.y, "end_x": e.end_x, "end_y": e.end_y}
             for e in chain if e.type in MOVE_TYPES and e.team_id == team_id and e.x is not None]
    return steps[-limit:]


def match_seconds(e: Event) -> int:
    """Continuous match time in seconds (minute keeps counting through added time)."""
    return e.minute * 60 + e.second


class MatchState:
    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self.run_id = 0  # when this run of the match kicked off (ms); a re-stream from kick-off gets a new one
        self.home_id: str | None = None
        self.away_id: str | None = None
        self.last_seq = 0
        self.period = 0
        self.minute = 0
        self.clock = "0'"
        self.home_score = 0
        self.away_score = 0
        self.finished = False
        self.teams: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.players: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.threat_by_minute: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self.recent: deque[Event] = deque(maxlen=RECENT)
        self.off: set[str] = set()  # substituted off or sent off
        self.keepers: dict[str, str] = {}  # team -> goalkeeper, from the kick-off team sheets
        self.player_team: dict[str, str] = {}  # every player seen on the ball -> their team
        # The whole match, compact, for charts and the full-time reel (`recent` only reaches back ~200 events).
        self.shots: list[dict[str, Any]] = []
        self.p1_end_minute = 45  # the second half's clock restarts at 45'; charts continue from here
        # Threat per continuous minute (as ShotPoint.t), for charts: `threat_by_minute` keys on the clock minute,
        # where first-half stoppage time and the second half's 45'-48' share a key.
        self.threat_by_t: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self.goal_moves: list[dict[str, Any]] = []  # {seq, clock, player_id, steps}
        self.triggers: dict[str, Any] = {}  # bookkeeping owned by TriggerEngine

    # -- folding ---------------------------------------------------------------------------------------------

    def apply(self, e: Event) -> bool:
        """Fold one event in. Returns False for duplicates and stale redeliveries, which must be ignored."""
        if e.seq == 1 and self.last_seq > 1:
            self.__init__(self.match_id)  # the same match id is being replayed from the start
        if e.seq <= self.last_seq:
            return False
        if e.seq == 1:
            self.run_id = time.time_ns() // 1_000_000
        self.last_seq = e.seq
        self.period, self.minute, self.clock = e.period, e.minute, e.clock
        self.home_score, self.away_score = e.home_score, e.away_score
        if e.type == "period_start" and e.period == 1:
            gk = next((p["player_id"] for p in e.qualifiers.get("lineup", []) if p.get("role") == "GK"), None)
            if gk:
                self.keepers[e.team_id] = gk
            if self.home_id is None:
                self.home_id = e.team_id
            elif self.away_id is None and e.team_id != self.home_id:
                self.away_id = e.team_id
        if e.type == "period_end" and e.period == 2:
            self.finished = True
        if e.type == "period_end" and e.period == 1:
            self.p1_end_minute = max(45, e.minute)
        add_event(self.teams[e.team_id], e)
        if e.player_id and e.type != "substitution":
            add_event(self.players[e.player_id], e)
            self.player_team[e.player_id] = e.team_id
        if e.player_id and (e.type == "substitution" or (e.type == "card" and e.outcome in ("red", "second_yellow"))):
            self.off.add(e.player_id)
        t = threat(e)
        if t:
            self.threat_by_minute[e.team_id][e.minute] += t
            self.threat_by_t[e.team_id][int(self._t(e))] += t
        self.recent.append(e)
        if e.type == "shot" and e.x is not None:
            t = self._t(e)
            self.shots.append({"seq": e.seq, "period": e.period, "minute": e.minute, "t": round(t, 2),
                               "clock": e.clock, "team_id": e.team_id,
                               "player_id": e.player_id, "x": e.x, "y": e.y, "xg": e.xg or 0.0,
                               "outcome": e.outcome})
            if e.outcome == "goal":
                self.goal_moves.append({"seq": e.seq, "clock": e.clock, "player_id": e.player_id,
                                        "steps": move_steps(self.possession_chain(e.possession_id), e.team_id)})
        return True

    # -- views -----------------------------------------------------------------------------------------------

    def team_stats(self, team_id: str | None) -> dict[str, float]:
        return finish(self.teams[team_id]) if team_id else {}

    def player_stats(self, player_id: str | None) -> dict[str, float]:
        return finish(self.players[player_id]) if player_id else {}

    def possession(self) -> tuple[float, float]:
        h = self.teams[self.home_id].get("passes", 0.0) if self.home_id else 0.0
        a = self.teams[self.away_id].get("passes", 0.0) if self.away_id else 0.0
        total = h + a or 1.0
        return round(h / total, 3), round(a / total, 3)

    def momentum(self, window_min: int = 10) -> tuple[float, float]:
        """Attacking threat for (home, away) over the last `window_min` match minutes."""
        lo = self.minute - window_min + 1

        def total(team_id: str | None) -> float:
            return sum(v for m, v in self.threat_by_minute[team_id].items() if m >= lo) if team_id else 0.0

        return round(total(self.home_id), 2), round(total(self.away_id), 2)

    def _t(self, e: Event) -> float:
        """Minutes from kick-off without the restart at 45' (see `ShotPoint.t`)."""
        return e.minute + e.second / 60 + (self.p1_end_minute - 44 if e.period == 2 else 0)

    def match_minute(self) -> float:
        """Where the match is, in the shots' continuous minutes (see `ShotPoint.t`)."""
        last = self.recent[-1] if self.recent else None
        second = last.second / 60 if last else 0.0
        return round(self.minute + second + (self.p1_end_minute - 44 if self.period == 2 else 0), 2)

    def possession_chain(self, possession_id: int, limit: int = 30) -> list[Event]:
        return [e for e in self.recent if e.possession_id == possession_id][-limit:]

    def opponent(self, team_id: str | None) -> str | None:
        if team_id == self.home_id:
            return self.away_id
        if team_id == self.away_id:
            return self.home_id
        return None

    # -- persistence -----------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "run_id": self.run_id,
            "home_id": self.home_id,
            "away_id": self.away_id,
            "last_seq": self.last_seq,
            "period": self.period,
            "minute": self.minute,
            "clock": self.clock,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "finished": self.finished,
            "teams": {k: dict(v) for k, v in self.teams.items()},
            "players": {k: dict(v) for k, v in self.players.items()},
            "threat_by_minute": {k: {str(m): t for m, t in v.items()} for k, v in self.threat_by_minute.items()},
            "threat_by_t": {k: {str(m): t for m, t in v.items()} for k, v in self.threat_by_t.items()},
            "recent": [e.model_dump(mode="json") for e in self.recent],
            "off": sorted(self.off),
            "keepers": self.keepers,
            "player_team": self.player_team,
            "shots": self.shots,
            "p1_end_minute": self.p1_end_minute,
            "goal_moves": self.goal_moves,
            "triggers": self.triggers,
        }

    @classmethod
    def restore(cls, snap: dict[str, Any]) -> MatchState:
        s = cls(snap["match_id"])
        s.run_id = snap.get("run_id", 0)
        for k in ("home_id", "away_id", "last_seq", "period", "minute", "clock", "home_score", "away_score",
                  "finished"):
            setattr(s, k, snap[k])
        for k, v in snap["teams"].items():
            s.teams[k].update(v)
        for k, v in snap["players"].items():
            s.players[k].update(v)
        for k, v in snap["threat_by_minute"].items():
            s.threat_by_minute[k].update({int(m): t for m, t in v.items()})
        for k, v in snap.get("threat_by_t", {}).items():
            s.threat_by_t[k].update({int(m): t for m, t in v.items()})
        s.recent.extend(Event.model_validate(e) for e in snap["recent"])
        s.off = set(snap.get("off", []))
        s.keepers = dict(snap.get("keepers", {}))
        s.player_team = dict(snap.get("player_team", {}))
        s.shots = list(snap.get("shots", []))
        s.p1_end_minute = snap.get("p1_end_minute", 45)
        s.goal_moves = list(snap.get("goal_moves", []))
        s.triggers = dict(snap.get("triggers", {}))
        return s

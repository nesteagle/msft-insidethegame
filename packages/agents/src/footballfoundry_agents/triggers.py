"""Trigger rules: decide which events are worth an explanation.

Plain code, not an LLM. A match has ~1,900 events; only a handful a match become agent runs. Must-show moments
(goals, red cards, missed penalties, half-time, full-time) always fire. Minor ones (big chances, momentum swings,
rolling summaries) are rate-limited per match so a busy spell can't flood the agents. When nothing has fired for
a while, a spotlight moment lets the analysts find a story on their own initiative (see spotlight.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from footballfoundry_schema import Event, InsightKind

from footballfoundry_agents.spotlight import Angle, find_angles
from footballfoundry_agents.state import MatchState, match_seconds


@dataclass(frozen=True)
class TriggerConfig:
    big_chance_xg: float = 0.3
    summary_every_min: int = 15
    momentum_window_min: int = 10
    momentum_share: float = 0.72  # one side's share of threat in the window
    momentum_min_threat: float = 4.0  # ignore "swings" in quiet spells
    momentum_cooldown_min: int = 12
    minor_cap: int = 3  # minor moments allowed per `minor_window_min`
    minor_window_min: int = 10
    quiet_after_major_s: int = 60  # no minor moment this soon after a must-show one
    # Spotlight: no insight for this many real seconds AND this many match minutes -> the analysts find a story.
    # Real time is what the viewer feels (10 s is 5 match minutes at 30x); the match-time floor stops a live (1x)
    # stream from asking for one every 10 seconds.
    idle_after_s: float = 10.0
    idle_min_gap_min: int = 3


@dataclass
class Moment:
    match_id: str
    kind: InsightKind
    anchor: Event
    evidence: list[Event] = field(default_factory=list)
    team_id: str | None = None
    player_ids: list[str] = field(default_factory=list)
    priority: int = 2  # 1 must show, 2 notable, 3 filler
    angle: Angle | None = None  # spotlight: the story suggested to the analysts

    @property
    def insight_id(self) -> str:
        # Deterministic, so a redelivered event or a restarted service upserts instead of duplicating.
        return f"{self.match_id}-{self.kind}-{self.anchor.seq:05d}"


class TriggerEngine:
    def __init__(self, config: TriggerConfig | None = None, clock: Callable[[], float] = time.time) -> None:
        self.cfg = config or TriggerConfig()
        self.clock = clock  # wall clock (seconds), for the spotlight's idle time; injectable for tests

    def check(self, state: MatchState, e: Event) -> list[Moment]:
        """Call after `state.apply(e)` returned True."""
        t = state.triggers
        moments: list[Moment] = []
        major = self._major(state, e)
        if major:
            t["last_major_s"] = match_seconds(e)
            moments.append(major)
        else:
            minor = self._minor(state, e)
            if minor and self._admit_minor(state, e):
                moments.append(minor)
        if not moments:
            spotlight = self._spotlight(state, e)
            if spotlight:
                moments.append(spotlight)
        if moments or e.type == "period_start":  # the idle clock restarts at kick-off and after each insight
            t["last_moment_wall"] = self.clock()
            t["last_moment_s"] = match_seconds(e)
        return moments

    # -- initiative ------------------------------------------------------------------------------------------

    def _spotlight(self, state: MatchState, e: Event) -> Moment | None:
        """The match has gone quiet: suggest a story the analysts haven't told yet."""
        t = state.triggers
        if "last_moment_wall" not in t or e.type not in ("pass", "carry") or "+" in e.clock:
            return None
        if self.clock() - t["last_moment_wall"] < self.cfg.idle_after_s:
            return None
        if match_seconds(e) - t.get("last_moment_s", 0) < self.cfg.idle_min_gap_min * 60:
            return None
        angles = find_angles(state, set(t.get("angles_used", [])))
        if not angles:
            return None
        angle = angles[0]
        t["angles_used"] = t.get("angles_used", []) + [angle.angle_id]
        return Moment(state.match_id, "spotlight", e, [], angle.team_id,
                      [angle.player_id] if angle.player_id else [], 3, angle=angle)

    # -- must-show -------------------------------------------------------------------------------------------

    def _major(self, state: MatchState, e: Event) -> Moment | None:
        if e.type == "shot" and e.outcome == "goal":
            chain = state.possession_chain(e.possession_id)
            return Moment(state.match_id, "goal", e, chain, e.team_id, _players(chain, e), 1)
        if e.type == "shot" and e.set_piece == "penalty":
            chain = state.possession_chain(e.possession_id, limit=5)
            return Moment(state.match_id, "penalty", e, chain, e.team_id, [e.player_id] if e.player_id else [], 1)
        # Kick-off: both teams emit period_start; react to the away team's, which comes second, so both line-ups
        # are known. A preview means viewers get something from the first whistle, not only after the first chance.
        if e.type == "period_start" and e.period == 1 and e.team_id == state.away_id:
            return Moment(state.match_id, "kick_off", e, [], None, [], 2)
        if e.type == "tactical_change":  # a formation switch or a player moved: visible, so worth explaining
            return Moment(state.match_id, "tactical_change", e, [e], e.team_id, [e.player_id] if e.player_id else [], 2)
        if e.type == "card" and e.outcome in ("red", "second_yellow"):
            before = [x for x in list(state.recent)[-6:] if x.type == "foul" or x.seq == e.seq]
            return Moment(state.match_id, "red_card", e, before, e.team_id, [e.player_id] if e.player_id else [], 1)
        # Both teams emit period_end; react to the away team's, which comes second, so both teams'
        # physical totals are in the recent events.
        if e.type == "period_end" and e.team_id == state.away_id:
            kind: InsightKind = "half_time" if e.period == 1 else "full_time"
            return Moment(state.match_id, kind, e, [], None, [], 1)
        return None

    # -- notable / filler ------------------------------------------------------------------------------------

    def _minor(self, state: MatchState, e: Event) -> Moment | None:
        t = state.triggers
        if e.type == "period_start":
            t["next_summary_min"] = e.minute + self.cfg.summary_every_min
            return None
        if e.type == "shot" and (e.xg or 0) >= self.cfg.big_chance_xg:
            chain = state.possession_chain(e.possession_id)
            return Moment(state.match_id, "big_chance", e, chain, e.team_id, _players(chain, e), 2)

        if e.type in ("pass", "carry", "shot", "tackle", "interception") and state.home_id and state.away_id:
            home, away = state.momentum(self.cfg.momentum_window_min)
            total = home + away
            if total >= self.cfg.momentum_min_threat and e.minute >= self.cfg.momentum_window_min:
                leader = state.home_id if home >= away else state.away_id
                share = max(home, away) / total
                cooled = e.minute - t.get("last_momentum_min", -999) >= self.cfg.momentum_cooldown_min
                if share >= self.cfg.momentum_share and leader != t.get("momentum_leader") and cooled:
                    t["momentum_leader"] = leader
                    t["last_momentum_min"] = e.minute
                    return Moment(state.match_id, "momentum", e, [], leader, [], 2)

        next_summary = t.get("next_summary_min", self.cfg.summary_every_min)
        # Not in added time: the half-time / full-time report is moments away.
        if e.minute >= next_summary and e.type in ("pass", "carry") and "+" not in e.clock:
            t["next_summary_min"] = (e.minute // self.cfg.summary_every_min + 1) * self.cfg.summary_every_min
            return Moment(state.match_id, "summary", e, [], None, [], 3)
        return None

    def _admit_minor(self, state: MatchState, e: Event) -> bool:
        t = state.triggers
        now = match_seconds(e)
        if now - t.get("last_major_s", -10_000) < self.cfg.quiet_after_major_s:
            return False
        window = self.cfg.minor_window_min * 60
        recent = [s for s in t.get("minor_s", []) if now - s < window]
        if len(recent) >= self.cfg.minor_cap:
            t["minor_s"] = recent
            return False
        t["minor_s"] = recent + [now]
        return True


def _players(chain: list[Event], anchor: Event) -> list[str]:
    """Shooter first, then the last two other players involved in the move."""
    ids = [anchor.player_id] if anchor.player_id else []
    for e in reversed(chain):
        if e.player_id and e.player_id not in ids and e.team_id == anchor.team_id:
            ids.append(e.player_id)
        if len(ids) >= 3:
            break
    return ids

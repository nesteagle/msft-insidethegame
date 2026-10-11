"""Possession-chain match engine.

The match is simulated one on-ball action at a time. At each step the player
on the ball chooses an action (pass, carry, take-on, cross, shot, clearance)
with probabilities shaped by their attributes, their team's tactical style,
where they are on the pitch, opposition pressure and the game state. Each
action resolves to "possession continues" or a restart for one of the teams.

The engine is deterministic for a given seed and uses no LLMs. The constants
below are tuned so that season averages land in Premier League-like ranges;
see calibration.py and tests/test_calibration.py.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime

from footballfoundry.formations import (ATTACKING_ROLES, COMPATIBLE, DEFENSIVE_ROLES, FORMATIONS, ROLE_COORDS,
                                       ROLE_EQUIVALENTS)
from footballfoundry.pitch import (
    PENALTY_SPOT,
    clamp,
    distance_m,
    expected_goals,
    flip,
    in_box,
)
from footballfoundry_schema import Attributes, Club, Event, LineupEntry, League, MatchInfo, Player, TeamStyle
from footballfoundry.world import role_rating

PERIOD_S = 45 * 60

# Dead time before each restart is taken: (mean, sd) in seconds.
DEAD_TIME = {
    "kick_off": (8, 3),
    "goal_kick": (25, 6),
    "throw_in": (13, 4),
    "corner": (33, 8),
    "free_kick": (24, 9),
    "penalty": (70, 15),
    "keeper": (7, 3),
    "open_play": (0, 0),
}
GOAL_CELEBRATION = (65, 12)
SUB_DEAD_TIME = (25, 6)
CARD_DEAD_TIME = (25, 8)

# Pass model
PASS_BASE = 0.955
PASS_PER_METRE = 0.0050
PASS_SKILL = 0.003
PASS_PRESSURE = 0.10
PASS_FINAL_THIRD = 0.06
PASS_INTO_BOX = 0.18
PASS_LONG = 0.10
CROSS_BASE = 0.24
OFFSIDE_BASE = 0.05

# Action selection weights (relative to a pass = 1.0)
CARRY_BASE = 0.42
TAKE_ON_BASE = 0.05
CROSS_BASE_W = 0.22
SHOT_SCALE = 0.205
CLEARANCE_W = 0.5

# Duels and discipline
CHALLENGE_ON_CARRY = 0.22
FOUL_SHARE = 0.14
YELLOW_BASE = 0.17
RED_DIRECT = 0.0015
BOX_FOUL_FACTOR = 0.06  # defenders avoid fouling in their own box
FINAL_THIRD_CHALLENGE = 0.38
OUT_FOR_THROW = 0.22  # share of misplaced passes that end up in touch

# Shot outcomes for non-goals
SAVE_SHARE = 0.29
BLOCK_SHARE = 0.26
POST_SHARE = 0.045

# Match dynamics: what makes a match ebb and flow rather than play out evenly.
MOMENTUM_TAU_S = 240  # a spell of pressure fades over ~4 match minutes
MOMENTUM_EDGE = 0.03  # quality edge at full momentum against none
FATIGUE_DEFENCE = 0.12  # tired defenders make late chances easier
CHASE_START_MIN = 55  # trailing teams start to push from here, fully by +30 min
COUNTER_ACTIONS = 4  # a fast break lasts at most this many actions
THROUGH_BALL_BASE = 0.007
LONG_SHOT_W = 0.018
CUTBACK_SHARE = 0.2
PENALTY_BASE = 0.77  # conversion before taker and keeper quality

# Diminishing returns on ability: in the engine, ratings sit closer to the league average than on the player card,
# so an XI of 90+ players dominates without scoring 130 goals a season. Physical data uses the raw ratings.
ATTR_PIVOT = 72
ATTR_SPREAD = 0.75
# Passes into the box find late runners from midfield and the flanks, not only the centre-forward: a lone striker
# takes about a quarter to a third of his team's shots, not half.
BOX_X_RUNS = 80
BOX_STRIKER_W = 0.4
BOX_RUNNER_W = 1.6
LATE_RUNNERS = {"AM", "CM", "RW", "LW", "RM", "LM"}

# Physical model (km/h). Sprint threshold as used by top-flight tracking providers.
SPRINT_KMH = 25.2
DISTANCE_PER_90_KM = {
    "GK": 5.4, "CB": 10.0, "RB": 10.8, "LB": 10.8, "RWB": 11.0, "LWB": 11.0, "DM": 11.1, "CM": 11.6,
    "AM": 11.1, "RM": 11.0, "LM": 11.0, "RW": 10.7, "LW": 10.7, "ST": 10.2,
}
SPRINTS_PER_90 = {
    "GK": 0, "CB": 12, "RB": 30, "LB": 30, "RWB": 34, "LWB": 34, "DM": 16, "CM": 20, "AM": 24, "RM": 30,
    "LM": 30, "RW": 34, "LW": 34, "ST": 30,
}


@dataclass
class Slot:
    """A player currently on the pitch in a formation role."""

    player: Player
    role: str
    base_x: float
    base_y: float
    fatigue: float = 0.0
    yellow: bool = False
    on_minute: float = 0.0  # when this player came on (0 for starters)
    off_minute: float | None = None  # when they left the pitch
    top_speed_kmh: float = 0.0  # fastest on-ball carry so far
    a: Attributes = field(init=False, repr=False)  # effective ratings in the match, see ATTR_SPREAD

    def __post_init__(self) -> None:
        raw = self.player.attributes
        self.a = Attributes.model_construct(**{k: int(round(ATTR_PIVOT + (v - ATTR_PIVOT) * ATTR_SPREAD))
                                               for k, v in raw.model_dump().items()})

    @property
    def vmax_kmh(self) -> float:
        """Top running speed: 29 km/h for the slowest players, ~36 for the quickest."""
        return 27.5 + self.player.attributes.pace * 0.085


@dataclass
class Side:
    club: Club
    is_home: bool
    slots: list[Slot]
    bench: list[Player]
    planned_subs: list[float]
    subs_made: int = 0
    score: int = 0
    sent_off: int = 0
    style_override: dict[str, float] = field(default_factory=dict)
    momentum: float = 0.0  # 0..1, built by sustained attacking, fades over a few minutes
    formation: str = ""
    finished: list[Slot] = field(default_factory=list)  # players who left the pitch, for the physical summary

    @property
    def id(self) -> str:
        return self.club.club_id

    def goalkeeper(self) -> Slot:
        return next(s for s in self.slots if s.role == "GK")


@dataclass
class Restart:
    side: int
    kind: str
    x: float
    y: float
    player: Slot | None = None


@dataclass
class TacticChange:
    """A scheduled change to a team's style dials, applied from `minute` onward.

    This is the hook the scenario injector uses: e.g. "home team presses higher
    from 58'" is TacticChange(58, side=0, changes={"press_intensity": 0.3}).
    Values are deltas added to the club's base style. Style changes are not
    announced in the event stream (a real data feed doesn't either): they are
    for the agents to discover, with the answer in the scenario's truth file.
    """

    minute: float
    side: int
    changes: dict[str, float]


@dataclass
class RoleChange:
    """A player moves to another role from `minute`, e.g. a winger moved into a central role. If someone
    already plays that role, the two swap. Visible on the pitch, so it's announced as `tactical_change`."""

    minute: float
    side: int
    player_id: str
    role: str


@dataclass
class FormationChange:
    """A team switches formation from `minute`; players are re-assigned to the best-fitting roles."""

    minute: float
    side: int
    formation: str


@dataclass
class MatchResult:
    info: MatchInfo
    events: list[Event]


def pick_lineup(squad: list[Player], formation: str) -> tuple[list[tuple[Player, str, float, float]], list[Player]]:
    """Greedy best-XI for the formation, then a 9-player bench with a reserve keeper."""
    available = list(squad)
    slots = FORMATIONS[formation]
    chosen: dict[int, Player] = {}
    # Fill the scarcest roles first so specialists are not used up elsewhere.
    for idx in sorted(range(len(slots)), key=lambda i: len(COMPATIBLE[slots[i][0]])):
        best = max(available, key=lambda p: role_rating(p, slots[idx][0]))
        available.remove(best)
        chosen[idx] = best
    xi = [(chosen[i], role, bx, by) for i, (role, bx, by) in enumerate(slots)]

    keepers = sorted((p for p in available if p.position == "GK"), key=lambda p: -p.overall)
    outfield = sorted((p for p in available if p.position != "GK"), key=lambda p: -p.overall)
    bench = keepers[:1] + outfield[:8]
    return xi, bench


class MatchEngine:
    def __init__(
        self,
        league: League,
        home_club_id: str,
        away_club_id: str,
        *,
        match_id: str = "M0001",
        seed: int = 0,
        matchday: int = 1,
        kickoff: datetime | None = None,
        tactic_changes: list[TacticChange] | None = None,
        role_changes: list[RoleChange] | None = None,
        formation_changes: list[FormationChange] | None = None,
    ) -> None:
        self.league = league
        self.match_id = match_id
        self.seed = seed
        self.matchday = matchday
        self.kickoff = kickoff or datetime(2026, 8, 15, 15, 0)
        self.rng = random.Random(seed)
        # Speeds and physical totals use their own stream, so they never change what happens in the match.
        self.prng = random.Random(seed * 7919 + 17)
        self.tactic_changes = sorted(tactic_changes or [], key=lambda c: c.minute)
        self.pending_changes: list[RoleChange | FormationChange] = sorted(
            [*(role_changes or []), *(formation_changes or [])], key=lambda c: c.minute)
        self.applied_changes: list[dict] = []  # what actually happened, for the truth file
        self.counter = 0  # actions left in a fast break
        self.sides = [self._build_side(home_club_id, True), self._build_side(away_club_id, False)]
        self.referee = self._referee_name()

        self.events: list[Event] = []
        self.lineups: list[LineupEntry] = self._lineup_entries()
        self.period = 1
        self.t = 0.0
        self.added: list[int] = []
        self._added_this_period: int | None = None
        self._period_counts = {"goals": 0, "subs": 0, "cards": 0}
        self.ball_in_play = 0.0
        self.poss = 0
        self.possession_id = 0
        self.won_in_play_t: float | None = None  # when the side on the ball won it back in open play
        self.spell_t0 = 0.0  # when the side on the ball got it, for possession_change
        self.spell_passes = 0
        self.carrier: Slot | None = None
        self.x = 50.0
        self.y = 50.0
        self.set_piece = "open_play"

    # ------------------------------------------------------------------ setup

    def _build_side(self, club_id: str, is_home: bool) -> Side:
        club = self.league.club(club_id)
        xi, bench = pick_lineup(self.league.squad(club_id), club.formation)
        slots = [Slot(p, role, bx, by) for p, role, bx, by in xi]
        n_subs = self.rng.choice([2, 3, 3, 4, 4, 5, 5, 5])
        planned = sorted(self.rng.uniform(55, 88) for _ in range(n_subs))
        return Side(club, is_home, slots, bench, planned, formation=club.formation)

    def _referee_name(self) -> str:
        first = ["Alan", "Chris", "Daniel", "Graham", "Lee", "Mark", "Paul", "Robert", "Simon", "Tom", "Rebecca", "Sian"]
        last = ["Ashworth", "Bramley", "Carver", "Denholm", "Fairley", "Hartigan", "Lowther", "Pennock", "Ridley", "Thwaite"]
        return f"{self.rng.choice(first)} {self.rng.choice(last)}"

    def _lineup_entries(self) -> list[LineupEntry]:
        entries = []
        for side in self.sides:
            entries += [LineupEntry(club_id=side.id, player_id=s.player.player_id, role=s.role, starter=True) for s in side.slots]
            entries += [LineupEntry(club_id=side.id, player_id=p.player_id, role=None, starter=False) for p in side.bench]
        return entries

    def _team_sheet(self, i: int) -> dict:
        """Formation, starting XI (role and base position in the team's frame) and bench, sent with kick-off so
        a live consumer can draw the teams without the match file, which holds the final score."""
        side = self.sides[i]
        return {
            "formation": side.club.formation,
            "lineup": [{"player_id": s.player.player_id, "role": s.role, "x": s.base_x, "y": s.base_y}
                       for s in side.slots],
            "bench": [p.player_id for p in side.bench],
        }

    # ------------------------------------------------------------------ main loop

    def simulate(self) -> MatchResult:
        for period in (1, 2):
            self.period = period
            self.t = 0.0
            self._added_this_period = None
            self._period_counts = {"goals": 0, "subs": 0, "cards": 0}
            for i in (0, 1):
                self._emit("period_start", i, None, qualifiers=self._team_sheet(i) if period == 1 else None)
            restart: Restart | None = Restart(0 if period == 1 else 1, "kick_off", 50.0, 50.0)
            while restart is not None and not self._period_over():
                restart = self._play_possession(restart)
            for i in (0, 1):
                self._emit("period_end", i, None, qualifiers={"physical": self._physical(i)})
        return MatchResult(self._info(), self.events)

    def _physical(self, i: int) -> dict[str, dict[str, float]]:
        """Distance, sprints and top speed so far for everyone who has played for side i.

        The engine simulates the ball, not every off-ball run, so these are modelled: role workload, minutes
        played, stamina, the team's pressing, plus the fastest carry actually made on the ball.
        """
        side = self.sides[i]
        minute = self._minute()
        press = self._style(i).press_intensity
        out = {}
        for s in [*side.finished, *side.slots]:
            end = s.off_minute if s.off_minute is not None else minute
            played = max(0.0, end - s.on_minute)
            if played <= 0:
                continue
            share = played / 90
            work = (0.93 + s.player.attributes.stamina / 900) * (0.96 + 0.08 * press)
            dist = DISTANCE_PER_90_KM.get(s.role, 10.5) * share * work * self.prng.uniform(0.96, 1.04)
            sprints = SPRINTS_PER_90.get(s.role, 20) * share * (0.6 + s.player.attributes.pace / 160) * self.prng.uniform(0.8, 1.2)
            top = max(s.top_speed_kmh, s.vmax_kmh * self.prng.uniform(0.88, 0.98)) if s.role != "GK" else \
                s.vmax_kmh * 0.7
            out[s.player.player_id] = {"distance_km": round(dist, 2), "sprints": int(round(sprints)),
                                       "top_speed_kmh": round(top, 1), "minutes": round(played)}
        return out

    # ------------------------------------------------------------------ scenario changes

    def _apply_due_changes(self) -> None:
        """Role and formation changes due by now. Applied at a stoppage, as a manager would."""
        minute = self._minute()
        while self.pending_changes and self.pending_changes[0].minute <= minute:
            change = self.pending_changes.pop(0)
            side = self.sides[change.side]
            if isinstance(change, FormationChange):
                self._switch_formation(side, change.formation)
                quals = {"kind": "formation", "formation": change.formation}
            else:
                # `player_id` may name a role instead ("RW": whoever plays there), so a scenario works for any club;
                # a formation without that role uses its nearest equivalent (RW -> RM -> RWB).
                slot = next((s for s in side.slots if s.player.player_id == change.player_id), None)
                for role in [change.player_id, *ROLE_EQUIVALENTS.get(change.player_id, [])]:
                    slot = slot or next((s for s in side.slots if s.role == role), None)
                if slot is None:  # substituted or sent off before the change was due
                    continue
                change = RoleChange(change.minute, change.side, slot.player.player_id, change.role)
                swapped = self._move_player(side, slot, change.role)
                quals = {"kind": "role", "role": change.role, "from_role": swapped[0]}
                if swapped[1]:
                    quals["swapped_with"] = swapped[1]
            quals["lineup"] = [{"player_id": s.player.player_id, "role": s.role, "x": s.base_x, "y": s.base_y}
                               for s in side.slots]
            player = next((s for s in side.slots if isinstance(change, RoleChange)
                           and s.player.player_id == change.player_id), None)
            self._emit("tactical_change", change.side, player, qualifiers=quals)
            self.applied_changes.append({"minute": round(minute, 1), "side": change.side, **{
                k: v for k, v in quals.items() if k != "lineup"},
                **({"player_id": change.player_id} if isinstance(change, RoleChange) else {})})

    def _move_player(self, side: Side, slot: Slot, role: str) -> tuple[str, str | None]:
        old_role = slot.role
        other = next((s for s in side.slots if s.role == role and s is not slot), None)
        if other is not None:  # swap positions
            other.role, other.base_x, other.base_y, slot.role, slot.base_x, slot.base_y = (
                slot.role, slot.base_x, slot.base_y, other.role, other.base_x, other.base_y)
            return old_role, other.player.player_id
        bx, by = ROLE_COORDS[role]
        slot.role, slot.base_x, slot.base_y = role, bx, by
        return old_role, None

    def _switch_formation(self, side: Side, formation: str) -> None:
        roles = FORMATIONS[formation]
        outfield = [s for s in side.slots if s.role != "GK"]
        free = [r for r in roles if r[0] != "GK"]
        # Fill the scarcest roles first with whoever suits them best, as in pick_lineup.
        for role, bx, by in sorted(free, key=lambda r: len(COMPATIBLE[r[0]])):
            if not outfield:
                break
            best = max(outfield, key=lambda s: role_rating(s.player, role))
            outfield.remove(best)
            best.role, best.base_x, best.base_y = role, bx, by
        side.formation = formation

    def _info(self) -> MatchInfo:
        home, away = self.sides
        return MatchInfo(
            match_id=self.match_id,
            league_id=self.league.league_id,
            season=self.league.season,
            matchday=self.matchday,
            kickoff=self.kickoff,
            venue=home.club.stadium,
            referee=self.referee,
            seed=self.seed,
            home_club_id=home.id,
            away_club_id=away.id,
            home_formation=home.club.formation,
            away_formation=away.club.formation,
            lineups=self.lineups,
            home_score=home.score,
            away_score=away.score,
            added_time=self.added,
            ball_in_play_s=round(self.ball_in_play, 1),
        )

    def _period_over(self) -> bool:
        if self.t < PERIOD_S:
            return False
        if self._added_this_period is None:
            c = self._period_counts
            base = 1.0 if self.period == 1 else 3.0
            added = base + 0.5 * c["goals"] + 0.3 * c["subs"] + 0.3 * c["cards"] + self.rng.gauss(0, 0.8)
            self._added_this_period = int(clamp(round(added), 0, 6 if self.period == 1 else 10))
            self.added.append(self._added_this_period)
        return self.t >= PERIOD_S + self._added_this_period * 60

    def _play_possession(self, restart: Restart) -> Restart | None:
        changed = restart.side != self.poss and self.t > 0
        cause = self.events[-1].type if changed else None
        spell = {"previous_team_id": self.sides[self.poss].id, "previous_duration_s": round(self.t - self.spell_t0, 1),
                 "previous_passes": self.spell_passes}
        if restart.side != self.poss or self.t == 0:
            self.spell_t0, self.spell_passes = self.t, 0
        self.won_in_play_t = self.t if restart.kind == "open_play" else None
        self.poss = restart.side
        self.possession_id += 1
        self.x, self.y = restart.x, restart.y
        self.set_piece = restart.kind
        self.counter = 0
        if restart.kind != "open_play":
            mean, sd = DEAD_TIME[restart.kind]
            if restart.kind != "kick_off" or self.t > 0:
                dead = max(1.0, self.rng.gauss(mean, sd))
                if self._lead(restart.side) > 0:  # the leading side takes its time
                    dead *= 1 + 0.35 * self._urgency()
                self._advance(dead, in_play=False)
            self._maybe_substitute()
            self._apply_due_changes()
        elif restart.x < 50:
            self._maybe_counter(restart)
        self.carrier = restart.player or self._taker(restart)
        if changed:
            self._emit("possession_change", self.poss, self.carrier, x=self.x, y=self.y,
                       qualifiers={"cause": cause, **spell})

        result = self._restart_action(restart)
        self.set_piece = "open_play"
        while result is None:
            if self._period_over():
                return None
            result = self._open_play_action()
        return result

    # ------------------------------------------------------------------ time, state helpers

    def _advance(self, dt: float, *, in_play: bool = True) -> None:
        self.t += dt
        if in_play:
            self.ball_in_play += dt
        decay = math.exp(-dt / MOMENTUM_TAU_S)
        for side in self.sides:
            side.momentum *= decay
            for s in side.slots:
                s.fatigue += dt / 5400 * (1.4 - s.a.stamina / 100)

    def _minute(self) -> float:
        return (self.period - 1) * 45 + self.t / 60

    def _lead(self, i: int) -> int:
        return self.sides[i].score - self.sides[1 - i].score

    def _urgency(self) -> float:
        """0 before the hour mark, rising to 1 by the last ten minutes: how hard the game state bites."""
        return clamp((self._minute() - CHASE_START_MIN) / 30, 0.0, 1.0)

    def _bump(self, i: int, amount: float) -> None:
        """Attacking pressure builds momentum: shots, corners, entries into the box."""
        self.sides[i].momentum = min(1.0, self.sides[i].momentum + amount)

    def _style(self, i: int) -> TeamStyle:
        """Base style + scheduled tactic changes + automatic game-state adjustments."""
        values = self.sides[i].club.style.model_dump()
        minute = self._minute()
        for change in self.tactic_changes:
            if change.minute > minute:
                break
            if change.side == i:
                for k, v in change.changes.items():
                    values[k] += v
        lead, u = self._lead(i), self._urgency()
        if lead < 0 and u > 0:  # chasing the game: higher, wider, more direct
            push = u * (1.0 if lead == -1 else 0.7)  # two down, heads drop a little
            values["directness"] += 0.15 * push
            values["press_intensity"] += 0.25 * push
            values["width"] += 0.12 * push
            values["defensive_line"] += 0.15 * push
            values["tempo"] += 0.1 * push
        elif lead > 0 and u > 0:  # protecting a lead: deeper, slower
            values["press_intensity"] -= 0.18 * u
            values["defensive_line"] -= 0.2 * u
            values["tempo"] -= 0.15 * u
        return TeamStyle(**{k: clamp(v, 0.0, 1.0) for k, v in values.items()})

    def _fatigue(self, i: int) -> float:
        outfield = [s.fatigue for s in self.sides[i].slots if s.role != "GK"]
        return sum(outfield) / len(outfield) if outfield else 0.0

    def _edge(self, i: int) -> float:
        """Attacking side's quality edge, applied to most success probabilities."""
        side, opp = self.sides[i], self.sides[1 - i]
        edge = (side.club.strength - opp.club.strength) * 0.0035
        edge += 0.013 if side.is_home else -0.013
        edge += 0.05 * (opp.sent_off - side.sent_off)
        edge -= 0.06 * self.carrier.fatigue if self.carrier else 0
        edge += FATIGUE_DEFENCE * self._fatigue(1 - i)  # tired legs at the back open games up late on
        edge += MOMENTUM_EDGE * (side.momentum - opp.momentum)
        if self._lead(i) < 0:  # the leader sits deeper and concedes territory
            edge += 0.03 * self._urgency()
        return edge

    def _pressure(self) -> bool:
        st = self._style(1 - self.poss)
        p = 0.22 + (0.32 * st.press_intensity if self.x < 50 else 0.0) + (0.25 if self.x > 70 else 0.0) + (0.2 if in_box(self.x, self.y) else 0.0)
        p -= 0.06 * self.sides[1 - self.poss].sent_off
        if self.counter:  # a fast break runs at a retreating defence
            p *= 0.45
        return self.rng.random() < p

    def _press(self) -> None:
        """Announce the defender closing down the carrier. Picked from `prng`, so it never changes the match."""
        opp = self.sides[1 - self.poss]
        fx, fy = flip(self.x, self.y)
        shift = (fx - 50) * 0.35
        cands = [s for s in opp.slots if s.role != "GK"] or opp.slots
        weights = [math.exp(-math.hypot(clamp(s.base_x + shift, 3, 95) - fx, s.base_y - fy) / 12) + 1e-6 for s in cands]
        presser = self.prng.choices(cands, weights=weights)[0]
        quals = {"counterpress": True} if self.won_in_play_t is not None and self.t - self.won_in_play_t <= 5 else {}
        self._emit("pressure", 1 - self.poss, presser, x=fx, y=fy, related=self.carrier, qualifiers=quals)

    def _maybe_counter(self, r: Restart) -> None:
        """Ball won in your own half against a team that has pushed up: a fast break."""
        opp = self._style(1 - r.side)
        own = self._style(r.side)
        p = 0.04 + 0.12 * opp.defensive_line + 0.06 * own.directness + 0.12 * self._urgency() * (self._lead(r.side) > 0)
        if self.rng.random() < p:
            self.counter = COUNTER_ACTIONS

    def _flags(self) -> dict:
        return {"fast_break": True} if self.counter else {}

    # ------------------------------------------------------------------ player selection

    def _weighted(self, slots: list[Slot], weights: list[float]) -> Slot:
        return self.rng.choices(slots, weights=weights)[0]

    def _receiver(
        self, tx: float, ty: float, exclude: Slot | None, *, aerial: bool = False, side_idx: int | None = None,
        attackers: bool = False,
    ) -> Slot:
        """Team-mate most likely to be at (tx, ty); the block shifts up/down with the ball."""
        side = self.sides[self.poss if side_idx is None else side_idx]
        shift = (tx - 50) * 0.35
        cands, weights = [], []
        for s in side.slots:
            if s is exclude:
                continue
            bx = clamp(s.base_x + shift, 3, 95)
            d = math.hypot(bx - tx, s.base_y - ty)
            w = math.exp(-d / 11)
            if s.role == "GK":
                w *= 0.3 if tx < 25 else 0.0
            if aerial:
                w *= 0.5 + s.a.heading / 60
                if tx > BOX_X_RUNS and s.role == "ST":  # crosses are attacked by several runners, not one target
                    w *= BOX_STRIKER_W ** 0.5
            if attackers:  # runs in behind are made by forwards, fast ones especially
                w *= (3.0 if s.role in ATTACKING_ROLES else 0.3) * (0.5 + s.a.pace / 100)
            elif tx > BOX_X_RUNS and not aerial:
                w *= BOX_STRIKER_W if s.role == "ST" else BOX_RUNNER_W if s.role in LATE_RUNNERS else 1.0
            cands.append(s)
            weights.append(w + 1e-6)
        return self._weighted(cands, weights)

    def _defender(self, x: float, y: float, *, include_gk: bool = False) -> Slot:
        """Opponent most likely to be involved at attacking-frame location (x, y)."""
        opp = self.sides[1 - self.poss]
        fx, fy = flip(x, y)
        shift = (fx - 50) * 0.35
        cands, weights = [], []
        for s in opp.slots:
            if s.role == "GK" and not include_gk:
                continue
            bx = clamp(s.base_x + shift, 3, 95)
            d = math.hypot(bx - fx, s.base_y - fy)
            cands.append(s)
            weights.append(math.exp(-d / 12) + 1e-6)
        return self._weighted(cands, weights)

    def _taker(self, r: Restart) -> Slot:
        slots = self.sides[r.side].slots
        outfield = [s for s in slots if s.role != "GK"]
        if r.kind in ("goal_kick", "keeper"):
            return next(s for s in slots if s.role == "GK")
        if r.kind == "kick_off":
            fwd = [s for s in outfield if s.role in ATTACKING_ROLES] or outfield
            return self.rng.choice(fwd)
        if r.kind == "penalty":
            return max(outfield, key=lambda s: s.a.finishing + s.a.composure)
        if r.kind == "corner":
            return max(outfield, key=lambda s: s.a.passing + self.rng.uniform(0, 8))
        if r.kind == "throw_in":
            return min(outfield, key=lambda s: abs(s.base_y - r.y) + abs(s.base_x - r.x) * 0.5 + self.rng.uniform(0, 10))
        # free kick: nearest decent passer
        return min(outfield, key=lambda s: math.hypot(s.base_x - r.x, s.base_y - r.y) - s.a.passing * 0.2 + self.rng.uniform(0, 8))

    # ------------------------------------------------------------------ restarts

    def _restart_action(self, r: Restart) -> Restart | None:
        rng = self.rng
        if r.kind == "open_play":  # turnover: the recovering player simply plays on
            return None
        if r.kind == "kick_off":
            return self._pass(target=(rng.uniform(30, 42), rng.uniform(30, 70)), bonus=0.2)
        if r.kind == "goal_kick":
            self.x, self.y = 5.5, rng.choice([40.0, 60.0])
            return self._pass(long=rng.random() < 0.2 + 0.6 * self._style(self.poss).directness)
        if r.kind == "keeper":
            return self._pass(long=rng.random() < 0.1 + 0.4 * self._style(self.poss).directness)
        if r.kind == "throw_in":
            return self._pass(long=False, bonus=0.0)
        if r.kind == "corner":
            return self._cross(corner=True)
        if r.kind == "penalty":
            self.x, self.y = PENALTY_SPOT
            return self._shot(penalty=True)
        if r.kind == "free_kick":
            if self.x > 70 and 25 < self.y < 75 and distance_m(self.x, self.y, 100, 50) < 32 and rng.random() < 0.4:
                # The free-kick specialist: usually a creative midfielder or winger, and not always the same one.
                self.carrier = max((s for s in self.sides[self.poss].slots if s.role not in ("GK", "ST")),
                                   key=lambda s: s.a.passing + 0.6 * s.a.finishing + rng.uniform(0, 18))
                return self._shot(free_kick=True)
            if self.x > 68 and rng.random() < 0.6:
                return self._cross()
            return self._pass(long=None, bonus=0.05)
        raise ValueError(r.kind)

    # ------------------------------------------------------------------ open play

    def _open_play_action(self) -> Restart | None:
        rng = self.rng
        s = self.carrier
        a = s.a
        x, y = self.x, self.y
        st = self._style(self.poss)
        pressure = self._pressure()

        if s.role == "GK":
            self.counter = 0
            if pressure:
                self._press()
            return self._pass(pressure=pressure, long=rng.random() < 0.15 + 0.5 * st.directness)

        side = self.sides[self.poss]
        zone_take_on = 1.8 if x > 66 else 0.6 if x > 40 else 0.1
        chase = 1 + 0.5 * self._urgency() if self._lead(self.poss) < 0 else 1.0
        fast = 1.8 if self.counter else 1.0
        weights = {
            "pass": 1.0,
            "carry": CARRY_BASE * (0.6 + a.dribbling / 100) * (1.3 - st.tempo * 0.5) * fast,
            "take_on": TAKE_ON_BASE * (a.dribbling / 60) ** 2 * zone_take_on,
            "shot": (self._shot_weight(x, y) + self._long_shot_weight(x, y, a.finishing))
                    * (0.75 + a.finishing / 200) * chase * (1 + 0.15 * side.momentum) * (0.7 if pressure else 1.0),
            "cross": CROSS_BASE_W * (0.4 + st.width) if x > 70 and (y < 22 or y > 78) else 0.0,
            "clearance": CLEARANCE_W if x < 18 and pressure and s.role in DEFENSIVE_ROLES else 0.0,
        }
        action = rng.choices(list(weights), weights=list(weights.values()))[0]
        if self.counter:
            self.counter -= 1
        if pressure and action in ("pass", "carry", "shot", "clearance"):  # the actions played under_pressure
            self._press()
        if action == "pass":
            return self._pass(pressure=pressure)
        if action == "carry":
            return self._carry(pressure)
        if action == "take_on":
            return self._take_on()
        if action == "shot":
            return self._shot(pressure=pressure)
        if action == "cross":
            return self._cross()
        return self._clearance_from_possession()

    @staticmethod
    def _shot_weight(x: float, y: float) -> float:
        if x < 70:
            return 0.0
        return SHOT_SCALE * expected_goals(x, y) ** 0.5

    @staticmethod
    def _long_shot_weight(x: float, y: float, finishing: int) -> float:
        """Speculative efforts from 20-30m: rarely go in, sometimes spectacular."""
        if not 64 < x < 80 or not 28 < y < 72:
            return 0.0
        return LONG_SHOT_W * (finishing / 80) ** 3

    def _pass(
        self,
        *,
        pressure: bool = False,
        long: bool | None = None,
        target: tuple[float, float] | None = None,
        bonus: float = 0.0,
    ) -> Restart | None:
        rng = self.rng
        st = self._style(self.poss)
        passer = self.carrier
        x, y = self.x, self.y

        through = False
        if target:
            tx, ty = target
            long = False
        elif long is None and 52 < x < 84 and self.set_piece == "open_play" and rng.random() < (
                THROUGH_BALL_BASE * (0.5 + passer.a.passing / 100) * (0.6 + self._style(1 - self.poss).defensive_line)
                * (3.0 if self.counter else 1.0)):
            # A ball in behind the defence, into the channel or straight down the middle.
            through, long = True, False
            tx, ty = rng.uniform(84, 92), clamp(y + rng.gauss(0, 10), 32, 68)
        else:
            if long is None:
                p_long = 0.03 + 0.14 * st.directness + (0.25 if pressure and x < 35 else 0.0)
                long = rng.random() < p_long
            if long:
                dx, dy = rng.gauss(min(34.0, (100 - x) * 0.55), 8), rng.gauss(0, 22)
            else:
                fwd = (2.5 + 7 * st.directness - 2.5 * st.possession) * (1 - x / 130)
                dx, dy = rng.gauss(fwd, 9), rng.gauss(0, 13)
            # Ground passes rarely reach the six-yard box; balls beyond it are overhit.
            tx = clamp(x + dx, 1.0, 94.0 if x + dx < 101 else 99.5)
            ty = y + dy
            if ty < 1 or ty > 99:  # reflect off the touchline, sometimes it simply goes out
                ty = -ty if ty < 1 else 198 - ty
            ty = clamp(ty, 0.5, 99.5)

        dist = distance_m(x, y, tx, ty)
        p = PASS_BASE - PASS_PER_METRE * dist + PASS_SKILL * (passer.a.passing - 65) + self._edge(self.poss) + bonus
        p += 0.03 * st.possession
        if pressure:
            p -= PASS_PRESSURE * (1.1 - passer.a.composure / 200)
        if tx > 66:
            p -= PASS_FINAL_THIRD
        if in_box(tx, ty):
            p -= PASS_INTO_BOX
        if long:
            p -= PASS_LONG
        if through:
            p += 0.10 if self.counter else 0.04  # the run is timed; the defence is turned
        p = clamp(p, 0.1, 0.985)

        self._advance(1.0 + dist / 17 + rng.uniform(0, 0.8))
        # Ball speed: driven long balls and through balls are quicker than short passes.
        speed = (self.prng.uniform(55, 80) if long or through else self.prng.uniform(28, 55)) * (0.85 + dist / 200)
        quals = {"length_m": round(dist, 1), "speed_kmh": round(min(speed, 95), 1), "difficulty": round(1 - p, 2)}
        if long:
            quals["long_ball"] = True
        if through:
            quals["through_ball"] = True
        if tx - x > 15 and tx > 50:
            quals["progressive"] = True
        quals |= self._flags()

        # Offside on forward balls into the final third; balls in behind are timed on the line.
        opp_line = self._style(1 - self.poss).defensive_line
        offside_p = OFFSIDE_BASE * (0.4 + opp_line) * (1.3 if through else 1.0)
        forward_ball = self.set_piece not in ("throw_in", "corner", "goal_kick") and tx > 72 and tx - x > 10
        if forward_ball and rng.random() < offside_p:
            receiver = self._receiver(tx, ty, passer, attackers=through)
            self._emit("pass", self.poss, passer, outcome="offside", x=x, y=y, end_x=tx, end_y=ty,
                       related=receiver, pressure=pressure, qualifiers=quals)
            fx, fy = flip(tx, ty)
            return Restart(1 - self.poss, "free_kick", fx, fy)

        if rng.random() < p:
            receiver = self._receiver(tx, ty, passer, aerial=long, attackers=through)
            self._emit("pass", self.poss, passer, outcome="complete", x=x, y=y, end_x=tx, end_y=ty,
                       related=receiver, pressure=pressure, qualifiers=quals)
            self.carrier, self.x, self.y = receiver, tx, ty
            if in_box(tx, ty):
                self._bump(self.poss, 0.05)
            elif tx > 66 >= x:
                self._bump(self.poss, 0.02)
            if through and rng.random() < 0.55:  # through on goal
                return self._shot(one_on_one=True)
            if long and rng.random() < 0.15:
                return self._aerial_foul_check()
            return None

        self._emit("pass", self.poss, passer, outcome="incomplete", x=x, y=y, end_x=tx, end_y=ty,
                   pressure=pressure, qualifiers=quals)
        if tx >= 99:
            return Restart(1 - self.poss, "goal_kick", 5.5, 50.0)
        if (ty < 4 or ty > 96) and rng.random() < 0.75 or rng.random() < OUT_FOR_THROW:
            fx, fy = flip(tx, ty)
            return Restart(1 - self.poss, "throw_in", clamp(fx, 1, 99), 0.0 if fy < 50 else 100.0)
        if long and rng.random() < 0.38:  # second ball won back
            return self._recover(self.poss, tx, ty)
        if dist < 25 and rng.random() < 0.55:
            return self._turnover("interception", tx, ty)
        return self._recover(1 - self.poss, *flip(tx, ty))

    def _aerial_foul_check(self) -> Restart | None:
        """Long balls are contested; occasionally that produces a foul either way."""
        defender = self._defender(self.x, self.y)
        if in_box(self.x, self.y) and self.rng.random() > BOX_FOUL_FACTOR:
            return None
        if self.rng.random() < 0.5:
            return self._foul(defender, self.x, self.y)
        # attacker fouls the defender: free kick to the defending side
        attacker = self.carrier
        self._advance(1.0)
        self._emit("foul", self.poss, attacker, x=self.x, y=self.y, related=defender, qualifiers={"aerial": True})
        self._maybe_card(self.poss, attacker, self.x)
        return Restart(1 - self.poss, "free_kick", *flip(self.x, self.y))

    def _cross(self, *, corner: bool = False) -> Restart | None:
        rng = self.rng
        crosser = self.carrier
        if corner:
            self.x, self.y = 99.5, rng.choice([0.5, 99.5])
            self._bump(self.poss, 0.06)
        x, y = self.x, self.y
        # From near the byline, a low pull-back to the edge of the six-yard box is often the better ball.
        cutback = not corner and x > 86 and rng.random() < CUTBACK_SHARE
        if cutback:
            tx, ty = rng.uniform(86, 91), rng.uniform(38, 62)
        else:
            tx, ty = rng.uniform(86, 97), rng.uniform(32, 68)
        p = CROSS_BASE + 0.004 * (crosser.a.passing - 65) + self._edge(self.poss) + (0.04 if corner else 0.0)
        if cutback:
            p += 0.16
        self._advance(2.0 + rng.uniform(0, 1))
        quals = {"cross": True, "length_m": round(distance_m(x, y, tx, ty), 1),
                 "speed_kmh": round(self.prng.uniform(38, 58) if cutback else self.prng.uniform(55, 85), 1),
                 "difficulty": round(1 - clamp(p, 0.05, 0.95), 2)}
        if cutback:
            quals["cutback"] = True
        quals |= self._flags()

        if rng.random() < p:
            receiver = self._receiver(tx, ty, crosser, aerial=not cutback)
            self._emit("pass", self.poss, crosser, outcome="complete", x=x, y=y, end_x=tx, end_y=ty,
                       related=receiver, qualifiers=quals)
            self.carrier, self.x, self.y = receiver, tx, ty
            self._bump(self.poss, 0.05)
            if cutback:
                return self._shot(assisted_cross=True) if rng.random() < 0.6 else None
            if rng.random() < (0.7 if corner else 0.55):
                header = rng.random() < (0.85 if corner else 0.6)
                return self._shot(header=header, assisted_cross=True)
            return None

        self._emit("pass", self.poss, crosser, outcome="incomplete", x=x, y=y, end_x=tx, end_y=ty, qualifiers=quals)
        defender = self._defender(tx, ty, include_gk=rng.random() < 0.2)
        fx, fy = flip(tx, ty)
        if defender.role == "GK":
            self._emit("save", 1 - self.poss, defender, outcome="claimed", x=fx, y=fy)
            return Restart(1 - self.poss, "keeper", 6.0, 50.0, defender)
        self._emit("clearance", 1 - self.poss, defender, x=fx, y=fy, body="head")
        self._advance(1.5)
        if rng.random() < 0.3:
            return Restart(self.poss, "corner", 99.5, 0.5)
        if rng.random() < 0.42:
            return self._recover(self.poss, rng.uniform(55, 72), rng.uniform(15, 85))
        return self._recover(1 - self.poss, rng.uniform(28, 45), rng.uniform(15, 85))

    def _carry(self, pressure: bool) -> Restart | None:
        rng = self.rng
        s = self.carrier
        x, y = self.x, self.y
        final_third = x > 66
        if pressure and rng.random() < (FINAL_THIRD_CHALLENGE if final_third else CHALLENGE_ON_CARRY):
            outcome = self._duel(s.a.dribbling * 0.5 + s.a.composure * 0.5)
            if outcome != "keep":
                return outcome
        if self.counter:  # driving into open space on the break
            dx = rng.uniform(5, 14)
        else:
            dx = rng.uniform(0.5, 4) if final_third else rng.uniform(1, 9)
        tx = clamp(x + dx * (0.7 + s.a.pace / 100), 0.5, 93.0)
        ty = clamp(y + rng.gauss(0, 5) + (50 - y) * (0.1 if final_third else 0.0), 1.0, 99.0)
        dist = distance_m(x, y, tx, ty)
        self._advance(0.4 + dist / rng.uniform(4.0, 6.5))
        quals = self._carry_speed(s, dist, open_space=bool(self.counter))
        quals |= self._flags()
        self._emit("carry", self.poss, s, x=x, y=y, end_x=tx, end_y=ty, pressure=pressure, qualifiers=quals)
        if in_box(tx, ty) and not in_box(x, y):
            self._bump(self.poss, 0.05)
        self.x, self.y = tx, ty
        return None

    def _carry_speed(self, s: Slot, dist: float, *, open_space: bool) -> dict:
        """Running speed with the ball: near top speed on a break, far below it when crowded."""
        frac = self.prng.uniform(0.75, 0.97) if open_space and dist > 8 else self.prng.uniform(0.35, 0.7)
        if dist < 3:
            frac *= 0.7
        kmh = round(s.vmax_kmh * frac, 1)
        s.top_speed_kmh = max(s.top_speed_kmh, kmh)
        return {"speed_kmh": kmh, "sprint": True} if kmh >= SPRINT_KMH else {"speed_kmh": kmh}

    def _take_on(self) -> Restart | None:
        rng = self.rng
        s = self.carrier
        defender = self._defender(self.x, self.y)
        self._advance(1.5)
        p_foul = 0.13 + (65 - defender.a.discipline) * 0.002
        if in_box(self.x, self.y):
            p_foul *= BOX_FOUL_FACTOR
        if rng.random() < p_foul:
            self._emit("take_on", self.poss, s, outcome="fouled", x=self.x, y=self.y)
            return self._foul(defender, self.x, self.y)
        p = 0.48 + (s.a.dribbling - defender.a.tackling) * 0.006 + self._edge(self.poss)
        if rng.random() < p:
            self._emit("take_on", self.poss, s, outcome="complete", x=self.x, y=self.y, related=defender,
                       qualifiers=self._flags())
            nx = clamp(self.x + rng.uniform(4, 10), 0.5, 99.0)
            ny = clamp(self.y + rng.gauss(0, 6), 1.0, 99.0)
            quals = self._carry_speed(s, distance_m(self.x, self.y, nx, ny), open_space=True) | self._flags()
            self._emit("carry", self.poss, s, x=self.x, y=self.y, end_x=nx, end_y=ny, qualifiers=quals)
            if self.x > 66:
                self._bump(self.poss, 0.04)
            self.x, self.y = nx, ny
            return None
        self._emit("take_on", self.poss, s, outcome="failed", x=self.x, y=self.y, related=defender)
        fx, fy = flip(self.x, self.y)
        self._emit("tackle", 1 - self.poss, defender, outcome="won", x=fx, y=fy, related=s)
        if (self.y < 12 or self.y > 88) and rng.random() < 0.4:
            return Restart(self.poss, "throw_in", self.x, 0.0 if self.y < 50 else 100.0)
        return Restart(1 - self.poss, "open_play", fx, fy, defender)

    def _duel(self, attack_skill: float) -> Restart | str:
        """A challenge on the ball carrier: returns 'keep' or the resulting restart."""
        rng = self.rng
        defender = self._defender(self.x, self.y)
        st = self._style(1 - self.poss)
        self._advance(1.0)
        p_foul = FOUL_SHARE + (65 - defender.a.discipline) * 0.003 + 0.08 * st.press_intensity
        if defender.yellow:
            p_foul *= 0.6
        if in_box(self.x, self.y):
            p_foul *= BOX_FOUL_FACTOR
        if rng.random() < p_foul:
            return self._foul(defender, self.x, self.y)
        p_win = 0.5 + (defender.a.tackling - attack_skill) * 0.006 - self._edge(self.poss)
        fx, fy = flip(self.x, self.y)
        if rng.random() < p_win:
            self._emit("tackle", 1 - self.poss, defender, outcome="won", x=fx, y=fy, related=self.carrier)
            if (self.y < 12 or self.y > 88) and rng.random() < 0.4:
                return Restart(self.poss, "throw_in", self.x, 0.0 if self.y < 50 else 100.0)
            return Restart(1 - self.poss, "open_play", fx, fy, defender)
        self._emit("tackle", 1 - self.poss, defender, outcome="lost", x=fx, y=fy, related=self.carrier)
        return "keep"

    def _clearance_from_possession(self) -> Restart | None:
        rng = self.rng
        s = self.carrier
        self._advance(1.5)
        tx, ty = clamp(self.x + rng.uniform(25, 45), 1, 99), rng.uniform(5, 95)
        self._emit("clearance", self.poss, s, x=self.x, y=self.y, end_x=tx, end_y=ty, pressure=True)
        if rng.random() < 0.25:
            fx, fy = flip(tx, ty)
            return Restart(1 - self.poss, "throw_in", fx, 0.0 if fy < 50 else 100.0)
        if rng.random() < 0.4:
            return self._recover(self.poss, tx, ty)
        return self._recover(1 - self.poss, *flip(tx, ty))

    # ------------------------------------------------------------------ shots

    def _shot(
        self,
        *,
        pressure: bool = False,
        header: bool = False,
        penalty: bool = False,
        free_kick: bool = False,
        assisted_cross: bool = False,
        one_on_one: bool = False,
    ) -> Restart | None:
        rng = self.rng
        s = self.carrier
        x, y = self.x, self.y
        xg = expected_goals(x, y, header=header, penalty=penalty)
        if free_kick:
            xg = round(xg * 0.35, 4)
        if one_on_one:  # only the keeper to beat
            xg = round(min(0.62, xg * 1.6 + 0.08), 4)
        elif self.counter:  # fewer defenders back
            xg = round(min(0.7, xg * 1.15), 4)
        if pressure and not one_on_one:
            xg = round(xg * 0.8, 4)
        gk = self.sides[1 - self.poss].goalkeeper()
        finishing = s.a.heading if header else s.a.finishing
        if penalty:
            p_goal = clamp(PENALTY_BASE + (s.a.composure - 65) * 0.003 + (s.a.finishing - 65) * 0.002
                           - (gk.a.goalkeeping - 65) * 0.003, 0.55, 0.92)
        else:
            p_goal = xg * (0.7 + finishing / 200 + (s.a.composure - 65) / 400) * (1 + (60 - gk.a.goalkeeping) / 200)
        self._advance(1.0)

        body = "head" if header else ("left_foot" if s.player.foot == "left" else "right_foot")
        if s.player.foot == "both" or rng.random() < 0.2:
            body = "head" if header else rng.choice(["left_foot", "right_foot"])
        set_piece = "penalty" if penalty else ("free_kick" if free_kick else self.set_piece)
        quals: dict = {"speed_kmh": self._shot_speed(s, header, distance_m(x, y, 100, 50))}
        if assisted_cross:
            quals["from_cross"] = True
        if one_on_one:
            quals["one_on_one"] = True
        if x < 80:
            quals["long_range"] = True
        quals |= self._flags()
        end_y = clamp(50 + rng.gauss(0, 4), 44.6, 55.4)
        self._bump(self.poss, 0.10)

        if rng.random() < p_goal:
            side = self.sides[self.poss]
            side.score += 1
            self._period_counts["goals"] += 1
            # How hard the finish was to stop: a goal's on-target xG is above its pre-shot xG.
            quals["on_target_xg"] = round(clamp(max(xg, 0.05) * self.prng.uniform(1.2, 3.2), 0.08, 0.99), 3)
            self._emit("shot", self.poss, s, outcome="goal", x=x, y=y, end_x=100.0, end_y=end_y, xg=xg,
                       body=body, pressure=pressure, set_piece=set_piece, qualifiers=quals)
            # The scorer's spell eases off; the conceding side has to respond.
            side.momentum *= 0.5
            self._bump(1 - self.poss, 0.22)
            self._advance(max(30.0, rng.gauss(*GOAL_CELEBRATION)), in_play=False)
            return Restart(1 - self.poss, "kick_off", 50.0, 50.0)

        r = rng.random()
        fx, fy = flip(x, y)
        if r < SAVE_SHARE or penalty and r < 0.66:
            on_target = round(clamp(max(xg, 0.03) * self.prng.uniform(0.5, 2.4), 0.02, 0.95), 3)
            quals["on_target_xg"] = on_target
            self._emit("shot", self.poss, s, outcome="saved", x=x, y=y, end_x=100.0, end_y=end_y, xg=xg,
                       body=body, pressure=pressure, set_piece=set_piece, qualifiers=quals)
            difficulty = "great" if on_target >= 0.4 else "good" if on_target >= 0.15 else "routine"
            self._emit("save", 1 - self.poss, gk, outcome="saved", x=2.0, y=100 - end_y, related=s,
                       qualifiers={"on_target_xg": on_target, "difficulty": difficulty})
            r2 = rng.random()
            if r2 < 0.55:
                return Restart(1 - self.poss, "keeper", 6.0, 50.0, gk)
            if r2 < 0.82:
                return Restart(self.poss, "corner", 99.5, 0.5)
            if rng.random() < 0.45:
                return self._recover(self.poss, rng.uniform(86, 95), rng.uniform(30, 70))
            return self._recover(1 - self.poss, rng.uniform(5, 14), rng.uniform(30, 70))
        if r < SAVE_SHARE + BLOCK_SHARE and not penalty:
            blocker = self._defender(x, y)
            self._emit("shot", self.poss, s, outcome="blocked", x=x, y=y, xg=xg,
                       body=body, pressure=pressure, set_piece=set_piece, qualifiers=quals)
            self._emit("block", 1 - self.poss, blocker, x=fx, y=fy, related=s)
            if rng.random() < 0.35:
                return Restart(self.poss, "corner", 99.5, 0.5)
            if rng.random() < 0.45:
                return self._recover(self.poss, clamp(x - rng.uniform(3, 12), 50, 99), rng.uniform(20, 80))
            return self._recover(1 - self.poss, clamp(fx + rng.uniform(3, 12), 1, 50), rng.uniform(20, 80))
        if r < SAVE_SHARE + BLOCK_SHARE + POST_SHARE:
            self._emit("shot", self.poss, s, outcome="post", x=x, y=y, end_x=100.0, end_y=44.6 if rng.random() < 0.5 else 55.4,
                       xg=xg, body=body, pressure=pressure, set_piece=set_piece, qualifiers=quals)
            if rng.random() < 0.4:
                return self._recover(self.poss, rng.uniform(85, 95), rng.uniform(30, 70))
            return self._recover(1 - self.poss, rng.uniform(5, 15), rng.uniform(30, 70))
        wide = rng.choice([rng.uniform(25, 44), rng.uniform(56, 75)])
        self._emit("shot", self.poss, s, outcome="off_target", x=x, y=y, end_x=100.0, end_y=wide, xg=xg,
                   body=body, pressure=pressure, set_piece=set_piece, qualifiers=quals)
        return Restart(1 - self.poss, "goal_kick", 5.5, 50.0)

    def _shot_speed(self, s: Slot, header: bool, dist_m: float) -> float:
        """Ball speed off the boot or head, km/h: headers 40-70, side-foot finishes 60-90, strikes up to ~120."""
        if header:
            return round(self.prng.uniform(40, 70), 1)
        power = 62 + s.a.finishing * 0.32 + min(dist_m, 30) * 0.6
        return round(clamp(power + self.prng.gauss(0, 9), 55, 125), 1)

    # ------------------------------------------------------------------ turnovers, fouls, cards

    def _recover(self, side_idx: int, x: float, y: float) -> Restart | None:
        """Loose ball won by `side_idx` at (x, y) in that side's frame."""
        self._advance(1.0 + self.rng.uniform(0, 1.5))
        player = self._receiver(x, y, None, side_idx=side_idx)
        self._emit("ball_recovery", side_idx, player, x=x, y=y)
        if side_idx == self.poss:
            self.carrier, self.x, self.y = player, x, y
            return None
        return Restart(side_idx, "open_play", x, y, player)

    def _turnover(self, kind: str, tx: float, ty: float) -> Restart:
        defender = self._defender(tx, ty)
        fx, fy = flip(tx, ty)
        self._emit(kind, 1 - self.poss, defender, x=fx, y=fy)
        return Restart(1 - self.poss, "open_play", fx, fy, defender)

    def _foul(self, defender: Slot, x: float, y: float) -> Restart:
        """Defender fouls the carrier at attacking-frame (x, y)."""
        fx, fy = flip(x, y)
        self._emit("foul", 1 - self.poss, defender, x=fx, y=fy, related=self.carrier)
        self._maybe_card(1 - self.poss, defender, fx, attacker_x=x)
        if in_box(x, y):
            return Restart(self.poss, "penalty", *PENALTY_SPOT)
        return Restart(self.poss, "free_kick", x, y)

    def _maybe_card(self, side_idx: int, slot: Slot, x: float, attacker_x: float = 0.0) -> None:
        rng = self.rng
        p_yellow = YELLOW_BASE + (60 - slot.a.discipline) * 0.004 + (0.08 if attacker_x > 70 else 0.0)
        if slot.yellow:
            p_yellow *= 0.4
        r = rng.random()
        side = self.sides[side_idx]
        if slot.role == "GK":
            r = 1.0  # keep goalkeepers on the pitch; replacing them is out of scope for v1
        card = None
        if r < RED_DIRECT:
            card = "red"
        elif r < p_yellow:
            card = "second_yellow" if slot.yellow else "yellow"
            slot.yellow = True
        if card is None:
            return
        self._emit("card", side_idx, slot, outcome=card)
        self._period_counts["cards"] += 1
        self._advance(max(5.0, rng.gauss(*CARD_DEAD_TIME)), in_play=False)
        if card in ("red", "second_yellow"):
            side.slots.remove(slot)
            slot.off_minute = self._minute()
            side.finished.append(slot)
            side.sent_off += 1
            if self.carrier is slot:
                self.carrier = None

    def _maybe_substitute(self) -> None:
        minute = self._minute()
        for i, side in enumerate(self.sides):
            while side.planned_subs and side.planned_subs[0] <= minute and side.subs_made < 5 and side.bench:
                side.planned_subs.pop(0)
                outfield = [s for s in side.slots if s.role != "GK"]
                if not outfield:
                    break
                off = max(outfield, key=lambda s: s.fatigue + (0.15 if s.yellow else 0) + self.rng.uniform(0, 0.2))
                options = [p for p in side.bench if p.position != "GK"]
                if not options:
                    break
                on = max(options, key=lambda p: role_rating(p, off.role))
                side.bench.remove(on)
                idx = side.slots.index(off)
                side.slots[idx] = Slot(on, off.role, off.base_x, off.base_y, on_minute=minute)
                off.off_minute = minute
                side.finished.append(off)
                if self.carrier is off:
                    self.carrier = side.slots[idx]
                side.subs_made += 1
                self._period_counts["subs"] += 1
                self._emit("substitution", i, off, related_player=on.player_id, qualifiers={"role": off.role})
                for e in self.lineups:
                    if e.player_id == on.player_id:
                        e.role = off.role
                self._advance(max(5.0, self.rng.gauss(*SUB_DEAD_TIME)), in_play=False)

    # ------------------------------------------------------------------ event emission

    def _clock(self) -> tuple[int, int, str]:
        t = self.t
        offset = (self.period - 1) * 45
        minute = offset + int(t // 60)
        second = int(t % 60)
        if t >= PERIOD_S:
            added = int((t - PERIOD_S) // 60) + 1
            label = f"{offset + 45}+{added}'"
        else:
            label = f"{minute + 1}'"
        return minute, second, label

    def _emit(
        self,
        etype: str,
        side_idx: int,
        slot: Slot | None,
        *,
        outcome: str | None = None,
        x: float | None = None,
        y: float | None = None,
        end_x: float | None = None,
        end_y: float | None = None,
        related: Slot | None = None,
        related_player: str | None = None,
        pressure: bool = False,
        xg: float | None = None,
        body: str | None = None,
        set_piece: str | None = None,
        qualifiers: dict | None = None,
    ) -> Event:
        minute, second, label = self._clock()
        home, away = self.sides
        r = lambda v: None if v is None else round(clamp(v, 0.0, 100.0), 1)  # noqa: E731
        ev = Event(
            event_id=f"{self.match_id}-{len(self.events) + 1:05d}",
            match_id=self.match_id,
            seq=len(self.events) + 1,
            period=self.period,
            elapsed_s=round(self.t, 2),
            minute=minute,
            second=second,
            clock=label,
            type=etype,
            team_id=self.sides[side_idx].id,
            player_id=slot.player.player_id if slot else None,
            related_player_id=related.player.player_id if related else related_player,
            outcome=outcome,
            x=r(x),
            y=r(y),
            end_x=r(end_x),
            end_y=r(end_y),
            possession_id=self.possession_id,
            possession_team_id=self.sides[self.poss].id,
            set_piece=set_piece or self.set_piece,
            under_pressure=pressure,
            body_part=body,
            xg=xg,
            home_score=home.score,
            away_score=away.score,
            qualifiers=qualifiers or {},
        )
        self.events.append(ev)
        if etype == "pass" and side_idx == self.poss:
            self.spell_passes += 1
        return ev


def simulate_match(league: League, home_club_id: str, away_club_id: str, **kwargs) -> MatchResult:
    return MatchEngine(league, home_club_id, away_club_id, **kwargs).simulate()

"""Data contract for everything footballfoundry produces.

These models are the shared contract between the generator, the agents,
the Fabric tables and the frontend. Change them deliberately and bump
SCHEMA_VERSION when you do.

Coordinate convention (all events):
    x: 0 -> 100, from the acting team's own goal line to the opponent's goal line.
    y: 0 -> 100, from the acting team's right touchline to its left touchline.
    The pitch is 105m x 68m, so 1 unit of x = 1.05m and 1 unit of y = 0.68m.
Because coordinates are relative to the acting team, an interception at x=20
means "20% up the pitch from the interceptor's own goal".
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.3.0"

Rating = Annotated[int, Field(ge=1, le=99)]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Coord = Annotated[float, Field(ge=0.0, le=100.0)]

Position = Literal["GK", "RB", "CB", "LB", "DM", "CM", "AM", "RW", "LW", "ST"]
Role = Literal["GK", "RB", "CB", "LB", "RWB", "LWB", "DM", "CM", "AM", "RM", "LM", "RW", "LW", "ST"]
Formation = Literal["4-3-3", "4-2-3-1", "4-4-2", "3-4-3", "3-5-2"]

EventType = Literal[
    "period_start",
    "period_end",
    "pass",
    "carry",
    "take_on",
    "shot",
    "save",
    "block",
    "tackle",
    "interception",
    "clearance",
    "ball_recovery",
    "foul",
    "card",
    "substitution",
    "tactical_change",
    "pressure",
    "possession_change",
]

SetPiece = Literal["open_play", "kick_off", "goal_kick", "throw_in", "corner", "free_kick", "penalty", "keeper"]


class Attributes(BaseModel):
    pace: Rating
    passing: Rating
    dribbling: Rating
    finishing: Rating
    heading: Rating
    tackling: Rating
    positioning: Rating
    stamina: Rating
    composure: Rating
    discipline: Rating
    goalkeeping: Rating


class Player(BaseModel):
    player_id: str
    club_id: str
    name: str
    short_name: str
    nationality: str = Field(description="FIFA-style 3-letter code")
    birth_date: date
    position: Position
    foot: Literal["left", "right", "both"]
    shirt_number: int
    overall: Rating
    attributes: Attributes


class TeamStyle(BaseModel):
    """Tactical identity, each dial 0..1. The engine reads these every action."""

    possession: Unit = Field(description="Preference for keeping the ball with short passes")
    directness: Unit = Field(description="Preference for forward/long passes")
    press_intensity: Unit = Field(description="How aggressively the team presses high")
    defensive_line: Unit = Field(description="Height of the back line (affects offsides)")
    width: Unit = Field(description="Use of wide areas and crosses")
    tempo: Unit = Field(description="Speed of ball circulation")


class Club(BaseModel):
    club_id: str
    name: str
    short_name: str
    city: str
    stadium: str
    capacity: int
    founded: int
    colors: list[str]
    manager: str
    formation: Formation
    strength: Rating
    style: TeamStyle


class League(BaseModel):
    schema_version: str = SCHEMA_VERSION
    league_id: str
    name: str
    season: str
    seed: int
    clubs: list[Club]
    players: list[Player]

    def club(self, club_id: str) -> Club:
        return next(c for c in self.clubs if c.club_id == club_id)

    def squad(self, club_id: str) -> list[Player]:
        return [p for p in self.players if p.club_id == club_id]


class Fixture(BaseModel):
    match_id: str
    matchday: int
    kickoff: datetime
    home_club_id: str
    away_club_id: str


class LineupEntry(BaseModel):
    club_id: str
    player_id: str
    role: Role | None = Field(description="Role in the formation; None for unused subs")
    starter: bool


class MatchInfo(BaseModel):
    schema_version: str = SCHEMA_VERSION
    match_id: str
    league_id: str
    season: str
    matchday: int
    kickoff: datetime
    venue: str
    referee: str
    seed: int
    home_club_id: str
    away_club_id: str
    home_formation: Formation
    away_formation: Formation
    lineups: list[LineupEntry]
    home_score: int
    away_score: int
    added_time: list[int] = Field(description="Minutes added at the end of each period")
    ball_in_play_s: float


class LineupSlot(BaseModel):
    player_id: str
    role: Role
    x: Coord = Field(description="Base position in the team's own frame")
    y: Coord


class PhysicalLine(BaseModel):
    distance_km: float
    sprints: int
    top_speed_kmh: float
    minutes: int


class Qualifiers(BaseModel):
    """Every key an event's `qualifiers` may carry; an unknown key or a wrong type fails validation.

    `Event.qualifiers` stays a plain dict on the wire and in code (`e.qualifiers.get("cross")`); this model is the
    contract it is checked against. Keys are absent rather than null when they don't apply.
    """

    model_config = ConfigDict(extra="forbid")

    # pass (crosses included)
    length_m: float | None = Field(default=None, description="pass: distance from x/y to end_x/end_y in metres")
    speed_kmh: float | None = Field(
        default=None, description="pass, shot: ball speed; carry: running speed with the ball")
    difficulty: float | Literal["routine", "good", "great"] | None = Field(
        default=None, description="pass: 1 - completion probability (0..1); save: routine, good or great")
    long_ball: bool | None = Field(default=None, description="pass: driven or lofted ball over distance")
    through_ball: bool | None = Field(default=None, description="pass: played in behind the defensive line")
    progressive: bool | None = Field(default=None, description="pass: at least 15 units forward, ending past halfway")
    cross: bool | None = Field(default=None, description="pass: delivered from a wide area into the box")
    cutback: bool | None = Field(default=None, description="pass: low cross pulled back from near the byline")
    # shot / save
    from_cross: bool | None = Field(default=None, description="shot: first-time from a cross or cutback")
    one_on_one: bool | None = Field(default=None, description="shot: only the goalkeeper to beat")
    long_range: bool | None = Field(default=None, description="shot: from outside about 21m")
    on_target_xg: float | None = Field(
        default=None, description="shot on target, save: chance of scoring given where the shot was placed")
    # carry
    sprint: bool | None = Field(default=None, description="carry: speed_kmh at or above 25.2")
    # any open-play action
    fast_break: bool | None = Field(default=None, description="during a counter-attack")
    # foul
    aerial: bool | None = Field(default=None, description="foul: in a contested header")
    # pressure
    counterpress: bool | None = Field(
        default=None, description="pressure: within 5 seconds of the pressing team losing the ball")
    # possession_change
    cause: EventType | None = Field(default=None, description="possession_change: type of the event that ended "
                                    "the previous spell (interception, tackle, shot, pass out of play, ...)")
    previous_team_id: str | None = Field(default=None, description="possession_change: the side that lost the ball")
    previous_duration_s: float | None = Field(
        default=None, description="possession_change: length of that side's spell on the ball, dead time included")
    previous_passes: int | None = Field(
        default=None, description="possession_change: passes that side attempted in its spell")
    # period_start (team sheet), tactical_change, substitution
    formation: Formation | None = Field(default=None, description="period_start, tactical_change")
    lineup: list[LineupSlot] | None = Field(default=None, description="period_start, tactical_change: the XI now")
    bench: list[str] | None = Field(default=None, description="period_start (first period): unused player ids")
    kind: Literal["formation", "role"] | None = Field(default=None, description="tactical_change")
    role: Role | None = Field(default=None, description="tactical_change: the new role; substitution: role taken over")
    from_role: Role | None = Field(default=None, description="tactical_change (role)")
    swapped_with: str | None = Field(default=None, description="tactical_change (role): team-mate who swapped roles")
    # period_end
    physical: dict[str, PhysicalLine] | None = Field(
        default=None, description="period_end: running totals so far per player id")


def _check_qualifiers(value: dict[str, Any]) -> dict[str, Any]:
    Qualifiers.model_validate(value)
    return value


class Event(BaseModel):
    """One on-ball or match-control action. Flat on purpose: maps 1:1 to Parquet/KQL columns.

    Each period opens with one `period_start` per team and closes with one `period_end` per team, home team first,
    so a consumer can tell home from away from the stream alone. Each team's first `period_start` also carries its
    team sheet in `qualifiers`: `formation`, `lineup` (player_id, role and base x/y in the team's frame) and
    `bench`. Substitutions: `player_id` goes off, `related_player_id` comes on. A `tactical_change` (formation
    switch or a player moved to another role) carries `kind`, the new `formation` or `role`, and the new `lineup`;
    changes of style (pressing, line height) are not announced, as in a real data feed.

    Off-ball context events: a `pressure` is a defender closing down the ball carrier just before the carrier's
    next action (which is then `under_pressure`); `team_id`/`player_id` are the presser, `related_player_id` the
    carrier, x/y in the presser's frame. A `possession_change` opens each spell on the ball by the other side;
    `team_id` gains it, `player_id` is the first player on it, `set_piece` says how play restarted, and
    `qualifiers` say why the other side lost it and how long they had it.

    Physical and quality data in `qualifiers`: `speed_kmh` (ball speed for passes and shots, running speed for
    carries, with `sprint` at 25.2 km/h and above), `length_m` and `difficulty` for passes (1 - completion
    probability), `on_target_xg` (shots on target and saves; saves also carry `difficulty`: routine, good or great),
    and each team's `period_end` carries `physical`: distance_km, sprints, top_speed_kmh and minutes per player so
    far. Chance types: `through_ball`, `cutback`, `one_on_one`, `long_range`, `fast_break`. `Qualifiers` lists
    every key and is enforced on validation.
    """

    event_id: str
    match_id: str
    seq: int
    period: int
    elapsed_s: float = Field(description="Seconds since the start of the period")
    minute: int = Field(description="Cumulative match minute, continues through added time")
    second: int
    clock: str = Field(description="Broadcast clock, e.g. \"23'\" or \"45+2'\"")
    type: EventType
    team_id: str
    player_id: str | None = None
    related_player_id: str | None = Field(
        default=None, description="Pass receiver, substitute coming on, or player fouled"
    )
    outcome: str | None = None
    x: Coord | None = None
    y: Coord | None = None
    end_x: Coord | None = None
    end_y: Coord | None = None
    possession_id: int
    possession_team_id: str
    set_piece: SetPiece = "open_play"
    under_pressure: bool = False
    body_part: Literal["right_foot", "left_foot", "head", "other"] | None = None
    xg: float | None = None
    home_score: int = Field(description="Score after this event (a goal's own event includes it)")
    away_score: int
    qualifiers: Annotated[dict[str, Any], AfterValidator(_check_qualifiers)] = Field(
        default_factory=dict, description="Type-specific detail; see Qualifiers")
    stream_id: str | None = Field(
        default=None, description="Set by `footballfoundry stream`, one per run: tells two streams of a match apart")


def event_json_schema() -> dict[str, Any]:
    """JSON Schema for one event message, with `qualifiers` spelled out: the contract for overlay renderers and any
    consumer outside Python."""
    schema = Event.model_json_schema()
    q = Qualifiers.model_json_schema()
    defs = schema.setdefault("$defs", {})
    defs.update(q.pop("$defs", {}))
    defs["Qualifiers"] = q
    schema["properties"]["qualifiers"] = {"$ref": "#/$defs/Qualifiers", "description": "Type-specific detail"}
    return schema


InsightKind = Literal[
    "goal", "red_card", "penalty", "big_chance", "momentum", "summary", "half_time", "full_time", "tactical_change",
    "spotlight",  # a quiet spell: the analysts find a story in the data on their own initiative
]
Audience = Literal["broadcast", "analyst", "casual", "player"]


VisualType = Literal["move", "shot_map", "xg_race", "momentum", "compare"]


class MoveStep(BaseModel):
    """One on-ball action of an animated move. Coordinates are the acting team's, as in `Event`."""

    seq: int
    clock: str
    type: str
    team_id: str
    player_id: str | None = None
    outcome: str | None = None
    x: Coord
    y: Coord
    end_x: Coord | None = None
    end_y: Coord | None = None


class Clip(BaseModel):
    """A move to animate: the actions of one possession, ending in `label`'s moment."""

    label: str = Field(description="e.g. \"34' Rossi\"")
    steps: list[MoveStep]


class ShotPoint(BaseModel):
    seq: int
    period: int = 1
    minute: int
    t: float = Field(default=0.0, description="Minutes from kick-off without the restart at 45': second-half "
                                              "minutes follow on from the end of first-half added time")
    clock: str
    team_id: str
    player_id: str | None = None
    x: Coord
    y: Coord
    xg: float
    outcome: str | None = None


class MinuteThreat(BaseModel):
    minute: int = Field(description="Minutes from kick-off without the restart at 45' (as ShotPoint.t)")
    home: float
    away: float


class CompareRow(BaseModel):
    key: str = Field(description="A box-score key: goals, xg, shots, shots_on_target, possession, passes, ...")
    home: float
    away: float


class Visual(BaseModel):
    """A chart or an animation to show with an insight or a chat answer, rendered by the web app.

    Built in code from the match events, never by a model, so the numbers are always the real ones. A model only
    picks which visuals to show and writes the caption. Which fields are set depends on `type`:

        move        clips      the move (or, in a recap, every goal) replayed on a mini pitch, looping like a GIF
        shot_map    shots      every shot so far, sized by xG
        xg_race     shots      cumulative xG per team by minute, goals marked
        momentum    threat     attacking threat per minute for each side
        compare     rows       the two teams' numbers side by side
    """

    type: VisualType
    home_id: str
    away_id: str
    caption: str = ""
    clips: list[Clip] = Field(default_factory=list)
    shots: list[ShotPoint] = Field(default_factory=list)
    threat: list[MinuteThreat] = Field(default_factory=list)
    rows: list[CompareRow] = Field(default_factory=list)
    now: float | None = Field(default=None, description="xg_race: the match minute it was drawn at (as ShotPoint.t)")
    half_time: float | None = Field(default=None, description="xg_race, momentum: where the second half starts "
                                                              "(as ShotPoint.t)")


class Trivia(BaseModel):
    """A "Did you know?" for casual fans: a fact about a player or club from the league data, sometimes told as a
    short story by the storyteller agent.

    `key` and `params` let any client write the fact in its own language (the web app does, in five); `text` is
    the English version. When the storyteller wrote it, `text` is its story and `translations` holds the same
    story in other languages, keyed by BCP 47 tag.
    """

    trivia_id: str = Field(description="`<key>:<subject id>`: the same fact is never shown twice in a match")
    key: str = Field(description="Which fact, e.g. youngest_in_squad, fastest_in_league, founded, biggest_ground")
    subject_id: str = Field(description="The player or club it's about")
    params: dict[str, Any] = Field(default_factory=dict, description="Values for the client's wording of `key`")
    text: str
    translations: dict[str, str] = Field(default_factory=dict)
    source: Literal["data", "storyteller"] = "data"


class InsightVariant(BaseModel):
    """An insight rewritten for one audience, language and focus."""

    audience: Audience
    language: str = Field(description="BCP 47 tag, e.g. en, es, pt-BR")
    focus_club_id: str | None = None
    focus_player_id: str | None = None
    headline: str
    text: str
    translated_by: Literal["translator", "agent"] | None = Field(
        default=None, description="Who produced the non-English text: Azure Translator, or the personaliser agent")


class Insight(BaseModel):
    """One agent-produced explanation of a match moment.

    Timed and machine-readable so it can drive an on-screen overlay: place it at `seq` / `clock`, show `headline`
    and `stats` for `display_s` seconds. `event_ids` are the evidence the explanation is built on.
    """

    schema_version: str = SCHEMA_VERSION
    insight_id: str
    match_id: str
    kind: InsightKind
    seq: int = Field(description="Last event the insight covers; nothing after it was known when it was written")
    period: int
    minute: int
    clock: str
    team_id: str | None = None
    player_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    headline: str = Field(description="Lower-third text, at most about 60 characters")
    text: str = Field(description="Broadcast narrative, two to four sentences")
    why_it_matters: str
    stats: dict[str, str] = Field(default_factory=dict, description="Key numbers for the overlay, label -> value")
    priority: int = Field(default=2, ge=1, le=3, description="1 = must show, 3 = filler")
    display_s: float = 8.0
    verified: bool = Field(default=False, description="Passed the fact-checker")
    language: str = "en"
    created_at: datetime
    visuals: list[Visual] = Field(default_factory=list, description="Charts and animated replays for this moment")
    trivia: Trivia | None = Field(default=None, description="A fun fact about a player or club in the moment")
    variants: dict[str, InsightVariant] = Field(default_factory=dict, description="Keyed by variant_key()")

    @staticmethod
    def variant_key(audience: str, language: str, club_id: str | None = None, player_id: str | None = None) -> str:
        return "|".join((audience, language, club_id or "", player_id or ""))

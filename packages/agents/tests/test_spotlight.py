from footballfoundry_agents.briefs import build
from footballfoundry_agents.relay import HALF_TIME_S
from footballfoundry_agents.state import MatchState, match_seconds
from footballfoundry_agents.templates import template_insight
from footballfoundry_agents.triggers import TriggerEngine


class StreamClock:
    """Wall-clock time as a stream at `speed`x would produce it: match time / speed (plus the half-time break)."""

    def __init__(self, speed: float) -> None:
        self.speed, self.now_s = speed, 0.0

    def follow(self, e, p1_end_s: float) -> None:
        self.now_s = e.elapsed_s if e.period == 1 else p1_end_s + HALF_TIME_S + e.elapsed_s

    def __call__(self) -> float:
        return self.now_s / self.speed


def run(match, speed: float, resets: list | None = None):
    """Moments as (moment, match seconds, wall seconds). `resets` collects every point the idle clock restarts:
    each moment, and each kick-off."""
    clock = StreamClock(speed)
    engine, state, moments = TriggerEngine(clock=clock), MatchState("M0001"), []
    p1_end = next(e.elapsed_s for e in match.events if e.type == "period_end")
    for e in match.events:
        clock.follow(e, p1_end)
        if state.apply(e):
            found = [(m, match_seconds(e), clock()) for m in engine.check(state, e)]
            moments += found
            if resets is not None and (found or e.type == "period_start"):
                resets.append((match_seconds(e), clock()))
    return state, moments


def test_quiet_spells_get_a_spotlight_at_30x(match):
    resets: list = []
    _, moments = run(match, 30, resets)
    spots = [m for m, _, _ in moments if m.kind == "spotlight"]
    assert spots, "a whole match at 30x has quiet spells"
    assert len({m.angle.angle_id for m in spots}) == len(spots)  # never the same story twice
    assert all(m.priority == 3 for m in spots)  # filler: dropped first if the agents fall behind
    # Each spotlight came after 10 s and 3 match minutes without any insight (or since kick-off).
    for m, s, wall in moments:
        if m.kind == "spotlight":
            prev_s, prev_wall = max((r for r in resets if r[1] < wall), key=lambda r: r[1])
            assert wall - prev_wall >= 10 and s - prev_s >= 180


def test_live_speed_is_limited_by_match_minutes(match):
    _, moments = run(match, 1)
    spots = [(m, s) for m, s, _ in moments if m.kind == "spotlight"]
    assert spots and len(spots) <= 30  # a 10 s rule alone would ask for hundreds at 1x


def test_a_fast_replay_needs_no_spotlight(match):
    _, moments = run(match, 10_000)  # 10 real seconds would be over a day of football
    assert not [m for m, _, _ in moments if m.kind == "spotlight"]


def test_spotlight_brief_and_template(match, ref):
    state, engine = MatchState("M0001"), TriggerEngine(clock=StreamClock(30))
    clock = engine.clock
    p1_end = next(e.elapsed_s for e in match.events if e.type == "period_end")
    brief = None
    for e in match.events:
        clock.follow(e, p1_end)
        if state.apply(e):
            for m in engine.check(state, e):
                if m.kind == "spotlight" and brief is None:
                    brief = build(state, m, ref)
        if brief:
            break
    assert "SPOTLIGHT" in brief.text and brief.facts["angle"]["angle_id"]
    insight = template_insight(brief, ref)
    assert insight.kind == "spotlight" and insight.headline and insight.stats
    want = brief.facts["angle"]["visual"]
    assert [v.type for v in insight.visuals] == ([want] if want in brief.visuals else [])

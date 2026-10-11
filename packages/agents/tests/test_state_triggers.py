from collections import Counter

from footballfoundry_agents.briefs import build
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.triggers import TriggerEngine


def run(events, engine=None):
    engine = engine or TriggerEngine()
    state, moments = MatchState(events[0].match_id), []
    for e in events:
        if state.apply(e):
            moments += [(m, state.last_seq) for m in engine.check(state, e)]
    return state, moments


def test_state_follows_the_match(match):
    state, _ = run(match.events)
    info = match.info
    assert (state.home_id, state.away_id) == (info.home_club_id, info.away_club_id)
    assert (state.home_score, state.away_score) == (info.home_score, info.away_score)
    assert state.team_stats(info.home_club_id)["goals"] == info.home_score
    assert state.finished


def test_duplicates_are_ignored_and_a_replay_restarts(match):
    state = MatchState("M0001")
    assert state.apply(match.events[0])
    assert not state.apply(match.events[0])
    for e in match.events[1:100]:
        state.apply(e)
    assert state.apply(match.events[0]) and state.last_seq == 1  # seq 1 again: the match is being replayed


def test_snapshot_roundtrip(match):
    state, _ = run(match.events[:900])
    restored = MatchState.restore(state.snapshot())
    assert restored.snapshot() == state.snapshot()
    assert restored.momentum() == state.momentum()


def test_triggers_fire_the_must_show_moments_once(match):
    _, moments = run(match.events)
    kinds = Counter(m.kind for m, _ in moments)
    goals = [e for e in match.events if e.type == "shot" and e.outcome == "goal"]
    assert kinds["goal"] == len(goals)
    assert kinds["half_time"] == kinds["full_time"] == 1
    assert [m.anchor.seq for m, _ in moments if m.kind == "goal"] == [g.seq for g in goals]
    assert all("+" not in m.anchor.clock for m, _ in moments if m.kind == "summary")


def test_minor_moments_are_rate_limited(match):
    engine = TriggerEngine()
    _, moments = run(match.events, engine)
    minor = [m.anchor.minute * 60 + m.anchor.second for m, _ in moments if m.priority > 1]
    window = engine.cfg.minor_window_min * 60
    assert all(sum(1 for s in minor if t - window < s <= t) <= engine.cfg.minor_cap for t in minor)


def test_brief_never_contains_later_events(match, ref):
    engine, state = TriggerEngine(), MatchState("M0001")
    briefs = []
    for e in match.events:
        if state.apply(e):
            briefs += [build(state, m, ref) for m in engine.check(state, e)]
    goal = next(b for b in briefs if b.kind == "goal")
    assert all(int(i.split("-")[1]) <= goal.seq for i in goal.event_ids)
    assert ref.player_name(goal.player_ids[0]) in goal.text
    assert f"{goal.facts['score'][0]}-{goal.facts['score'][1]}" in goal.text


def test_tactical_change_becomes_an_insight(league, ref):
    from footballfoundry.engine import FormationChange, simulate_match

    from footballfoundry_agents.templates import template_insight

    r = simulate_match(league, "T05", "T12", seed=3, formation_changes=[FormationChange(60, 1, "3-5-2")])
    state, engine, briefs = MatchState(r.info.match_id), TriggerEngine(), []
    for e in r.events:
        if state.apply(e):
            briefs += [build(state, m, ref) for m in engine.check(state, e) if m.kind == "tactical_change"]
    assert len(briefs) == 1
    insight = template_insight(briefs[0], ref)
    assert "3-5-2" in insight.headline and insight.team_id == "T12"

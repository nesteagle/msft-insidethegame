import asyncio
import json

import pytest
from starlette.testclient import TestClient

from footballfoundry_agents.api import create_app
from footballfoundry_agents.briefs import build
from footballfoundry_agents.chat import chat_endpoint, offline_visual
from footballfoundry_agents.reference import Reference
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.stores import memory_stores
from footballfoundry_agents.templates import template_insight
from footballfoundry_agents.triggers import TriggerEngine
from footballfoundry_agents.visuals import build as build_visual
from footballfoundry_agents.workflow import AgentWriter


@pytest.fixture(scope="module")
def played(match, ref):
    """The full match folded into a state, and the first brief of each kind."""
    engine, state, briefs = TriggerEngine(), MatchState("M0001"), {}
    for e in match.events:
        if state.apply(e):
            for m in engine.check(state, e):
                briefs.setdefault(m.kind, build(state, m, ref))
    return state, briefs


def goals(match):
    return [e for e in match.events if e.type == "shot" and e.outcome == "goal"]


def test_state_keeps_every_shot_and_goal_move_through_a_snapshot(match, played):
    state, _ = played
    assert len(state.shots) == sum(e.type == "shot" for e in match.events)
    assert [g["seq"] for g in state.goal_moves] == [e.seq for e in goals(match)]
    for g in state.goal_moves:  # each move ends with its goal, all actions by the scoring team
        assert g["steps"][-1]["seq"] == g["seq"] and len({s["team_id"] for s in g["steps"]}) == 1
    restored = MatchState.restore(json.loads(json.dumps(state.snapshot())))
    assert restored.shots == state.shots and restored.goal_moves == state.goal_moves


def test_a_goal_replays_its_own_move(match, played, ref):
    _, briefs = played
    goal = briefs["goal"]
    assert "VISUALS YOU CAN SHOW" in goal.text and "move:" in goal.text
    insight = template_insight(goal, ref)
    assert [v.type for v in insight.visuals] == ["move"]
    steps = insight.visuals[0].clips[0].steps
    assert steps[-1].seq == goal.seq and steps[-1].type == "shot"
    # Built at trigger time: no shot after the goal is on the xG chart it offers.
    assert all(s.seq <= goal.seq for s in goal.visuals["xg_race"].shots)


def test_full_time_recap_has_the_goals_reel(match, played, ref):
    _, briefs = played
    insight = template_insight(briefs["full_time"], ref)
    kinds = [v.type for v in insight.visuals]
    assert kinds == (["move", "xg_race", "shot_map"] if goals(match) else ["xg_race", "shot_map"])
    if goals(match):
        assert len(insight.visuals[0].clips) == len(goals(match))
        assert insight.visuals[0].clips[0].label.startswith(goals(match)[0].clock)


def test_compare_and_momentum(played, ref):
    state, _ = played
    compare = build_visual(state, "compare", ref)
    row = {r.key: r for r in compare.rows}
    assert row["goals"].home == state.home_score and row["goals"].away == state.away_score
    assert row["possession"].home + row["possession"].away == pytest.approx(1, abs=0.01)
    momentum = build_visual(state, "momentum", ref)
    # One bar per minute from kick-off to now, the second half after first-half stoppage time (no shared minutes),
    # and every bit of threat in exactly one bar.
    assert len(momentum.threat) == int(state.match_minute()) + 1
    assert momentum.half_time == state.p1_end_minute + 1
    for side, team in (("home", state.home_id), ("away", state.away_id)):
        total = sum(state.threat_by_minute[team].values())
        assert sum(getattr(m, side) for m in momentum.threat) == pytest.approx(total, abs=0.05)


def test_agent_picks_visuals_only_from_the_offer(played, ref, scripted):
    _, briefs = played
    insight = asyncio.run(AgentWriter(scripted(), ref).write(briefs["goal"]))
    # The scripted analyst picks xg_race (offered for a goal) and compare (not offered): only xg_race survives.
    assert [(v.type, v.caption) for v in insight.visuals] == [("xg_race", "They have been creating more all half.")]


def test_unknown_reference_still_draws():
    state = MatchState("X")
    assert build_visual(state, "compare", Reference(None)) is None  # no teams yet: nothing to draw


def test_offline_keywords():
    assert offline_visual("Show me the shot map") == "shot_map"
    assert offline_visual("Who's winning the xG?") == "xg_race"
    assert offline_visual("Replay the goal") == "move"
    assert offline_visual("What's the score?") is None


def _chat(service, question, as_of):
    body = {"threadId": "t1", "runId": "r1", "messages": [{"id": "m1", "role": "user", "content": question}],
            "tools": [], "context": [], "forwardedProps": {}, "state": {"match_id": "M0001", "as_of": as_of}}
    with TestClient(create_app(service, chat=chat_endpoint(service, None, None))) as http:
        r = http.post("/api/chat", json=body)
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def test_offline_chat_draws_a_visual_without_spoilers(match, ref):
    service = AnalystService(memory_stores(), TemplateWriter(ref), ref)

    async def feed():
        for e in match.events:
            await service.ingest(e)
        await service.drain()

    asyncio.run(feed())
    halfway = match.events[len(match.events) // 2].seq
    events = _chat(service, "Show me the xG race", halfway)
    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    visual = json.loads(result["content"])["visual"]
    assert visual["type"] == "xg_race" and all(s["seq"] <= halfway for s in visual["shots"])
    assert events[-1]["type"] == "RUN_FINISHED"
    assert "offline" in "".join(e.get("delta", "") for e in events)

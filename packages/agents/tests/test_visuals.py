import asyncio
import json

import pytest
from starlette.testclient import TestClient

from footballfoundry_agents.api import create_app
from footballfoundry_agents.briefs import build
from footballfoundry_agents.chat import chat_endpoint, offline_visual
from footballfoundry_agents.reference import Reference
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.state import TOUCH_TYPES, MatchState
from footballfoundry_agents.stores import memory_stores
from footballfoundry_agents.templates import template_insight
from footballfoundry_agents.triggers import TriggerEngine
from footballfoundry_agents.visuals import DEFENSIVE_TYPES, describe_offer
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


def test_pass_network(match, played, ref):
    state, _ = played
    restored = MatchState.restore(json.loads(json.dumps(state.snapshot())))
    assert restored.passing == state.passing and restored.spots == state.spots
    net = build_visual(restored, "pass_network", ref, team_id=state.away_id)
    assert net.team_id == state.away_id
    for team in (state.home_id, state.away_id):
        completed = [e for e in match.events if e.team_id == team and e.type == "pass" and e.outcome == "complete"
                     and e.related_player_id and e.end_x is not None]
        nodes = {n.player_id: n for n in net.nodes if n.team_id == team}
        assert sum(n.passes for n in nodes.values()) == 2 * len(completed)  # made + received
        links = [link for link in net.links if link.team_id == team]
        assert links and all(link.a in nodes and link.b in nodes for link in links)
        assert min(link.passes for link in links) >= 2
        assert {p for p, n in nodes.items() if not n.on_pitch} <= state.off
    # Without a team (half time, full time) the home side comes first.
    assert build_visual(state, "pass_network", ref).team_id == state.home_id


def test_touch_map(match, played, ref):
    state, _ = played
    busiest = max(state.player_team, key=lambda p: sum(r[4] == p for r in state.spots))
    v = build_visual(state, "touch_map", ref, player_id=busiest)
    mine = [e for e in match.events if e.player_id == busiest and e.type in TOUCH_TYPES and e.x is not None]
    assert v.player_id == busiest and v.team_id == state.player_team[busiest]
    assert [s.seq for s in v.spots] == [e.seq for e in mine]
    assert f"{len(mine)} touches" in describe_offer({"touch_map": v}, ref)
    assert build_visual(state, "touch_map", ref) is None  # a touch map is always somebody's


def test_defensive_map(match, played, ref):
    state, _ = played
    v = build_visual(state, "defensive_map", ref, team_id=state.away_id)
    assert v.team_id == state.away_id and v.player_id is None
    want = [e.seq for e in match.events if e.type in DEFENSIVE_TYPES and e.player_id and e.x is not None]
    assert [s.seq for s in v.spots] == want
    away_player = next(s.player_id for s in v.spots if s.team_id == state.away_id)
    home_player = next(s.player_id for s in v.spots if s.team_id == state.home_id)
    # A player is picked out only on their own team's map.
    assert build_visual(state, "defensive_map", ref, team_id=state.away_id, player_id=away_player).player_id \
        == away_player
    assert build_visual(state, "defensive_map", ref, team_id=state.away_id, player_id=home_player).player_id is None
    tackles = sum(s.type == "tackle" and s.team_id == state.away_id for s in v.spots)
    assert f"tackle {tackles}," in describe_offer({"defensive_map": v}, ref).split(";")[0]


def test_half_time_offers_the_pitch_maps(played):
    _, briefs = played
    ht = briefs["half_time"]
    assert {"pass_network", "defensive_map"} <= set(ht.visuals)
    assert "pass_network:" in ht.text and "strongest link" in ht.text and "defensive_map:" in ht.text
    # Built at half time: nothing from the second half.
    assert all(s.seq <= ht.seq for s in ht.visuals["defensive_map"].spots)


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
    assert offline_visual("Show their pass network") == "pass_network"
    assert offline_visual("Where do they win the ball back?") == "defensive_map"
    assert offline_visual("Show his touches") == "touch_map"
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

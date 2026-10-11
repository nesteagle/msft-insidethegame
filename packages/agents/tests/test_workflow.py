import asyncio

import pytest

from footballfoundry_agents.briefs import build
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.triggers import TriggerEngine
from footballfoundry_agents.workflow import AgentWriter


@pytest.fixture(scope="module")
def briefs(match, ref):
    engine, state, out = TriggerEngine(), MatchState("M0001"), {}
    for e in match.events:
        if state.apply(e):
            for m in engine.check(state, e):
                out.setdefault(m.kind, build(state, m, ref))
    return out


def formats(client):
    return [name for name, _ in client.calls]


def test_fact_checker_sends_the_analysis_back_once(briefs, ref, scripted):
    client = scripted(verdicts=[False, True])
    insight = asyncio.run(AgentWriter(client, ref).write(briefs["goal"]))
    assert formats(client) == ["Analysis", "Verdict", "Analysis", "Verdict", "Narration"]
    assert "FACT-CHECKER FOUND" in client.calls[2][1] and "The minute is wrong" in client.calls[2][1]
    assert insight.verified and insight.headline == "Vertooren strikes"
    assert insight.stats == {"xG": "0.27"} and insight.seq == briefs["goal"].seq


def test_unverified_after_max_attempts(briefs, ref, scripted):
    client = scripted(verdicts=[False, False])
    insight = asyncio.run(AgentWriter(client, ref, max_attempts=2).write(briefs["goal"]))
    assert formats(client) == ["Analysis", "Verdict", "Analysis", "Verdict", "Narration"]
    assert not insight.verified


def test_full_time_goes_to_the_recap_writer(briefs, ref, scripted):
    client = scripted()
    writer = AgentWriter(client, ref)
    asyncio.run(writer.write(briefs["full_time"]))
    assert formats(client) == ["Analysis", "Verdict", "Narration"]
    assert "full-time recap" in client.instructions[0]


def test_failure_falls_back_to_template(briefs, ref, scripted):
    client = scripted(fail={"Narration"})
    insight = asyncio.run(AgentWriter(client, ref).write(briefs["goal"]))
    assert insight.headline.startswith("GOAL") and not insight.verified


def test_personalize(briefs, ref, scripted):
    client = scripted()
    writer = AgentWriter(client, ref)
    insight = asyncio.run(writer.write(briefs["goal"]))
    v = asyncio.run(writer.personalize(insight, "casual", "es", None, None))
    assert v.language == "es" and v.headline.startswith("¡Gol")
    assert "language: es" in client.calls[-1][1]

import asyncio
import json

from agent_framework import (BaseChatClient, ChatResponse, ChatResponseUpdate, Content, FunctionInvocationLayer,
                             Message, ResponseStream)
from starlette.testclient import TestClient

from footballfoundry_agents.api import create_app
from footballfoundry_agents.chat import chat_endpoint
from footballfoundry_agents.relay import HALF_TIME_S, MatchLog
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.stores import memory_stores


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        kind = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), None)
        data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), None)
        if data:
            out.append((kind, json.loads(data)))
    return out


def test_log_times_include_half_time_and_ignore_duplicates(match):
    log = MatchLog("M0001")
    for e in match.events:
        log.add(e)
    assert not log.add(match.events[10])
    p1_end = next(e for e in match.events if e.type == "period_end").elapsed_s
    second_half = next(i for i, e in enumerate(log.events) if e.period == 2)
    assert log.times[second_half] == round(p1_end + HALF_TIME_S, 2)
    assert log.finished and len(log.team_sheets()) == 2
    assert all(len(s["lineup"]) == 11 for s in log.team_sheets().values())
    log.add(match.events[0])  # streamed again from kick-off
    assert log.run == 1 and len(log.events) == 1


def make_service(match, ref):
    service = AnalystService(memory_stores(), TemplateWriter(ref), ref)

    async def feed():
        for e in match.events:
            await service.ingest(e)
        await service.drain()

    asyncio.run(feed())
    return service


def test_events_relay_replays_from_a_point(match, ref):
    service = make_service(match, ref)
    with TestClient(create_app(service)) as http:
        assert http.get("/api/matches/M0001/session").json()["summary"]["finished"]
        body = http.get("/api/matches/M0001/events", params={"from": 600, "speed": "live"}).text
    events = sse_events(body)
    assert events[0][0] == "reset" and events[-1][0] == "end"
    sent = [d for k, d in events if k == "event"]
    assert len(sent) == len(match.events)
    assert all(d["catchup"] == (d["t"] < 600) for d in sent)


class ToolCallingClient(FunctionInvocationLayer, BaseChatClient):
    """First turn: call match_state. Second turn: answer with what the tool returned."""

    def __init__(self) -> None:
        super().__init__()
        self.seen_tool_results: list[str] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        if results:
            self.seen_tool_results = [str(c.result) for c in results]
            updates = [ChatResponseUpdate(contents=[Content.from_text("Here is the state.")], role="assistant")]
        else:
            call = Content.from_function_call("call-1", "match_state", arguments="{}")
            updates = [ChatResponseUpdate(contents=[call], role="assistant")]

        async def gen():
            for u in updates:
                yield u

        if stream:
            return ResponseStream(gen(), finalizer=lambda ups: ChatResponse.from_updates(ups))

        async def single():
            return ChatResponse(messages=[Message("assistant", updates[0].contents)])
        return single()


def chat_request(as_of):
    return {"threadId": "t1", "runId": "r1", "messages": [{"id": "m1", "role": "user", "content": "What's the score?"}],
            "tools": [], "context": [], "forwardedProps": {}, "state": {"match_id": "M0001", "as_of": as_of}}


def test_chat_tools_cannot_see_past_the_viewer(match, ref):
    service = make_service(match, ref)
    first_goal = next(e for e in match.events if e.type == "shot" and e.outcome == "goal")
    client = ToolCallingClient()
    app = create_app(service, chat=chat_endpoint(service, client, None))
    with TestClient(app) as http:
        r = http.post("/api/chat", json=chat_request(first_goal.seq - 1))
    assert r.status_code == 200
    kinds = [json.loads(line[6:])["type"] for line in r.text.splitlines() if line.startswith("data: ")]
    assert "TOOL_CALL_START" in kinds and "TEXT_MESSAGE_CONTENT" in kinds and kinds[-1] == "RUN_FINISHED"
    state = json.loads(client.seen_tool_results[0])
    assert state["as_of_seq"] == first_goal.seq - 1 and state["goals"] == []


def test_offline_chat_answers_with_facts(match, ref):
    service = make_service(match, ref)
    with TestClient(create_app(service, chat=chat_endpoint(service, None, None))) as http:
        r = http.post("/api/chat", json=chat_request(10_000))
    text = "".join(json.loads(line[6:]).get("delta", "") for line in r.text.splitlines() if line.startswith("data: "))
    assert "offline" in text and f"{match.info.home_score}-{match.info.away_score}" in text

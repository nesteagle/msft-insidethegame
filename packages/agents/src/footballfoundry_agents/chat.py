"""The match assistant: chat with the agents about the match you're watching, over AG-UI.

    POST /api/chat     AG-UI RunAgentInput -> Server-Sent Events of AG-UI events

The page sends its state with every message: the match, how far the viewer has watched (`as_of`, an event seq),
and their audience, language, club and player. The assistant's tools are built per request and bound to that
match and position on the server, so it can't see past the viewer's point in the match, whatever the model asks.

Tools:
    match_state, events, player_stats, lookup, insights    from the relay log and the insights store
    season_query                                            through the MCP server, before this match's matchday
    explain_moment                                          hands one event to the analyst workflow (analyst,
                                                            fact-checker, narrator) for a verified explanation
Frontend tools (declared by the page, run in the browser): seek_to, focus_player, set_view.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from agent_framework import Agent, BaseChatClient, ChatResponse, ChatResponseUpdate, Content, Message, \
    ResponseStream, tool
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from footballfoundry_agents.briefs import build
from footballfoundry_agents.service import AnalystService
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.triggers import Moment
from footballfoundry_mcp import views

log = logging.getLogger(__name__)

AUDIENCE_STYLE = {
    "broadcast": "neutral and clear, like a studio presenter",
    "analyst": "precise and tactical, with numbers",
    "casual": "friendly and plain, no jargon",
    "player": "focused on the viewer's chosen player",
}


class ChatState(BaseModel):
    """What the page tells the assistant with every message."""

    match_id: str | None = None
    as_of: int | None = None  # the last event seq the viewer has seen
    audience: Literal["broadcast", "analyst", "casual", "player"] = "broadcast"
    language: str = "en"
    club_id: str | None = None
    player_id: str | None = None


def instructions(state: ChatState, service: AnalystService) -> str:
    ref = service.ref
    log_ = service.log.get(state.match_id) if state.match_id else None
    seen = log_.up_to(state.as_of) if log_ else []
    if seen:
        last = seen[-1]
        s = log_.summary()
        where = (f"{ref.club_name(s['home_id'])} {last.home_score}-{last.away_score} {ref.club_name(s['away_id'])}, "
                 f"{last.clock} (event seq {last.seq})")
    else:
        where = "the match hasn't started for this viewer yet"
    viewer = [f"answer in the language with BCP 47 tag '{state.language}'",
              f"style: {AUDIENCE_STYLE[state.audience]}"]
    if state.club_id:
        viewer.append(f"they support {ref.club_name(state.club_id)}")
    if state.player_id:
        viewer.append(f"they are following {ref.player_name(state.player_id)} ({state.player_id})")
    return f"""You are the FootballFoundry match assistant, chatting with a viewer during a live football match.
The viewer is watching match {state.match_id or '(none selected)'}: {where}.
Viewer: {'; '.join(viewer)}.

Rules:
- Use your tools for facts. They only see the match up to where the viewer is; never guess at what comes later.
- Be concise: 2-5 sentences unless asked for more. Mention minutes and names, not ids.
- For "why" questions about a specific moment, call explain_moment: it runs the analyst team and fact-checker.
- The page has tools you can call: seek_to(seq) replays the match from an event (use it when the viewer asks to
  see or rewatch something), focus_player(player_id) follows one player, set_view(audience, language) changes
  how insights are written. Say what you did when you use them.
- Football ids: clubs T01-T20, players like P0124; coordinates x 0-100 towards the opponent's goal."""


def bound_tools(state: ChatState, service: AnalystService, mcp: Any) -> list[Any]:
    """Tools fixed to this viewer's match and position."""
    match_id = state.match_id or ""
    as_of = state.as_of if state.as_of is not None else 0
    league = service.ref.league

    def seen():
        return service.log.get(match_id).up_to(as_of)

    @tool(description="Score, clock, possession, team stats, attacking threat (last 10 min), goals, cards and subs")
    async def match_state() -> dict[str, Any]:
        return views.match_state(match_id, seen(), league)

    @tool(description="Events so far, newest last. Filter by types (e.g. ['shot']), team_id, player_id, "
                      "possession_id or from_seq; limit caps the count (default 30).")
    async def events(types: list[str] | None = None, team_id: str | None = None, player_id: str | None = None,
                     possession_id: int | None = None, from_seq: int | None = None,
                     limit: int = 30) -> list[dict[str, Any]]:
        return views.filter_events(seen(), types=types, team_id=team_id, player_id=player_id,
                                   possession_id=possession_id, from_seq=from_seq, limit=limit)

    @tool(description="One player's numbers in this match so far")
    async def player_stats(player_id: str) -> dict[str, Any]:
        return views.player_view(seen(), player_id, league)

    @tool(description="Find clubs or players by id or name: colours, stadium, style, position, attributes")
    async def lookup(query: str) -> list[dict[str, Any]]:
        return views.lookup(league, query)

    @tool(description="Insights the analyst agents have published for this match so far")
    async def insights() -> list[dict[str, Any]]:
        items = await service.stores.insights.list(match_id, as_of)
        return [{"clock": i.clock, "kind": i.kind, "headline": i.headline, "text": i.text,
                 "why_it_matters": i.why_it_matters, "seq": i.seq} for i in items]

    @tool(description="Season queries (only matchdays before this match): standings, top_scorers {top}, "
                      "team_form {club_id, last}, player_season {player_id}, head_to_head {club_a, club_b}")
    async def season_query(name: str, params: dict[str, Any] | None = None) -> Any:
        if mcp is None:
            return {"error": "season data needs the MCP server (MCP_URL)"}
        result = await mcp.call_tool("season_query", name=name, params=params or {}, before_match_id=match_id)
        return result if isinstance(result, str) else str(result)

    @tool(description="A verified explanation of one moment (by event_id), written by the analyst team")
    async def explain_moment(event_id: str) -> dict[str, Any]:
        events_ = seen()
        anchor = next((e for e in events_ if e.event_id == event_id), None)
        if anchor is None:
            return {"error": f"{event_id} hasn't happened yet for this viewer, or doesn't exist"}
        state_ = MatchState(match_id)
        for e in events_:
            if e.seq > anchor.seq:
                break
            state_.apply(e)
        kind = ("goal" if anchor.type == "shot" and anchor.outcome == "goal"
                else "red_card" if anchor.type == "card" and anchor.outcome in ("red", "second_yellow")
                else "big_chance" if anchor.type == "shot" else "summary")
        chain = state_.possession_chain(anchor.possession_id)
        players = [p for p in dict.fromkeys(e.player_id for e in reversed(chain)
                                     if e.player_id and e.type != "pressure")][:3]
        brief = build(state_, Moment(match_id, kind, anchor, chain, anchor.team_id, players, 2), service.ref)
        insight = await service.writer.write(brief)
        return {"headline": insight.headline, "text": insight.text, "why_it_matters": insight.why_it_matters,
                "stats": insight.stats, "verified": insight.verified}

    return [match_state, events, player_stats, lookup, insights, season_query, explain_moment]


class OfflineChatClient(BaseChatClient):
    """Without a Foundry model the assistant still answers, with the facts it can compute."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        if not stream:
            async def respond() -> ChatResponse:
                return ChatResponse(messages=[Message("assistant", [self.text])])
            return respond()

        async def updates():
            for word in self.text.split(" "):
                yield ChatResponseUpdate(contents=[Content.from_text(word + " ")], role="assistant")

        return ResponseStream(updates(), finalizer=lambda ups: ChatResponse.from_updates(ups))


def offline_answer(state: ChatState, service: AnalystService) -> str:
    events_ = service.log.get(state.match_id).up_to(state.as_of) if state.match_id else []
    if not events_:
        return "I'm running offline (no Foundry model configured). Pick a match and start watching, then ask again."
    s = views.match_state(state.match_id or "", events_, service.ref.league)
    h, a = s["home"], s["away"]
    goals = ", ".join(f"{service.ref.player_name(g.get('player_id'))} {g['clock']}" for g in s["goals"]) or "none"
    return (f"I'm running offline (no Foundry model configured), so here are the facts: {h['name']} {h['score']}-"
            f"{a['score']} {a['name']} at {s['clock']}. Shots {h['stats'].get('shots', 0):.0f}-"
            f"{a['stats'].get('shots', 0):.0f}, xG {h['stats'].get('xg', 0):.2f}-{a['stats'].get('xg', 0):.2f}. "
            f"Goals: {goals}.")


def chat_endpoint(service: AnalystService, client: Any | None, mcp: Any | None):
    from ag_ui.encoder import EventEncoder
    from agent_framework_ag_ui import AgentFrameworkAgent, AGUIRequest

    async def chat(request: Request) -> Response:
        try:
            body = AGUIRequest.model_validate(await request.json())
            input_data = body.model_dump(exclude_none=True)
            state = ChatState.model_validate(input_data.get("state") or {})
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if client is None:
            agent = Agent(OfflineChatClient(offline_answer(state, service)), name="match_assistant")
        else:
            agent = Agent(client, instructions(state, service), name="match_assistant",
                          tools=bound_tools(state, service, mcp))
        runner = AgentFrameworkAgent(agent=agent, require_confirmation=False)

        async def stream():
            encoder = EventEncoder()
            try:
                async for event in runner.run(input_data):
                    yield encoder.encode(event)
            except Exception:  # noqa: BLE001
                log.exception("chat run failed")
                from ag_ui.core import RunErrorEvent

                yield encoder.encode(RunErrorEvent(message="The assistant hit an error. Try again."))

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return chat

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
    show_visual                                             a chart or an animated move replay, drawn in the chat
    lore                                                    fun facts and the profile of a player or club
Frontend tools (declared by the page, run in the browser): seek_to, focus_player, set_view.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from agent_framework import Agent, BaseChatClient, ChatResponse, ChatResponseUpdate, Content, \
    FunctionInvocationLayer, Message, ResponseStream, tool
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from footballfoundry_agents.briefs import build
from footballfoundry_agents.service import AnalystService
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.triggers import Moment
from footballfoundry_agents.visuals import ABOUT, VISUAL_TYPES
from footballfoundry_agents.visuals import build as build_visual
from footballfoundry_mcp import views
from footballfoundry_schema import VisualType

log = logging.getLogger(__name__)

AUDIENCE_STYLE = {
    "broadcast": "neutral and clear, like a studio presenter",
    "analyst": "precise and tactical, with numbers",
    "casual": "friendly and plain, no jargon, with a fun fact about a player or club now and then",
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
- Show, don't just tell: when a chart or a replay would help (a goal or chance, the xG race, shots, momentum, team
  comparison), call show_visual; the page draws it under your answer. Mention what to look for in it. explain_moment
  answers come with their own visuals.
- Casual fans love a story: when a player or club comes up, you may add one fun fact from the lore tool (keep it
  to one sentence, and never present it as something that happened in this match).
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

    def replay(until_seq: int | None = None) -> MatchState:
        """The match state as it was at `until_seq` (default: where the viewer is)."""
        state_ = MatchState(match_id)
        for e in seen():
            if until_seq is not None and e.seq > until_seq:
                break
            state_.apply(e)
        return state_

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
        state_ = replay(anchor.seq)
        kind = ("goal" if anchor.type == "shot" and anchor.outcome == "goal"
                else "red_card" if anchor.type == "card" and anchor.outcome in ("red", "second_yellow")
                else "big_chance" if anchor.type == "shot" else "summary")
        chain = state_.possession_chain(anchor.possession_id)
        players = [p for p in dict.fromkeys(e.player_id for e in reversed(chain)
                                     if e.player_id and e.type != "pressure")][:3]
        brief = build(state_, Moment(match_id, kind, anchor, chain, anchor.team_id, players, 2), service.ref)
        insight = await service.writer.write(brief)
        return {"headline": insight.headline, "text": insight.text, "why_it_matters": insight.why_it_matters,
                "stats": insight.stats, "verified": insight.verified,
                "visuals": [v.model_dump(mode="json") for v in insight.visuals]}

    @tool(description="Draw a visual in the chat: " + "; ".join(f"{t} = {ABOUT[t]}" for t in VISUAL_TYPES)
                      + ". For move, pass the event_id of the shot or goal to replay its move; without one it "
                        "replays every goal so far. touch_map needs a player_id (default: the player the viewer "
                        "follows); pass_network and defensive_map show team_id first (both teams are drawn).")
    async def show_visual(type: VisualType, event_id: str | None = None, team_id: str | None = None,
                          player_id: str | None = None) -> dict[str, Any]:
        anchor = next((e for e in seen() if e.event_id == event_id), None) if event_id else None
        if event_id and anchor is None:
            return {"error": f"{event_id} hasn't happened yet for this viewer, or doesn't exist"}
        player_id = player_id or state.player_id
        if type == "touch_map" and not player_id:
            return {"error": "touch_map needs a player_id; use lookup to find it"}
        state_ = replay(anchor.seq if anchor else None)
        visual = build_visual(state_, type, service.ref, anchor=anchor if type == "move" else None, team_id=team_id,
                              player_id=player_id)
        if visual is None:
            return {"error": f"nothing to draw for {type} yet"
                             + (f" for {player_id}" if type == "touch_map" else "")}
        return {"shown": type, "visual": visual.model_dump(mode="json")}

    @tool(description="Fun facts and the profile of a player or club (by id, e.g. P0124 or T03): fastest, "
                      "youngest, oldest club, biggest ground... From the league data, true all season")
    async def lore(subject_id: str) -> dict[str, Any]:
        if subject_id not in service.lore.facts:
            return {"error": f"no player or club {subject_id}; use lookup to find the id"}
        return service.lore.about(subject_id)

    return [match_state, events, player_stats, lookup, insights, season_query, explain_moment, show_visual, lore]


# Offline, a question that mentions one of these gets the matching visual (first match wins).
OFFLINE_VISUALS = (("shot map", "shot_map"), ("shots", "shot_map"), ("xg", "xg_race"), ("momentum", "momentum"),
                   ("pass network", "pass_network"), ("passing", "pass_network"), ("touch", "touch_map"),
                   ("defensive", "defensive_map"), ("win the ball", "defensive_map"), ("tackles", "defensive_map"),
                   ("on top", "momentum"), ("compare", "compare"), ("stats", "compare"), ("numbers", "compare"),
                   ("goal", "move"), ("replay", "move"), ("move", "move"))


def offline_visual(question: str) -> str | None:
    q = question.lower()
    return next((v for word, v in OFFLINE_VISUALS if word in q), None)


class OfflineChatClient(FunctionInvocationLayer, BaseChatClient):
    """Without a Foundry model the assistant still answers, with the facts it can compute, and draws the visual a
    question asks for (by keyword) through the same show_visual tool the model would call."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def _reply(self, messages: list[Message]) -> list[Content]:
        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        answered = any(c.type == "function_result" for m in messages[last_user + 1:] for c in m.contents)
        visual = offline_visual(messages[last_user].text) if last_user >= 0 and not answered else None
        if visual:
            return [Content.from_function_call(f"offline-{len(messages)}", "show_visual", arguments={"type": visual})]
        return [Content.from_text(self.text)]

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        contents = self._reply(list(messages))
        if not stream:
            async def respond() -> ChatResponse:
                return ChatResponse(messages=[Message("assistant", contents)])
            return respond()

        async def updates():
            if contents[0].type != "text":
                yield ChatResponseUpdate(contents=contents, role="assistant")
                return
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
            agent = Agent(OfflineChatClient(offline_answer(state, service)), name="match_assistant",
                          tools=bound_tools(state, service, mcp))
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

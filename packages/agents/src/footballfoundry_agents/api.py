"""HTTP API for viewers and overlay renderers.

    GET  /healthz
    GET  /api/matches                                    matches seen so far, with score and clock
    GET  /api/matches/{id}/insights?as_of=&<viewer>      insights so far, as JSON
    GET  /api/matches/{id}/stream?<viewer>               Server-Sent Events: past insights, then live ones
    POST /api/matches/{id}/insights/{insight_id}/personalize   {audience, language, club_id, player_id}
    GET  /api/profiles/{user_id}
    PUT  /api/profiles/{user_id}
    GET  /api/league                                     clubs and players (names, kits, positions)
    GET  /api/season?through=<matchday>                  table, club and player totals, see season.py
    GET  /api/season/players/{id}?through=<matchday>     one player's match log
    GET  /api/matches/{id}/session                       team sheets and live status of one match
    GET  /api/matches/{id}/events?from=<t|live>&speed=<x|live>
                                                         SSE relay of the match's events, as a DVR: `reset`, every
                                                         event before `from` (match-clock seconds) as catch-up, the
                                                         rest paced at `speed`, following live once caught up;
                                                         stoppages are cut to DEAD_BALL_S of match time
    POST /api/chat                                       the match assistant (AG-UI), see chat.py
    *    /api/control/...                                the control room for testers (start/stop streams,
                                                         stories, season files), see control.py
    GET  /*                                              the web app, if built (packages/web)

<viewer> is either `user=<id>` (settings come from the stored profile) or any of `audience`, `lang`, `club`,
`player`. Every insight is returned with a `view` (headline and text for that viewer) next to the original.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from footballfoundry_schema import Insight

from footballfoundry_agents.control import routes as control_routes
from footballfoundry_agents.relay import HALF_TIME_S
from footballfoundry_agents.season import routes as season_routes
from footballfoundry_agents.service import AnalystService
from footballfoundry_agents.stores import Profile

KEEPALIVE_S = 15.0
# Paced replays cut each stoppage (ball out, foul, card, substitution) to this many match seconds: at 5x a 40 s wait
# for a goal kick would otherwise freeze the pitch for 8 s. Half-time is a break, not a stoppage, and keeps its length.
DEAD_BALL_S = 5.0
POLL_S = 5.0  # with several replicas, insights written elsewhere reach this viewer through the store
AUDIENCES = ("broadcast", "analyst", "casual", "player")


@dataclass
class Viewer:
    audience: str = "broadcast"
    language: str = "en"
    club_id: str | None = None
    player_id: str | None = None


async def _viewer(request: Request, service: AnalystService) -> Viewer:
    q = request.query_params
    v = Viewer()
    user = q.get("user") or request.headers.get("x-ms-client-principal-id")  # Container Apps authentication
    if user and (p := await service.stores.profiles.get(user)):
        v = Viewer(p.audience, p.language, p.favourite_club_ids[0] if p.favourite_club_ids else None,
                   p.focus_player_id)
    if q.get("audience") in AUDIENCES:
        v.audience = q["audience"]
    v.language = q.get("lang", v.language)
    v.club_id = q.get("club", v.club_id)
    v.player_id = q.get("player", v.player_id)
    return v


async def _view(service: AnalystService, insight: Insight, viewer: Viewer) -> dict[str, Any]:
    variant = await service.variant(insight, viewer.audience, viewer.language, viewer.club_id, viewer.player_id)
    data = insight.model_dump(mode="json", exclude={"variants"})
    data["view"] = variant.model_dump(mode="json")
    return data


class SpaFiles(StaticFiles):
    """Static files that fall back to index.html, so client-side routes survive a reload."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except Exception:  # noqa: BLE001 - starlette raises HTTPException(404) for missing files
            return await super().get_response("index.html", scope)


def create_app(service: AnalystService, *, cors_origins: list[str] | None = None, chat=None,
               web_dir: str | None = None, control=None, season=None) -> Starlette:
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "matches": len(service.states)})

    async def matches(request: Request) -> Response:
        # The relay log is current and, in Azure, complete on every replica; stored snapshots add matches from
        # before this replica started.
        found = {m["match_id"]: m for m in await service.stores.state.summaries()}
        for m in service.log.matches.values():
            if m.events:
                found[m.match_id] = m.summary()
        return JSONResponse(sorted(found.values(), key=lambda m: m["match_id"]))

    async def league(request: Request) -> Response:
        lg = service.ref.league
        if lg is None:
            return JSONResponse({"error": "no league file loaded"}, status_code=404)
        return JSONResponse({
            "name": lg.name, "season": lg.season,
            "clubs": {c.club_id: c.model_dump(mode="json", include={"name", "short_name", "colors", "stadium",
                                                                     "formation", "manager"})
                      for c in lg.clubs},
            "players": {p.player_id: {"name": p.name, "short_name": p.short_name, "number": p.shirt_number,
                                      "position": p.position, "club_id": p.club_id, "nationality": p.nationality}
                        for p in lg.players},
        })

    async def session(request: Request) -> Response:
        log = service.log.get(request.path_params["match_id"])
        return JSONResponse({"summary": log.summary(), "team_sheets": log.team_sheets(), "run": log.run,
                             "halftime_s": HALF_TIME_S})

    async def events(request: Request) -> Response:
        log = service.log.get(request.path_params["match_id"])
        q = request.query_params
        try:
            speed = 0.0 if q.get("speed", "live") in ("live", "0") else min(max(float(q["speed"]), 0.1), 600.0)
            start_at = None if q.get("from", "0") == "live" else max(float(q.get("from", "0")), 0.0)
        except ValueError:
            return JSONResponse({"error": "bad speed or from"}, status_code=400)

        def sse(kind: str, data: dict) -> str:
            return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"

        async def stream() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            while True:  # one pass per run of the match; streaming it again from kick-off starts over
                run = log.run
                frm = (log.times[-1] if log.times else 0.0) if start_at is None else start_at
                yield sse("reset", {"from": frm, "speed": speed or "live", "run": run})
                i = 0
                while i < len(log.events) and log.times[i] < frm and log.run == run:
                    yield sse("event", {"t": log.times[i], "catchup": True, "e": log.events[i].model_dump(mode="json")})
                    i += 1
                started = loop.time()
                skipped, paced = 0.0, -1  # match seconds cut from stoppages so far; the event they were counted for
                while log.run == run:
                    if i >= len(log.events):
                        if log.finished:
                            yield sse("end", {})
                            return
                        if not await log.wait(i, run, KEEPALIVE_S):
                            yield ": keepalive\n\n"
                        continue
                    t = log.times[i]
                    if speed and paced != i:
                        paced = i
                        prev = max(log.times[i - 1], frm) if i else frm
                        halftime = i > 0 and log.events[i - 1].type == "period_end"
                        if not halftime and t - prev > DEAD_BALL_S:
                            skipped += t - prev - DEAD_BALL_S
                    if speed and (wait := started + (t - frm - skipped) / speed - loop.time()) > 0:
                        await asyncio.sleep(min(wait, KEEPALIVE_S))
                        continue
                    yield sse("event", {"t": t, "catchup": False, "e": log.events[i].model_dump(mode="json")})
                    i += 1

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def insights(request: Request) -> Response:
        match_id = request.path_params["match_id"]
        as_of = request.query_params.get("as_of")
        viewer = await _viewer(request, service)
        items = await service.stores.insights.list(match_id, int(as_of) if as_of else None)
        return JSONResponse(await asyncio.gather(*(_view(service, i, viewer) for i in items)))

    async def stream(request: Request) -> Response:
        match_id = request.path_params["match_id"]
        viewer = await _viewer(request, service)
        queue = service.subscribe(match_id)  # subscribe first so nothing published meanwhile is missed

        async def events() -> AsyncIterator[str]:
            # Insights reach this stream two ways: pushed by this replica's writer, or found in the shared store
            # (written by another replica). `sent` stops repeats; a rewritten insight (a replayed match) has a new
            # created_at and is sent again. Clients upsert by insight_id.
            sent: dict[str, object] = {}

            async def fresh(items: list[Insight]) -> AsyncIterator[str]:
                for i in items:
                    if sent.get(i.insight_id) != i.created_at:
                        sent[i.insight_id] = i.created_at
                        yield f"event: insight\ndata: {json.dumps(await _view(service, i, viewer))}\n\n"

            try:
                async for chunk in fresh(await service.stores.insights.list(match_id)):
                    yield chunk
                idle = 0.0
                while True:
                    try:
                        items = [await asyncio.wait_for(queue.get(), timeout=POLL_S)]
                    except asyncio.TimeoutError:
                        items = await service.stores.insights.list(match_id)
                        idle += POLL_S
                    async for chunk in fresh(items):
                        idle = 0.0
                        yield chunk
                    if idle >= KEEPALIVE_S:
                        idle = 0.0
                        yield ": keepalive\n\n"
            finally:
                service.unsubscribe(match_id, queue)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def personalize(request: Request) -> Response:
        match_id, insight_id = request.path_params["match_id"], request.path_params["insight_id"]
        insight = await service.stores.insights.get(match_id, insight_id)
        if insight is None:
            return JSONResponse({"error": "insight not found"}, status_code=404)
        body = await request.json()
        audience = body.get("audience", "broadcast")
        if audience not in AUDIENCES:
            return JSONResponse({"error": f"audience must be one of {AUDIENCES}"}, status_code=400)
        v = await service.variant(insight, audience, body.get("language", "en"), body.get("club_id"),
                                  body.get("player_id"))
        return JSONResponse(v.model_dump(mode="json"))

    async def profile(request: Request) -> Response:
        user_id = request.path_params["user_id"]
        if request.method == "PUT":
            try:
                p = Profile.model_validate({**await request.json(), "user_id": user_id})
            except (ValidationError, ValueError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            await service.stores.profiles.put(p)
            return JSONResponse(p.model_dump(mode="json"))
        p = await service.stores.profiles.get(user_id)
        if p is None:
            return JSONResponse({"error": "profile not found"}, status_code=404)
        return JSONResponse(p.model_dump(mode="json"))

    routes = [
        Route("/healthz", health),
        Route("/api/matches", matches),
        Route("/api/matches/{match_id}/insights", insights),
        Route("/api/matches/{match_id}/stream", stream),
        Route("/api/matches/{match_id}/insights/{insight_id}/personalize", personalize, methods=["POST"]),
        Route("/api/profiles/{user_id}", profile, methods=["GET", "PUT"]),
        Route("/api/league", league),
        Route("/api/matches/{match_id}/session", session),
        Route("/api/matches/{match_id}/events", events),
    ]
    if chat is not None:
        routes.append(Route("/api/chat", chat, methods=["POST"]))
    routes += control_routes(control)
    routes += season_routes(season, service)
    if web_dir:
        routes.append(Mount("/", SpaFiles(directory=web_dir, html=True), name="web"))
    middleware = [Middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST", "PUT"],
                             allow_headers=["*"])] if cors_origins else []
    return Starlette(routes=routes, middleware=middleware)

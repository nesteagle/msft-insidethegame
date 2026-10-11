"""The control room: start and stop match streams, find matches worth watching, from the web app.

Testers drive everything from /control instead of juggling terminals:

    streams   a fixture, a matchday or a one-off match, at any speed, optionally with a scenario. Locally the
              events go straight into this service; in Azure (or with --stream-to eventhub) they go to Event Hubs,
              so the whole pipeline (every replica, the archive, Fabric) sees them, exactly like
              `footballfoundry stream`.
    stories   the story finder (comebacks, late winners, red cards...) with a "stream it" button
    truth     a scenario's ground truth once its match has been simulated
    season    regenerate the season files the MCP server reads (local only)

Open on localhost. On any other host it's off unless CONTROL_KEY is set, and then every request needs that key:
a public deployment must not let anyone with the link start streams and spend model tokens.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from footballfoundry_schema import Event

log = logging.getLogger(__name__)

MAX_STREAMS = 3
SPEEDS = (0, 1, 5, 10, 30, 60, 120)


@dataclass
class StreamJob:
    job_id: str
    stream_id: str
    label: str
    match_ids: list[str]
    speed: float
    target: str
    scenario: str | None
    total: int
    sent: int = 0
    clock: str = "0'"
    status: str = "running"  # running, finished, stopped, failed
    error: str | None = None
    started: float = field(default_factory=time.time)
    truth: list[dict] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)

    def view(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "stream_id": self.stream_id, "label": self.label, "match_ids": self.match_ids,
                "speed": self.speed, "target": self.target, "scenario": self.scenario, "total": self.total,
                "sent": self.sent, "clock": self.clock, "status": self.status, "error": self.error,
                "started": self.started, "has_truth": bool(self.truth)}


class Control:
    def __init__(self, service, *, seed: int = 42, target: str = "local", eventhub: dict | None = None,
                 scenarios_dir: str | Path | None = None, data_dir: str | Path | None = None,
                 key: str | None = None, local_only: bool = True, season=None) -> None:
        self.service = service
        self.seed = seed
        self.target = target
        self.eventhub = eventhub  # {"eventhub", "namespace", "connection_string"} or None
        self.scenarios_dir = Path(scenarios_dir) if scenarios_dir else None
        self.data_dir = Path(data_dir) if data_dir else None
        self.key = key
        self.local_only = local_only  # season files can only be written on a developer machine
        self.jobs: dict[str, StreamJob] = {}
        self._league = None
        if season is None:
            from footballfoundry_agents.season import Season

            season = Season(seed)
        self.season = season  # the simulated season, shared with the season pages
        self.season_job: dict[str, Any] = {"status": "idle"}

    # -- reference ---------------------------------------------------------------------------------------------

    def league(self):
        return self.season.league()

    def scenarios(self) -> list[dict[str, str]]:
        if not self.scenarios_dir or not self.scenarios_dir.exists():
            return []
        import json

        out = []
        for path in sorted(self.scenarios_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                out.append({"file": path.name, "name": data.get("name", path.stem),
                            "description": data.get("description", "")})
            except ValueError:
                log.warning("skipping unreadable scenario %s", path)
        return out

    def info(self) -> dict[str, Any]:
        from footballfoundry.stories import STORIES
        from footballfoundry.world import generate_fixtures

        lg = self.league()
        return {
            "seed": self.seed, "target": self.target, "eventhub": self.eventhub is not None,
            "protected": self.key is not None, "max_streams": MAX_STREAMS, "speeds": SPEEDS,
            "local_only": self.local_only, "scenarios": self.scenarios(), "stories": STORIES,
            "clubs": [{"club_id": c.club_id, "name": c.name, "short_name": c.short_name} for c in lg.clubs],
            "fixtures": [{"match_id": f.match_id, "matchday": f.matchday, "home": f.home_club_id,
                          "away": f.away_club_id} for f in generate_fixtures(lg)],
            "season": self.season_job,
        }

    # -- streams -----------------------------------------------------------------------------------------------

    def _simulate(self, spec: dict[str, Any]):
        """Pick and simulate the matches (CPU work, run in a thread)."""
        from footballfoundry.engine import simulate_match
        from footballfoundry.scenario import Scenario, truth
        from footballfoundry.season import play_fixture
        from footballfoundry.world import generate_fixtures, match_seed

        lg = self.league()
        scenario = Scenario()
        if spec.get("scenario"):
            path = (self.scenarios_dir or Path("scenarios")) / Path(spec["scenario"]).name  # nothing outside it
            if not path.is_file():
                raise ValueError(f"no scenario {path.name}")
            scenario = Scenario.load(path)
        kwargs = scenario.engine_kwargs()
        if spec.get("home") or spec.get("away"):
            match_id = spec.get("match_id") or f"X{secrets.token_hex(2).upper()}"
            results = [simulate_match(lg, spec.get("home") or "T01", spec.get("away") or "T02", match_id=match_id,
                                      seed=match_seed(lg.seed, match_id), **kwargs)]
            label = f"{lg.club(results[0].info.home_club_id).short_name} v " \
                    f"{lg.club(results[0].info.away_club_id).short_name} ({match_id})"
        else:
            fixtures = generate_fixtures(lg)
            if spec.get("matchday"):
                chosen = [f for f in fixtures if f.matchday == int(spec["matchday"])]
                label = f"Matchday {spec['matchday']}"
            else:
                chosen = [f for f in fixtures if f.match_id == spec.get("match_id", "M0001")]
                label = chosen[0].match_id if chosen else "?"
            if not chosen:
                raise ValueError("no such fixture (matchdays 1-38, match ids M0001-M0380)")
            results = [play_fixture(lg, f, **kwargs) for f in chosen]
            if len(results) == 1:
                i = results[0].info
                label = f"{i.match_id} {lg.club(i.home_club_id).short_name} v {lg.club(i.away_club_id).short_name}"
        truths = [truth(r, scenario) for r in results] if scenario else []
        return results, label, scenario.name if scenario else None, truths

    async def start(self, spec: dict[str, Any]) -> StreamJob:
        from footballfoundry.stream import HALF_TIME_S, merge

        if sum(j.status == "running" for j in self.jobs.values()) >= MAX_STREAMS:
            raise ValueError(f"at most {MAX_STREAMS} streams at once; stop one first")
        speed = float(spec.get("speed", 30))
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        target = spec.get("target") or self.target
        if target == "eventhub" and self.eventhub is None:
            raise ValueError("Event Hubs isn't configured for this service")
        results, label, scenario, truths = await asyncio.to_thread(self._simulate, spec)
        stream_id = secrets.token_hex(4)
        matches = [[e.model_copy(update={"stream_id": stream_id}) for e in r.events] for r in results]
        halftime = spec.get("halftime")
        halftime_s = float(halftime) * speed if halftime not in (None, "") and speed else HALF_TIME_S
        items = merge(matches, halftime_s)
        job = StreamJob(secrets.token_hex(3), stream_id, label, [r.info.match_id for r in results], speed, target,
                        scenario, len(items), truth=truths)
        self.jobs[job.job_id] = job
        runner = self._run_local(job, items) if target == "local" else asyncio.to_thread(self._run_eventhub, job, items)
        asyncio.create_task(self._supervise(job, runner), name=f"control-{job.job_id}")
        log.info("control: streaming %s (%d events, %sx, to %s, stream %s)", label, len(items), speed, target,
                 stream_id)
        return job

    async def _supervise(self, job: StreamJob, runner) -> None:
        try:
            await runner
            if job.status == "running":
                job.status = "stopped" if job.stop.is_set() else "finished"
        except Exception as exc:  # noqa: BLE001 - shown in the control room
            log.exception("control: stream %s failed", job.job_id)
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

    async def _run_local(self, job: StreamJob, items: list[tuple[float, Event]]) -> None:
        """Feed the events straight into this service, paced on the match clock."""
        from footballfoundry.stream import FAST_CHUNK, SEND_WINDOW_S

        loop = asyncio.get_running_loop()
        start, i = loop.time(), 0
        while i < len(items) and not job.stop.is_set():
            if job.speed > 0:
                wait = start + items[i][0] / job.speed - loop.time()
                if wait > 0:
                    await asyncio.sleep(min(wait, 0.5))
                    continue
                end_t = items[i][0] + SEND_WINDOW_S * job.speed
                j = i + 1
                while j < len(items) and items[j][0] <= end_t:
                    j += 1
            else:
                j = min(i + FAST_CHUNK, len(items))
            for _, e in items[i:j]:
                await self.service.ingest(e)
            job.sent, job.clock, i = j, items[j - 1][1].clock, j
            await asyncio.sleep(0)

    def _run_eventhub(self, job: StreamJob, items: list[tuple[float, Event]]) -> None:
        """Send to Event Hubs (in a thread: the producer is synchronous), like `footballfoundry stream`."""
        from footballfoundry.stream import FAST_CHUNK, SEND_WINDOW_S, EventHubSink

        sink = EventHubSink.connect(self.eventhub["eventhub"], namespace=self.eventhub.get("namespace"),
                                    connection_string=self.eventhub.get("connection_string"))
        try:
            start, i = time.monotonic(), 0
            while i < len(items) and not job.stop.is_set():
                if job.speed > 0:
                    wait = start + items[i][0] / job.speed - time.monotonic()
                    if wait > 0:
                        job.stop.wait(min(wait, 0.5))
                        continue
                    end_t = items[i][0] + SEND_WINDOW_S * job.speed
                    j = i + 1
                    while j < len(items) and items[j][0] <= end_t:
                        j += 1
                else:
                    j = min(i + FAST_CHUNK, len(items))
                sink.send([e for _, e in items[i:j]])
                job.sent, job.clock, i = j, items[j - 1][1].clock, j
        finally:
            sink.close()

    def stop(self, job_id: str) -> StreamJob:
        job = self.jobs[job_id]
        job.stop.set()
        if job.status == "running":
            job.status = "stopped"
        return job

    def list(self) -> list[dict[str, Any]]:
        return [j.view() for j in sorted(self.jobs.values(), key=lambda j: -j.started)][:20]

    # -- stories -----------------------------------------------------------------------------------------------

    async def stories(self, wanted: list[str], limit: int = 12) -> list[dict[str, Any]]:
        from footballfoundry.stories import STORIES

        book = await self.season.ready_book()  # instant once the book is loaded (at startup)
        wanted = [w for w in wanted if w in STORIES]
        found = [m for m in book["matches"]
                 if m.get("stories") and all(w in m["stories"] for w in wanted)]
        found.sort(key=lambda m: (-m.get("drama", 0), m["id"]))
        lg = self.league()
        return [{"match_id": m["id"], "matchday": m["md"], "home": lg.club(m["h"]).name,
                 "away": lg.club(m["a"]).name, "score": [m["hs"], m["as"]], "drama": m["drama"],
                 "stories": m["stories"]} for m in found[:limit]]

    # -- season files (local only) -----------------------------------------------------------------------------

    async def generate_season(self) -> dict[str, Any]:
        if not self.local_only or self.data_dir is None:
            raise ValueError("season files can only be generated on a local run")
        if self.season_job.get("status") == "running":
            return self.season_job
        self.season_job = {"status": "running", "started": time.time(), "out": str(self.data_dir)}

        def work() -> None:
            from footballfoundry import export
            from footballfoundry.season import simulate_season
            from footballfoundry.stats import standings
            from footballfoundry.world import generate_fixtures

            lg = self.league()
            results = simulate_season(lg, workers=1)
            export.write_league(lg, self.data_dir)
            export.write_fixtures(generate_fixtures(lg), self.data_dir)
            for r in results:
                export.write_match(r, self.data_dir)
            export.write_standings(standings(lg, results), self.data_dir)
            self.season.use_results(results)
            from footballfoundry import book

            self.season.replace_book(book.build(lg, results))

        async def run() -> None:
            try:
                await asyncio.to_thread(work)
                self.season_job |= {"status": "finished", "finished": time.time()}
            except Exception as exc:  # noqa: BLE001
                log.exception("control: season generation failed")
                self.season_job |= {"status": "failed", "error": str(exc)}

        asyncio.create_task(run(), name="control-season")
        return self.season_job

    def allowed(self, provided: str | None) -> bool:
        return self.key is None or (provided is not None and secrets.compare_digest(provided, self.key))


def routes(control: Control | None) -> list:
    """/api/control/*. With control off, only GET /api/control answers, saying so (the page explains how to turn it on)."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    def guarded(fn):
        async def endpoint(request: Request) -> Response:
            if control is None:
                return JSONResponse({"error": "the control room is off on this deployment"}, status_code=404)
            if not control.allowed(request.headers.get("x-control-key")):
                return JSONResponse({"error": "wrong or missing control key"}, status_code=401)
            try:
                return await fn(request)
            except KeyError:
                return JSONResponse({"error": "not found"}, status_code=404)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        return endpoint

    async def info(request: Request) -> Response:
        if control is None:
            return JSONResponse({"enabled": False})
        if not control.allowed(request.headers.get("x-control-key")):
            return JSONResponse({"enabled": True, "protected": True, "authorized": False})
        return JSONResponse({"enabled": True, "authorized": True, **await asyncio.to_thread(control.info)})

    async def streams(request: Request) -> Response:
        if request.method == "POST":
            job = await control.start(await _body(request))
            return JSONResponse(job.view(), status_code=201)
        return JSONResponse(control.list())

    async def stop(request: Request) -> Response:
        return JSONResponse(control.stop(request.path_params["job_id"]).view())

    async def truth(request: Request) -> Response:
        return JSONResponse(control.jobs[request.path_params["job_id"]].truth)

    async def stories(request: Request) -> Response:
        body = await _body(request)
        return JSONResponse(await control.stories(list(body.get("stories") or []), int(body.get("limit", 12))))

    async def season(request: Request) -> Response:
        return JSONResponse(await control.generate_season(), status_code=202)

    return [
        Route("/api/control", info),
        Route("/api/control/streams", guarded(streams), methods=["GET", "POST"]),
        Route("/api/control/streams/{job_id}/stop", guarded(stop), methods=["POST"]),
        Route("/api/control/streams/{job_id}/truth", guarded(truth)),
        Route("/api/control/stories", guarded(stories), methods=["POST"]),
        Route("/api/control/season", guarded(season), methods=["POST"]),
    ]


async def _body(request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise ValueError("expected a JSON body") from None
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    return body

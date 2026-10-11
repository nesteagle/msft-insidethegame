import asyncio
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from footballfoundry_agents.control import Control, routes

SCENARIOS = Path(__file__).parents[3] / "scenarios"


class FakeService:
    def __init__(self):
        self.events = []

    async def ingest(self, e):
        self.events.append(e)


async def wait_for(job, *statuses, timeout=60.0):
    for _ in range(int(timeout * 20)):
        if job.status in statuses:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"job still {job.status}")


def test_a_fast_stream_feeds_the_service_stamped_with_its_stream_id(match):
    async def run():
        service = FakeService()
        control = Control(service)
        job = await control.start({"match_id": "M0001", "speed": 0})
        await wait_for(job, "finished")
        return service.events, job

    events, job = asyncio.run(run())
    assert [e.event_id for e in events] == [e.event_id for e in match.events]
    assert {e.stream_id for e in events} == {job.stream_id}
    assert job.sent == job.total == len(match.events) and job.label.startswith("M0001")


def test_stop_ends_a_paced_stream():
    async def run():
        service = FakeService()
        control = Control(service)
        job = await control.start({"home": "T03", "away": "T04", "speed": 1})
        await asyncio.sleep(0.3)
        control.stop(job.job_id)
        await asyncio.sleep(0.6)
        return service.events, job

    events, job = asyncio.run(run())
    assert job.status == "stopped"
    assert job.match_ids[0].startswith("X") and len(events) < job.total


def test_a_scenario_stream_has_ground_truth():
    async def run():
        control = Control(FakeService(), scenarios_dir=SCENARIOS)
        assert any(s["file"] == "winger-central.json" for s in control.scenarios())
        job = await control.start({"match_id": "M0002", "speed": 0, "scenario": "winger-central.json"})
        await wait_for(job, "finished")
        return job

    job = asyncio.run(run())
    assert job.scenario and job.truth[0]["match_id"] == "M0002" and job.truth[0]["changes"]


def test_bad_requests_are_refused():
    async def run():
        control = Control(FakeService(), scenarios_dir=SCENARIOS)
        for spec in ({"speed": 7}, {"matchday": 99, "speed": 0}, {"scenario": "../pyproject.toml", "speed": 0},
                     {"speed": 0, "target": "eventhub"}):
            with pytest.raises(ValueError):
                await control.start(spec)

    asyncio.run(run())


def test_the_key_guards_every_route():
    async def run():
        control = Control(FakeService(), key="sesame")
        transport = httpx.ASGITransport(app=Starlette(routes=routes(control)))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            locked = (await client.get("/api/control")).json()
            refused = await client.get("/api/control/streams")
            ok = await client.post("/api/control/streams", headers={"x-control-key": "sesame"},
                                   json={"match_id": "M0003", "speed": 0})
            listed = (await client.get("/api/control/streams", headers={"x-control-key": "sesame"})).json()
            bad = await client.post("/api/control/streams", headers={"x-control-key": "sesame"}, json={"speed": 7})
        return locked, refused.status_code, ok.status_code, listed, bad.status_code

    locked, refused, ok, listed, bad = asyncio.run(run())
    assert locked == {"enabled": True, "protected": True, "authorized": False}
    assert (refused, ok, bad) == (401, 201, 400)
    assert listed[0]["match_ids"] == ["M0003"]


def test_control_off_says_so():
    async def run():
        transport = httpx.ASGITransport(app=Starlette(routes=routes(None)))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return (await client.get("/api/control")).json(), (await client.get("/api/control/streams")).status_code

    assert asyncio.run(run()) == ({"enabled": False}, 404)

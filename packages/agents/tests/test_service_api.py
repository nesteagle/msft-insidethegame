import asyncio

from starlette.testclient import TestClient

from footballfoundry_agents.api import create_app
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.stores import file_stores, memory_stores
from footballfoundry_agents.workflow import AgentWriter


async def feed(service, events):
    for e in events:
        await service.ingest(e)
    await service.drain()


def test_restart_resumes_without_duplicates(match, ref, tmp_path):
    async def scenario():
        # One uninterrupted run, for reference.
        clean = AnalystService(memory_stores(), TemplateWriter(ref), ref)
        await feed(clean, match.events)
        expected = [i.insight_id for i in await clean.stores.insights.list("M0001")]
        await clean.close()

        # Crash after 1,000 events, restart, and get every event redelivered from an older checkpoint.
        stores = file_stores(tmp_path)
        first = AnalystService(stores, TemplateWriter(ref), ref, snapshot_every=50)
        await feed(first, match.events[:1000])
        await first.close()
        second = AnalystService(file_stores(tmp_path), TemplateWriter(ref), ref, snapshot_every=50)
        await feed(second, match.events[700:])
        got = [i.insight_id for i in await second.stores.insights.list("M0001")]
        await second.close()
        return expected, got

    expected, got = asyncio.run(scenario())
    assert got == expected


def test_api_personalises_and_stores_profiles(match, ref, scripted):
    client_llm = scripted()
    service = AnalystService(memory_stores(), AgentWriter(client_llm, ref), ref)
    asyncio.run(feed(service, match.events))

    with TestClient(create_app(service)) as http:
        assert http.get("/healthz").json() == {"ok": True, "matches": 1}
        m = http.get("/api/matches").json()[0]
        assert m["finished"] and [m["home_score"], m["away_score"]] == [match.info.home_score, match.info.away_score]

        items = http.get("/api/matches/M0001/insights").json()
        assert items and all(i["view"]["text"] == i["text"] for i in items)  # broadcast/en: no rewrite
        assert http.get("/api/matches/M0001/insights", params={"as_of": 600}).json() == \
            [i for i in items if i["seq"] <= 600]

        calls = len(client_llm.calls)
        put = http.put("/api/profiles/u1", json={"audience": "casual", "language": "es",
                                                 "favourite_club_ids": [match.info.away_club_id]})
        assert put.status_code == 200 and http.get("/api/profiles/u1").json()["language"] == "es"
        spanish = http.get("/api/matches/M0001/insights", params={"user": "u1"}).json()
        assert {i["view"]["language"] for i in spanish} == {"es"}
        assert len(client_llm.calls) == calls + len(items)  # one personaliser call per insight
        http.get("/api/matches/M0001/insights", params={"user": "u1"})
        assert len(client_llm.calls) == calls + len(items)  # cached for the next viewer

        goal = next(i for i in items if i["kind"] == "goal")
        r = http.post(f"/api/matches/M0001/insights/{goal['insight_id']}/personalize",
                      json={"audience": "nonsense"})
        assert r.status_code == 400
        assert http.get("/api/profiles/nobody").status_code == 404


def test_a_match_streamed_again_shows_only_the_new_runs_insights(match, ref):
    import asyncio

    from footballfoundry_agents.service import AnalystService, TemplateWriter
    from footballfoundry_agents.stores import memory_stores

    service = AnalystService(memory_stores(), TemplateWriter(ref), ref)

    async def feed(events):
        for e in events:
            await service.ingest(e)
        await service.drain()
        return await service.stores.insights.list("M0001")

    async def both():
        first = await feed(match.events)  # a full run
        await asyncio.sleep(0.01)  # run ids are kick-off times in ms
        return first, await feed(match.events[:400])  # streamed again from kick-off, stopped early

    first, second = asyncio.run(both())
    assert len({i.run_id for i in first}) == 1 and len({i.run_id for i in second}) == 1
    assert second[0].run_id > first[0].run_id
    assert max(i.seq for i in second) <= match.events[399].seq  # nothing left over from the first run

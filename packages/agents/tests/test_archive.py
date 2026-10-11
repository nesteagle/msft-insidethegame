import asyncio

from footballfoundry_mcp.archive import FileArchive, MemoryArchive

from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.stores import memory_stores


def run(service, events):
    async def go():
        for e in events:
            await service.ingest(e)
        await service.drain()
    asyncio.run(go())


def test_every_event_and_the_result_are_archived(match, ref):
    archive = MemoryArchive()
    run(AnalystService(memory_stores(), TemplateWriter(ref), ref, archive=archive), match.events)
    stored = asyncio.run(archive.events("M0001", 10_000))
    assert [e.seq for e in stored] == [e.seq for e in match.events]
    result = asyncio.run(archive.results())[0]
    assert (result["home_score"], result["away_score"]) == (match.info.home_score, match.info.away_score)
    goals = sum(p["goals"] for p in result["players"].values())
    assert goals == match.info.home_score + match.info.away_score
    assert asyncio.run(archive.events("M0001", 100))[-1].seq == 100  # as_of is respected


def test_a_restream_replaces_the_archived_run(match, ref, tmp_path):
    archive = FileArchive(tmp_path)

    async def go():
        service = AnalystService(memory_stores(), TemplateWriter(ref), ref, archive=archive)
        for e in [*match.events, *match.events[:300]]:  # the same match streamed again from kick-off
            await service.ingest(e)
        await service.drain()

    asyncio.run(go())
    stored = asyncio.run(archive.events("M0001", 10_000))
    assert [e.seq for e in stored] == list(range(1, 301))

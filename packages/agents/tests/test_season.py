import asyncio

import httpx
import pytest
from starlette.applications import Starlette

from footballfoundry import book
from footballfoundry.season import play_fixture
from footballfoundry.world import generate_fixtures
from footballfoundry_agents.relay import EventLog
from footballfoundry_agents.season import Season, routes


@pytest.fixture(scope="module")
def two_matchdays(league):
    """The first two matchdays of the seed-42 season, and their season book."""
    results = [play_fixture(league, f) for f in generate_fixtures(league) if f.matchday <= 2]
    return results, book.build(league, results)


def test_book_lines_add_up(two_matchdays):
    results, data = two_matchdays
    for r, m in zip(sorted(results, key=lambda r: (r.info.matchday, r.info.match_id)), data["matches"]):
        goals = {r.info.home_club_id: r.info.home_score, r.info.away_club_id: r.info.away_score}
        for club, n in goals.items():
            assert sum(line.get("g", 0) for line in m["lines"] if line["c"] == club) == n
        assists = sum(line.get("a", 0) for line in m["lines"])
        assert assists <= m["hs"] + m["as"]
        assert all(0 < line["min"] <= 130 for line in m["lines"])


def test_view_through_a_matchday(two_matchdays):
    _, data = two_matchdays
    season = Season()
    season._set_book(data)
    one, two = season.view(1), season.view(2)
    assert one["played"] == 10 and two["played"] == 20
    assert sum(r["points"] for r in one["table"]) == sum(3 if m["hs"] != m["as"] else 2
                                                         for m in data["matches"] if m["md"] == 1)
    leader = two["table"][0]
    assert leader["played"] == 2 and len(leader["form"]) == 2 and leader["previous"] is not None
    assert one["table"][0]["next"]["matchday"] == 2
    total_goals = sum(p["g"] for p in two["players"])
    assert total_goals == sum(m["hs"] + m["as"] for m in data["matches"])
    log = season.player_log(two["players"][0]["p"], 2)
    assert [m["matchday"] for m in log] == [1, 2]


def test_live_matchday_is_not_spoiled(two_matchdays, match):
    _, data = two_matchdays
    season = Season()
    season._set_book(data)

    class Service:
        log = EventLog()

    service = Service()
    m2 = next(m["id"] for m in data["matches"] if m["md"] == 2)

    async def run():
        transport = httpx.ASGITransport(app=Starlette(routes=routes(season, service)))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            before = (await client.get("/api/season")).json()
            for e in match.events[:50]:  # a matchday-2 fixture starts streaming
                service.log.add(e.model_copy(update={"match_id": m2}))
            during = (await client.get("/api/season")).json()
            asked = (await client.get("/api/season", params={"through": 2})).json()
        return before, during, asked

    before, during, asked = asyncio.run(run())
    assert (before["through"], before["live_matchday"]) == (2, None)
    assert (during["through"], during["live_matchday"], during["played"]) == (1, 2, 10)
    assert asked["through"] == 2  # the viewer can still choose to see it


def test_the_book_is_built_when_missing(tmp_path, league, two_matchdays):
    results, _ = two_matchdays
    season = Season(path=tmp_path / "book.json")
    season.use_results(results)

    async def run():
        assert season.book() is None and season.status == "building"
        await season._building
        return season.book()

    data = asyncio.run(run())
    assert len(data["matches"]) == 20 and (tmp_path / "book.json").exists()


def test_story_finder_reads_the_book(two_matchdays):
    from footballfoundry_agents.control import Control

    _, data = two_matchdays
    control = Control(object())
    control.season._set_book(data)
    every = asyncio.run(control.stories([], limit=50))
    assert every and every == sorted(every, key=lambda s: (-s["drama"], s["match_id"]))
    tag = next(iter(every[0]["stories"]))
    assert all(tag in s["stories"] for s in asyncio.run(control.stories([tag])))
    assert asyncio.run(control.stories(["not-a-story"], limit=50)) == every  # unknown stories are ignored


def test_an_old_book_is_rebuilt(tmp_path, two_matchdays):
    import json

    results, data = two_matchdays
    path = tmp_path / "book.json"
    path.write_text(json.dumps({**data, "version": 1}), encoding="utf-8")
    season = Season(path=path)
    season.use_results(results)

    async def run():
        return await season.ready_book()

    assert asyncio.run(run())["version"] == book.BOOK_VERSION

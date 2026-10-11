import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from footballfoundry import export
from footballfoundry.season import simulate_season
from footballfoundry.world import generate_fixtures, generate_league
from footballfoundry_mcp.backends import FileBackend
from footballfoundry_mcp.server import create_server


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    """Two matchdays of the seed-42 season, written the way `footballfoundry season` writes them."""
    out = tmp_path_factory.mktemp("data")
    league = generate_league(42)
    export.write_league(league, out)
    export.write_fixtures(generate_fixtures(league), out)
    for r in simulate_season(league, limit=20):
        export.write_match(r, out)
    return out


def call(data_dir, tool, **args):
    async def go():
        async with create_connected_server_and_client_session(create_server(FileBackend(data_dir))) as session:
            result = await session.call_tool(tool, args)
            assert not result.isError, result.content
            payload = result.structuredContent
            return payload.get("result", payload) if isinstance(payload, dict) else payload
    return asyncio.run(go())


def test_tools_are_listed_read_only(data_dir):
    async def go():
        async with create_connected_server_and_client_session(create_server(FileBackend(data_dir))) as session:
            return (await session.list_tools()).tools
    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == {"get_match_state", "get_events", "get_player_stats", "get_team_stats", "lookup",
                          "season_query", "get_insights"}
    assert all(t.annotations.readOnlyHint for t in tools.values())


def test_match_state_never_looks_past_as_of(data_dir):
    events = [json.loads(line) for line in (data_dir / "events" / "M0001.jsonl").read_text().splitlines()]
    first_goal = next(e for e in events if e["type"] == "shot" and e["outcome"] == "goal")
    before = call(data_dir, "get_match_state", match_id="M0001", as_of=first_goal["seq"] - 1)
    after = call(data_dir, "get_match_state", match_id="M0001", as_of=first_goal["seq"])
    assert before["as_of_seq"] == first_goal["seq"] - 1 and before["goals"] == []
    assert [g["event_id"] for g in after["goals"]] == [first_goal["event_id"]]
    assert before["home"]["club_id"] == "T20" and before["home"]["name"]  # home team = first period_start


def test_events_filters(data_dir):
    shots = call(data_dir, "get_events", match_id="M0001", as_of=900, types=["shot"], limit=100)
    assert shots and all(e["type"] == "shot" and e["seq"] <= 900 for e in shots)
    chain = call(data_dir, "get_events", match_id="M0001", as_of=900, possession_id=shots[-1]["possession_id"])
    assert chain[-1]["event_id"] == shots[-1]["event_id"] or chain[-1]["seq"] <= 900


def test_lookup_and_player_stats(data_dir):
    found = call(data_dir, "lookup", query="T20")
    assert found[0]["kind"] == "club" and found[0]["club_id"] == "T20"
    events = [json.loads(line) for line in (data_dir / "events" / "M0001.jsonl").read_text().splitlines()]
    shooters = [e["player_id"] for e in events if e["type"] == "shot"]
    top = max(set(shooters), key=shooters.count)
    goals = sum(e["player_id"] == top and e["outcome"] == "goal" for e in events if e["type"] == "shot")
    p = call(data_dir, "get_player_stats", match_id="M0001", player_id=top, as_of=10_000)
    assert p["name"] and p["stats"]["shots"] == shooters.count(top) and p["stats"].get("goals", 0) == goals


def test_season_queries_respect_the_matchday_cutoff(data_dir):
    fixtures = json.loads((data_dir / "fixtures.json").read_text())
    md2 = next(f["match_id"] for f in fixtures if f["matchday"] == 2)
    table_all = call(data_dir, "season_query", name="standings")
    table_md1 = call(data_dir, "season_query", name="standings", before_match_id=md2)
    table_none = call(data_dir, "season_query", name="standings", before_match_id="M0001")
    assert sum(r["played"] for r in table_all) == 40 and sum(r["played"] for r in table_md1) == 20
    assert all(r["played"] == 0 for r in table_none)
    scorers = call(data_dir, "season_query", name="top_scorers", params={"top": 3})
    assert len(scorers) == 3 and scorers[0]["goals"] >= scorers[-1]["goals"]
    form = call(data_dir, "season_query", name="team_form", params={"club_id": "T20"})
    assert [f["matchday"] for f in form] == [1, 2]


class Broken:
    """An Eventhouse that is down, or so slow it times out."""

    def __init__(self, slow=False):
        self.league, self.slow = None, slow

    async def events(self, match_id, as_of):
        if self.slow:
            await asyncio.sleep(5)
        raise ConnectionError("eventhouse unavailable")

    async def matchday(self, match_id):
        raise ConnectionError("eventhouse unavailable")

    async def season(self, name, params, before_matchday):
        raise ConnectionError("eventhouse unavailable")


def archive_from(data_dir):
    """An archive holding the same matches as the season files, as the agents service would have written it."""
    from footballfoundry.season import simulate_season  # noqa: F401  (generator is a dev dependency)
    from footballfoundry_mcp.archive import MemoryArchive, result_doc
    from footballfoundry_schema import Event
    from footballfoundry_schema.stats import add_event
    from collections import defaultdict

    archive = MemoryArchive()
    for path in sorted((data_dir / "events").glob("*.jsonl")):
        events = [Event.model_validate_json(line) for line in path.read_text().splitlines()]
        info = json.loads((data_dir / "matches" / f"{path.stem}.json").read_text())
        players = defaultdict(lambda: defaultdict(float))
        for e in events:
            if e.player_id and e.type != "substitution":
                add_event(players[e.player_id], e)
        asyncio.run(archive.append(path.stem, 1, events))
        asyncio.run(archive.put_result(result_doc(path.stem, 1, info["home_club_id"], info["away_club_id"],
                                                  info["home_score"], info["away_score"], players)))
    return archive


def test_fallback_when_the_eventhouse_fails(data_dir):
    from footballfoundry_mcp.backends import ArchiveBackend, FallbackBackend

    files = FileBackend(data_dir)
    archive = ArchiveBackend(archive_from(data_dir), files.fixtures, files.league)
    chain = FallbackBackend(live=[("eventhouse", Broken()), ("archive", archive)],
                            season=[("eventhouse", Broken(slow=True)), ("archive", archive)], timeout_s=0.5)

    events, source = asyncio.run(chain.events_with_source("M0001", 400))
    assert source == "archive" and events[-1].seq == 400
    fixtures = json.loads((data_dir / "fixtures.json").read_text())
    md2 = next(f["match_id"] for f in fixtures if f["matchday"] == 2)
    table = asyncio.run(chain.season("standings", {}, asyncio.run(chain.matchday(md2))))
    expected = asyncio.run(files.season("standings", {}, 2))
    assert [(r["club_id"], r["points"]) for r in table] == [(r["club_id"], r["points"]) for r in expected]


def test_tools_say_which_backend_answered(data_dir):
    from footballfoundry_mcp.backends import ArchiveBackend, FallbackBackend

    files = FileBackend(data_dir)
    chain = FallbackBackend(live=[("eventhouse", Broken()), ("archive", ArchiveBackend(
        archive_from(data_dir), files.fixtures, files.league)), ("files", files)], season=[("files", files)])

    async def go():
        async with create_connected_server_and_client_session(create_server(chain)) as session:
            return (await session.call_tool("get_match_state", {"match_id": "M0001", "as_of": 500})).structuredContent
    assert asyncio.run(go())["source"] == "archive"

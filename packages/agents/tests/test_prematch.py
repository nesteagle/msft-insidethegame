import asyncio

import pytest

from footballfoundry.season import play_fixture
from footballfoundry.world import generate_fixtures
from footballfoundry_agents.prematch import starters
from footballfoundry_agents.season import _view
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.stores import memory_stores


class FakeSeason:
    """The parts of agents.season.Season the opening minutes use, over a hand-made book."""

    def __init__(self, book):
        self._book = book

    def book(self):
        return self._book

    def matchday_of(self):
        return {m["id"]: m["md"] for m in self._book["matches"]}

    def view(self, through):
        return _view(self._book, through)


def _row(mid, md, h, a, hs, as_, lines=()):
    return {"id": mid, "md": md, "ko": "2026-08-15T15:00:00", "h": h, "a": a, "hs": hs, "as": as_, "hxg": 1.0,
            "axg": 0.5, "hpos": 0.5, "apos": 0.5, "lines": list(lines), "stories": {}}


@pytest.fixture(scope="module")
def md2(league):
    """A matchday-2 fixture, played, with a season book in which its home side won on matchday 1 and one of its
    starters scored twice."""
    fixture = next(f for f in generate_fixtures(league) if f.matchday == 2)
    result = play_fixture(league, fixture)
    state = MatchState(fixture.match_id)
    for e in result.events[:10]:
        state.apply(e)
    h, a = fixture.home_club_id, fixture.away_club_id
    scorer = starters(state, h)[-1]
    book = {"matches": [_row("M9001", 1, h, "T99", 3, 0, [{"p": scorer, "c": h, "min": 90, "st": 1, "g": 2}]),
                        _row("M9002", 1, "T98", a, 1, 1),
                        _row(fixture.match_id, 2, h, a, 0, 0)]}
    return fixture, result, scorer, FakeSeason(book)


def opening(result, ref, season, upto=400):
    service = AnalystService(memory_stores(), TemplateWriter(ref), ref)
    service.season = season

    async def feed():
        for e in result.events[:upto]:
            await service.ingest(e)
        await service.drain()
        return await service.stores.insights.list(result.info.match_id)

    return service, asyncio.run(feed())


def test_kick_off_tells_the_season_so_far(md2, ref):
    fixture, result, _, season = md2
    _, items = opening(result, ref, season)
    kick = next(i for i in items if i.kind == "kick_off")
    assert f"{ref.club_name(fixture.home_club_id)} are 1st with 3 points from 1 (form W)" in kick.text
    assert "(form D)" in kick.text  # the away side drew
    # The preview already names the ground and the managers: the "did you know?" is something else.
    assert kick.trivia and kick.trivia.key not in ("ground", "biggest_ground", "manager")


def test_players_to_watch_after_the_kick_off(md2, ref):
    fixture, result, scorer, season = md2
    _, items = opening(result, ref, season)
    kinds = [i.kind for i in items]
    assert kinds.index("kick_off") < kinds.index("players_to_watch")
    watch = next(i for i in items if i.kind == "players_to_watch")
    assert watch.minute >= 2 and len(watch.player_ids) == 2
    assert watch.player_ids[0] == scorer and "top scorer this season, with 2 goals" in watch.text
    assert ref.player_name(watch.player_ids[1]) in watch.text  # the away side's pick, from the lore
    assert "'s's" not in watch.text and "s's " not in watch.text  # Rangers' top scorer, not Rangers's
    assert watch.trivia is None or watch.trivia.text not in watch.text


def test_opening_day_and_one_off_matches(league, ref, md2):
    _, _, _, season = md2
    first = generate_fixtures(league)[0]
    day1 = FakeSeason({"matches": [_row(first.match_id, 1, first.home_club_id, first.away_club_id, 0, 0)]})
    _, items = opening(play_fixture(league, first), ref, day1, upto=50)
    assert "opening day of the season" in next(i for i in items if i.kind == "kick_off").text
    # No season book (or a one-off match, not on the fixture list): the preview still comes, without a season line.
    _, items = opening(play_fixture(league, first), ref, None, upto=50)
    kick = next(i for i in items if i.kind == "kick_off")
    assert "points" not in kick.text and "opening day" not in kick.text

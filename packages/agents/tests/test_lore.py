import asyncio
from datetime import date

import pytest

from footballfoundry_agents.lore import TEXT, Lore, Storyteller, _age
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.stores import memory_stores
from footballfoundry_schema import Insight


@pytest.fixture(scope="module")
def lore(ref):
    return Lore(ref)


def insight(kind, player_ids=(), team_id=None):
    return Insight(insight_id=f"M0001-{kind}", match_id="M0001", kind=kind, seq=100, period=1, minute=10,
                   clock="10'", team_id=team_id, player_ids=list(player_ids), headline="h", text="t",
                   why_it_matters="w", created_at="2026-08-15T15:00:00Z")


def facts_by_key(lore, key):
    return [f for fs in lore.facts.values() for f in fs if f.key == key]


def test_every_player_and_club_has_facts(league, lore):
    assert all(lore.facts[p.player_id] for p in league.players) and all(lore.facts[c.club_id] for c in league.clubs)
    assert all(f.key in TEXT for fs in lore.facts.values() for f in fs)


def test_league_records_are_true(league, lore):
    players = {p.player_id: p for p in league.players}
    on = date(int(league.season[:4]), 8, 1)
    for f in facts_by_key(lore, "youngest_in_league"):
        assert players[f.subject_id].birth_date == max(p.birth_date for p in league.players)
        assert f.params["age"] == _age(players[f.subject_id].birth_date, on)
    for f in facts_by_key(lore, "among_fastest_in_league"):
        best = max(p.attributes.pace for p in league.players if p.position != "GK")
        assert players[f.subject_id].attributes.pace == best
        assert f.params["count"] == sum(p.attributes.pace == best for p in league.players if p.position != "GK")
    for f in facts_by_key(lore, "rare_nationality"):
        n = sum(p.nationality == players[f.subject_id].nationality for p in league.players)
        assert f.params["count"] == n
    biggest = max(league.clubs, key=lambda c: c.capacity)
    assert [f.subject_id for f in facts_by_key(lore, "biggest_ground")] == [biggest.club_id]
    oldest = min(league.clubs, key=lambda c: c.founded)
    assert all(f.subject_id == oldest.club_id for f in facts_by_key(lore, "oldest_club"))


def test_squad_records_are_true(league, lore):
    players = {p.player_id: p for p in league.players}
    for f in facts_by_key(lore, "fastest_in_squad"):
        me = players[f.subject_id]
        mates = [p for p in league.players if p.club_id == me.club_id and p.position != "GK" and p is not me]
        assert all(p.attributes.pace < me.attributes.pace for p in mates)


def test_a_goal_gets_a_fact_about_the_scorer_and_never_the_same_one_twice(league, lore):
    scorer = next(p for p in league.players if p.position == "ST")
    clubs = (scorer.club_id, next(c.club_id for c in league.clubs if c.club_id != scorer.club_id))
    used: set[str] = set()
    seen = []
    for _ in range(len(lore.facts[scorer.player_id]) + 2):
        t = asyncio.run(lore.pick(insight("goal", [scorer.player_id], scorer.club_id), clubs, used))
        assert t is not None and t.trivia_id not in used
        used.add(t.trivia_id)
        seen.append(t.subject_id)
    # The scorer's facts first, then the club's.
    n = len(lore.facts[scorer.player_id])
    assert seen[:n] == [scorer.player_id] * n and seen[n] == scorer.club_id


def test_half_time_is_about_the_clubs(league, lore):
    home, away = league.clubs[0].club_id, league.clubs[1].club_id
    t = asyncio.run(lore.pick(insight("half_time"), (home, away), set()))
    assert t.subject_id == home


def test_storyteller_retells_in_five_languages_and_caches(league, ref, scripted):
    client = scripted()
    teller = Storyteller(client)
    lore = Lore(ref, teller)
    p = league.players[0]
    t = asyncio.run(lore.pick(insight("goal", [p.player_id], p.club_id), (p.club_id, None), set()))
    assert t.source == "storyteller" and t.text == "They call him the Rocket."
    assert set(t.translations) == {"es", "fr", "pt", "de"}
    asyncio.run(lore.pick(insight("goal", [p.player_id], p.club_id), (p.club_id, None), set()))
    assert [name for name, _ in client.calls] == ["Story"]  # the second ask came from the cache
    assert "FACT:" in client.calls[0][1] and p.name in client.calls[0][1]


def test_storyteller_failure_falls_back_to_the_plain_fact(league, ref, scripted):
    lore = Lore(ref, Storyteller(scripted(fail={"Story"})))
    p = league.players[1]
    t = asyncio.run(lore.pick(insight("goal", [p.player_id], p.club_id), (p.club_id, None), set()))
    assert t.source == "data" and t.text == lore.facts[p.player_id][0].text


def test_published_insights_carry_trivia_without_repeats(match, ref):
    service = AnalystService(memory_stores(), TemplateWriter(ref), ref)

    async def feed():
        for e in match.events:
            await service.ingest(e)
        await service.drain()
        return await service.stores.insights.list("M0001", as_of=10_000)

    items = asyncio.run(feed())
    with_trivia = [i for i in items if i.trivia]
    assert len(with_trivia) == len(items)
    ids = [i.trivia.trivia_id for i in with_trivia]
    assert len(ids) == len(set(ids))
    goal = next(i for i in items if i.kind == "goal")
    assert goal.trivia.subject_id in goal.player_ids[:1] + [goal.team_id]


def test_full_time_still_gets_a_fact_when_the_clubs_are_used_up(league, lore):
    home, away = league.clubs[0].club_id, league.clubs[1].club_id
    used = {f.trivia_id for c in (home, away) for f in lore.facts[c]}  # every club fact already shown
    t = asyncio.run(lore.pick(insight("full_time"), (home, away), used))
    squad = {p.player_id for p in league.players if p.club_id in (home, away)}
    assert t is not None and t.subject_id in squad
    assert t.key != "profile"  # the most remarkable players come first

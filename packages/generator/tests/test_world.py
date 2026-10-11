from collections import Counter

from footballfoundry.world import generate_fixtures, generate_league, match_seed


def test_league_is_deterministic():
    assert generate_league(3).model_dump() == generate_league(3).model_dump()
    assert generate_league(3).model_dump() != generate_league(4).model_dump()


def test_league_shape(league):
    assert len(league.clubs) == 20
    assert len({c.name for c in league.clubs}) == 20
    ids = [p.player_id for p in league.players]
    assert len(ids) == len(set(ids)) == 20 * 26
    for club in league.clubs:
        squad = league.squad(club.club_id)
        assert sum(p.position == "GK" for p in squad) == 3
        assert len({p.shirt_number for p in squad}) == len(squad)


def test_stronger_clubs_have_better_players(league):
    by_strength = sorted(league.clubs, key=lambda c: c.strength)
    weakest, strongest = by_strength[0], by_strength[-1]
    avg = lambda club: sum(p.overall for p in league.squad(club.club_id)) / 26  # noqa: E731
    assert avg(strongest) > avg(weakest)


def test_fixtures_double_round_robin(league):
    fixtures = generate_fixtures(league)
    assert len(fixtures) == 380
    assert len({f.match_id for f in fixtures}) == 380
    pairs = Counter((f.home_club_id, f.away_club_id) for f in fixtures)
    assert all(v == 1 for v in pairs.values()) and len(pairs) == 380
    homes = Counter(f.home_club_id for f in fixtures)
    assert set(homes.values()) == {19}
    for md in range(1, 39):
        teams = [t for f in fixtures if f.matchday == md for t in (f.home_club_id, f.away_club_id)]
        assert len(teams) == len(set(teams)) == 20


def test_match_seed_is_stable():
    assert match_seed(42, "M0001") == match_seed(42, "M0001")
    assert match_seed(42, "M0001") != match_seed(42, "M0002")

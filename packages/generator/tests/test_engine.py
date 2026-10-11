"""Structural invariants every generated match must satisfy."""

from footballfoundry.engine import TacticChange, simulate_match
from footballfoundry.season import simulate_season
from footballfoundry.stats import match_stats


def test_same_seed_same_match(league):
    a = simulate_match(league, "T01", "T02", seed=123)
    b = simulate_match(league, "T01", "T02", seed=123)
    c = simulate_match(league, "T01", "T02", seed=124)
    assert [e.model_dump() for e in a.events] == [e.model_dump() for e in b.events]
    assert [e.model_dump() for e in a.events] != [e.model_dump() for e in c.events]


def test_parallel_season_matches_serial(league):
    serial = simulate_season(league, limit=8, workers=1)
    parallel = simulate_season(league, limit=8, workers=2)
    assert [r.info for r in serial] == [r.info for r in parallel]
    assert [r.events for r in serial] == [r.events for r in parallel]


def test_sequence_and_clock(results):
    for r in results:
        assert [e.seq for e in r.events] == list(range(1, len(r.events) + 1))
        for period in (1, 2):
            times = [e.elapsed_s for e in r.events if e.period == period]
            assert times == sorted(times)
            assert times[-1] >= 45 * 60
        assert r.events[0].type == "period_start" and r.events[-1].type == "period_end"
        assert len(r.info.added_time) == 2


def test_score_matches_goal_events(results):
    for r in results:
        goals = [e for e in r.events if e.type == "shot" and e.outcome == "goal"]
        assert sum(e.team_id == r.info.home_club_id for e in goals) == r.info.home_score
        assert sum(e.team_id == r.info.away_club_id for e in goals) == r.info.away_score
        assert (r.events[-1].home_score, r.events[-1].away_score) == (r.info.home_score, r.info.away_score)


def test_players_are_on_the_pitch(results):
    """Every actor must be on the pitch for their own team when the event happens."""
    for r in results:
        on_pitch = {e.player_id: e.club_id for e in r.info.lineups if e.starter}
        subs = {r.info.home_club_id: 0, r.info.away_club_id: 0}
        for e in r.events:
            if e.player_id is not None:
                assert on_pitch.get(e.player_id) == e.team_id, (r.info.match_id, e)
            if e.type == "substitution":
                del on_pitch[e.player_id]
                on_pitch[e.related_player_id] = e.team_id
                subs[e.team_id] += 1
            if e.type == "card" and e.outcome in ("red", "second_yellow"):
                del on_pitch[e.player_id]
        assert max(subs.values()) <= 5


def test_coordinates_in_range(results):
    for r in results:
        for e in r.events:
            for v in (e.x, e.y, e.end_x, e.end_y):
                assert v is None or 0 <= v <= 100
            if e.type == "shot":
                assert 0 < e.xg < 1


def test_tactic_change_hook_shifts_behaviour(league):
    """A big press increase from kick-off should make the opponent pass under pressure more."""

    def pressured_share(changes):
        total = pressured = 0
        for seed in range(6):
            r = simulate_match(league, "T05", "T06", seed=seed, tactic_changes=changes)
            for e in r.events:
                if e.type == "pass" and e.team_id == "T06":
                    total += 1
                    pressured += e.under_pressure
        return pressured / total

    baseline = pressured_share([])
    pressing = pressured_share([TacticChange(minute=0, side=0, changes={"press_intensity": 1.0})])
    assert pressing > baseline + 0.03


def test_match_stats_shape(results):
    stats = match_stats(results[0])
    assert abs(stats["home"]["possession"] + stats["away"]["possession"] - 1) < 1e-6


def test_pressure_precedes_the_action_it_presses(results):
    for r in results:
        pressures = 0
        for prev, e in zip(r.events, r.events[1:]):
            if prev.type == "pressure":
                pressures += 1
                if e.team_id == prev.team_id:  # the press wins a challenge on the carrier
                    assert e.type in ("tackle", "foul") and e.related_player_id == prev.related_player_id
                else:  # or the carrier plays on under it
                    assert e.player_id == prev.related_player_id and e.under_pressure
                    assert e.type in ("pass", "carry", "shot", "clearance")
        assert pressures > 100


def test_possession_change_opens_each_spell_by_the_other_side(results):
    for r in results:
        changes = [e for e in r.events if e.type == "possession_change"]
        assert 120 < len(changes) < 300
        for e in changes:
            assert e.possession_team_id == e.team_id != e.qualifiers["previous_team_id"]
            assert e.qualifiers["previous_duration_s"] >= 0 and e.qualifiers["previous_passes"] >= 0

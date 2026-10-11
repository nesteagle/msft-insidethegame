"""Simulate a whole season of fixtures, optionally in parallel."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

from footballfoundry.engine import MatchResult, simulate_match
from footballfoundry_schema import Fixture, League
from footballfoundry.world import generate_fixtures, match_seed

_worker_league: League | None = None


def _init_worker(league: League) -> None:
    global _worker_league
    _worker_league = league


def _run_fixture(fixture: Fixture) -> MatchResult:
    return play_fixture(_worker_league, fixture)


def play_fixture(league: League, fixture: Fixture, **engine_kwargs) -> MatchResult:
    """`engine_kwargs`: scenario changes (tactic_changes, role_changes, formation_changes)."""
    return simulate_match(
        league,
        fixture.home_club_id,
        fixture.away_club_id,
        match_id=fixture.match_id,
        seed=match_seed(league.seed, fixture.match_id),
        matchday=fixture.matchday,
        kickoff=fixture.kickoff,
        **engine_kwargs,
    )


def simulate_season(league: League, *, limit: int | None = None, workers: int | None = 1) -> list[MatchResult]:
    """Play the league's fixtures in order. Results are identical for any worker count."""
    fixtures = generate_fixtures(league)[:limit]
    workers = workers or os.cpu_count() or 1
    if workers <= 1 or len(fixtures) < 8:
        return [play_fixture(league, f) for f in fixtures]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(league,)) as pool:
        return list(pool.map(_run_fixture, fixtures, chunksize=4))

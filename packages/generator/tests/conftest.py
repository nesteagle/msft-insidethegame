import pytest

from footballfoundry.season import simulate_season
from footballfoundry.world import generate_league


@pytest.fixture(scope="session")
def league():
    return generate_league(seed=7)


@pytest.fixture(scope="session")
def results(league):
    return simulate_season(league, limit=20, workers=1)

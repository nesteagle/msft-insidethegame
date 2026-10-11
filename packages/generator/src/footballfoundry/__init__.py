"""footballfoundry: synthetic, football-realistic match data generator."""

from footballfoundry.engine import MatchEngine, MatchResult
from footballfoundry_schema import SCHEMA_VERSION, Club, Event, League, MatchInfo, Player
from footballfoundry.world import generate_fixtures, generate_league

__all__ = [
    "SCHEMA_VERSION",
    "Club",
    "Event",
    "League",
    "MatchEngine",
    "MatchInfo",
    "MatchResult",
    "Player",
    "generate_fixtures",
    "generate_league",
]

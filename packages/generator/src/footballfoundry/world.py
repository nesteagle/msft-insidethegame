"""World generator: a fictional league of clubs, squads and fixtures.

Everything here is invented. Club and stadium names come from curated lists of
fictional towns; player names come from Faker across many locales so squads
look international. Nothing references a real club, player or competition.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta

from faker import Faker

from footballfoundry.formations import COMPATIBLE, FORMATIONS
from footballfoundry.pitch import clamp
from footballfoundry_schema import Attributes, Club, Fixture, League, Player, TeamStyle

TOWNS = [
    "Ashcombe", "Blackmere", "Corrandale", "Dunmarsh", "Eldermere", "Fenhollow",
    "Greystow", "Halbury", "Ironvale", "Kestonbridge", "Larkhaven", "Marrowgate",
    "Northwick Vale", "Oakenshaw", "Pellingford", "Quarrington", "Ravensholm",
    "Saltmarsh", "Thornbeck", "Upperlee", "Westerhay", "Wyndford", "Yarrowby", "Calderbrook",
]
SUFFIXES = ["United", "City", "Town", "Rovers", "Athletic", "Albion", "Wanderers", "County", "Rangers", "Harriers"]
STADIUM_SUFFIXES = ["Park", "Road", "Lane", "Ground", "Stadium", "Meadow", "Arena"]
COLOURS = [
    "red", "blue", "sky blue", "claret", "white", "black", "amber", "green",
    "navy", "orange", "maroon", "yellow", "purple", "teal", "silver",
]

# (Faker locale, nationality code, weight). Weighted toward a typical top-flight mix.
LOCALES = [
    ("en_GB", "ENG", 34), ("en_IE", "IRL", 3), ("es_ES", "ESP", 7), ("es_MX", "MEX", 2),
    ("fr_FR", "FRA", 8), ("pt_BR", "BRA", 7), ("pt_PT", "POR", 5), ("de_DE", "GER", 4),
    ("it_IT", "ITA", 3), ("nl_NL", "NED", 5), ("da_DK", "DEN", 3), ("no_NO", "NOR", 3),
    ("sv_SE", "SWE", 3), ("pl_PL", "POL", 2), ("hr_HR", "CRO", 2), ("cs_CZ", "CZE", 2),
    ("tr_TR", "TUR", 2), ("en_US", "USA", 2), ("tw_GH", "GHA", 3), ("yo_NG", "NGA", 3),
]

# 26-player squad template by natural position.
SQUAD_TEMPLATE = ["GK"] * 3 + ["RB"] * 2 + ["CB"] * 4 + ["LB"] * 2 + ["DM"] * 2 + ["CM"] * 4 + ["AM"] * 2 + ["RW"] * 2 + ["LW"] * 2 + ["ST"] * 3

# Attribute offsets from a player's base quality, by natural position.
PROFILES: dict[str, dict[str, int]] = {
    "GK": dict(pace=-15, passing=-12, dribbling=-30, finishing=-45, heading=-20, tackling=-35, positioning=5, stamina=-5, composure=3, goalkeeping=6),
    "CB": dict(pace=-6, passing=-6, dribbling=-15, finishing=-25, heading=10, tackling=10, positioning=8, stamina=0, composure=0, goalkeeping=-50),
    "FB": dict(pace=6, passing=-2, dribbling=-3, finishing=-20, heading=-4, tackling=6, positioning=2, stamina=8, composure=-2, goalkeeping=-50),
    "DM": dict(pace=-4, passing=4, dribbling=-4, finishing=-14, heading=0, tackling=8, positioning=6, stamina=5, composure=2, goalkeeping=-50),
    "CM": dict(pace=-2, passing=8, dribbling=2, finishing=-6, heading=-4, tackling=0, positioning=2, stamina=6, composure=3, goalkeeping=-50),
    "AM": dict(pace=0, passing=8, dribbling=8, finishing=2, heading=-8, tackling=-14, positioning=-2, stamina=0, composure=5, goalkeeping=-50),
    "W": dict(pace=10, passing=2, dribbling=10, finishing=0, heading=-10, tackling=-16, positioning=-4, stamina=3, composure=0, goalkeeping=-50),
    "ST": dict(pace=3, passing=-4, dribbling=2, finishing=10, heading=6, tackling=-20, positioning=-4, stamina=0, composure=4, goalkeeping=-50),
}
PROFILE_OF = {"GK": "GK", "CB": "CB", "RB": "FB", "LB": "FB", "RWB": "FB", "LWB": "FB", "DM": "DM",
              "CM": "CM", "AM": "AM", "RW": "W", "LW": "W", "RM": "W", "LM": "W", "ST": "ST"}

# How the overall rating is weighted for each profile.
OVERALL_WEIGHTS: dict[str, dict[str, float]] = {
    "GK": dict(goalkeeping=0.7, positioning=0.15, composure=0.15),
    "CB": dict(tackling=0.3, positioning=0.3, heading=0.2, passing=0.1, pace=0.1),
    "FB": dict(pace=0.25, tackling=0.25, positioning=0.15, passing=0.15, stamina=0.1, dribbling=0.1),
    "DM": dict(tackling=0.3, passing=0.3, positioning=0.25, stamina=0.15),
    "CM": dict(passing=0.4, dribbling=0.15, stamina=0.15, composure=0.15, tackling=0.15),
    "AM": dict(passing=0.35, dribbling=0.3, finishing=0.15, composure=0.2),
    "W": dict(pace=0.3, dribbling=0.35, passing=0.15, finishing=0.2),
    "ST": dict(finishing=0.45, composure=0.15, heading=0.15, pace=0.15, dribbling=0.1),
}

SEASON_START = date(2026, 8, 1)


def role_rating(player: Player, role: str) -> float:
    """How good a player is in a given formation role (natural position gets a bonus)."""
    weights = OVERALL_WEIGHTS[PROFILE_OF[role]]
    a = player.attributes
    score = sum(getattr(a, k) * w for k, w in weights.items())
    prefs = COMPATIBLE[role]
    if player.position in prefs:
        score -= 3 * prefs.index(player.position)
    else:
        score -= 15
    return score


class _NameFactory:
    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._fakers: dict[str, Faker] = {}
        self._used: set[str] = set()

    def _faker(self, locale: str) -> Faker:
        if locale not in self._fakers:
            fake = Faker(locale)
            fake.seed_instance(self._rng.randrange(2**32))
            self._fakers[locale] = fake
        return self._fakers[locale]

    def player(self) -> tuple[str, str, str]:
        locale, nat, _ = self._rng.choices(LOCALES, weights=[w for *_, w in LOCALES])[0]
        fake = self._faker(locale)
        for _ in range(20):
            first, last = fake.first_name_male(), fake.last_name_male()
            name = f"{first} {last}"
            if name not in self._used:
                break
        self._used.add(name)
        return name, f"{first[0]}. {last}", nat

    def person(self) -> str:
        fake = self._faker("en_GB")
        return f"{fake.first_name()} {fake.last_name()}"


def _style(rng: random.Random, strength: int) -> TeamStyle:
    rel = (strength - 72) / 100
    possession = clamp(0.5 + rel * 2.5 + rng.gauss(0, 0.12), 0.05, 0.95)
    press = clamp(0.45 + rel * 2.0 + rng.gauss(0, 0.18), 0.05, 0.95)
    return TeamStyle(
        possession=round(possession, 2),
        directness=round(clamp(1 - possession + rng.gauss(0, 0.12), 0.05, 0.95), 2),
        press_intensity=round(press, 2),
        defensive_line=round(clamp(0.3 + 0.5 * press + rng.gauss(0, 0.1), 0.05, 0.95), 2),
        width=round(rng.uniform(0.2, 0.8), 2),
        tempo=round(rng.uniform(0.3, 0.8), 2),
    )


def _formation(rng: random.Random, style: TeamStyle) -> str:
    if style.possession > 0.55:
        options, weights = ["4-3-3", "4-2-3-1", "3-4-3"], [5, 4, 1]
    else:
        options, weights = ["4-4-2", "4-2-3-1", "3-5-2", "4-3-3"], [4, 3, 2, 1]
    return rng.choices(options, weights=weights)[0]


def _attributes(rng: random.Random, position: str, quality: float) -> Attributes:
    profile = PROFILES[PROFILE_OF[position]]
    values = {k: int(clamp(round(quality + off + rng.gauss(0, 4)), 1, 99)) for k, off in profile.items()}
    values["discipline"] = int(clamp(round(rng.gauss(65, 12)), 20, 99))
    return Attributes(**values)


def _overall(position: str, attrs: Attributes) -> int:
    weights = OVERALL_WEIGHTS[PROFILE_OF[position]]
    return int(round(sum(getattr(attrs, k) * w for k, w in weights.items())))


def _shirt_numbers(rng: random.Random, positions: list[str]) -> list[int]:
    preferred = {"GK": [1, 13, 31], "RB": [2, 22], "LB": [3, 15], "CB": [4, 5, 6, 16],
                 "DM": [6, 14], "CM": [8, 18, 23], "AM": [10, 20], "RW": [7, 17], "LW": [11, 19], "ST": [9, 21, 29]}
    used: set[int] = set()
    numbers = []
    for pos in positions:
        n = next((n for n in preferred[pos] if n not in used), None)
        while n is None or n in used:
            n = rng.randint(12, 45)
        used.add(n)
        numbers.append(n)
    return numbers


def generate_league(seed: int = 42, n_clubs: int = 20, season: str = "2026-27") -> League:
    """Build a deterministic fictional league for the given seed."""
    if n_clubs > len(TOWNS):
        raise ValueError(f"at most {len(TOWNS)} clubs supported")
    rng = random.Random(seed)
    names = _NameFactory(random.Random(rng.randrange(2**32)))

    towns = rng.sample(TOWNS, n_clubs)
    strengths = sorted((int(clamp(round(rng.gauss(72, 6)), 60, 88)) for _ in range(n_clubs)), reverse=True)
    clubs: list[Club] = []
    players: list[Player] = []

    for i, (town, strength) in enumerate(zip(towns, strengths), start=1):
        club_id = f"T{i:02d}"
        style = _style(rng, strength)
        suffix = rng.choice(SUFFIXES)
        clubs.append(Club(
            club_id=club_id,
            name=f"{town} {suffix}",
            short_name=town.split()[0],
            city=town,
            stadium=f"{rng.choice(TOWNS + ['Victoria', 'Albert', 'Riverside', 'Kingsway'])} {rng.choice(STADIUM_SUFFIXES)}",
            capacity=int(round(rng.uniform(18_000, 62_000) * (0.6 + strength / 180), -2)),
            founded=rng.randint(1865, 1920),
            colors=rng.sample(COLOURS, 2),
            manager=names.person(),
            formation=_formation(rng, style),
            strength=strength,
            style=style,
        ))

        numbers = _shirt_numbers(rng, SQUAD_TEMPLATE)
        for j, (position, number) in enumerate(zip(SQUAD_TEMPLATE, numbers), start=1):
            quality = strength + rng.gauss(-1.5, 5)
            attrs = _attributes(rng, position, quality)
            name, short, nat = names.player()
            age_days = int(rng.triangular(18, 35, 26) * 365.25)
            players.append(Player(
                player_id=f"P{i:02d}{j:02d}",
                club_id=club_id,
                name=name,
                short_name=short,
                nationality=nat,
                birth_date=SEASON_START - timedelta(days=age_days),
                position=position,
                foot=rng.choices(["right", "left", "both"], weights=[70, 25, 5])[0]
                if position not in ("LB", "LW") else rng.choices(["left", "right", "both"], weights=[70, 25, 5])[0],
                shirt_number=number,
                overall=_overall(position, attrs),
                attributes=attrs,
            ))

    return League(
        league_id="FFL",
        name="FootballFoundry League",
        season=season,
        seed=seed,
        clubs=clubs,
        players=players,
    )


def match_seed(league_seed: int, match_id: str) -> int:
    """Stable per-match seed (hashlib, not hash(), which is salted per process).

    48 bits so it fits Parquet/KQL int64 columns and JavaScript numbers exactly.
    """
    digest = hashlib.sha256(f"{league_seed}:{match_id}".encode()).digest()
    return int.from_bytes(digest[:6], "big")


def generate_fixtures(league: League, seed: int | None = None) -> list[Fixture]:
    """Double round-robin (circle method), one matchday per week from mid-August."""
    rng = random.Random(league.seed if seed is None else seed)
    ids = [c.club_id for c in league.clubs]
    rng.shuffle(ids)
    if len(ids) % 2:
        ids.append("BYE")
    n = len(ids)
    rounds: list[list[tuple[str, str]]] = []
    rotation = ids[:]
    for r in range(n - 1):
        pairs = []
        for k in range(n // 2):
            a, b = rotation[k], rotation[n - 1 - k]
            pairs.append((a, b) if (r + k) % 2 == 0 else (b, a))
        rounds.append(pairs)
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    rounds += [[(b, a) for a, b in pairs] for pairs in rounds]

    first_saturday = datetime(2026, 8, 15, 15, 0)
    kickoff_slots = [(0, 12, 30), (0, 15, 0), (0, 15, 0), (0, 15, 0), (0, 17, 30), (1, 14, 0), (1, 16, 30), (2, 20, 0)]
    fixtures: list[Fixture] = []
    match_no = 0
    for md, pairs in enumerate(rounds, start=1):
        base = first_saturday + timedelta(weeks=md - 1)
        for home, away in pairs:
            if "BYE" in (home, away):
                continue
            match_no += 1
            day, hour, minute = rng.choice(kickoff_slots)
            kickoff = (base + timedelta(days=day)).replace(hour=hour, minute=minute)
            fixtures.append(Fixture(
                match_id=f"M{match_no:04d}",
                matchday=md,
                kickoff=kickoff,
                home_club_id=home,
                away_club_id=away,
            ))
    return fixtures

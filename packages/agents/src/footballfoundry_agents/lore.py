"""Lore for casual fans: "Did you know?" facts about the players and clubs in a moment.

Every fact is computed from the league data (the youngest player in the squad, the fastest in the league, the
oldest club, the biggest ground...), so it's true in this fictional world and works offline. Each one carries a
`key` and `params`, so the web app writes it in the viewer's language.

With Microsoft Foundry configured, the storyteller agent retells a fact as a short story (a nickname, a fan
tradition, a childhood detail: invented colour for an invented league) in English and the web app's other
languages. It may add colour but never numbers or match facts, and any failure falls back to the plain fact.

    insight ─► Lore.pick (subject: the player in the moment, else the clubs; never a fact twice in a match)
                 └─► Storyteller.tell (optional, cached per fact)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from agent_framework import Agent
from pydantic import BaseModel, Field

from footballfoundry_schema import Club, Player, Trivia

from footballfoundry_agents.reference import Reference

log = logging.getLogger(__name__)

LANGUAGES = ("es", "fr", "pt", "de")  # the web app's languages besides English
PLAYER_MOMENTS = ("goal", "penalty", "big_chance", "red_card", "tactical_change", "spotlight", "players_to_watch")

POSITION = {"GK": "goalkeeper", "RB": "right-back", "CB": "centre-back", "LB": "left-back",
            "DM": "defensive midfielder", "CM": "central midfielder", "AM": "attacking midfielder",
            "RW": "right winger", "LW": "left winger", "ST": "striker"}
COUNTRY = {"ENG": "England", "IRL": "Ireland", "ESP": "Spain", "MEX": "Mexico", "FRA": "France", "BRA": "Brazil",
           "POR": "Portugal", "GER": "Germany", "ITA": "Italy", "NED": "the Netherlands", "DEN": "Denmark",
           "NOR": "Norway", "SWE": "Sweden", "POL": "Poland", "CRO": "Croatia", "CZE": "the Czech Republic",
           "TUR": "Turkey", "USA": "the United States", "GHA": "Ghana", "NGA": "Nigeria"}

# English wording of each fact; the web app has the same keys in every language (i18n.ts, `trivia`).
TEXT = {
    "fastest_in_league": "{player} is the fastest player in the whole league.",
    "top_finisher_in_league": "{player} is the deadliest finisher in the league, by the scouts' ratings.",
    "best_keeper_in_league": "{player} is rated the best goalkeeper in the league.",
    "best_in_air_in_league": "Nobody in the league is better in the air than {player}.",
    "among_fastest_in_league": "{player} is one of the {count} fastest players in the league.",
    "among_top_finishers_in_league": "{player} is one of the {count} best finishers in the league.",
    "among_best_in_air_in_league": "{player} is one of the {count} best headers of the ball in the league.",
    "youngest_in_league": "{player} is the youngest player in the league, aged just {age}.",
    "oldest_in_league": "{player} is the oldest player in the league, still going at {age}.",
    "fastest_in_squad": "{player} is the quickest player at {club}.",
    "top_finisher_in_squad": "{player} is the best finisher at {club}.",
    "youngest_in_squad": "{player} is the youngest player at {club}, aged {age}.",
    "veteran": "At {age}, {player} is one of the most experienced players on the pitch.",
    "rare_nationality": "{player} is one of only {count} players from {country} in the league.",
    "profile": "{player} is a {age}-year-old {foot}-footed {position} from {country}.",
    "oldest_club": "{club} are the oldest club in the league, founded in {year}.",
    "founded": "{club} were founded in {year}, {years} years ago.",
    "biggest_ground": "{stadium}, home of {club}, is the biggest ground in the league: {capacity} seats.",
    "ground": "{club} play at {stadium}, which holds {capacity} fans.",
    "press_kings": "{club} press higher and harder than any other team in the league.",
    "possession_kings": "{club} love the ball: no team in the league keeps it more.",
    "most_direct": "{club} are the most direct team in the league: they go forward fast.",
    "manager": "{manager} manages {club}, usually in a {formation}.",
}


class Moment(Protocol):
    """What a fun fact is picked for: a brief (while the analysts write) or a published insight."""

    kind: str
    player_ids: list[str]
    team_id: str | None


def _age(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def _tied(items: list[Any], key) -> list[Any]:
    """Every item sharing the best value."""
    if not items:
        return []
    best = max(key(i) for i in items)
    return [i for i in items if key(i) == best]


def _top(items: list[Any], key) -> Any:
    """The single best item, or None when several share the top (a "the fastest" fact must be unambiguous)."""
    tied = _tied(items, key)
    return tied[0] if len(tied) == 1 else None


class Lore:
    """All the facts about a league, computed once, and the pick for each insight."""

    def __init__(self, ref: Reference, storyteller: Storyteller | None = None) -> None:
        self.ref = ref
        self.storyteller = storyteller
        self.facts: dict[str, list[Trivia]] = {}  # subject id -> facts, most interesting first
        league = ref.league
        if league is None:
            return
        self.season_start = date(int(league.season[:4]), 8, 1)
        self._players(league.players, league.clubs)
        self._clubs(league.clubs)

    def _fact(self, key: str, subject_id: str, **params: Any) -> Trivia:
        shown = {k: (f"{v:,}" if isinstance(v, int) and v >= 10_000 else v) for k, v in params.items()}
        return Trivia(trivia_id=f"{key}:{subject_id}", key=key, subject_id=subject_id, params=params,
                      text=TEXT[key].format(**shown))

    def _players(self, players: list[Player], clubs: list[Club]) -> None:
        age = {p.player_id: _age(p.birth_date, self.season_start) for p in players}
        outfield = [p for p in players if p.position != "GK"]
        nat_count: dict[str, int] = {}
        for p in players:
            nat_count[p.nationality] = nat_count.get(p.nationality, 0) + 1
        league_bests = {
            "best_keeper_in_league": _top([p for p in players if p.position == "GK"],
                                          lambda p: p.attributes.goalkeeping),
            "youngest_in_league": _top(players, lambda p: p.birth_date),
            "oldest_in_league": _top(players, lambda p: -p.birth_date.toordinal()),
        }
        # Ratings are whole numbers, so the top is often shared: "the fastest" alone, "one of the 3 fastest" if tied.
        shared: dict[str, list[Player]] = {}
        for key, attr in (("fastest_in_league", "pace"), ("top_finisher_in_league", "finishing"),
                          ("best_in_air_in_league", "heading")):
            tied = _tied(outfield, lambda p, a=attr: getattr(p.attributes, a))
            if len(tied) == 1:
                league_bests[key] = tied[0]
            elif len(tied) <= 3:
                shared[f"among_{key}".replace("top_finisher", "top_finishers")] = tied
        squad_bests: dict[str, dict[str, Player | None]] = {}
        for c in clubs:
            squad = [p for p in players if p.club_id == c.club_id]
            field = [p for p in squad if p.position != "GK"]
            squad_bests[c.club_id] = {
                "fastest_in_squad": _top(field, lambda p: p.attributes.pace),
                "top_finisher_in_squad": _top(field, lambda p: p.attributes.finishing),
                "youngest_in_squad": _top(squad, lambda p: p.birth_date),
            }
        for p in players:
            club = self.ref.club_name(p.club_id)
            base = {"player": p.name, "club": club, "age": age[p.player_id]}
            facts = [self._fact(k, p.player_id, **base) for k, best in league_bests.items() if best is p]
            facts += [self._fact(k, p.player_id, **base, count=len(tied)) for k, tied in shared.items()
                      if p in tied]
            facts += [self._fact(k, p.player_id, **base) for k, best in squad_bests[p.club_id].items()
                      if best is p and not any(f.key == k.replace("squad", "league") for f in facts)]
            if age[p.player_id] >= 33 and not any(f.key == "oldest_in_league" for f in facts):
                facts.append(self._fact("veteran", p.player_id, **base))
            if p.nationality != "ENG" and nat_count[p.nationality] <= 12:
                facts.append(self._fact("rare_nationality", p.player_id, **base, count=nat_count[p.nationality],
                                        country=COUNTRY.get(p.nationality, p.nationality),
                                        nationality=p.nationality))
            facts.append(self._fact("profile", p.player_id, **base, foot=p.foot if p.foot != "both" else "two",
                                    position=POSITION[p.position], position_code=p.position,
                                    country=COUNTRY.get(p.nationality, p.nationality), nationality=p.nationality))
            self.facts[p.player_id] = facts

    def _clubs(self, clubs: list[Club]) -> None:
        oldest = _top(clubs, lambda c: -c.founded)
        biggest = _top(clubs, lambda c: c.capacity)
        styles = {"press_kings": _top(clubs, lambda c: c.style.press_intensity),
                  "possession_kings": _top(clubs, lambda c: c.style.possession),
                  "most_direct": _top(clubs, lambda c: c.style.directness)}
        for c in clubs:
            base = {"club": c.name}
            facts = [self._fact(k, c.club_id, **base) for k, best in styles.items() if best is c]
            if c is oldest:
                facts.insert(0, self._fact("oldest_club", c.club_id, **base, year=c.founded))
            ground = dict(stadium=c.stadium, capacity=c.capacity)
            facts.append(self._fact("biggest_ground" if c is biggest else "ground", c.club_id, **base, **ground))
            if c is not oldest:
                facts.append(self._fact("founded", c.club_id, **base, year=c.founded,
                                        years=self.season_start.year - c.founded))
            facts.append(self._fact("manager", c.club_id, **base, manager=c.manager, formation=c.formation))
            self.facts[c.club_id] = facts

    # -- picking -------------------------------------------------------------------------------------------------

    def subjects(self, insight: Moment, clubs: tuple[str | None, str | None]) -> list[str]:
        """Who the fun fact can be about, best first: the player in the moment, then their club, then the other.
        A club has only a few facts, which a match's summaries and momentum swings use up by full time, so after
        the clubs come the two squads' players, the most remarkable first."""
        player = insight.player_ids[:1] if insight.kind in PLAYER_MOMENTS else []
        team = [insight.team_id] if insight.team_id else []
        first = team + player if insight.kind == "tactical_change" else player + team  # a change is the manager's
        teams = [c for c in clubs if c]
        return [s for s in dict.fromkeys(first + teams + self._squads(teams)) if s in self.facts]

    def _squads(self, club_ids: list[str]) -> list[str]:
        """The clubs' players, those with a league record first, then a squad record, and so on."""
        league = self.ref.league
        if league is None:
            return []
        rank = {key: i for i, key in enumerate(TEXT)}  # TEXT lists the facts from most to least remarkable
        players = [p.player_id for p in league.players if p.club_id in club_ids and self.facts.get(p.player_id)]
        return sorted(players, key=lambda pid: rank[self.facts[pid][0].key])

    async def pick(self, insight: Moment, clubs: tuple[str | None, str | None], used: set[str]) -> Trivia | None:
        """A fact nobody has seen this match, told by the storyteller when there is one."""
        fact = next((f for s in self.subjects(insight, clubs) for f in self.facts[s] if f.trivia_id not in used), None)
        if fact is None or self.storyteller is None:
            return fact
        return await self.storyteller.tell(fact, self.profile(fact.subject_id))

    def about(self, subject_id: str) -> dict[str, Any]:
        """Everything known about a player or club, for the chat assistant."""
        return {"profile": self.profile(subject_id), "facts": [f.text for f in self.facts.get(subject_id, [])]}

    def profile(self, subject_id: str) -> str:
        """The plain facts the storyteller may build on."""
        p, c = self.ref.player(subject_id), self.ref.club(subject_id)
        if p:
            club = self.ref.club(p.club_id)
            return (f"{p.name}, {_age(p.birth_date, self.season_start)}, {POSITION[p.position]}, "
                    f"{COUNTRY.get(p.nationality, p.nationality)}, {p.foot}-footed, shirt {p.shirt_number}, plays "
                    f"for {club.name if club else p.club_id}" + (f" of {club.city}" if club else ""))
        if c:
            return (f"{c.name} of {c.city}, founded {c.founded}, play at {c.stadium} ({c.capacity:,}), colours "
                    f"{' and '.join(c.colors)}, manager {c.manager}, {c.formation}")
        return subject_id


# -- the storyteller agent ---------------------------------------------------------------------------------------

STORYTELLER = f"""You write the "Did you know?" corner of a live football broadcast, for casual fans. The league,
its clubs and its players are fictional. You get one true fact and the subject's profile.

Retell the fact as a warm, fun story of one or two short sentences. Keep the fact and every number exactly as
given. You may add harmless colour that fits the profile: a nickname, a fan chant or tradition, a childhood detail,
a dressing-room habit. Never add other numbers, statistics, results, transfers or anything about the match being
played, and never mention real clubs, players, people or competitions.

Write it in English (text) and in Spanish (es), French (fr), Portuguese (pt) and German (de); keep names as they
are. Languages: {", ".join(LANGUAGES)}."""


class Story(BaseModel):
    text: str = Field(description="English, one or two short sentences")
    es: str
    fr: str
    pt: str
    de: str


@dataclass
class _Story:
    text: str
    translations: dict[str, str]


class Storyteller:
    """Foundry-backed retelling of facts, cached per fact for the life of the service."""

    def __init__(self, client: Any, timeout_s: float = 25.0) -> None:
        self.agent = Agent(client, STORYTELLER, name="storyteller")
        self.timeout_s = timeout_s
        self._cache: dict[str, _Story] = {}
        self._in_flight: dict[str, asyncio.Task] = {}

    async def tell(self, fact: Trivia, profile: str) -> Trivia:
        story = self._cache.get(fact.trivia_id)
        if story is None:
            task = self._in_flight.get(fact.trivia_id)
            if task is None:
                task = asyncio.create_task(self._write(fact, profile))
                self._in_flight[fact.trivia_id] = task
            try:
                story = await asyncio.shield(task)
            except Exception:  # noqa: BLE001 - the plain fact is still worth showing
                log.exception("storyteller failed for %s; showing the plain fact", fact.trivia_id)
                return fact
            finally:
                if task.done():
                    self._in_flight.pop(fact.trivia_id, None)
            self._cache[fact.trivia_id] = story
        return fact.model_copy(update={"text": story.text, "translations": story.translations,
                                       "source": "storyteller"})

    async def _write(self, fact: Trivia, profile: str) -> _Story:
        prompt = f"FACT: {fact.text}\nPROFILE: {profile}"
        response = await asyncio.wait_for(self.agent.run(prompt, options={"response_format": Story}),
                                          timeout=self.timeout_s)
        s = response.value
        if s is None:
            raise ValueError("storyteller returned no story")
        return _Story(s.text, {lang: getattr(s, lang) for lang in LANGUAGES if getattr(s, lang)})

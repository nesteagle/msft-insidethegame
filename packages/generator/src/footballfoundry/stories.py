"""Find the matches that tell a story: comebacks, late winners, red cards, wonder goals...

Every fixture is deterministic for a seed, so a match found here can be streamed again exactly:
    footballfoundry find --story comeback --story late-winner
    footballfoundry stream --match-id M0123 --speed 30
"""

from __future__ import annotations

from dataclasses import dataclass, field

from footballfoundry.engine import MatchResult
from footballfoundry_schema import League

STORIES = {
    "comeback": "a team came from behind to win",
    "fightback": "a team came from behind to draw",
    "late-winner": "the winning goal came at 85' or later",
    "late-equaliser": "an equaliser at 85' or later",
    "stoppage-time": "a goal in added time",
    "lead-swing": "the lead changed hands",
    "red-card": "someone was sent off",
    "penalty": "a penalty was taken",
    "penalty-miss": "a penalty was missed or saved",
    "wonder-goal": "a goal from a chance under 0.05 xG",
    "great-save": "a save rated great (on-target xG 0.4+)",
    "woodwork": "hit the post or bar twice or more",
    "goal-fest": "five or more goals",
    "hat-trick": "a player scored three",
    "counter-goal": "a goal on the fast break",
    "one-on-one": "a goal through on goal from a ball in behind",
    "nil-nil": "goalless",
    "upset": "the weaker side won by two or more",
}


@dataclass
class MatchStory:
    match_id: str
    matchday: int
    home: str
    away: str
    score: tuple[int, int]
    tags: dict[str, str] = field(default_factory=dict)  # tag -> one-line detail

    @property
    def drama(self) -> int:
        """Rough excitement score for ranking: rarer, bigger stories count more."""
        weights = {"comeback": 5, "late-winner": 5, "late-equaliser": 4, "lead-swing": 3, "hat-trick": 3,
                   "fightback": 3, "stoppage-time": 2, "red-card": 2, "penalty-miss": 2, "wonder-goal": 2,
                   "great-save": 1, "upset": 3, "goal-fest": 2, "counter-goal": 1, "one-on-one": 1}
        return sum(weights.get(t, 1) for t in self.tags)


def _who(league: League, pid: str | None) -> str:
    return next((p.short_name for p in league.players if p.player_id == pid), pid or "?")


def tell(result: MatchResult, league: League) -> MatchStory:
    info, events = result.info, result.events
    home, away = info.home_club_id, info.away_club_id
    s = MatchStory(info.match_id, info.matchday, league.club(home).name, league.club(away).name,
                   (info.home_score, info.away_score))
    shots = [e for e in events if e.type == "shot"]
    goals = [e for e in shots if e.outcome == "goal"]
    name = lambda e: _who(league, e.player_id)  # noqa: E731

    # Lead changes and comebacks, from the score after each goal.
    leader, trailed = 0, {home: False, away: False}
    for g in goals:
        diff = g.home_score - g.away_score
        now = (diff > 0) - (diff < 0)
        if now and leader and now != leader:
            s.tags.setdefault("lead-swing", f"{g.clock} {name(g)} turns it round")
        if diff < 0:
            trailed[home] = True
        if diff > 0:
            trailed[away] = True
        leader = now or leader
    hs, as_ = s.score
    winner = home if hs > as_ else away if as_ > hs else None
    if winner and trailed[winner]:
        s.tags["comeback"] = f"{league.club(winner).short_name} came from behind to win {hs}-{as_}"
    elif not winner and goals and (trailed[home] or trailed[away]) and hs > 0:
        s.tags["fightback"] = f"pegged back to {hs}-{as_}"

    if goals:
        last = goals[-1]
        if last.minute >= 84:
            if winner and abs(hs - as_) == 1 and last.team_id == winner:
                s.tags["late-winner"] = f"{last.clock} {name(last)}"
            elif not winner:
                s.tags["late-equaliser"] = f"{last.clock} {name(last)}"
    added = [g for g in goals if "+" in g.clock]
    if added:
        s.tags["stoppage-time"] = ", ".join(f"{g.clock} {name(g)}" for g in added)

    reds = [e for e in events if e.type == "card" and e.outcome in ("red", "second_yellow")]
    if reds:
        s.tags["red-card"] = ", ".join(f"{e.clock} {_who(league, e.player_id)}" for e in reds)
    pens = [e for e in shots if e.set_piece == "penalty"]
    if pens:
        s.tags["penalty"] = ", ".join(f"{e.clock} {name(e)} ({e.outcome})" for e in pens)
        missed = [e for e in pens if e.outcome != "goal"]
        if missed:
            s.tags["penalty-miss"] = ", ".join(f"{e.clock} {name(e)} {e.outcome}" for e in missed)
    wonder = [g for g in goals if (g.xg or 1) < 0.05]
    if wonder:
        s.tags["wonder-goal"] = ", ".join(f"{g.clock} {name(g)} (xG {g.xg:.2f})" for g in wonder)
    great = [e for e in events if e.type == "save" and e.qualifiers.get("difficulty") == "great"]
    if great:
        s.tags["great-save"] = ", ".join(f"{e.clock} {_who(league, e.player_id)}" for e in great)
    posts = [e for e in shots if e.outcome == "post"]
    if len(posts) >= 2:
        s.tags["woodwork"] = f"{len(posts)} times"
    if hs + as_ >= 5:
        s.tags["goal-fest"] = f"{hs + as_} goals"
    scorers: dict[str, int] = {}
    for g in goals:
        scorers[g.player_id] = scorers.get(g.player_id, 0) + 1
    for pid, n in scorers.items():
        if n >= 3:
            s.tags["hat-trick"] = f"{_who(league, pid)} ({n})"
    counters = [g for g in goals if g.qualifiers.get("fast_break")]
    if counters:
        s.tags["counter-goal"] = ", ".join(f"{g.clock} {name(g)}" for g in counters)
    one_v_one = [g for g in goals if g.qualifiers.get("one_on_one")]
    if one_v_one:
        s.tags["one-on-one"] = ", ".join(f"{g.clock} {name(g)}" for g in one_v_one)
    if not goals:
        s.tags["nil-nil"] = "0-0"
    if winner:
        w, loser = (home, away) if winner == home else (away, home)
        if league.club(w).strength + 6 <= league.club(loser).strength and abs(hs - as_) >= 2:
            s.tags["upset"] = f"{league.club(w).short_name} ({league.club(w).strength}) beat " \
                              f"{league.club(loser).short_name} ({league.club(loser).strength})"
    return s


def find(results: list[MatchResult], league: League, wanted: list[str] | None = None) -> list[MatchStory]:
    """Matches with all the wanted stories (any story if none given), most dramatic first."""
    stories = [tell(r, league) for r in results]
    if wanted:
        stories = [s for s in stories if all(w in s.tags for w in wanted)]
    else:
        stories = [s for s in stories if s.tags]
    return sorted(stories, key=lambda s: (-s.drama, s.match_id))


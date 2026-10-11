"""Realism targets and a harness to check the engine against them.

Targets are per-match totals (both teams) in ranges typical of a modern
top-flight season. They are deliberately ranges, not point values: the goal
is "looks like real football", not reproducing any specific competition.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass

from footballfoundry.engine import MatchResult
from footballfoundry_schema import League
from footballfoundry.season import simulate_season
from footballfoundry.stats import match_stats
from footballfoundry.world import generate_league


@dataclass(frozen=True)
class Target:
    lo: float
    hi: float
    label: str


TARGETS: dict[str, Target] = {
    "goals": Target(2.4, 3.2, "Goals per match"),
    "shots": Target(21, 29, "Shots per match"),
    "shots_on_target": Target(7, 10.5, "Shots on target per match"),
    "xg": Target(2.3, 3.2, "xG per match"),
    "passes": Target(800, 1100, "Passes per match"),
    "pass_accuracy": Target(0.78, 0.86, "Pass completion"),
    "corners": Target(8.5, 12, "Corners per match"),
    "fouls": Target(17, 25, "Fouls per match"),
    "yellow_cards": Target(3.0, 4.8, "Yellow cards per match"),
    "red_cards": Target(0.03, 0.25, "Red cards per match"),
    "offsides": Target(2.5, 5, "Offsides per match"),
    "ball_in_play_min": Target(52, 62, "Ball-in-play minutes"),
    "home_win_rate": Target(0.38, 0.52, "Home win rate"),
    "draw_rate": Target(0.18, 0.32, "Draw rate"),
    # How matches unfold, not just totals: when goals come, whether leads hold, the near misses.
    "second_half_goal_share": Target(0.50, 0.62, "Goals in the 2nd half"),
    "late_goal_share": Target(0.19, 0.30, "Goals from 76'"),
    "stoppage_goal_share": Target(0.04, 0.12, "Goals in added time"),
    "first_scorer_win_rate": Target(0.60, 0.76, "First scorer wins"),
    "penalties": Target(0.15, 0.40, "Penalties per match"),
    "penalty_conversion": Target(0.65, 0.90, "Penalty conversion"),
    "woodwork": Target(0.6, 1.4, "Woodwork per match"),
    "xg_per_shot": Target(0.09, 0.13, "xG per shot"),
    # The shape of a full season, as the web app's table and leaders show it (meaningful for 380 matches).
    "top_scorer_goals": Target(20, 46, "Top scorer's goals"),
    "top_team_goals": Target(75, 106, "Most goals by a team"),
    "top_shooter_share": Target(0.2, 0.38, "Most shots by one player (team share)"),
}


def simulate_sample(
    n_matches: int = 380, seed: int = 42, league: League | None = None, workers: int = 1
) -> tuple[League, list[MatchResult]]:
    league = league or generate_league(seed)
    return league, simulate_season(league, limit=n_matches, workers=workers)


def summarize(results: list[MatchResult]) -> dict[str, float]:
    per_match: dict[str, list[float]] = {k: [] for k in TARGETS}
    home_wins = draws = 0
    for r in results:
        ms = match_stats(r)
        h, a = ms["home"], ms["away"]
        for key in ("goals", "shots", "shots_on_target", "xg", "passes", "corners", "fouls",
                    "yellow_cards", "red_cards", "offsides"):
            per_match[key].append(h.get(key, 0) + a.get(key, 0))
        passes = h.get("passes", 0) + a.get("passes", 0)
        completed = h.get("passes_completed", 0) + a.get("passes_completed", 0)
        per_match["pass_accuracy"].append(completed / passes if passes else 0)
        per_match["ball_in_play_min"].append(r.info.ball_in_play_s / 60)
        home_wins += r.info.home_score > r.info.away_score
        draws += r.info.home_score == r.info.away_score
    summary = {k: statistics.fmean(v) for k, v in per_match.items() if v}
    summary["home_win_rate"] = home_wins / len(results)
    summary["draw_rate"] = draws / len(results)
    summary |= _flow(results)
    return summary


def _flow(results: list[MatchResult]) -> dict[str, float]:
    goal_minutes, stoppage, first_wins, decided = [], 0, 0, 0
    shots = penalties = penalty_goals = woodwork = 0
    xg = 0.0
    player_goals: Counter[str] = Counter()
    team_goals: Counter[str] = Counter()
    player_shots: Counter[tuple[str, str]] = Counter()
    team_shots: Counter[str] = Counter()
    for r in results:
        match_shots = [e for e in r.events if e.type == "shot"]
        goals = [e for e in match_shots if e.outcome == "goal"]
        player_goals.update(g.player_id for g in goals)
        team_goals.update(g.team_id for g in goals)
        player_shots.update((e.team_id, e.player_id) for e in match_shots)
        team_shots.update(e.team_id for e in match_shots)
        shots += len(match_shots)
        xg += sum(e.xg or 0 for e in match_shots)
        penalties += sum(e.set_piece == "penalty" for e in match_shots)
        penalty_goals += sum(e.set_piece == "penalty" for e in goals)
        woodwork += sum(e.outcome == "post" for e in match_shots)
        goal_minutes += [g.minute for g in goals]
        stoppage += sum("+" in g.clock for g in goals)
        if goals:
            decided += 1
            home_first = goals[0].home_score == 1
            hs, as_ = r.info.home_score, r.info.away_score
            first_wins += (hs > as_) if home_first else (as_ > hs)
    n, g = len(results), max(1, len(goal_minutes))
    return {
        "second_half_goal_share": sum(m >= 45 for m in goal_minutes) / g,
        "late_goal_share": sum(m >= 75 for m in goal_minutes) / g,
        "stoppage_goal_share": stoppage / g,
        "first_scorer_win_rate": first_wins / max(1, decided),
        "penalties": penalties / n,
        "penalty_conversion": penalty_goals / max(1, penalties),
        "woodwork": woodwork / n,
        "xg_per_shot": xg / max(1, shots),
        "top_scorer_goals": max(player_goals.values(), default=0),
        "top_team_goals": max(team_goals.values(), default=0),
        "top_shooter_share": max((n / team_shots[t] for (t, _), n in player_shots.items()), default=0),
    }


def out_of_range(summary: dict[str, float]) -> list[str]:
    return [k for k, t in TARGETS.items() if not t.lo <= summary.get(k, float("nan")) <= t.hi]


def report(summary: dict[str, float]) -> str:
    lines = [f"{'metric':<28}{'value':>10}   target"]
    for key, t in TARGETS.items():
        v = summary.get(key, float("nan"))
        ok = "ok " if t.lo <= v <= t.hi else "OUT"
        lines.append(f"{t.label:<28}{v:>10.2f}   [{t.lo:g}, {t.hi:g}] {ok}")
    return "\n".join(lines)

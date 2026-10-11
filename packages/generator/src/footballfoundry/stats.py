"""Derive box-score statistics and league tables from generated events."""

from __future__ import annotations

from typing import Iterable

from footballfoundry.engine import MatchResult
from footballfoundry_schema import League
from footballfoundry_schema.stats import possession_share, team_stats


def match_stats(result: MatchResult) -> dict[str, dict[str, float]]:
    info = result.info
    home = team_stats(result.events, info.home_club_id)
    away = team_stats(result.events, info.away_club_id)
    home["possession"], away["possession"] = possession_share(result.events, info.home_club_id, info.away_club_id)
    return {"home": home, "away": away}


def standings(league: League, results: Iterable[MatchResult]) -> list[dict]:
    table = {c.club_id: dict(club_id=c.club_id, club=c.name, played=0, won=0, drawn=0, lost=0,
                             goals_for=0, goals_against=0, points=0) for c in league.clubs}
    for r in results:
        i = r.info
        for team, gf, ga in ((i.home_club_id, i.home_score, i.away_score), (i.away_club_id, i.away_score, i.home_score)):
            row = table[team]
            row["played"] += 1
            row["goals_for"] += gf
            row["goals_against"] += ga
            if gf > ga:
                row["won"] += 1
                row["points"] += 3
            elif gf == ga:
                row["drawn"] += 1
                row["points"] += 1
            else:
                row["lost"] += 1
    rows = list(table.values())
    for row in rows:
        row["goal_difference"] = row["goals_for"] - row["goals_against"]
    rows.sort(key=lambda r: (-r["points"], -r["goal_difference"], -r["goals_for"], r["club"]))
    for pos, row in enumerate(rows, start=1):
        row["position"] = pos
    return rows

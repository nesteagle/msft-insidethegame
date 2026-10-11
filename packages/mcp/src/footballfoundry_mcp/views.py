"""Tool results computed from a match's events up to `as_of`. Shared by every backend."""

from __future__ import annotations

from typing import Any, Iterable

from footballfoundry_schema import Event, League
from footballfoundry_schema.stats import player_stats, possession_share, team_stats, threat

EVENT_FIELDS = ("event_id", "seq", "clock", "period", "type", "team_id", "player_id", "related_player_id", "outcome",
                "x", "y", "end_x", "end_y", "possession_id", "set_piece", "under_pressure", "body_part", "xg",
                "home_score", "away_score", "qualifiers")


def compact(e: Event) -> dict[str, Any]:
    """An event without empty fields, to keep tool results small."""
    d = e.model_dump(mode="json", include=set(EVENT_FIELDS))
    return {k: v for k, v in d.items() if v not in (None, {}, False) or k in ("home_score", "away_score")}


def sides(events: list[Event]) -> tuple[str | None, str | None]:
    """Home and away: each period opens with one period_start per team, home first."""
    starts = [e.team_id for e in events if e.type == "period_start" and e.period == 1]
    return (starts[0] if starts else None), (starts[1] if len(starts) > 1 else None)


def round_stats(s: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in s.items()}


def match_state(match_id: str, events: list[Event], league: League | None) -> dict[str, Any]:
    if not events:
        return {"match_id": match_id, "status": "no events yet"}
    home, away = sides(events)
    last = events[-1]
    hp, ap = possession_share(events, home, away)
    lo = last.minute - 9
    momentum = {t: round(sum(threat(e) for e in events if e.team_id == t and e.minute >= lo), 2) for t in (home, away)}
    names = {c.club_id: c.name for c in league.clubs} if league else {}
    return {
        "match_id": match_id,
        "as_of_seq": last.seq,
        "clock": last.clock,
        "period": last.period,
        "status": "full_time" if last.type == "period_end" and last.period == 2 else "live",
        "home": {"club_id": home, "name": names.get(home), "score": last.home_score,
                 "possession": hp, "stats": round_stats(team_stats(events, home)),
                 "threat_last_10_min": momentum[home]},
        "away": {"club_id": away, "name": names.get(away), "score": last.away_score,
                 "possession": ap, "stats": round_stats(team_stats(events, away)),
                 "threat_last_10_min": momentum[away]},
        "goals": [compact(e) for e in events if e.type == "shot" and e.outcome == "goal"],
        "cards": [compact(e) for e in events if e.type == "card"],
        "substitutions": [compact(e) for e in events if e.type == "substitution"],
    }


def filter_events(events: Iterable[Event], *, from_seq: int | None = None, possession_id: int | None = None,
                  types: list[str] | None = None, team_id: str | None = None, player_id: str | None = None,
                  limit: int = 50) -> list[dict[str, Any]]:
    out = [e for e in events
           if (from_seq is None or e.seq >= from_seq)
           and (possession_id is None or e.possession_id == possession_id)
           and (not types or e.type in types)
           and (team_id is None or e.team_id == team_id)
           and (player_id is None or player_id in (e.player_id, e.related_player_id))]
    return [compact(e) for e in out[-max(1, min(limit, 500)):]]


def player_view(events: list[Event], player_id: str, league: League | None) -> dict[str, Any]:
    p = next((p for p in league.players if p.player_id == player_id), None) if league else None
    return {
        "player_id": player_id,
        "name": p.name if p else None,
        "club_id": p.club_id if p else None,
        "position": p.position if p else None,
        "as_of_seq": events[-1].seq if events else 0,
        "stats": round_stats(player_stats(events, player_id)),
        "substituted_off": any(e.type == "substitution" and e.player_id == player_id for e in events),
        "came_on": any(e.type == "substitution" and e.related_player_id == player_id for e in events),
        "cards": [e.outcome for e in events if e.type == "card" and e.player_id == player_id],
        "physical": _physical(events, player_id),
    }


def _physical(events: list[Event], player_id: str) -> dict[str, Any]:
    """Distance, sprints and top speed from the latest half-time or full-time totals, plus on-ball speeds."""
    out: dict[str, Any] = {}
    for e in reversed(events):
        p = e.qualifiers.get("physical", {}).get(player_id) if e.type == "period_end" else None
        if p:
            out = {**p, "as_of": e.clock}
            break
    own = [e for e in events if e.player_id == player_id]
    shots = [e.qualifiers["speed_kmh"] for e in own if e.type == "shot" and "speed_kmh" in e.qualifiers]
    carries = [e.qualifiers["speed_kmh"] for e in own if e.type == "carry" and "speed_kmh" in e.qualifiers]
    if shots:
        out["fastest_shot_kmh"] = max(shots)
    if carries:
        out["fastest_carry_kmh"] = max(carries)
        out["sprints_on_ball"] = sum(1 for e in own if e.qualifiers.get("sprint"))
    return out


def lookup(league: League | None, query: str, limit: int = 10) -> list[dict[str, Any]]:
    if not league:
        return []
    q = query.strip().lower()
    clubs = [c for c in league.clubs if q in (c.club_id.lower(), c.short_name.lower()) or q in c.name.lower()]
    players = [p for p in league.players if q == p.player_id.lower() or q in p.name.lower()]
    out: list[dict[str, Any]] = [
        {"kind": "club", **c.model_dump(mode="json")} for c in clubs[:limit]]
    out += [{"kind": "player", **p.model_dump(mode="json")} for p in players[: max(0, limit - len(out))]]
    return out

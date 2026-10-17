"""What viewers get before anything has happened: the story coming in, and the players to watch.

    kick-off          the teams' shapes, managers and styles (briefs.preview), plus the season so far: league
                      position, points, form, and an earlier meeting this season
    players to watch  ~2' in: one starter from each side, the season's top scorer at the club, else the one with
                      the most remarkable record (the fastest in the league, the club's best finisher...)

Season facts come from the season book (agents/season.py) and only from matchdays before this match's, so a viewer
is never told how this matchday ends. A one-off match (an X... id) isn't on the fixture list, so it gets no season
context; nor does anything before the book has loaded.
"""

from __future__ import annotations

from typing import Any

from footballfoundry_agents.lore import TEXT, Lore
from footballfoundry_agents.reference import Reference, possessive
from footballfoundry_agents.state import MatchState


def covered(brief: Any, lore: Lore | None) -> set[str]:
    """Fun facts the insight already tells, so the card's "Did you know?" doesn't repeat them: a players-to-watch
    reason that is a fun fact, and the grounds and managers the kick-off preview names."""
    ids = {w["trivia_id"] for w in brief.facts.get("watch", []) if w.get("trivia_id")}
    if brief.kind == "kick_off" and lore is not None:
        clubs = [p["club_id"] for p in (brief.facts.get("preview") or {}).values() if isinstance(p, dict)]
        ids |= {f.trivia_id for c in clubs for f in lore.facts.get(c, [])
                if f.key in ("ground", "biggest_ground", "manager")}
    return ids


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def season_context(season: Any, match_id: str, home: str | None, away: str | None) -> dict[str, Any] | None:
    """Both teams' season before this match's matchday: position, points, form, top scorer, an earlier meeting."""
    if season is None or not home or not away or season.book() is None:
        return None
    md = season.matchday_of().get(match_id)
    if md is None:
        return None  # a one-off match, outside the fixture list
    out: dict[str, Any] = {"matchday": md, "opening_day": md == 1}
    if md == 1:
        return out
    view = season.view(md - 1)
    table = {r["club_id"]: r for r in view["table"]}
    for side, club in (("home", home), ("away", away)):
        r = table.get(club)
        if r is None:
            continue
        scorers = [p for p in view["players"] if p["c"] == club and p["g"] > 0]
        top = max(scorers, key=lambda p: (p["g"], p["a"]), default=None)
        out[side] = {"position": r["position"], "points": r["points"], "played": r["played"],
                     "form": "".join(f["result"] for f in r["form"]),
                     "top_scorer": {"player_id": top["p"], "goals": top["g"]} if top else None}
    book = season.book()
    earlier = [m for m in book["matches"] if m["md"] < md and {m["h"], m["a"]} == {home, away}]
    if earlier:
        m = earlier[-1]
        out["earlier_meeting"] = {"matchday": m["md"], "home": m["h"], "away": m["a"], "score": [m["hs"], m["as"]]}
    return out


def describe_context(ctx: dict[str, Any] | None, ref: Reference, home: str, away: str) -> str:
    """One or two sentences on the season so far, for the template and the fact sheet."""
    if not ctx:
        return ""
    if ctx.get("opening_day"):
        return "It's the opening day of the season."
    bits = []
    for side, club in (("home", home), ("away", away)):
        s = ctx.get(side)
        if s:
            bits.append(f"{ref.club_name(club)} are {_ordinal(s['position'])} with {s['points']} points "
                        f"from {s['played']} (form {s['form']})")
    text = "; ".join(bits) + "." if bits else ""
    m = ctx.get("earlier_meeting")
    if m:
        text += (f" They met on matchday {m['matchday']}: {ref.club_name(m['home'])} {m['score'][0]}-{m['score'][1]} "
                 f"{ref.club_name(m['away'])}.")
    return text.strip()


def starters(state: MatchState, team_id: str | None) -> list[str]:
    """The team's starting XI, from its kick-off team sheet."""
    sheet = next((e for e in state.recent if e.type == "period_start" and e.period == 1 and e.team_id == team_id), None)
    return [p["player_id"] for p in (sheet.qualifiers.get("lineup", []) if sheet else [])]


def players_to_watch(state: MatchState, lore: Lore | None, ctx: dict[str, Any] | None,
                     ref: Reference) -> list[dict[str, Any]]:
    """One starter per side worth watching, with the reason as a sentence. `trivia_id` marks a reason that is a lore
    fact, so the same fact isn't shown again as the card's "Did you know?"."""
    rank = {key: i for i, key in enumerate(TEXT)}
    out = []
    for side, team in (("home", state.home_id), ("away", state.away_id)):
        xi = starters(state, team)
        if not xi:
            continue
        top = ((ctx or {}).get(side) or {}).get("top_scorer")
        if top and top["player_id"] in xi and top["goals"] >= 2:
            pid = top["player_id"]
            out.append({"player_id": pid, "team_id": team, "trivia_id": None,
                        "reason": f"{ref.player_name(pid)} is {possessive(ref.club_name(team))} top scorer this season, "
                                  f"with {top['goals']} goals."})
            continue
        facts = [f for pid in xi for f in (lore.facts.get(pid, []) if lore else []) if f.key != "profile"]
        if facts:
            best = min(facts, key=lambda f: rank.get(f.key, len(rank)))
            out.append({"player_id": best.subject_id, "team_id": team, "trivia_id": best.trivia_id,
                        "reason": best.text})
            continue
        attacker = next((pid for pid in xi if ref.player(pid) and ref.player(pid).position in ("ST", "AM", "RW", "LW")),
                        xi[-1])
        out.append({"player_id": attacker, "team_id": team, "trivia_id": None,
                    "reason": f"{ref.player_name(attacker)} leads the line for {ref.club_name(team)}."})
    return out

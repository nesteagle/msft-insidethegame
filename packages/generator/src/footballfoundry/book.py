"""The season book: every match's result and every player's line in it, compact enough for a web page.

The web app's season pages (table, stats leaders, players) aggregate it up to any matchday, so a viewer watching
matchday 12 live can see the table as it stood before kick-off. Built once from the simulated season:
`footballfoundry book` (or `footballfoundry season`, which writes it with the other files).

Lines use short keys:
    min  minutes            st   started           g    goals             a    assists
    xg   expected goals     sh   shots             sot  shots on target   kp   chances created
    pa   passes             pc   passes completed  tk   tackles           tkw  tackles won
    int  interceptions      rec  ball recoveries   clr  clearances        blk  blocks
    sav  saves              yc   yellow cards      rc   red cards         cs   clean sheet (60+ minutes, none conceded)
    km   distance (km)      spr  sprints           top  top speed (km/h)

Each match also carries its stories (footballfoundry.stories: comebacks, late winners...), so the control room's
story finder is a lookup.

An assist is the last completed pass to the scorer in the same move: no opposition touch in between and no more
than 20 seconds before the shot. Penalties have no assist. A chance created is the same, for any shot.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from footballfoundry.engine import MatchResult
from footballfoundry_schema import Event, League
from footballfoundry_schema.stats import CONTEXT, possession_share

BOOK_VERSION = 2  # 2: stories per match
MOVE_GAP_S = 20.0
CLEAN_SHEET_MIN = 60

COUNT = {"interception": "int", "ball_recovery": "rec", "clearance": "clr", "block": "blk", "save": "sav"}


def _t(e: Event) -> float:
    return (e.period - 1) * 10_000 + e.minute * 60 + e.second


def _creator(events: list[Event], i: int) -> str | None:
    """Who set up the shot at events[i]: the passer of the last completed pass to the shooter in the move."""
    shot = events[i]
    if shot.set_piece == "penalty":
        return None
    for j in range(i - 1, -1, -1):
        e = events[j]
        if e.type in CONTEXT:
            continue
        if _t(shot) - _t(e) > MOVE_GAP_S or (e.team_id and e.team_id != shot.team_id):
            return None
        if e.type == "pass":
            ok = e.outcome == "complete" and e.related_player_id == shot.player_id and e.player_id != shot.player_id
            return e.player_id if ok else None
    return None


def match_lines(result: MatchResult) -> list[dict[str, Any]]:
    info, events = result.info, result.events
    lines: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(float))
    club_of = {entry.player_id: entry.club_id for entry in info.lineups}
    for entry in info.lineups:
        if entry.starter:
            lines[entry.player_id]["st"] = 1
    for i, e in enumerate(events):
        if not e.player_id or e.type == "substitution":
            continue
        s, t, o = lines[e.player_id], e.type, e.outcome
        if t == "pass":
            s["pa"] += 1
            s["pc"] += o == "complete"
        elif t == "shot":
            s["sh"] += 1
            s["xg"] += e.xg or 0.0
            s["sot"] += o in ("goal", "saved")
            s["g"] += o == "goal"
            if creator := _creator(events, i):
                lines[creator]["kp"] += 1
                if o == "goal":
                    lines[creator]["a"] += 1
        elif t == "tackle":
            s["tk"] += 1
            s["tkw"] += o == "won"
        elif t == "card":
            s["yc"] += o == "yellow"
            s["rc"] += o in ("red", "second_yellow")
        elif t in COUNT:
            s[COUNT[t]] += 1
    # Minutes and physical totals: each team's last period_end carries them for everyone who played.
    physical: dict[str, dict] = {}
    for e in events:
        if e.type == "period_end" and e.qualifiers.get("physical"):
            physical.update(e.qualifiers["physical"])
    conceded = {info.home_club_id: info.away_score, info.away_club_id: info.home_score}
    out = []
    for pid, phys in physical.items():
        s = lines[pid]
        club = club_of.get(pid)
        line = {"p": pid, "c": club, "min": int(phys.get("minutes", 0)), "km": phys.get("distance_km", 0.0),
                "spr": int(phys.get("sprints", 0)), "top": phys.get("top_speed_kmh", 0.0)}
        line |= {k: (round(v, 2) if k == "xg" else int(v)) for k, v in s.items() if v}
        if line["min"] >= CLEAN_SHEET_MIN and club and conceded[club] == 0:
            line["cs"] = 1
        out.append(line)
    return out


def match_record(result: MatchResult, league: League | None = None) -> dict[str, Any]:
    info, events = result.info, result.events
    hxg = sum(e.xg or 0 for e in events if e.type == "shot" and e.team_id == info.home_club_id)
    axg = sum(e.xg or 0 for e in events if e.type == "shot" and e.team_id == info.away_club_id)
    hpos, apos = possession_share(events, info.home_club_id, info.away_club_id)
    record = {"id": info.match_id, "md": info.matchday, "ko": info.kickoff.isoformat(), "h": info.home_club_id,
              "a": info.away_club_id, "hs": info.home_score, "as": info.away_score, "hxg": round(hxg, 2),
              "axg": round(axg, 2), "hpos": hpos, "apos": apos, "lines": match_lines(result)}
    if league is not None:
        from footballfoundry.stories import tell

        story = tell(result, league)
        record["stories"] = story.tags
        record["drama"] = story.drama
    return record


def build(league: League, results: Iterable[MatchResult]) -> dict[str, Any]:
    matches = sorted((match_record(r, league) for r in results), key=lambda m: (m["md"], m["id"]))
    return {"version": BOOK_VERSION, "seed": league.seed, "season": league.season, "matches": matches}

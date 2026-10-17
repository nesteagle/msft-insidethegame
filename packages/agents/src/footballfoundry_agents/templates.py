"""Deterministic insights without an LLM.

Used in two places: `--offline` mode (local development, tests, demos without Azure), and as the fallback when the
agent workflow fails or times out, so a goal never goes unannounced because a model call did.
"""

from __future__ import annotations

from datetime import datetime, timezone

from footballfoundry_schema import Insight, InsightVariant

from footballfoundry_agents.briefs import Brief
from footballfoundry_agents.reference import Reference
from footballfoundry_agents.visuals import choose

DISPLAY_S = {1: 12.0, 2: 8.0, 3: 6.0}


def make_insight(brief: Brief, *, headline: str, text: str, why: str, stats: dict[str, str],
                 verified: bool, player_ids: list[str] | None = None, event_ids: list[str] | None = None,
                 visuals: list[tuple[str, str]] | None = None) -> Insight:
    """`visuals` are the author's picks, (type, caption); without any, the moment's default visuals are shown."""
    return Insight(
        insight_id=brief.insight_id, match_id=brief.match_id, kind=brief.kind, seq=brief.seq, period=brief.period,
        minute=brief.minute, clock=brief.clock, team_id=brief.team_id,
        player_ids=player_ids if player_ids is not None else brief.player_ids,
        event_ids=event_ids if event_ids is not None else brief.event_ids,
        headline=headline[:80], text=text, why_it_matters=why, stats=stats, priority=brief.priority,
        display_s=DISPLAY_S[brief.priority], verified=verified, created_at=datetime.now(timezone.utc),
        visuals=choose(brief.visuals, brief.kind, visuals),
    )


def template_insight(brief: Brief, ref: Reference) -> Insight:
    f = brief.facts
    home, away = ref.club_name(f["home"]), ref.club_name(f["away"])
    hs, as_ = f["home_stats"], f["away_stats"]
    score = f"{home} {f['score'][0]}-{f['score'][1]} {away}"
    team = ref.club_name(brief.team_id)
    lead = ref.player_name(brief.player_ids[0]) if brief.player_ids else team
    side = hs if brief.team_id == f["home"] else as_
    xg_line = {"xG": f"{hs['xg']:.2f} - {as_['xg']:.2f}", "Shots": f"{hs['shots']:.0f} - {as_['shots']:.0f}"}

    anchor = brief.facts.get("anchor", {})
    q = anchor.get("qualifiers", {})
    how = _how(q, anchor)
    if brief.kind == "goal":
        headline = f"GOAL {brief.clock} {lead} ({team})"
        text = f"{lead} scores{how} for {team}. {score}."
        xg = anchor.get("xg")
        rarity = f" from a {xg:.2f} xG chance" if xg is not None and xg < 0.08 else ""
        why = f"{team} have {side['shots']:.0f} shots worth {side['xg']:.2f} xG so far{rarity}."
    elif brief.kind == "penalty":
        headline = f"Penalty missed {brief.clock}: {lead}"
        text = f"{lead} fails to convert from the spot for {team}. {score}."
        why = "A penalty is worth about 0.78 xG; missing one is a big swing."
    elif brief.kind == "red_card":
        headline = f"RED CARD {brief.clock}: {lead}"
        text = f"{lead} is sent off. {team} play the rest of the match with ten."
        why = "Ten men usually means less possession and more defending for the rest of the game."
    elif brief.kind == "big_chance":
        outcome = anchor.get("outcome")
        keeper = ref.player_name(brief.facts.get("keeper")) if brief.facts.get("keeper") else "the keeper"
        if outcome == "saved" and q.get("on_target_xg", 0) >= 0.4:
            headline = f"What a save! {keeper} denies {lead}"
            text = f"{keeper} pulls off a superb stop to keep out {lead}{how}. {score}."
        elif outcome == "post":
            headline = f"Off the woodwork! {lead} {brief.clock}"
            text = f"{lead} hits the frame of the goal{how}. {score}."
        elif outcome == "saved":
            headline = f"Saved! {lead} {brief.clock}"
            text = f"{lead} is denied by {keeper}{how}. {score}."
        else:
            headline = f"Big chance {brief.clock}: {lead}"
            text = f"{lead} goes close for {team}{how}. {score}."
        why = f"That chance was worth {anchor.get('xg', 0.3):.2f} xG, one of the best openings of the match."
    elif brief.kind == "tactical_change":
        if q.get("kind") == "formation":
            headline = f"{team} switch to {q.get('formation')}"
            text = f"{team} change shape to a {q.get('formation')} at {brief.clock}. {score}."
        else:
            headline = f"{lead} moves to {q.get('role')}"
            text = f"{team} move {lead} from {q.get('from_role')} to {q.get('role')} at {brief.clock}. {score}."
        why = "A change of shape shifts who gets on the ball where; watch the next few minutes."
    elif brief.kind == "momentum":
        hm, am = f["momentum_10min"]
        headline = f"{team} on top"
        text = f"{team} have dominated the last ten minutes of attacking play ({hm:.1f} v {am:.1f} threat)."
        why = "Sustained pressure like this often comes before a goal."
    elif brief.kind in ("half_time", "full_time"):
        label = "Half-time" if brief.kind == "half_time" else "Full-time"
        headline = f"{label}: {score}"
        text = (f"{label}. {home} {hs['shots']:.0f} shots ({hs['xg']:.2f} xG), "
                f"{away} {as_['shots']:.0f} shots ({as_['xg']:.2f} xG).")
        better = home if hs["xg"] >= as_["xg"] else away
        why = f"On chances created, {better} have had the better of it."
        if f.get("physical"):
            leaders = "; ".join(f["physical"][:2])
            text += f" {leaders[0].upper()}{leaders[1:]}."
    else:  # summary
        headline = f"{brief.clock}: {score}"
        text = (f"Possession {f['possession'][0]:.0%}-{f['possession'][1]:.0%}. "
                f"Shots {hs['shots']:.0f}-{as_['shots']:.0f}, xG {hs['xg']:.2f}-{as_['xg']:.2f}.")
        why = "The match so far in numbers."
    return make_insight(brief, headline=headline, text=text, why=why, stats=xg_line, verified=False)


def _how(q: dict, anchor: dict) -> str:
    """How a chance came about, in a few words: one-on-one, on the break, a rocket from distance..."""
    bits = []
    if q.get("one_on_one"):
        bits.append("one-on-one")
    elif q.get("fast_break"):
        bits.append("on the break")
    if q.get("long_range"):
        bits.append("from distance")
    if anchor.get("set_piece") == "penalty":
        bits.append("from the spot")
    elif anchor.get("body_part") == "head":
        bits.append("with a header")
    speed = q.get("speed_kmh")
    if speed and speed >= 100 and anchor.get("body_part") != "head":
        bits.append(f"at {speed:.0f} km/h")
    return f" ({', '.join(bits)})" if bits else ""


def template_variant(insight: Insight, audience: str, language: str, club_id: str | None,
                     player_id: str | None, ref: Reference) -> InsightVariant:
    """Offline personalisation: reshapes the text but cannot translate, so the language stays English."""
    if audience == "analyst":
        numbers = "; ".join(f"{k} {v}" for k, v in insight.stats.items())
        text = f"{insight.text} {insight.why_it_matters} [{numbers}]"
    elif audience == "casual":
        text = insight.text
    else:
        text = f"{insight.text} {insight.why_it_matters}"
    if player_id and player_id in insight.player_ids:
        text = f"{ref.player_name(player_id)} involved. {text}"
    return InsightVariant(audience=audience, language="en", focus_club_id=club_id, focus_player_id=player_id,
                          headline=insight.headline, text=text)

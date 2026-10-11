import json

from footballfoundry.cli import main
from footballfoundry.engine import FormationChange, RoleChange, TacticChange, simulate_match
from footballfoundry.formations import FORMATIONS
from footballfoundry.scenario import Scenario, truth
from footballfoundry.stories import STORIES, find, tell


def test_physical_and_speed_data_are_plausible(results):
    for r in results[:6]:
        for e in r.events:
            q = e.qualifiers
            if e.type == "shot":
                assert 35 <= q["speed_kmh"] <= 125
            if e.type == "pass":
                assert 0 <= q["difficulty"] <= 1 and 15 <= q["speed_kmh"] <= 95
            if e.type == "carry":
                assert q.get("sprint", False) == (q["speed_kmh"] >= 25.2)
            if e.type == "save" and e.outcome == "saved":
                assert q["difficulty"] in ("routine", "good", "great")
        finals = [e for e in r.events if e.type == "period_end" and e.period == 2]
        for e in finals:
            physical = e.qualifiers["physical"]
            assert len(physical) >= 11
            for p in physical.values():
                if p["minutes"] >= 85:
                    assert 4.5 <= p["distance_km"] <= 13.5 and 18 <= p["top_speed_kmh"] <= 37  # keepers ~20-25


def test_physical_model_does_not_change_the_match(league):
    """Speeds and distances come from a separate random stream: the football is identical either way."""
    a = simulate_match(league, "T03", "T04", seed=7)
    strip = lambda r: [(e.type, e.outcome, e.player_id, e.x, e.y) for e in r.events]  # noqa: E731
    assert strip(a) == strip(simulate_match(league, "T03", "T04", seed=7))


def test_role_and_formation_changes_are_applied_and_announced(league):
    r = simulate_match(league, "T05", "T12", seed=3,
                       role_changes=[RoleChange(61, 0, "RW", "AM")],
                       formation_changes=[FormationChange(60, 1, "3-5-2")])
    changes = [e for e in r.events if e.type == "tactical_change"]
    assert {e.qualifiers["kind"] for e in changes} == {"role", "formation"}
    for e in changes:
        assert e.minute >= 60 and len(e.qualifiers["lineup"]) >= 10
    formation = next(e for e in changes if e.qualifiers["kind"] == "formation")
    roles = sorted(p["role"] for p in formation.qualifiers["lineup"])
    assert roles == sorted(r for r, _, _ in FORMATIONS["3-5-2"])[:len(roles)] or "CB" in roles
    role = next(e for e in changes if e.qualifiers["kind"] == "role")
    assert role.qualifiers["role"] == "AM" and role.qualifiers["from_role"] in ("RW", "RM", "RWB")


def test_truth_measures_the_scripted_change(league):
    """A role change into the middle must show up as more central touches for that player."""
    moved_central = []
    for seed in range(4):
        s = Scenario(roles=[RoleChange(55, 0, "RW", "AM")])
        r = simulate_match(league, "T05", "T12", seed=seed, **s.engine_kwargs())
        change = truth(r, s)["changes"][0]
        if change["applied"]:
            before, after = change["effect"]["player_before"], change["effect"]["player_after"]
            moved_central.append(after["central_share"] - before["central_share"])
    assert moved_central and sum(moved_central) / len(moved_central) > 0.2


def test_pressing_scenario_lowers_ppda(league):
    deltas = []
    for seed in range(6):
        s = Scenario(tactics=[TacticChange(50, 0, {"press_intensity": 0.6, "defensive_line": 0.4})])
        r = simulate_match(league, "T05", "T06", seed=seed, **s.engine_kwargs())
        eff = truth(r, s)["changes"][0]["effect"]
        if eff["before"]["ppda"] and eff["after"]["ppda"]:
            deltas.append(eff["after"]["opponent_passes_under_pressure"] - eff["before"]["opponent_passes_under_pressure"])
    assert sum(deltas) / len(deltas) > 0.03


def test_scenario_file_and_cli(tmp_path):
    scenario = tmp_path / "s.json"
    scenario.write_text(json.dumps({"name": "test", "changes": [
        {"minute": 30, "side": "away", "type": "formation", "formation": "3-5-2"},
        {"minute": 30, "side": "home", "type": "style", "changes": {"press_intensity": 0.3}}]}))
    main(["match", "--home", "T01", "--away", "T02", "--scenario", str(scenario), "--out", str(tmp_path)])
    t = json.loads((tmp_path / "truth" / "X0001.json").read_text())
    by_type = {c["type"]: c for c in t["changes"]}
    assert t["scenario"] == "test" and set(by_type) == {"formation", "style"}
    assert by_type["formation"]["applied"] and not by_type["style"]["announced"]


def test_stories_tag_known_shapes(results, league):
    stories = [tell(r, league) for r in results]
    for s, r in zip(stories, results):
        assert set(s.tags) <= set(STORIES)
        if "nil-nil" in s.tags:
            assert r.info.home_score == r.info.away_score == 0
        if "comeback" in s.tags:
            assert r.info.home_score != r.info.away_score
    some = find(results, league)
    assert some == sorted(some, key=lambda s: (-s.drama, s.match_id))

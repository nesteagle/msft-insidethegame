import pytest
from pydantic import ValidationError

from footballfoundry_schema import Event

BASE = dict(
    event_id="m1-1", match_id="m1", seq=1, period=1, elapsed_s=12.5, minute=0, second=12, clock="1'",
    type="pass", team_id="h", player_id="h-1", outcome="complete", x=50.0, y=50.0,
    possession_id=1, possession_team_id="h", home_score=0, away_score=0,
)


def test_event_json_roundtrip():
    event = Event(**BASE)
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_coordinates_must_be_on_the_pitch():
    with pytest.raises(ValidationError):
        Event(**{**BASE, "x": 101.0})


def test_insight_variant_key_is_stable():
    from datetime import datetime, timezone

    from footballfoundry_schema import Insight

    i = Insight(insight_id="m1-goal-500", match_id="m1", kind="goal", seq=500, period=1, minute=27, clock="28'",
                headline="h", text="t", why_it_matters="w", created_at=datetime.now(timezone.utc))
    assert Insight.variant_key("casual", "es", "T01") == "casual|es|T01|"
    assert Insight.model_validate_json(i.model_dump_json()) == i


def test_qualifiers_are_checked_against_the_schema():
    assert Event(**BASE, qualifiers={"length_m": 18.2, "speed_kmh": 41.0, "difficulty": 0.12}).qualifiers["length_m"]
    with pytest.raises(ValidationError):
        Event(**BASE, qualifiers={"made_up": 1})  # every key must be declared in Qualifiers
    with pytest.raises(ValidationError):
        Event(**BASE, qualifiers={"difficulty": "impossible"})


def test_event_json_schema_spells_out_qualifiers():
    from footballfoundry_schema import event_json_schema

    schema = event_json_schema()
    assert schema["properties"]["qualifiers"]["$ref"] == "#/$defs/Qualifiers"
    q = schema["$defs"]["Qualifiers"]
    assert q["additionalProperties"] is False and {"speed_kmh", "counterpress", "cause", "physical"} <= set(q["properties"])
    assert {"pressure", "possession_change"} <= set(schema["properties"]["type"]["enum"])

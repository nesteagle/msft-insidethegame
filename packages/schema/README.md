# footballfoundry-schema

The contract between every FootballFoundry piece. Depend on this package, not on the generator, when you only need
to read the data.

| Module | What's in it |
| --- | --- |
| `footballfoundry_schema` | Pydantic models: `League`, `Club`, `Player`, `Fixture`, `MatchInfo` and `Event` (what the generator produces), `Qualifiers` (every key an event's `qualifiers` may carry, enforced on validation), and `Insight` / `InsightVariant` (what the agents produce); `event_json_schema()` for the same contract as JSON Schema |
| `footballfoundry_schema.stats` | One definition of the box-score numbers (`team_stats`, `player_stats`, `possession_share`) and of attacking `threat`, shared by the generator, the agents and the MCP server |

```python
from footballfoundry_schema import Event
from footballfoundry_schema.stats import team_stats

events = [Event.model_validate_json(line) for line in open("data/events/M0001.jsonl")]
print(team_stats(events, "T20")["xg"])
```

Change the models deliberately and bump `SCHEMA_VERSION` when you do.

# footballfoundry-mcp

A Model Context Protocol (MCP) server exposing FootballFoundry match data as tools. The agents in
[`packages/agents`](../agents/) call it through Agent Framework's `MCPStreamableHTTPTool`. Any other MCP client
(GitHub Copilot, VS Code, Foundry Agent Service) can use the same tools.

## Run it

```bash
footballfoundry season                                   # the data it reads
footballfoundry-mcp --data data                          # streamable HTTP on http://127.0.0.1:8001/mcp
footballfoundry-mcp --data data --transport stdio        # for MCP clients that start the server themselves

# Live matches that aren't in the season files (e.g. one-off matches): also read the stream's output file.
footballfoundry stream --home T03 --away T11 --sink file --out data/stream.jsonl
footballfoundry-mcp --data data --live data/stream.jsonl

# Against the Fabric Eventhouse (see fabric/README.md)
footballfoundry-mcp --kusto-uri https://<eventhouse>.kusto.fabric.microsoft.com --kusto-db footballfoundry
```

To use it from VS Code or GitHub Copilot, add it to `.vscode/mcp.json`:

```json
{ "servers": { "footballfoundry": { "type": "http", "url": "http://127.0.0.1:8001/mcp" } } }
```

## Tools

All tools are read-only.

| Tool | Returns |
| --- | --- |
| `get_match_state(match_id, as_of)` | Score, clock, possession, team stats, threat over the last 10 minutes, goals, cards, substitutions |
| `get_events(match_id, as_of, from_seq?, possession_id?, types?, team_id?, player_id?, limit?)` | A slice of events |
| `get_player_stats(match_id, player_id, as_of)` | One player's match numbers, cards, and whether they came on or went off |
| `get_team_stats(match_id, team_id, as_of)` | Team numbers for the match so far |
| `lookup(query)` | Clubs and players by id or name: colours, stadium, style dials, positions, attributes |
| `season_query(name, params?, before_match_id?)` | Named queries: `standings`, `top_scorers`, `team_form`, `player_season`, `head_to_head` |
| `get_insights(match_id, as_of)` | Insights the agents have published (needs `--agents-url`) |

**No spoilers.** Every match tool takes `as_of`, an event `seq`, and returns nothing after it. Season queries take
`before_match_id` and count only earlier matchdays. Both matter when a past match is replayed, because the data
already holds the whole season.

**No free-form queries.** `season_query` runs named, parameterised queries only. On the Eventhouse they are the
`FF_*` functions in [`fabric/eventhouse/functions.kql`](../../fabric/eventhouse/functions.kql).

## Backends

| | Reads | When |
| --- | --- | --- |
| `FileBackend` | `league.json`, `fixtures.json`, `matches/`, `events/`, optional live JSONL | Local runs; the Container App image bakes in the default-seed season |
| `KustoBackend` | The Eventhouse tables `MatchEvents` and `Fixtures`, via the `FF_*` functions | Set `KUSTO_URI` |
| `ArchiveBackend` | The agents' event archive: every event plus a result per finished match | Cosmos DB with `COSMOS_ENDPOINT`; locally `--archive <agents --store-dir>/archive` |

Every configured backend goes into a fallback chain, with a timeout per call:

| Tools | Order |
| --- | --- |
| Live match (`get_match_state`, `get_events`, `get_player_stats`, `get_team_stats`) | archive → Eventhouse → season files |
| `season_query` | Eventhouse → archive → season files |

A backend that fails, times out or has nothing for the request is skipped (and logged). Match tools return `source`,
the backend that answered. The archive leads for live matches because Eventstream ingestion can trail the stream.

```bash
# locally, with the agents writing their archive under data/agents
footballfoundry-agents serve --offline --store file --follow data/stream.jsonl
footballfoundry-mcp --data data --archive data/agents/archive
```

The server is stateless (`stateless_http`, JSON responses), so Container Apps can run several replicas behind its
internal ingress.

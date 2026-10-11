# Microsoft Fabric: Eventstream and Eventhouse

Every event lands in an Eventhouse (KQL database) for analytics, dashboards and the MCP server's season queries.
These steps are done in the Fabric portal. Fabric's Git integration can then save the items into this folder.

```text
Event Hubs match-events ──(consumer group "fabric")──► Eventstream ──► Eventhouse: MatchEvents
                                                                              │
                                     footballfoundry-mcp (KustoBackend) ◄─────┤ FF_* functions
                                     Real-Time Dashboard, Activator      ◄────┘
```

## 1. Eventhouse and tables

1. In a Fabric workspace, create an **Eventhouse** named `footballfoundry`. It comes with a KQL database of the same
   name.
2. Open a KQL queryset on the database and run [`eventhouse/tables.kql`](eventhouse/tables.kql), then
   [`eventhouse/functions.kql`](eventhouse/functions.kql).
3. Load the fixture list (it maps each match to its matchday, which the season queries need):

   ```bash
   footballfoundry world
   python fabric/eventhouse/fixtures_to_kql.py data/fixtures.json > fixtures.kql
   ```

   Paste `fixtures.kql` into the queryset and run it.

## 2. Eventstream

1. Create an **Eventstream** named `match-events`.
2. Add a source: **Azure Event Hubs**. Namespace and hub from `azd env get-values` (`EVENTHUB_NAMESPACE`,
   `match-events`), consumer group **`fabric`**, data format **JSON**. For the connection, use the shared access key
   of the `fabric-listen` rule (Event Hubs → match-events → Shared access policies).
3. Add a destination: **Eventhouse**, direct ingestion, database `footballfoundry`, existing table `MatchEvents`,
   format JSON, mapping `MatchEventsMapping`.
4. Publish, then stream a match (`footballfoundry stream --sink eventhub`) and check:

   ```kusto
   MatchEvents | summarize events = count(), last = max(seq) by match_id
   ```

## 3. Let the MCP server read it

Copy the Eventhouse **Query URI** and grant the apps' managed identity (`id-ff-*`) **Viewer** on the KQL database
(Database → Manage → Permissions). Then `azd env set KUSTO_URI <query URI>` and `azd provision`. Locally:
`footballfoundry-mcp --kusto-uri <query URI>` signs in with `az login`.

## Functions

| Function | Used by `season_query` | Returns |
| --- | --- | --- |
| `FF_MatchEvents(m, s)` | match tools | Events of one match up to seq `s`, latest run only |
| `FF_Results(md)` | | Final scores of matches before matchday `md` |
| `FF_Standings(md)` | `standings` | League table |
| `FF_TopScorers(md, n)` | `top_scorers` | Top `n` scorers |
| `FF_TeamForm(club, md, n)` | `team_form` | A club's last `n` results |
| `FF_PlayerSeason(player, md)` | `player_season` | Appearances, goals, shots, xG |
| `FF_HeadToHead(a, b, md)` | `head_to_head` | Earlier meetings |

Streaming the same match twice ingests it twice. The functions count each `event_id` once, and `FF_MatchEvents`
returns only the latest run.

## Next

- A **Real-Time Dashboard** on `MatchEvents`: live xG race, shot maps, possession by 5-minute window.
- **Activator** alerts, for example when one side's xG lead passes 1.5, notify the studio producer.

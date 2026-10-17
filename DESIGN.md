# FootballFoundry design

How the pieces fit together: the data flow, the agents service, the multi-agent workflow, the chat, and the Azure
infrastructure `azd up` creates. For how to run things, see the [README](README.md) and each package's README.

- [1. The big picture](#1-the-big-picture)
- [2. Inside the agents service](#2-inside-the-agents-service)
- [3. The multi-agent workflow](#3-the-multi-agent-workflow)
- [4. A goal, from kick to screen](#4-a-goal-from-kick-to-screen)
- [5. Chat with the match assistant](#5-chat-with-the-match-assistant)
- [6. Azure infrastructure](#6-azure-infrastructure)
- [7. Packages in the repository](#7-packages-in-the-repository)
- [Design decisions](#design-decisions)

## 1. The big picture

The generator produces a live match. Event Hubs fans the events out to three independent readers. The agents
service turns them into insights and serves the web app.

```mermaid
flowchart LR
    gen["footballfoundry stream<br/>(generator)"] -->|"one JSON event per message<br/>partition key = match_id"| eh[("Azure Event Hubs<br/>match-events")]

    eh -->|"consumer group: analyst<br/>(checkpointed, split across replicas)"| agents
    eh -->|"consumer group: viewer<br/>(every replica reads everything)"| agents
    eh -->|"consumer group: fabric"| es["Fabric Eventstream"]
    es --> kql[("Fabric Eventhouse<br/>KQL: MatchEvents")]

    subgraph agents["Agents service (Container App)"]
        direction TB
        relay["Event relay"]
        analysts["Trigger rules +<br/>analyst agents"]
        chat["Match assistant chat"]
        web["Web app (React)"]
    end

    agents <-->|"model calls"| foundry["Microsoft Foundry<br/>model deployment"]
    agents <-->|"MCP tools"| mcp["MCP server<br/>(Container App, internal)"]
    mcp -->|"season queries<br/>(first choice)"| kql
    agents <-->|"insights, state, profiles,<br/>event archive"| cosmos[("Cosmos DB")]
    mcp -->|"live-match data<br/>(first choice)"| cosmos
    browser["Viewer's browser"] <-->|"HTTPS: page, SSE, AG-UI chat"| agents
```

Each reader has its own consumer group, so each moves at its own pace:

| Consumer group | Reader | Behaviour |
| --- | --- | --- |
| `analyst` | The agents' analysis | Checkpointed to Blob Storage; replicas split the partitions between them |
| `viewer` | The agents' event relay, which feeds the pitch in the web app | No checkpoints; every replica reads every partition from the start of retention |
| `fabric` | Fabric Eventstream → Eventhouse | Analytics, dashboards and the MCP server's season queries |

## 2. Inside the agents service

One Python process (`footballfoundry-agents serve`) runs two loops: consuming events and serving HTTP.

```mermaid
flowchart TB
    subgraph sources["Event sources"]
        ehA["Event Hubs 'analyst'"]
        ehV["Event Hubs 'viewer'"]
        local["Local: stdin pipe or --follow file"]
    end

    ehV --> log["EventLog (relay.py)<br/>every event of every match,<br/>on the match clock"]
    local --> log
    local --> state
    ehA --> state["MatchState (state.py)<br/>score, stats, threat per minute,<br/>last 200 events"]

    state --> trig["TriggerEngine (triggers.py)<br/>goal, red card, penalty, big chance,<br/>momentum swing, summary, HT, FT,<br/>spotlight when it goes quiet"]
    trig --> brief["Brief (briefs.py)<br/>facts frozen at that moment"]
    brief --> q["Per-match queue<br/>(drops stale filler if behind)"]
    q --> writer{"Writer"}
    writer -->|"Foundry configured"| wf["Agent Framework workflow<br/>(workflow.py)"]
    writer -->|"offline or failure"| tpl["Templates (templates.py)"]
    wf --> ins["Insight<br/>+ visuals, fun fact"]
    tpl --> ins
    q --> lore["Lore (lore.py)<br/>fun fact, storyteller agent"]
    lore --> ins
    ins --> store[("InsightStore<br/>memory / file / Cosmos")]
    state -.->|"snapshots"| sstore[("StateStore")]

    subgraph api["HTTP API (api.py)"]
        e1["/api/matches/{id}/events<br/>DVR relay (SSE)"]
        e2["/api/matches/{id}/stream<br/>insights per viewer (SSE)"]
        e3["/api/chat<br/>AG-UI (chat.py)"]
        e4["/ (the built web app)"]
    end
    log --> e1
    store --> e2
    log --> e3
    store --> e3
```

- **Event handling never waits for a model.** Moments go onto a per-match queue, and one writer per match keeps
  insights in order. If the agents fall behind, waiting filler (rolling summaries) is dropped so the overlay stays
  close to live.
- **Briefs freeze the facts at trigger time.** A slow model call can't see events that happened after the moment.
  The visuals a moment can show (a replay of the move, the xG race, the shot map...) are built then too, in code,
  so their numbers are always the real ones.
- **The analysts take the initiative.** When no insight has fired for 10 real seconds and 3 match minutes, a
  spotlight moment asks them to find a story in the data (the player running the game, possession without threat,
  a busy keeper...); code suggests the best one.
- **A fun fact for casual fans** about a player or club in the moment, from the league data, is picked while the
  analysts write; with Foundry, the storyteller agent retells it in five languages.
- **Restarts and redeliveries are safe.** Insight ids are deterministic (`<match>-<kind>-<seq>`), match state
  ignores events it has already seen, and state is snapshotted at every moment and every 100 events.

## 3. The multi-agent workflow

Each trigger runs one Microsoft Agent Framework workflow. The agents share one Foundry model deployment but each
has its own role and instructions.

```mermaid
flowchart LR
    b["Brief"] --> router{"Router"}
    router -->|"most moments"| analyst["Analyst<br/>what happened, why it matters<br/>+ MCP tools"]
    router -->|"full time"| recap["Recap writer<br/>story of the match<br/>+ MCP tools"]
    analyst --> fc{"Fact-checker<br/>+ MCP tools"}
    recap --> fc
    fc -->|"problems found,<br/>attempts left"| analyst
    fc -->|"problems found,<br/>attempts left"| recap
    fc -->|"approved, or out of attempts<br/>(published as unverified)"| narr["Narrator<br/>broadcast copy"]
    narr --> out["Insight<br/>headline, text, why it matters,<br/>stats, visuals, event ids, verified flag"]

    out -.->|"on demand, per viewer, cached"| pers["Personaliser<br/>audience, club, player<br/>(English)"]
    pers -.->|"viewer's language"| tr["Azure Translator<br/>NMT, names protected;<br/>LLM + informal tone for fans"]
```

| Agent | Job | Tools |
| --- | --- | --- |
| Analyst | What happened, how it was created, why it matters; cites event ids; picks and captions the visuals; on a spotlight, chooses the story | MCP |
| Recap writer | The full-time story, from the timeline of earlier insights | MCP |
| Fact-checker | Checks every claim, visual captions included; sends problems back to the author (up to 2 attempts) | MCP |
| Narrator | Broadcast copy from the approved analysis only | none |
| Personaliser | Rewrites per audience, favourite club and followed player, without new facts | none |
| Storyteller | Retells a league fact as a short "Did you know?" story for casual fans, in five languages | none |
| Azure Translator | Not an agent: translates the personalised English into the viewer's language, names protected | n/a |

Agents hand off typed Pydantic outputs, not free text. If any step fails or times out, a template insight is
published instead, so a goal is never missed because a model call was.

## 4. A goal, from kick to screen

```mermaid
sequenceDiagram
    participant G as Generator
    participant EH as Event Hubs
    participant A as Agents service
    participant F as Foundry model
    participant DB as Cosmos DB
    participant W as Web app

    G->>EH: shot (outcome = goal), seq 500
    EH->>A: via 'viewer' group, to the EventLog
    EH->>A: via 'analyst' group, to MatchState
    A->>W: relay SSE: event 500 (ball, banner, score)
    A->>A: TriggerEngine: goal moment, Brief as of seq 500
    A->>F: Analyst, then Fact-checker, then Narrator
    F-->>A: verified insight
    A->>DB: save insight
    A->>W: insights SSE
    W->>W: hold until playback reaches seq 500
    A->>F: Personaliser (Spanish, casual, follows the scorer)
    F-->>A: variant, cached on the insight
    W->>W: card and lower third on the pitch
```

The web app holds each insight until playback reaches its `seq`, so viewers replaying the match, or watching behind
live, are never spoiled.

## 5. Chat with the match assistant

```mermaid
sequenceDiagram
    participant U as Viewer
    participant W as Web app
    participant C as /api/chat (AG-UI)
    participant M as Model
    participant T as Bound tools

    U->>W: "Show me how the first goal happened"
    W->>C: messages + state (match, as_of 612, language, audience)
    C->>C: build the agent, tools bound to the match and as_of 612
    C->>M: question + tools
    M->>T: events(types = shot)
    T-->>M: only events up to seq 612
    M->>T: explain_moment(M0001-00500)
    Note over T: runs the analyst, fact-checker, narrator workflow
    T-->>M: verified explanation
    M-->>W: tool call seek_to(500), a frontend tool
    W->>W: replay from just before seq 500
    W->>C: tool result: "Replaying from 28'"
    C->>M: continue
    M-->>W: streamed answer
```

The chat speaks [AG-UI](https://docs.ag-ui.com) through Agent Framework's `agent-framework-ag-ui`. The viewer's
playback position is bound into the tools **on the server**, so the assistant can't look ahead whatever the model
asks. Frontend tools (`seek_to`, `focus_player`, `set_view`) are declared by the page and run in the browser, so the
assistant can act on what the viewer sees.

| Tool | Source |
| --- | --- |
| `match_state`, `events`, `player_stats`, `lookup`, `insights` | The relay log and the insights store, up to `as_of` |
| `season_query` | The MCP server, matchdays before this match only |
| `explain_moment` | Hands one moment to the analyst workflow and returns the verified explanation, with its visuals |
| `show_visual` | Draws a chart or a move replay under the answer (works offline too, by keyword) |
| `lore` | A player's or club's profile and fun facts, for casual fans |
| `seek_to`, `focus_player`, `set_view` | The web page |

## 6. Azure infrastructure

What `azd up` creates from [`infra/`](infra/):

```mermaid
flowchart TB
    subgraph rg["Resource group rg-ENV"]
        mi["User-assigned managed identity<br/>id-ff-*"]

        subgraph cae["Container Apps environment"]
            agentsApp["agents<br/>public HTTPS: web + API + chat<br/>1-4 replicas, scales on Event Hubs backlog"]
            mcpApp["mcp<br/>internal only, 1-3 replicas"]
        end

        acr["Container registry<br/>(images built in the cloud)"]
        ehns["Event Hubs namespace (Standard)<br/>hub match-events, 4 partitions<br/>groups: analyst, viewer, fabric"]
        st["Storage account<br/>blob container: checkpoints"]
        cos["Cosmos DB (serverless)<br/>insights, matchState, profiles,<br/>events, matchResults"]
        ai["Foundry: AI Services account<br/>project + model deployment"]
        mon["Log Analytics + App Insights"]
    end

    fabric["Microsoft Fabric (manual, optional)<br/>Eventstream, Eventhouse"]

    agentsApp -->|"MCP over HTTP (by app name)"| mcpApp
    agentsApp --> ehns
    agentsApp --> st
    agentsApp --> cos
    agentsApp --> ai
    mcpApp -->|"event archive"| cos
    mcpApp -.->|"if KUSTO_URI is set"| fabric
    ehns -->|"fabric-listen key"| fabric
    acr -->|"images"| cae
    cae --> mon

    mi -. "Event Hubs Data Receiver" .-> ehns
    mi -. "Storage Blob Data Contributor" .-> st
    mi -. "Cosmos DB Data Contributor" .-> cos
    mi -. "Azure AI User" .-> ai
    mi -. "AcrPull" .-> acr
```

- **Identity:** one user-assigned managed identity for both apps. There are no keys in code. Cosmos DB, Foundry and
  Storage have key authentication turned off. The single exception is a listen-only key on the event hub for Fabric
  Eventstream (set `fabricEventstreamAccess` to `false` to remove it).
- **Network:** the agents app is the only public endpoint. The MCP server is reachable only inside the Container Apps
  environment.
- **Scaling:** the agents app scales on the Event Hubs backlog. Each match stays on the replica that owns its
  partition, while every replica's relay reads the whole stream, so any replica can serve any viewer.
- **Observability:** container logs go to Log Analytics; Agent Framework's OpenTelemetry traces (each agent run and
  tool call) go to Application Insights.
- **Not in the templates:** Microsoft Fabric. It's optional and set up by hand ([fabric/README.md](fabric/README.md));
  without it, the MCP server reads the season data baked into its image.

### The MCP server's fallback chain

```mermaid
flowchart LR
    subgraph live["Live-match tools"]
        direction LR
        l1["Cosmos DB archive"] -->|"error, timeout<br/>or no data"| l2["Eventhouse"] -->|"..."| l3["Season files"]
    end
    subgraph season["Season queries"]
        direction LR
        s1["Eventhouse (KQL)"] -->|"error, timeout<br/>or no data"| s2["Cosmos DB match results"] -->|"..."| s3["Season files"]
    end
```

Each source gets a few seconds; a failure is logged and the next one answers. Match tools return a `source` field
saying which one did. The archive comes first for live matches because Eventstream ingestion can trail the stream by
seconds or minutes, while the agents write each event as it's analysed.

## 7. Packages in the repository

```mermaid
flowchart LR
    schema["packages/schema<br/>Event, Insight, stats"]
    gen["packages/generator<br/>engine, CLI, stream"]
    mcp["packages/mcp<br/>MCP tools"]
    agents["packages/agents<br/>service, agents, chat, API"]
    web["packages/web<br/>React app"]
    infra["infra/ (Bicep + azd)"]
    fab["fabric/ (KQL)"]

    gen --> schema
    mcp --> schema
    agents --> schema
    agents --> mcp
    web -. "HTTP: /api" .-> agents
    infra -. "deploys" .-> agents
    infra -. "deploys" .-> mcp
    mcp -. "queries" .-> fab
```

The four Python packages share one [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) and
lockfile. The web app is built with npm and copied into the agents image, which serves it.

## Design decisions

| Decision | Why |
| --- | --- |
| Event Hubs, not self-hosted Kafka | Fully managed, Entra ID sign-in, native Fabric Eventstream source, low cost at this scale |
| Container Apps, not AKS | Two services: built-in HTTPS, managed identity and KEDA scaling without operating a cluster |
| Trigger rules in code, agents only for explanations | ~1,900 events a match, but only ~15-20 moments worth explaining; fast, cheap and predictable |
| Facts frozen in a brief at trigger time | A slow model can't leak what happened after the moment |
| Fact-checker loop and template fallback | Explanations are checked against the data, and a failed model call never loses a moment |
| Tools bound to `as_of` on the server | Spoiler safety doesn't depend on the model following instructions |
| Web app served by the agents container | One origin for the page, API, SSE and chat: no CORS, one fewer Azure resource |
| Cosmos DB serverless | Insights, state snapshots and profiles are small JSON documents; pay per request |
| Events in both Cosmos DB and the Eventhouse | Each event is sent once and fanned out by Event Hubs. Cosmos DB (written by the agents) is current as events arrive and serves the MCP server's live-match tools; the Eventhouse serves analytics and season queries. Each is the other's fallback, so a Fabric outage or ingestion lag doesn't starve the agents |
| Personalise in English, then Azure Translator | The fact-checked English stays the source of truth: neural translation can't add facts, names are protected, it's fast and cheap; Translator's LLM option adds tone for casual fans. The personaliser still translates if Translator is unavailable |
| MCP server as a separate app | The same match-data tools serve the agents, the chat and any MCP client (Copilot, VS Code) |

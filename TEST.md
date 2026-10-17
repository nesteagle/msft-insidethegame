# Deploy and test FootballFoundry

How to test FootballFoundry on your machine, deploy it to Azure with `azd`, and check that everything works. The
commands are for PowerShell, run from the repository root. For what each part does, see [DESIGN.md](DESIGN.md).

- [1. Test locally (no Azure)](#1-test-locally-no-azure)
- [2. Before you deploy](#2-before-you-deploy)
- [3. Deploy](#3-deploy)
- [4. Smoke-test the deployment](#4-smoke-test-the-deployment)
- [5. Test the web app](#5-test-the-web-app)
- [6. Check the data landed](#6-check-the-data-landed)
- [7. Test the harder features](#7-test-the-harder-features)
- [8. Several testers at once](#8-several-testers-at-once)
- [9. Optional: Microsoft Fabric](#9-optional-microsoft-fabric)
- [10. Update after code changes](#10-update-after-code-changes)
- [11. Troubleshooting](#11-troubleshooting)
- [12. Costs and clean-up](#12-costs-and-clean-up)

## 1. Test locally (no Azure)

```powershell
uv sync
uv run pytest -m "not slow"                       # all packages, ~1 minute
uv run pytest                                     # also the two full-season calibration runs, ~3 minutes
uv run footballfoundry world                      # data/league.json: names for the agents and the web app
cd packages\web; npm install; npm run build; cd ..\..
```

Run the agents service offline (template insights, no LLM):

```powershell
uv run footballfoundry-agents serve --offline --store file
```

Open the **control room** at http://127.0.0.1:8100/control and start a stream, e.g. fixture M0015 at 30×. Press
**Watch** to open it at http://127.0.0.1:8100. Offline you still get the visuals, the casual fan's "Did you know?"
and spotlights (from templates), and the chat draws charts when you ask for one ("show me the xG race"). The control room also runs scenarios (with their ground truth), finds
dramatic matches and regenerates the season files.

The terminal way still works, in two terminals, either order:

```powershell
uv run footballfoundry-agents serve --offline --store file --follow data/stream.jsonl
uv run footballfoundry stream --match-id M0015 --speed 30 --sink file --out data/stream.jsonl
```

Optionally run the MCP server against the agents' archive and the season files:

```powershell
uv run footballfoundry season --parquet           # season files for the MCP server (~1 minute)
uv run footballfoundry-mcp --data data --archive data/agents/archive
```

## 2. Before you deploy

| Check | How |
| --- | --- |
| Azure subscription with **Owner**, or **Contributor + User Access Administrator** | The templates create role assignments; Contributor alone fails |
| Model available in your region | In the Foundry portal, check **gpt-6-luna** (version 2026-09-22, Global Standard) and at least 100K tokens/min of quota. East US 2 and Sweden Central are good bets |
| Tools | `winget install Microsoft.Azd`, `winget install Microsoft.AzureCLI`; `uv` and Node. Docker isn't needed: images build in Azure |

```powershell
az login
azd auth login
uv run pytest -m "not slow"
az bicep build --file infra/main.bicep --stdout > $null
```

## 3. Deploy

```powershell
azd env new ff-dev                                # any name; becomes the resource group rg-ff-dev
azd env set AZURE_LOCATION eastus2                # a region with the model and quota
# optional:
# azd env set FOUNDRY_MODEL_NAME gpt-6-luna
# azd env set FOUNDRY_MODEL_VERSION 2026-09-22
# azd env set FOUNDRY_MODEL_CAPACITY 100                # thousands of tokens per minute; raise it for several matches at once
# azd env set TRANSLATOR_LLM_DEPLOYMENT <deployment>   # Translator's LLM option for casual fans
# turn on the control room (/control) with a random key; share it with testers:
azd env set CONTROL_KEY ([guid]::NewGuid().ToString("N"))
azd provision --preview                           # optional dry run: what will be created
azd up
```

`azd up` asks for a subscription the first time and takes 15–25 minutes: Cosmos DB and the Foundry account are the
slowest, and the MCP image simulates a season while it builds. It prints `AGENTS_URL` at the end.

Load the outputs into your shell, so local commands talk to Azure:

```powershell
azd env get-values | ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "env:$k" $v.Trim('"') }
$env:AGENTS_URL; $env:EVENTHUB_NAMESPACE
```

**Wait about 5 minutes after the first deploy**: new role assignments take a few minutes to apply, and until they
do the agents can't read Event Hubs or Cosmos DB.

## 4. Smoke-test the deployment

**The service is up:**

```powershell
curl "$env:AGENTS_URL/healthz"                    # {"ok": true, "matches": 0}
az containerapp list -g $env:AZURE_RESOURCE_GROUP -o table   # ca-agents-* and ca-mcp-*, both Running
```

**Startup logs:**

```powershell
$agents = az containerapp list -g $env:AZURE_RESOURCE_GROUP --query "[?contains(name,'agents')].name" -o tsv
$mcp    = az containerapp list -g $env:AZURE_RESOURCE_GROUP --query "[?contains(name,'mcp')].name" -o tsv
az containerapp logs show -n $agents -g $env:AZURE_RESOURCE_GROUP --tail 50
az containerapp logs show -n $mcp -g $env:AZURE_RESOURCE_GROUP --tail 50
```

| Container | Look for | Meaning |
| --- | --- | --- |
| agents | `agents: Foundry model gpt-6-luna, MCP http://ca-mcp-…/mcp` | Real agents, not offline mode |
| agents | `translation: Azure Translator (NMT)` | Translator configured |
| agents | `source: eventhub, store: cosmos, web: /app/web` | Reading Event Hubs, using Cosmos DB, serving the web app |
| mcp | `live tools: archive -> files; season queries: archive -> files` | The fallback chain (`eventhouse` appears once Fabric is added) |

**Stream a match** (M0015 is a good demo: Eldermere are 2-0 down after 37 minutes and win 2-3 with goals at 90+1' and 90+5'). The easiest way is to
open `$env:AGENTS_URL/control` and enter the key (`azd env get-value CONTROL_KEY`). Pick fixture M0015 at 30× and press
**Start**: the events go through Event Hubs like any other stream. Or stream from your machine:

```powershell
uv run footballfoundry stream --match-id M0015 --speed 30 --sink eventhub
uv run footballfoundry listen                     # second terminal: events arriving
```

## 5. Test the web app

Open `AGENTS_URL` in a browser.

| Do | Expect |
| --- | --- |
| Open `/control`, enter the key | The control room: start a stream, watch its progress, stop it |
| Open the page | M0015 marked "● live"; players moving; score updating |
| Kick-off | A **Kick-off** card previewing the match: both teams' shapes, managers and styles |
| Watch a minute | Insight cards for goals, chances and summaries; agent-written text, many with the **fact-checked** tick |
| A goal goes in | A goal banner, then a lower third over the pitch; the card shows **The move**, an animated replay of the goal |
| Half-time, then full-time | The xG race and a head-to-head; at full time a **Goals reel** (every goal in turn), the xG race and a shot map |
| A quiet spell (no insight for ~10 s at 30×) | A **Spotlight** card: a story the analysts found, e.g. who's running the game, with a chart |
| **View for = Casual fan** | A "Did you know?" on each card about a player or club in the moment; with Foundry, many say "Told by the storyteller agent" |
| Casual fan + **Español** | The "Did you know?" in Spanish too |
| **View for = Analyst** | Insight text becomes more tactical and number-heavy |
| **Language = Español** | Interface in Spanish; insights translated, with "Traducido con Azure Translator" |
| **Follow player** | A yellow ring on that player; their insights highlighted |
| ↺ on an insight | Replays from just before that moment; later insights hide until playback reaches them |
| Click a minute on the momentum strip | Replays from that minute |
| Chat: "How did the first goal come about?" | A streamed answer about only what you've seen, with notes like "Checked events" |
| Chat: "Show me that again" | The pitch replays the moment (a frontend tool) |
| Chat: "Show me the xG race" | The chart drawn under the answer |
| Chat (casual fan): "Tell me about the scorer" | The answer can include a fun fact from the lore tool |
| Chat: "How are both teams doing this season?" | An answer from season data, through the MCP server |
| **Season** tab while M0015 plays | The table after matchday 1, with a note that matchday 2 is live; **Show the full season** shows all 38 |
| Season → Stats → a leader | The player's profile: season totals, physical data, match by match |
| Season → Players: search, filter, sort, per 90 | The list follows; reload keeps the view |

## 6. Check the data landed

| Where | How | Expect |
| --- | --- | --- |
| Cosmos DB | Portal → Cosmos DB account → **Data Explorer** (key auth is off; your account has the data role) | `insights` (with cached viewer variants), `events` (~1,900 documents for M0015), `matchResults` (one after full time), `matchState`, `profiles` |
| Checkpoints | Portal → Storage account → container `checkpoints` | A blob per partition for the `analyst` consumer group |
| Traces | Portal → Application Insights → **Transaction search** or **Performance** | Agent runs and tool calls from Agent Framework |
| Foundry | Foundry portal → the project → the model deployment → metrics | Token usage rising during the match |

## 7. Test the harder features

**Scenario with ground truth**: in the control room, pick fixture M0001 and scenario **winger-central**, then start
it. **Ground truth** on the stream shows what was scripted. Or from the terminal (the truth file is written
locally):

```powershell
uv run footballfoundry stream --match-id M0001 --speed 30 --sink eventhub --scenario scenarios/winger-central.json
Get-Content data/truth/M0001.json
```

Expect a "tactical change" insight around 62'; compare the agents' explanations with the truth file.

**Several matches at once:**

```powershell
uv run footballfoundry stream --matchday 1 --speed 30 --sink eventhub
az containerapp replica list -n $agents -g $env:AZURE_RESOURCE_GROUP -o table   # one replica, by design
```

Ten matches at once call the model ten times as often. If the deployment's tokens-per-minute quota runs out, the
agents fall back to template insights (see [Troubleshooting](#11-troubleshooting)); for agent-written insights
throughout, stream one to three matches at a time or raise the quota.

**Restart recovery:** restart the agents in the middle of a match.

```powershell
az containerapp revision restart -n $agents -g $env:AZURE_RESOURCE_GROUP `
  --revision (az containerapp revision list -n $agents -g $env:AZURE_RESOURCE_GROUP --query "[0].name" -o tsv)
```

Expect a "resumed from snapshot" log line, insights carrying on without duplicates, and the page catching up.

**Find more demo matches:** use the control room's story finder (pick stories, **Find**, then **Stream it**), or:

```powershell
uv run footballfoundry find --story late-winner --story red-card
```

## 8. Several testers at once

Everyone can stream into the same Event Hub. Each `footballfoundry stream` run stamps its events with a
`stream_id`:

- **Different matches**: no interference. Each match is handled on its own, and all of them show in the web app.
- **The same match id**: the **first stream keeps the match**. Events from a second stream of that match are ignored
  while the first is live, with a warning in the agents' logs (`… is already being streamed …`). Once the first
  stream finishes, or goes quiet for a minute (20 minutes at half-time), the next one can take it over.
- **One-off matches** (`--home/--away`) get a fresh id per run (printed, e.g. `XECDF`), so testers don't collide.
  Pass `--match-id` to replay a specific one.
- Streaming with no `--match-id` uses fixture M0001 and prints a reminder. Pick your own fixture, e.g.
  `--match-id M0123`, or a matchday.

To check it: two people stream `--match-id M0005` a minute apart. The page shows one clean match, and the agents'
logs show the second stream being ignored.

## 9. Optional: Microsoft Fabric

Follow [fabric/README.md](fabric/README.md):

1. Create the Eventhouse, run `tables.kql` and `functions.kql`, and load the fixtures.
2. Create the Eventstream: source Event Hubs `match-events`, consumer group `fabric`, the `fabric-listen` key.
3. Give `id-ff-*` the Viewer role on the KQL database.
4. Point the MCP server at it:

```powershell
azd env set KUSTO_URI <query URI>
azd provision
```

Expect the MCP log to show `season queries: eventhouse -> archive -> files`.

## 10. Update after code changes

```powershell
azd deploy                 # rebuild and redeploy the containers (~5–10 minutes)
azd deploy agents          # one service only
azd provision              # after infra changes (Bicep, environment settings)
```

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `azd up` fails on the model deployment | Model or version not in the region, or no quota | Another region, or `azd env set FOUNDRY_MODEL_NAME` / `FOUNDRY_MODEL_VERSION`, then `azd up` |
| `azd up` fails on role assignments | Not Owner / User Access Administrator | Get the role, then rerun |
| Agents log 401/403 for Event Hubs, Cosmos DB or Foundry | Role assignments still propagating | Wait 5–10 minutes, then restart the revision |
| `stream --sink eventhub` fails with 401 | Your role isn't active yet, or `EVENTHUB_NAMESPACE` isn't loaded | Wait a few minutes; rerun the `azd env get-values` line |
| The match plays but no insights appear | Agents in offline mode, or Foundry calls failing | Agents logs: `FOUNDRY_PROJECT_ENDPOINT not set`, or Foundry errors |
| Insights stay English in Spanish | Translator not configured, or its role is missing | Agents logs: the `translation:` line, 403s from Translator |
| Chat answers "running offline" | No Foundry client | As for missing insights |
| A match you streamed doesn't appear | Someone else is streaming the same match id | Agents logs: `already being streamed`; use another `--match-id` |
| `AGENTS_URL` doesn't load, `/healthz` fails | The container apps were stopped to save money | Start them ([12. Costs and clean-up](#12-costs-and-clean-up)) |
| Many insights are unverified and read like templates ("GOAL 16' Michael Donnelly (Upperlee United)") | The agent workflow failed or timed out and the template stood in. Usually the model's tokens-per-minute quota, with several matches at once | Agents logs: `workflow failed, using template`, HTTP 429. Stream fewer matches at once, or raise the quota: `azd env set FOUNDRY_MODEL_CAPACITY 300` (thousands of tokens per minute, within your subscription's quota), then `azd provision` |
| No Spotlight cards | The stream runs very fast (a spotlight needs 10 real seconds without an insight), or every story has been told | Stream at 30× or slower |
| Old insights have no charts or "Did you know?" | They were written before the update; stored insights aren't rewritten | Stream the match again from the control room |
| Old matches stuck in the dropdown, "(no new events)" | A stream stopped partway | Ignore them, or restart the agents revision |
| `/control` says the control room is off | No `CONTROL_KEY` on the deployment | `azd env set CONTROL_KEY <key>`, then `azd provision` |
| A control-room stream to Event Hubs fails with 401 | The app's Data Sender role is still propagating | Wait 5–10 minutes and start it again |

## 12. Costs and clean-up

While deployed, the always-on parts cost roughly **$3–5 a day** at list prices:

| Resource | Roughly, per day |
| --- | --- |
| Container Apps: agents (1 vCPU, 2 GiB) and MCP (0.5 vCPU, 1 GiB), one replica each | $2–3.50 (the agents count as active, since they read Event Hubs all the time; the MCP server is mostly idle) |
| Event Hubs Standard, 1 throughput unit | ~$0.75 |
| Container Registry Basic | ~$0.17 |
| Log Analytics / Application Insights | Under the free 5 GB a month for light testing |

Cosmos DB (serverless), Storage, Translator and the model's tokens are pay-per-use: close to nothing while idle.
Each match streamed with the Foundry agents uses very roughly 0.5–1 million tokens (the insights, their viewer
variants, the storyteller and the chat); check your deployment's price per million in the Foundry portal. Real
spend: **Cost Management** in the portal, filtered to the resource group, a day behind.

**Stop the container apps when you're not testing.** It stops their compute charges, which are most of the bill;
a stopped deployment costs about $1 a day (Event Hubs and the registry can't be stopped). The Azure CLI has no
`az containerapp stop`, so these call the Container Apps Stop and Start operations through `az rest`:

```powershell
$rg  = azd env get-value AZURE_RESOURCE_GROUP      # or: $rg = "rg-<your env name>"
$ids = az containerapp list -g $rg --query "[].id" -o tsv

# Stop: the web app and API go offline; data in Cosmos DB is kept
foreach ($id in $ids) { az rest --method post --url "https://management.azure.com$id/stop?api-version=2024-03-01" }

# Start again (give it a minute, then check $env:AGENTS_URL/healthz)
foreach ($id in $ids) { az rest --method post --url "https://management.azure.com$id/start?api-version=2024-03-01" }

# Which state they're in
az containerapp list -g $rg --query "[].{name:name, status:properties.runningStatus}" -o table
```

Each app's **Overview** page in the portal has the same **Stop** and **Start** buttons. Start the apps before an
`azd deploy`. Events streamed while they were stopped stay in Event Hubs for a day, and the agents resume from their
checkpoint.

**Delete everything** to bring the cost to zero. `azd up` rebuilds it in 15–25 minutes, without the stored
insights and match history (the season files are rebuilt with the images):

```powershell
azd down --purge           # deletes everything, and purges the soft-deleted Foundry account
```

Judges need a working link until **November 10, 2026**: deploy the final version, keep it running, and put
`AGENTS_URL` in the submission.

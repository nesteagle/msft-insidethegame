# Infrastructure

Bicep templates for everything FootballFoundry runs on in Azure, deployed with the
[Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/) from the repository root.

## Deploy

```bash
azd auth login
azd env new footballfoundry-dev          # pick a region where the model below is available
azd env set FOUNDRY_MODEL_NAME gpt-6-luna       # optional; see "Model" below
azd up                                   # provision, then build and deploy the containers
```

The images are built in the container registry (`remoteBuild` in [`azure.yaml`](../azure.yaml)), so Docker doesn't
need to run on your machine.

Then stream into it:

```bash
set -a; eval "$(azd env get-values)"; set +a        # bash: export the outputs as environment variables
footballfoundry stream --matchday 1 --speed 10 --sink eventhub
curl -N "$AGENTS_URL/api/matches/M0001/stream?audience=casual&lang=es"
```

```powershell
azd env get-values | ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "env:$k" $v.Trim('"') }   # PowerShell
footballfoundry stream --matchday 1 --speed 10 --sink eventhub
```

The output names (`EVENTHUB_NAMESPACE`, `FOUNDRY_PROJECT_ENDPOINT`, `COSMOS_ENDPOINT`, ...) are the variables every
command reads, so with them exported the agents and MCP server also run locally against the Azure resources.
Open `AGENTS_URL` in a browser for the web app.

## What gets created

| Resource | Details |
| --- | --- |
| Event Hubs namespace (Standard) | Event hub `match-events` (4 partitions, 1-day retention), consumer groups `analyst` (agents), `viewer` (web relay) and `fabric` |
| Storage account | Blob container `checkpoints` for the agents' Event Hubs read positions; shared keys disabled |
| Cosmos DB for NoSQL (serverless) | Database `footballfoundry`, containers below; key auth disabled |
| Microsoft Foundry | AI Services account, project `footballfoundry`, one model deployment; its endpoint also serves Azure Translator |
| Container registry | Images pulled with the managed identity; no admin user |
| Container Apps | `agents` (public: the web app, its API and the chat; scales 1-4 on Event Hubs backlog), `mcp` (internal only) |
| Log Analytics + Application Insights | Container logs, and the OpenTelemetry traces Agent Framework emits per agent and tool call |
| User-assigned managed identity | Every app signs in with it; role assignments below |

| Cosmos DB container | Partition key | One document per |
| --- | --- | --- |
| `insights` | `/matchId` | Insight, with its cached viewer variants |
| `matchState` | `/matchId` | Match: the latest state snapshot |
| `profiles` | `/userId` | Viewer: audience, language, favourite clubs and players, focus player |
| `events` | `/matchId` | Event (id = event_id), written by the agents as each is analysed; expires after 30 days |
| `matchResults` | `/matchId` | Finished match: score, and each player's shots, goals and xG |

The MCP server reads `events` first for live-match tools (current even while the Eventhouse is still ingesting) and
`matchResults` as the fallback for season queries when the Eventhouse is unavailable.

**Roles.** The apps' identity gets Event Hubs Data Receiver, Storage Blob Data Contributor (checkpoints), Azure AI
User (Foundry), Cognitive Services User (Translator), AcrPull, and the Cosmos DB Built-in Data Contributor. You (`AZURE_PRINCIPAL_ID`, set by azd) get
Event Hubs Data Owner, Storage Blob Data Contributor, Azure AI User and the Cosmos DB data role, so the CLI, agents
and MCP server also run on your machine against the same resources.

**Keys.** None in the apps. The one exception is `fabric-listen`, a listen-only SAS rule on `match-events` for Fabric
Eventstream, whose Event Hubs source signs in with a key. Set `fabricEventstreamAccess` to `false` to remove it
and disable keys on the namespace entirely.

## Model

The agents use one deployment, `gpt-6-luna` version `2026-09-22` by default, Global Standard, 100K tokens per
minute. Override with `azd env set FOUNDRY_MODEL_NAME <name>` and `FOUNDRY_MODEL_VERSION <version>`. Check that the
model is available in your region and that your subscription has quota before `azd up`.

## Translation

Insights are translated by Azure Translator through the Foundry account's endpoint (`TRANSLATOR_ENDPOINT`), with
neural machine translation by default. To use Translator's LLM option for casual fans (informal tone), set a
deployment Translator supports for LLM translation, then provision:

```bash
azd env set TRANSLATOR_LLM_DEPLOYMENT <deployment name>
azd provision
```

## Control room

The web app's `/control` page lets testers start and stop streams into Event Hubs. It's off in Azure until you set
a key, which the page then asks for. The key is stored as a Container Apps secret:

```bash
azd env set CONTROL_KEY <a long random string>
azd provision
```

## Eventhouse

Fabric isn't deployed by these templates. Set it up with [fabric/README.md](../fabric/README.md), then point the MCP
server at it and redeploy:

```bash
azd env set KUSTO_URI https://<eventhouse>.kusto.fabric.microsoft.com
azd provision
```

Give the managed identity (`id-ff-*`) Viewer access to the KQL database, so the MCP server can query it.

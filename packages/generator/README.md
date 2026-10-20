# Football Foundry

FootballFoundry generates **synthetic, football-realistic** match data for the *Inside the Game* hackathon:
a fictional league of 20 clubs and 520 players, a 380-match fixture list, and ~1,900 on-ball events per match
(passes, carries, take-ons, shots with xG, tackles, fouls, cards, substitutions…).

No real competition, club or player data is used. Club names, stadiums and players are invented.

 In football, a "fixture" is a match on the league's schedule: two clubs, a matchday and a kick-off time. Premier League fans use the word all the time ("this weekend's fixtures").

In the control room, the three options mean:

| Option       | What it streams                                                                                                                                                                                            |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fixture**  | One match from the season's schedule (M0001–M0380), such as M0015 Halbury v Eldermere on matchday 2. It plays out the same every time, matches the Season page, and the agents' season data knows about it |
| **Matchday** | All 10 fixtures of one matchday at once, like a Saturday afternoon                                                                                                                                         |
| **One-off**  | Any two clubs you pick, outside the schedule, with a new id each time (such as XECDF). It isn't in the table or the season stats                                                                           |

## Run it offline (no Azure)

Create the python virtual environment and active it first.

You can run everything on your machine without Azure. In PowerShell, from the repository root:

**One-time setup** (and again after pulling changes):

```powershell
uv sync                                                   # Python packages
uv run footballfoundry world                              # data/league.json: club and player names
cd packages\web; npm install; npm run build; cd ..\..     # the web app
uv run footballfoundry book                               # data/season-book.json for the Season page (~30 s)
```

The `book` step is optional. Without it, your first visit to the Season page builds the file itself, which takes about 1.5 minutes.

**Start it:**

```powershell
uv run footballfoundry-agents serve --offline
```

Then open these in the browser:

|Page|URL|
|---|---|
|Control room: start and stop streams|[http://127.0.0.1:8100/control](http://127.0.0.1:8100/control)|
|Live match|[http://127.0.0.1:8100](http://127.0.0.1:8100/)|
|Season: table, stats, players|[http://127.0.0.1:8100/season](http://127.0.0.1:8100/season)|

**Try it:** in the control room, choose fixture **M0015** at **30×** and press **Start**, then **Watch**. Open the **Season** tab while it plays: it shows the table after matchday 1, because matchday 2 is live. **Show the full season** shows all 38 matchdays.

```powershell
uv sync                                           # create .venv with every package
.\.venv\Scripts\Activate.ps1                       # optional: then drop the "uv run" prefix
uv run footballfoundry world                      # data/league.json: club and player names
cd packages\web; npm install; npm run build; cd ..\..   # the web app, served by the agents service
```

Start the local host, then open the **control room** at http://127.0.0.1:8100/control. From the browser you can
start and stop streams (a fixture, a matchday or a one-off match, at any speed, with a scenario if you like), find
dramatic matches and regenerate the season files:

```powershell
uv run footballfoundry-agents serve --offline
```

Then watch at http://127.0.0.1:8100. Prefer the terminal? Stream from a second one instead (either order):

```powershell
uv run footballfoundry-agents serve --offline --follow data/stream.jsonl
uv run footballfoundry stream --matchday 1 --speed 30 --sink file --out data/stream.jsonl
```

To regenerate the season files from the terminal (about a minute, overwriting the old ones), which the MCP server
reads:

```powershell
uv run footballfoundry season --parquet
```

**Deploy to Azure and test everything:** [TEST.md](TEST.md) has the full checklist, from `azd up` to checking the
agents, translation, Cosmos DB and the web app. How it all fits together: [DESIGN.md](DESIGN.md).

## Repository layout

The solution is one repository with a separate project for each piece. The Python packages share one
[uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/), so they install together with one lockfile
but build, test and deploy on their own. [DESIGN.md](DESIGN.md) explains the architecture with diagrams.

| Path | Project | What it is |
| --- | --- | --- |
| [`packages/schema/`](packages/schema/) | `footballfoundry-schema` | Pydantic event and world models: the contract between every other piece |
| [`packages/generator/`](packages/generator/) | `footballfoundry` | Match engine, CLI, browser viewer, and the Event Hubs producer (`stream`, `listen`) |
| [`packages/agents/`](packages/agents/) | `footballfoundry-agents` | Event Hubs consumer, trigger rules, the Agent Framework multi-agent workflow, the match assistant chat (AG-UI), the event relay and the API; serves the web app |
| [`packages/web/`](packages/web/) | `footballfoundry-web` | React + TypeScript web app: live match, personalised insights, chat with the match assistant |
| [`packages/mcp/`](packages/mcp/) | `footballfoundry-mcp` | MCP server exposing match-data tools (live state, events, player stats, season queries) |
| [`infra/`](infra/) | Bicep + `azd` | Event Hubs, Container Apps, Cosmos DB, Foundry project and model, managed identity and roles |
| [`fabric/`](fabric/) | KQL + setup guide | Eventhouse tables and the KQL functions behind the season queries; Eventstream setup |

```text
footballfoundry stream ──► Azure Event Hubs (match-events)
                              ├─ consumer group "viewer"  ──► packages/agents relay ──► web app (pitch, DVR)
                              ├─ consumer group "analyst" ──► packages/agents ──► Foundry agents ──► web app (insights, chat)
                              │                                   │      ▲  tools
                              │                                   │      └── packages/mcp ──► Eventhouse, match state
                              │                                   └─► Cosmos DB (profiles, match state, insights, event archive)
                              └─ consumer group "fabric"  ──► Eventstream ──► Eventhouse (KQL) ──► dashboards
```

| Store | Holds | Used for |
| --- | --- | --- |
| Event Hubs | The live event stream | Ingest |
| Fabric Eventhouse | Every event, season stats | Analytics, milestones and dashboards, queried through the MCP tools |
| Cosmos DB | Viewer profiles, match state snapshots, the agent insights timeline, and the event archive (every event, a result per match) | Personalization, recovery after a restart, overlays, recaps; the MCP server's live-match data, and its fallback when Fabric is unavailable |

## Quick start

```bash
uv sync                                              # every package, plus the test and Azure/Parquet extras

footballfoundry match --home T01 --away T20          # one match, prints a box score
footballfoundry season --parquet                     # full season -> ./data (JSONL + Parquet + season book)
footballfoundry book                                 # only data/season-book.json, for the web app's season pages
footballfoundry stream --speed 30                    # replay a match live (~3 min) as JSON lines
footballfoundry serve --open                         # watch matches play out in the browser
footballfoundry calibrate                            # realism check, exits 1 if any metric is out of range
pytest                                               # all packages: unit + calibration tests (~1 min)
pytest -m "not slow"                                 # skip the 380-match calibration runs
```

Run the commands from the repository root. Activate the environment first (`.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` elsewhere), or prefix each command with `uv run`.

To install only the generator somewhere else, without the workspace: `pip install ./packages/schema ./packages/generator`.

Everything is deterministic: the same `--seed` produces the same league, fixtures and events, on any
machine and with any number of worker processes.

## Match viewer

`footballfoundry serve` opens a live 2D match view in your browser. It shows the ball and all 22 players on the pitch,
a scoreboard, live stats, a commentary-style feed and a momentum strip.

![Match viewer mid-match](docs/viewer.png)

```bash
footballfoundry serve --open                      # matchday 1 (10 matches, pick one from the menu) at 10x
footballfoundry serve --match-id M0042 --speed 30 # a single fixture
footballfoundry serve --home T01 --away T20       # a one-off match
```

The viewer runs at `http://127.0.0.1:8000` (change it with `--port`). It needs no build step or extra packages:
plain HTML, CSS and JavaScript in [`packages/generator/src/footballfoundry/viewer/`](packages/generator/src/footballfoundry/viewer/), served by Python's standard library.

| Part | What it shows |
| --- | --- |
| Pitch | The ball moves along each pass, carry and shot. The player on the ball is outlined, and teammates and opponents shift with the play. Shots and long balls leave a fading trail. A yellow ring marks a booked player, and dashed outlines mark the goalkeepers. |
| Scoreboard | The score, match clock (including added time like `45+2'`), period, and each club's kit colours |
| Match stats | Possession, shots, shots on target, xG, passes, pass accuracy, corners, fouls, offsides and cards, updated with every event |
| Live feed | Key moments (shots, cards, substitutions, offsides, kick-off and full-time) or every event, in plain English |
| Momentum | Attacking threat per minute for each team: passes into the final third and the box, carries into the box, and shots. Goals are marked. Hover a minute for details, or click it to replay the match from that point. |

The controls are a match menu, play/pause, restart, and speed from 1× (real time) to 120×. Changing speed or
pausing keeps your place in the match.

Home and away use a colour-blind-safe blue/orange pair in every chart and on the pitch. Text never relies on colour
alone. The page follows your system's light or dark setting.

How it works: the browser opens a [Server-Sent Events](https://developer.mozilla.org/docs/Web/API/Server-sent_events)
connection to `/api/events?match=M0001&speed=10&from=0`. The server replays everything before `from` (in match-clock
seconds) at once, then streams the rest at the requested speed, using the same pacing as `footballfoundry stream`.
`/api/session` provides clubs, players and lineups, but never the final score. In the browser, live events wait in a short
buffer (about 4 match seconds, between 0.25 s and 4 s of real time) and are played on a continuous match clock at
60 fps. The ball travels each pass, shot or carry at a believable speed and then moves on to where the next action
starts. Players move with a damped spring capped at sprint speed, so nobody teleports. With *reduce motion* turned on
in your operating system, positions update without animation.

## Real-time event streaming

`footballfoundry stream` turns the generator into a live feed. Matches are simulated up front (about 0.2 s each), then
each event is emitted when it happens on the match clock: a goal at 23:14 arrives 23 min 14 s after kick-off
at `--speed 1`, or after 2 min 19 s at `--speed 10`. The half-time break is included (15 match minutes by default).

### Choose what to stream

```bash
footballfoundry stream                                # fixture M0001 (the default)
footballfoundry stream --match-id M0042               # any fixture, M0001–M0380
footballfoundry stream --matchday 1                   # all 10 fixtures of a matchday, kicking off together
footballfoundry stream --home T03 --away T11          # a one-off match between any two clubs (T01–T20)
footballfoundry stream --file data/events/M0007.jsonl # replay a file written by `footballfoundry season`
```

The club and player reference data (names, squads, attributes) for these ids is in `data/league.json`. Run
`footballfoundry world` to write it without simulating a season.

### Control the pace

| `--speed` | One match (≈ 97 min + 15 min half-time) takes | Good for |
| --- | --- | --- |
| `1` | ≈ 1 h 52 min | a realistic live broadcast |
| `10` | ≈ 11 min | development with an agent in the loop |
| `30` | ≈ 4 min | the demo video |
| `0` | instant | loading data, tests |

Use `--halftime 10` to shorten the break to 10 real seconds.

### Choose where it goes

| `--sink` | Output |
| --- | --- |
| `stdout` (default) | one JSON event per line. Goals, red cards and period changes are also printed to stderr, so stdout stays clean JSONL for piping (add `--quiet` to hide them). |
| `file` | the same JSONL written to `--out` (default `data/stream.jsonl`) |
| `eventhub` | Azure Event Hubs, one message per event. See below. |

Pipe the stream straight into your own consumer:

```bash
footballfoundry stream --speed 10 | python my_live_analyst.py
```

```python
# my_live_analyst.py
import sys
from footballfoundry_schema import Event

for line in sys.stdin:
    e = Event.model_validate_json(line)
    if e.type == "shot":
        print(f"{e.clock} shot by {e.player_id}: {e.outcome} (xG {e.xg})")
```

### Stream to Azure Event Hubs

**1. Create the Event Hub (one time).** `azd up` creates it with everything else (see [infra](infra/README.md)).
To create only the Event Hub, run this in PowerShell:

```powershell
$rg  = "rg-insidethegame"
$loc = "eastus2"                                         # same region as your Foundry project
$ns  = "ehns-insidethegame-$(Get-Random -Maximum 99999)" # must be globally unique

az group create -n $rg -l $loc
az eventhubs namespace create -g $rg -n $ns -l $loc --sku Standard
az eventhubs eventhub create -g $rg --namespace-name $ns -n match-events --partition-count 2
az eventhubs eventhub consumer-group create -g $rg --namespace-name $ns --eventhub-name match-events -n analyst
az eventhubs eventhub consumer-group create -g $rg --namespace-name $ns --eventhub-name match-events -n fabric

# Allow your account to send and receive with Microsoft Entra ID (no keys in code)
$me   = az ad signed-in-user show --query id -o tsv
$nsId = az eventhubs namespace show -g $rg -n $ns --query id -o tsv
az role assignment create --assignee $me --role "Azure Event Hubs Data Owner" --scope $nsId
"EVENTHUB_NAMESPACE=$ns"
```

Use the Standard tier, not Basic, because Basic allows only the `$Default` consumer group. Role assignments can
take a few minutes to take effect.

**2. Install the Azure extras and sign in:**

```powershell
uv sync        # already includes the azure extra; outside the workspace: pip install "./packages/generator[azure]"
az login
$env:EVENTHUB_NAMESPACE = "<your namespace name>"   # e.g. ehns-insidethegame-12345
```

**3. Stream:**

```powershell
footballfoundry stream --matchday 1 --speed 10 --sink eventhub
```

**4. Check it arrives**, in a second terminal:

```powershell
footballfoundry listen                               # goals, red cards, kick-off / half-time / full-time
footballfoundry listen --all                         # every event as JSON
footballfoundry listen --consumer-group analyst --from-start
```

Authentication uses `DefaultAzureCredential` (your `az login` locally, or a managed identity when deployed). If you
use a connection string instead, set `EVENTHUB_CONNECTION_STRING` and leave out the namespace. `--eventhub-name`
defaults to `match-events`, or to `$EVENTHUB_NAME` if it's set.

**Message format.** Each Event Hubs message body is one [`Event`](packages/schema/src/footballfoundry_schema/__init__.py) as JSON, with
`content_type: application/json` and the application properties `match_id` and `type`. You can filter on these
without parsing the body. The partition key is `match_id`, so every event of a match lands on the same partition,
in order.

**Consume it in your own code:**

```python
from azure.eventhub import EventHubConsumerClient
from azure.identity import DefaultAzureCredential
from footballfoundry_schema import Event

client = EventHubConsumerClient(
    fully_qualified_namespace="<namespace>.servicebus.windows.net",
    eventhub_name="match-events",
    consumer_group="analyst",
    credential=DefaultAzureCredential(),
)

def on_event(partition_context, event):
    e = Event.model_validate_json(event.body_as_str())
    ...  # update match state, trigger the Foundry agent on goals / big chances, etc.

with client:
    client.receive(on_event=on_event, starting_position="@latest")
```

For Microsoft Fabric, add an **Eventstream** with the event hub as its source (consumer group `fabric`) and route it
to an Eventhouse (KQL database). The flat event schema maps directly to table columns.

### Things to know

- **The stream contains only events.** Load the clubs, players and lineups from `league.json` and
  `matches/<id>.json`. Don't give an agent `matches/<id>.json` during a live match: it includes the final score.
- **A matchday kicks off together.** `--matchday` starts all its fixtures at once, regardless of their scheduled
  kick-off times. That's convenient for a multi-match demo.
- **Ctrl+C stops the stream cleanly.** Messages already sent stay in Event Hubs for the retention period.
- **Several people can stream at once.** Each run stamps its events with a `stream_id` (printed at the start; set
  your own with `--stream-id`). Different matches never interfere. If two runs stream the **same match id**, the
  first keeps the match while it's live and the agents ignore the second, with a warning in their logs; once the
  first finishes or goes quiet for a minute (20 minutes at half-time), another stream can take over. One-off matches
  (`--home/--away`) get a fresh id per run, such as `XECDF`, so testers don't collide; pass `--match-id` to replay
  one. With no `--match-id`, `stream` uses fixture M0001 and says so.

## Match intelligence agents

[`footballfoundry-agents`](packages/agents/) turns the live stream into **insights**: timed, machine-readable
explanations of the moments that matter (a kick-off preview with the season so far, players to watch, goals,
red cards, big chances, momentum swings,
rolling summaries, half-time and full-time), each personalised per viewer by audience, language, favourite club and focus player, and
translated by Azure Translator. Insights carry charts and animated replays of the move (a goals reel at full time),
casual fans get a "Did you know?" about a player or club in the moment, and when the match goes quiet the analysts
take the initiative with a **spotlight**: a story they found in the data themselves.

```text
event ─► match state ─► trigger rules ─► analyst / recap writer ─► fact-checker ─► narrator ─► insight
                                              ▲      MCP tools       │ (sends problems back)        │
                                              └──────────────────────┘                    personaliser, per viewer
```

Run it locally without Azure (template insights, no LLM), then open the web app on http://127.0.0.1:8100:

```bash
footballfoundry world                                                        # data/league.json for names
(cd packages/web && npm install && npm run build)                            # the web app, served by the agents
footballfoundry-agents serve --offline              # then stream from http://127.0.0.1:8100/control
# Or pipe a stream in
footballfoundry stream --matchday 1 --speed 30 --quiet | footballfoundry-agents serve --offline
# Or in two terminals, started in either order: the stream writes a file, the service follows it
footballfoundry-agents serve --offline --follow data/stream.jsonl
footballfoundry stream --matchday 1 --speed 30 --sink file --out data/stream.jsonl
```

![Web app: live match, personalised insights and the match assistant](docs/web.png)

The [web app](packages/web/README.md) plays the same events the agents analyse, shows each insight as a card and
a lower third once playback reaches it, and has a chat with the match assistant: it only knows the match up to
where you are, answers in your language, and can replay moments or follow a player on the page.

With Microsoft Foundry and the MCP server, after `azd up` (or with your own Foundry project):

```bash
footballfoundry season                                                       # data for the MCP server
footballfoundry-mcp --data data                                              # http://127.0.0.1:8001/mcp
export FOUNDRY_PROJECT_ENDPOINT=... FOUNDRY_MODEL=...                         # or $env:... in PowerShell
footballfoundry stream --speed 30 --quiet | footballfoundry-agents serve --mcp-url http://127.0.0.1:8001/mcp
```

See [packages/agents](packages/agents/README.md) for the API and design, [packages/mcp](packages/mcp/README.md) for
the tools, [infra](infra/README.md) to deploy everything with `azd up`, and [fabric](fabric/README.md) for the
Eventhouse.

## How it works

Modules are in [`packages/generator/src/footballfoundry/`](packages/generator/src/footballfoundry/).

| Layer | Module | What it does |
| --- | --- | --- |
| World | `world.py` | Clubs with strength + tactical style dials (possession, directness, press, line height, width, tempo), 26-man squads with position-shaped attributes, double round-robin fixtures |
| Match engine | `engine.py` | Possession-chain simulation: the player on the ball picks pass / carry / take-on / cross / shot / clearance, shaped by attributes, team style, pitch zone, opposition pressure and game state (score, minute, fatigue, red cards) |
| Scenario hook | `engine.TacticChange` | Scheduled tactical changes (e.g. "home team presses higher from 58'") — the basis for scripted storylines with ground truth |
| Stats | `stats.py` | Box scores, possession, league table |
| Calibration | `calibration.py` | Target ranges and the harness that checks them |
| Export | `export.py` | JSON/JSONL and flat Parquet tables for a Fabric Lakehouse |
| Streaming | `stream.py` | Real-time replay on the match clock to stdout, a file or Azure Event Hubs |
| Viewer | `serve.py`, `viewer/` | Browser match view over Server-Sent Events (`footballfoundry serve`) |
| Scenarios | `scenario.py` | Scripted tactical changes (style, role, formation) and their measured ground truth |
| Stories | `stories.py` | Tags each match's storylines; `footballfoundry find` picks demo fixtures |

**Match dynamics.** Beyond each action's probabilities, a match ebbs and flows:

- **Momentum:** shots, corners and entries into the box build a side's momentum, which lifts its quality for a few
  minutes and fades, so pressure comes in spells.
- **Game state:** from the hour, a trailing side pushes higher, wider and faster and shoots more; a leading side
  drops deeper and slows the game down (and takes its time over restarts).
- **Fatigue:** tired defenders make late chances easier, so goals rise towards the end, as in real football.
- **Chance types:** counter-attacks after winning the ball in your own half against a high line, through balls
  that put a forward one-on-one, cutbacks from the byline, and speculative efforts from distance.
- **Physical data:** ball speed for passes and shots, running speed with a sprint flag on carries, pass difficulty,
  save difficulty, and per-player distance, sprints and top speed at half-time and full-time. These use their own
  random stream, so they never change what happens in the match.

### Realism

Season averages over 380 matches (seed 42), all checked by `packages/generator/tests/test_calibration.py`:

| Metric | footballfoundry | Target range |
| --- | --- | --- |
| Goals / match | 2.85 | 2.4 – 3.2 |
| Shots / match | 23.3 | 21 – 29 |
| Shots on target | 8.8 | 7 – 10.5 |
| xG / match | 2.86 | 2.3 – 3.2 |
| xG / shot | 0.12 | 0.09 – 0.13 |
| Passes / match | 1002 | 800 – 1100 |
| Pass completion | 85% | 78 – 86% |
| Corners | 9.5 | 8.5 – 12 |
| Fouls | 24.5 | 17 – 25 |
| Yellow / red cards | 4.0 / 0.16 | 3 – 4.8 / 0.03 – 0.25 |
| Offsides | 4.7 | 2.5 – 5 |
| Ball in play | 58 min | 52 – 62 |
| Home win / draw rate | 41% / 26% | 38 – 52% / 18 – 32% |

How matches unfold, which is what makes them watchable:

| Metric | footballfoundry | Target range | Real-world reference |
| --- | --- | --- | --- |
| Goals in the 2nd half | 56% | 50 – 62% | ~55% |
| Goals from 76' | 22% | 19 – 30% | ~25% |
| Goals in added time | 11% | 4 – 12% | rising with longer added time |
| First scorer wins | 69% | 60 – 76% | ~68% |
| Penalties / match, conversion | 0.26, 81% | 0.15 – 0.4, 65 – 90% | ~0.28, ~78% |
| Woodwork / match | 0.96 | 0.6 – 1.4 | ~0.9 |

The shape of the season, as the web app's table and leaders show it:

| Metric | footballfoundry | Target range | Real-world reference |
| --- | --- | --- | --- |
| Top scorer's goals | 40 | 20 – 46 | 20 – 36 |
| Most goals by a team | 92 | 75 – 106 | 85 – 106 |
| Most shots by one player, share of the team's | 33% | 20 – 38% | ~25 – 35% |

Ratings work with diminishing returns inside the engine, so an XI of 90-rated players dominates without scoring
130 goals a season. Passes and crosses into the box find late runners from midfield and the flanks, not only the
centre-forward, and direct free kicks go to a specialist, not always the striker.

## Scenarios and stories

**Find a match with a story.** Every fixture is deterministic for a seed, so a match found once can be streamed again
exactly:

```bash
footballfoundry find --story comeback --story late-winner      # most dramatic first
footballfoundry find --story red-card --limit 5 --json
footballfoundry stream --match-id M0028 --speed 30             # then stream the one you like
```

Stories: comeback, fightback, late-winner, late-equaliser, stoppage-time, lead-swing, red-card, penalty,
penalty-miss, wonder-goal, great-save, woodwork, goal-fest, hat-trick, counter-goal, one-on-one, nil-nil, upset.

**Script a tactical storyline, with ground truth.** A scenario changes how a team plays from a given minute:
its style (pressing, line height, directness, tempo, width, possession), a player's role, or the formation. Role and
formation changes are visible on the pitch, so they're announced as `tactical_change` events; style changes aren't
announced, as in a real data feed, so the agents have to notice them. Presets are in [`scenarios/`](scenarios/):

```bash
footballfoundry stream --match-id M0001 --speed 30 --scenario scenarios/high-press.json
footballfoundry match --home T05 --away T12 --move "61:home:RW:AM" --tactic "61:home:directness=+0.15"
footballfoundry match --formation "60:away:3-5-2" --tactic "60:away:press_intensity=+0.3,width=+0.2"
```

[`scenarios/README.md`](scenarios/README.md) explains each preset, how scenarios work, how strong their effects
are, and how to write your own. Each run writes the ground truth to `data/truth/<match_id>.json`: the scripted changes and what they measurably did
over the 20 minutes either side (shots, xG, PPDA, defensive line height, opponents' passes under pressure, final
third entries, and the moved player's touches and central share). That's the answer key for judging whether the
agents explained the change correctly. For example, moving a right winger to number 10 took his share of central
touches from 30% to 72%.

## Data contract

Defined as Pydantic models in the `footballfoundry-schema` package,
[`packages/schema/src/footballfoundry_schema/__init__.py`](packages/schema/src/footballfoundry_schema/__init__.py).
The agents and Fabric depend on this package only, not on the generator.

**Coordinates** are relative to the acting team: `x` 0→100 from its own goal line to the opponent's,
`y` 0→100 from its right touchline to its left. The pitch is 105 × 68 m.

Example event:

```json
{"event_id": "X0001-00447", "match_id": "X0001", "seq": 447, "period": 1, "elapsed_s": 1165.9,
 "minute": 19, "second": 25, "clock": "20'", "type": "shot", "team_id": "T01", "player_id": "P0124",
 "outcome": "goal", "x": 91.9, "y": 55.4, "end_x": 100.0, "end_y": 53.4, "possession_id": 55,
 "possession_team_id": "T01", "set_piece": "open_play", "under_pressure": true,
 "body_part": "right_foot", "xg": 0.2015, "home_score": 1, "away_score": 0, "qualifiers": {}}
```

`stream_id` is set by `footballfoundry stream`, one per run, so two streams of the same match can be told apart
(season files leave it empty). Each team's first `period_start` carries its team sheet (formation, XI with roles and
positions, bench). Physical and
chance data live in `qualifiers`: `length_m` and `speed_kmh` (passes and shots: ball speed; carries: running speed,
with `sprint` from 25.2 km/h), `difficulty` (passes: 1 − completion probability), `on_target_xg` and save
`difficulty` (routine, good, great), the chance types `through_ball`, `cutback`, `one_on_one`, `long_range`,
`fast_break`, and on each team's `period_end`, `physical`: distance, sprints, top speed and minutes for every player
so far.

Two off-ball event types carry context: `pressure` (a defender closing down the carrier just before the carrier's
next action; `counterpress` when it comes within 5 s of losing the ball) and `possession_change` (opens each spell by
the other side, with the `cause`, and the previous spell's `previous_duration_s` and `previous_passes`). Every
qualifier key is declared in `footballfoundry_schema.Qualifiers` and checked on validation, so an undeclared key
fails; `event_json_schema()` exports the full contract as JSON Schema for consumers outside Python.

Output layout:

```text
data/
  league.json  fixtures.json  standings.json
  matches/<match_id>.json        lineups, score, formations, seed, added time
  events/<match_id>.jsonl        one event per line
  parquet/{clubs,players,matches,lineups,events}.parquet
  truth/<match_id>.json          scenario ground truth (only when a scenario is used)
```

## Roadmap

- [x] Schema, world generator, match engine, calibration tests, JSONL/Parquet export
- [x] Scenario injector + truth files (scripted storylines with ground truth for evaluating agent explanations)
- [x] Match dynamics (momentum, game state, fatigue, counters, one-on-ones), physical data, story finder
- [x] Live replay streamer to stdout / file / Azure Event Hubs (`footballfoundry stream`, `footballfoundry listen`)
- [x] Browser match viewer (`footballfoundry serve`)
- [x] Analyst agents: trigger rules, Agent Framework workflow with fact-checking, personalisation, insights API
- [x] MCP server with spoiler-safe match tools; Eventhouse KQL functions; Bicep + `azd` deployment
- [x] Web app (React): live match over the agents' relay with DVR replay, personalised insights and lower thirds,
      audience / language / club / player controls, chat with the match assistant over AG-UI
- [x] Visuals: animated move replays, goals reel, shot map, xG race, momentum and team comparison in insights and chat
- [x] Lore for casual fans: "Did you know?" player and club facts from the league data, retold in five languages
      by a Foundry storyteller agent when one is configured
- [x] Spotlight: when no insight has fired for 10 seconds (and 3 match minutes), the analysts find a story themselves

# footballfoundry-agents

The match-analyst service. It reads match events (from Azure Event Hubs, stdin or a file), keeps live state per
match, decides which moments are worth explaining, and runs a Microsoft Agent Framework workflow of agents backed
by a Microsoft Foundry model. The results are **insights**: timed, machine-readable explanations that a viewer or an
overlay renderer can show next to the match, personalised per viewer.

## Run it

```bash
# No Azure, no LLM: template insights. Good for development and for checking the triggers.
footballfoundry world
footballfoundry-agents serve --offline     # then start streams from the control room, http://127.0.0.1:8100/control
footballfoundry stream --speed 30 --quiet | footballfoundry-agents serve --offline
# Or in two terminals, started in either order: the stream writes a file, the service follows it
footballfoundry-agents serve --offline --follow data/stream.jsonl
footballfoundry stream --speed 30 --sink file --out data/stream.jsonl

# Foundry agents with the MCP data tools.
footballfoundry-mcp --data data &
FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project> FOUNDRY_MODEL=gpt-6-luna \
  footballfoundry-agents serve --source eventhub --mcp-url http://127.0.0.1:8001/mcp
```

`serve` options, each also settable by environment variable (shown in brackets in `--help`):

| Option | Default | |
| --- | --- | --- |
| `--source` | `eventhub` if `EVENTHUB_NAMESPACE` is set, else `stdin` if something is piped in, else `none` | `stdin`, a JSONL file, `eventhub`, or `none` (only the control room's streams) |
| `--follow FILE` | off | Follow a file as `footballfoundry stream --sink file` writes it, like `tail -f`: waits for it, catches up, starts over when the stream restarts |
| `--store` | `cosmos` if `COSMOS_ENDPOINT` is set, else `memory` | `memory`, `file` (`--store-dir`), `cosmos` |
| `--offline` | off; on automatically without `FOUNDRY_PROJECT_ENDPOINT` | template insights only |
| `--mcp-url` | `MCP_URL` | the agents run without tools if unset |
| `--league` | `data/league.json` | club and player names |
| `--port` | `8100` | |
| `--control-key` | `CONTROL_KEY` | Turns the control room on for a non-local host (always on for localhost); every control request must carry it |
| `--no-control` | off | Turns the control room off |
| `--season-book` | `data/season-book.json` | The season book for `/api/season` (`footballfoundry book`). Built on first request and saved here if missing (about a minute and a half); baked into the image in Azure |
| `--scenarios` | the repository's `scenarios/` | Scenario files offered in the control room |

Azure settings: `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL`, `EVENTHUB_NAMESPACE`, `EVENTHUB_NAME`,
`EVENTHUB_CONSUMER_GROUP` (`analyst`), `CHECKPOINT_BLOB_URL`, `COSMOS_ENDPOINT`, `COSMOS_DATABASE`,
`APPLICATIONINSIGHTS_CONNECTION_STRING`. Sign-in is `DefaultAzureCredential`: `az login` locally, the managed
identity in Container Apps. `azd up` writes all of these to `.azure/<env>/.env`.

## Control room

`/control` in the web app is for testers. It's backed by `/api/control/*`
([control.py](src/footballfoundry_agents/control.py)):

- **Start a stream**: a fixture, a matchday or a one-off match (a fresh `X…` id each time), at any speed, with an
  optional half-time pause and scenario. Each stream gets its own `stream_id`, like `footballfoundry stream`. Up to 3
  can run at once, and any of them can be stopped.
- **Where events go**: when Event Hubs is the source (in Azure), streams go to Event Hubs. Every replica, the archive
  and Fabric then see them exactly as they would from `footballfoundry stream`; the app identity has the Data Sender
  role for this. Otherwise streams go straight into this service.
- **Ground truth** of a scenario stream, to compare with the agents' explanations (it gives away the match).
- **Story finder**: comebacks, late winners, red cards and more, each with "Stream it". Instant: every match's
  stories are in the season book, which loads at startup.
- **Season files** (localhost only): regenerate the JSON season files under `data/` for the MCP server.

It is always on for localhost. On any other host it's off unless `CONTROL_KEY` is set (`azd env set CONTROL_KEY …`),
and then the page asks for the key. A public link must not let anyone start streams that spend model tokens.

## API

| | |
| --- | --- |
| `GET /api/matches` | Matches seen so far: score, clock, finished |
| `GET /api/matches/{id}/insights?as_of=<seq>` | Insights so far, oldest first |
| `GET /api/matches/{id}/stream` | Server-Sent Events: the insights so far, then live ones (`event: insight`) |
| `POST /api/matches/{id}/insights/{insight_id}/personalize` | `{audience, language, club_id, player_id}` → a variant |
| `GET` / `PUT /api/profiles/{user_id}` | `{audience, language, favourite_club_ids, favourite_player_ids, focus_player_id}` |
| `GET /api/league` | Clubs and players: names, kits, positions |
| `GET /api/season?through=<matchday>` | The season pages: table (with form, movement, next fixture), club totals and player totals after that matchday. Without `through`, the standings before any matchday being streamed now, so the page doesn't spoil a live match. `202` while the season book is being built |
| `GET /api/season/players/{id}?through=<matchday>` | One player's match-by-match lines |
| `GET /api/matches/{id}/session` | Team sheets (formation, XI, bench) and live status |
| `GET /api/matches/{id}/events?from=<t\|live>&speed=<x\|live>` | The relay: a DVR over the match's events (below) |
| `POST /api/chat` | The match assistant over [AG-UI](https://docs.ag-ui.com) (below) |
| `GET /` | The web app, when built ([packages/web](../web/)) |

Viewer settings for the two `GET` insight routes: `user=<id>` loads a stored profile, and `audience`
(`broadcast`, `analyst`, `casual`, `player`), `lang`, `club` and `player` override it. Each insight comes back with
a `view` (headline and text for that viewer) next to the original. Variants are cached on the insight, so the
personaliser runs once per insight and setting, however many viewers share it.

An insight ([`footballfoundry_schema.Insight`](../schema/src/footballfoundry_schema/__init__.py)) carries `seq`,
`clock` and `period` to place it on the match timeline, `headline` for a lower third, `text`, `why_it_matters`,
`stats` for an on-screen graphic, `priority` and `display_s`, and `event_ids`: the events it's built on. It may
also carry `visuals` (charts and animated move replays) and `trivia` (a fun fact for casual fans), both below.

## Visuals

Insights and chat answers can carry **visuals** ([visuals.py](src/footballfoundry_agents/visuals.py)) that the web
app draws: `move` (the move behind a goal or chance replayed on a mini pitch, looping like a GIF; at full time, a
reel of every goal), `shot_map`, `xg_race`, `momentum` and `compare`. They're built in code from the match state
when the moment fires, so the numbers are always the real ones and nothing after the moment gets in. The fact sheet
lists the visuals a moment can show; the analyst picks up to two and captions them, and the fact-checker checks the
captions. Without a pick (offline, or the template fallback) each kind of moment has defaults.

## Lore for casual fans

Each insight carries a `trivia`: a "Did you know?" about a player or club in the moment
([lore.py](src/footballfoundry_agents/lore.py)). The facts come from the league data, so they're true and work
offline: the fastest or best finisher in the league or the squad (ties become "one of the 2 fastest"), the youngest
and oldest players, one of only N players from a country, the oldest club, the biggest ground, the team that
presses hardest, the manager and their formation. A goal gets a fact about the scorer, then their club; half-time
and full-time are about the clubs; no fact is shown twice in a match. The web app shows it to casual fans, worded
in the viewer's language from the fact's `key` and `params`.

With Foundry configured, the **storyteller** agent retells each fact as a one- or two-sentence story, adding
harmless colour (a nickname, a fan tradition) but no new numbers or match facts, in English, Spanish, French,
Portuguese and German at once. Stories are cached per fact; if the storyteller fails, the plain fact is shown. The
web app marks them "Told by the storyteller agent". The fact is picked, and the storyteller called, while the
analysts write the insight, so it never delays it.

## Translation

With `TRANSLATOR_ENDPOINT` set (the Foundry / AI Services account endpoint, signed in with Entra ID), a viewer's
language is handled in two steps: the personaliser rewrites the insight for the viewer **in English**, where it's
still fact-checkable, and **Azure Translator** (API 2026-06-06) translates that text.

| Audience | Translation |
| --- | --- |
| broadcast, analyst | Neural machine translation: translation only, so no new facts; player and club names marked "don't translate" |
| casual, player focus | With `TRANSLATOR_LLM_DEPLOYMENT`, Translator's LLM option with an informal tone and reference translations of football commentary; NMT if it fails |

If Translator fails, the personaliser writes in the viewer's language itself, as it does without Translator. Every
variant records `translated_by` (`translator` or `agent`), and the web app notes "Translated by Azure Translator".
Translations are cached on the insight like every variant, so each is made once per setting.

## Event relay

The web app shows the same events the agents analyse. The service keeps every match's events with their time on
the match clock, and `/events` sends a `reset`, everything before `from` as catch-up, then the rest at `speed`,
following live once it catches up (`speed=live` follows the producer; `from=live` starts at the live edge). In
Azure every replica fills this log from its own `viewer` consumer group, read from the start of retention without
checkpoints, so any replica can serve any match.

## Match assistant (chat)

`POST /api/chat` speaks AG-UI, through Agent Framework's `agent-framework-ag-ui`. The page sends its state with
every message: the match, `as_of` (the last event the viewer has seen), audience, language, club and player. The
assistant is built per request with tools **bound to that match and position on the server**, so it can't see
past the viewer whatever the model asks:

| Tool | |
| --- | --- |
| `match_state`, `events`, `player_stats`, `lookup`, `insights` | From the relay log and the insights store, up to `as_of` |
| `season_query` | Through the MCP server, matchdays before this match only |
| `explain_moment(event_id)` | Hands one moment to the analyst workflow (analyst → fact-checker → narrator) and returns the verified explanation, with its visuals |
| `show_visual(type, event_id?)` | Draws a visual under the answer: a move replay (the goals reel without an event), shot map, xG race, momentum or team comparison |
| `lore(subject_id)` | A player's or club's profile and fun facts, for casual fans |
| `seek_to`, `focus_player`, `set_view` | Declared by the page and run in the browser: replay a moment, follow a player, change the view |

Without a Foundry model the assistant still answers, with the score and key numbers computed from the log, and
draws the visual a question asks for ("show me the xG race", "replay the goal").

## Design

```text
Event Hubs ─► consumer ─► MatchState ─► TriggerEngine ─► Brief ─► per-match queue ─► workflow ─► insights store
                              │                                                                       │
                              └── snapshot ──► state store                         SSE ◄── publish ◄──┘
```

| Module | Role |
| --- | --- |
| [`sources.py`](src/footballfoundry_agents/sources.py) | JSONL or Event Hubs; checkpoints to Blob Storage so a restart resumes |
| [`state.py`](src/footballfoundry_agents/state.py) | Score, team and player stats, threat per minute, recent events; ignores duplicates; snapshots |
| [`triggers.py`](src/footballfoundry_agents/triggers.py) | Which events become moments. Must-show always; minor ones rate-limited |
| [`briefs.py`](src/footballfoundry_agents/briefs.py) | Freezes the facts at trigger time, so a slow model never sees later events |
| [`workflow.py`](src/footballfoundry_agents/workflow.py) | The Agent Framework workflow and the personaliser |
| [`templates.py`](src/footballfoundry_agents/templates.py) | Deterministic insights: offline mode, and the fallback when the workflow fails |
| [`visuals.py`](src/footballfoundry_agents/visuals.py) | Charts and move replays for insights and chat answers, built from the match state |
| [`lore.py`](src/footballfoundry_agents/lore.py) | "Did you know?" facts from the league data, and the storyteller agent |
| [`spotlight.py`](src/footballfoundry_agents/spotlight.py) | The stories a quiet spell can be spent on, and their offline write-up |
| [`service.py`](src/footballfoundry_agents/service.py) | Wires it together; one writer per match keeps insights in order |
| [`stores.py`](src/footballfoundry_agents/stores.py) | Insights, state snapshots and profiles in memory, files or Cosmos DB |
| [`guard.py`](src/footballfoundry_agents/guard.py) | First stream wins: ignores a second stream (another `stream_id`) of a match that's still live |
| [`translation.py`](src/footballfoundry_agents/translation.py) | Azure Translator: NMT with protected names, or the LLM option with tone and reference pairs |
| [`archiving.py`](src/footballfoundry_agents/archiving.py) | Copies every analysed event, and a result at full time, to the event archive the MCP server reads (Cosmos DB, or files with `--store file`), in background batches |
| [`relay.py`](src/footballfoundry_agents/relay.py) | Every match's events on the match clock, for the web app and the chat |
| [`chat.py`](src/footballfoundry_agents/chat.py) | The match assistant: per-request agent, bound tools, AG-UI endpoint |
| [`api.py`](src/footballfoundry_agents/api.py) | The HTTP API above, and the web app's static files |

**Triggers.** Goals, red cards, missed penalties, half-time and full-time always fire. Big chances (xG ≥ 0.3),
momentum swings (one side with ≥ 72% of the attacking threat over 10 minutes) and summaries every 15 minutes are
capped at 3 per 10 minutes, and held back for a minute after a must-show moment. If the agents fall behind,
waiting summaries are dropped so the overlay stays close to live.

**Spotlight: the agents' own initiative.** When no insight has fired for 10 real seconds *and* 3 match minutes
(`TriggerConfig.idle_after_s`, `idle_min_gap_min`), the trigger engine fires a `spotlight` moment
([spotlight.py](src/footballfoundry_agents/spotlight.py)). Code finds the stories in the match so far: the player
running the game, the one winning everything back, a dribbler, a busy keeper, possession without threat, a side
creating more without being ahead, no shot on target yet, corner after corner. The analyst gets the best one as a
suggestion and the others as alternatives, and may find a better story with the tools; offline, the template
writes up the suggestion, with the visual that shows it. Each story is told at most once per team per match.
Real seconds are what a viewer feels (10 s is 5 match minutes at 30×); the match-minute floor keeps a live 1×
stream from asking every 10 seconds, and a very fast replay gets none. Spotlights are filler (priority 3), so they're
the first to go if the agents fall behind.

**Agents.** Each has one job and its own instructions:

| Agent | Job | Tools |
| --- | --- | --- |
| Analyst | What happened, how it was created, why it matters; cites event ids; picks and captions the visuals. On a spotlight, chooses the story to tell | MCP |
| Recap writer | The full-time story, from the timeline of earlier insights; picks the goals reel, xG race or shot map | MCP |
| Fact-checker | Checks every claim, visual captions included; sends the problems back to the author (up to 2 attempts) | MCP |
| Narrator | Broadcast copy from the approved analysis only | none |
| Personaliser | Rewrites per audience, favourite club and focus player, without new facts (in English when Azure Translator is configured) | none |
| Storyteller | Retells a league fact as a short story for casual fans, in five languages | none |

Every agent returns structured output (Pydantic models), so the hand-offs are typed. An insight the fact-checker
never approved is published with `verified: false`. If any step fails or times out, the template writer produces
the insight instead, so a goal is never missed because a model call was.

**Recovery.** Insight ids are deterministic (`<match>-<kind>-<seq>`), the state ignores events it has seen, and
snapshots go to the state store at every moment and every 100 events. A restarted replica resumes from the last
checkpoint and snapshot without duplicating insights. With several replicas, each match stays on the replica that
owns its partition, and the SSE stream also polls the shared store, so a viewer on any replica gets every insight.

## Tests

`pytest packages/agents` runs the real Agent Framework workflow against a scripted chat client: the fact-checker
loop, recap routing, the template fallback, personalisation, restart recovery, the API, the relay, and a chat run
over AG-UI that checks the assistant's tools can't see past the viewer. Also: visuals (built at trigger time, so
nothing after the moment gets in; the agents' picks; the chat's offline charts), lore (the records are really true,
no fact twice in a match, the storyteller in five languages and its fallback), and the spotlight trigger at 30×, 1×
and a very fast replay, with a fake clock.

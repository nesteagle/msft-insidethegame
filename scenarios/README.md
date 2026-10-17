# Scenarios

A **scenario** is a scripted tactical storyline: "from the 58th minute, the home side press higher". You attach it
to a match, the match engine plays it out, and you get a **ground-truth file** that records what was scripted and
what it measurably changed.

Use scenarios to **test the analyst agents**. A real match never tells you why it changed; a scenario does, so you
can check whether the agents noticed the change and explained it correctly.

## The three presets

| Scenario | When | Who | What changes | Announced in the event stream? |
| --- | --- | --- | --- | --- |
| [`high-press`](high-press.json) | 58' | home | Pressing +0.35, defensive line +0.25, tempo +0.15 | **No**: the agents have to spot it |
| [`winger-central`](winger-central.json) | 61' | home | The right winger moves to the number 10 (AM) role, swapping with whoever played there; directness +0.15 | The move, yes (`tactical_change`); the directness, no |
| [`back-three-chase`](back-three-chase.json) | 60' | away | Switch to a 3-5-2; pressing +0.3, width +0.2, tempo +0.2, defensive line +0.2 | The formation, yes (`tactical_change`); the style, no |

**high-press** is the hardest test. Nothing in the data says the press changed, as with a real data feed. A good
analyst would notice the home side winning the ball higher up and the opponents passing under pressure more often,
and say what that changed. Expect a subtle effect (see [How strong the effects are](#how-strong-the-effects-are)).

**winger-central** is the clearest test. The move appears as a `tactical_change` event, so the agents write a
"tactical change" insight about it, at the next stoppage after 61' (usually 62'–63'). The question is whether they
then explain what it did: the player now gets the ball centrally instead of on the touchline.

**back-three-chase** tells a familiar story: the away side changes shape and throws caution to the wind to chase
the game. The formation switch is announced; the extra aggression isn't.

## How a scenario works

```
scenarios/high-press.json ─► Scenario.load ─► the match engine ─► events (as normal) ─► agents, web app, Fabric
                                                     │
                                                     └─► truth(): what was scripted + what it changed ─► truth file
```

A scenario file lists **changes**, each starting at a match minute for one side:

| Type | What it does | Announced? |
| --- | --- | --- |
| `style` | Adds to the club's six style dials (0–1 each): `press_intensity`, `defensive_line`, `directness`, `possession`, `width`, `tempo`. The engine reads these dials on every action, e.g. how likely a defender is to press the ball, how often a team plays long, or how often it crosses | No |
| `role` | Moves one player to another role. The player can be an id (`P0124`) or a role (`RW` = whoever plays right wing), so the same scenario works for any club. If the team has no RW, the nearest equivalent is used (RM, then RWB). If someone already plays the target role, the two swap | Yes, `tactical_change` |
| `formation` | Switches the team's formation; the best-suited players fill the new roles | Yes, `tactical_change` |

Things worth knowing:

- **Style changes start exactly at their minute. Role and formation changes wait for the next stoppage**, as a
  manager's would, so they usually land a minute or two later. The truth file records when (`at`).
- **The engine also adjusts style by itself.** A team that's behind late on goes higher, wider and more direct; a
  team protecting a lead drops deeper. A scenario adds to whatever the game state is doing at the time.
- **A scenario changes the rest of the match, not only the dials.** Every match is deterministic for its fixture,
  so a scenario is repeatable: the same fixture with the same scenario always plays out the same way. But any
  change sends the match down a different path from the unscripted version, with different chances and sometimes
  different goals. For example, M0001 with `high-press` has a 73rd-minute away goal that the normal M0001 doesn't.
- **The Season page never changes.** It always shows the unscripted season, so a fixture streamed with a scenario
  can end with a different score from the one the Season page shows. A matchday stream applies the scenario to all
  ten of its matches.
- **A role change is skipped if the player has already gone off** (substituted or sent off). The truth file then
  says `"applied": false`.

## Run one

**Control room** (http://127.0.0.1:8100/control locally): pick a fixture, matchday or one-off match, choose a scenario, and
press **Start**. When the match has been simulated, **Ground truth** on the stream shows the truth file. It gives
away what happens, so open it after watching.

**Terminal:**

```powershell
# Stream fixture M0001 with a scenario; the truth file goes to data/truth/M0001.json
uv run footballfoundry stream --match-id M0001 --speed 30 --scenario scenarios/winger-central.json

# Simulate a one-off match between any two clubs to files under data/, without streaming
uv run footballfoundry match --home T05 --away T12 --scenario scenarios/high-press.json

# Or script changes inline, no file needed (combine with --scenario to add to a preset)
uv run footballfoundry match --home T05 --away T12 --move "61:home:RW:AM" --tactic "61:home:directness=+0.15"
uv run footballfoundry match --formation "60:away:3-5-2" --tactic "60:away:press_intensity=+0.3,width=+0.2"
```

`--scenario`, `--tactic`, `--move` and `--formation` work with `stream`, `match` and `serve`. Use `stream
--match-id` to play a fixture: `match` always plays `--home` against `--away` (T01 v T02 by default), so don't give
it a fixture's id, or it will overwrite that fixture's files with a different match. To stream to Azure, add
`--sink eventhub`; the truth file is still written locally.

## The ground-truth file

For each change: what was scripted, whether and when it was applied, and an **effect**. The effect measures the
changed team over the **20 minutes before** the change and the **20 minutes after** it:

| Measure | Meaning |
| --- | --- |
| `shots`, `xg`, `goals` | Attacking output |
| `passes`, `pass_accuracy` | Possession and its quality |
| `final_third_entries` | Passes and carries into the attacking third |
| `ppda` | Opponent passes in their own 60% of the pitch per defensive action of ours there. **Lower means more pressing** |
| `defensive_action_height_m` | Average distance from our own goal of our tackles, interceptions and recoveries. Higher means defending further up |
| `opponent_passes_under_pressure` | Share of the opponents' passes made under pressure |
| `player_before` / `player_after` | For a role change: the player's touches, average position and `central_share` (touches in the middle 40% of the pitch's width) |

Abridged, from M0001 with `winger-central`:

```json
{
  "match_id": "M0001", "scenario": "winger-central", "final_score": [3, 0], "window_minutes": 20,
  "changes": [{
    "minute": 61.0, "side": "home", "type": "role", "to": "AM", "announced": true,
    "applied": true, "at": "63'", "player_id": "P2020",
    "effect": {
      "before": {"shots": 2, "xg": 0.72, "ppda": 4.71, "...": "..."},
      "after":  {"shots": 4, "xg": 0.28, "ppda": 4.17, "...": "..."},
      "player_before": {"touches": 27, "avg_y": 24.5, "central_share": 0.33},
      "player_after":  {"touches": 20, "avg_y": 40.1, "central_share": 0.35}
    }
  }]
}
```

## Using it to judge the agents

1. Stream a fixture with a scenario and watch it in the web app.
2. Note what the agents say around the change: the `tactical_change` insight (for role and formation changes),
   the momentum and summary insights after it, and the full-time recap. You can also ask the chat directly: "did
   anything change tactically after the hour?"
3. Open the ground truth and compare. Did they spot the change, place it at the right time, and describe its
   effect in the direction the numbers show?

There's no automatic scoring yet: the comparison is done by a person.

## How strong the effects are

Measured over 60 fixtures, each played with and without the scenario, for the changed team in the 20 minutes after
the change:

| Scenario | Measure | Without | With |
| --- | --- | --- | --- |
| winger-central | Starting right winger's share of touches in the centre | 28% | **61%** |
| high-press | PPDA (lower = more pressing) | 8.0 | 7.3 |
| high-press | Opponents' passes under pressure | 33% | 35% |
| high-press | Defensive action height | 40.6 m | 40.6 m |
| back-three-chase | PPDA | 7.4 | 6.8 |
| back-three-chase | Defensive action height | 36.7 m | 38.8 m |

- **Role and formation changes are big and easy to see.**
- **Style changes are real but subtle on average**, and in a single match chance can outweigh them. In M0001 with
  `high-press`, the home side's PPDA actually *rose* from 4.5 to 8.5 after 58', and the ground truth reports that
  honestly. A 20-minute window holds only a handful of defensive actions.

So when you judge the agents, read the ground truth as **what happened in this match**, not as the pure effect of
the scenario. For a fair test of whether they can spot a style change, use several fixtures, or make the change
bigger in your own scenario.

## Write your own

Copy a preset into this folder and edit it. The control room lists every `.json` file here (in Azure, the files
baked into the image at deploy time).

```json
{
  "name": "park-the-bus",
  "description": "The away side, a goal up, drop deep and sit on it from 70'.",
  "changes": [
    {"minute": 70, "side": "away", "type": "style", "changes": {"defensive_line": -0.3, "press_intensity": -0.3, "possession": -0.2}},
    {"minute": 70, "side": "away", "type": "formation", "formation": "3-5-2"}
  ]
}
```

| Field | Values |
| --- | --- |
| `side` | `home` or `away` |
| `type` | `style`, `role` or `formation` |
| `changes` (style) | Any of `press_intensity`, `defensive_line`, `directness`, `possession`, `width`, `tempo`, as amounts to add (negative to reduce). Each dial stays within 0–1 |
| `player` (role) | A player id, or a role: `GK`, `RB`, `CB`, `LB`, `RWB`, `LWB`, `DM`, `CM`, `AM`, `RM`, `LM`, `RW`, `LW`, `ST` |
| `to` (role) | The role to move to (same list) |
| `formation` | `4-3-3`, `4-2-3-1`, `4-4-2`, `3-4-3`, `3-5-2` |

A typo in a dial, role or formation name fails with an error that lists the valid values. The code is in
[`scenario.py`](../packages/generator/src/footballfoundry/scenario.py), and the engine's hooks
(`TacticChange`, `RoleChange`, `FormationChange`) are in
[`engine.py`](../packages/generator/src/footballfoundry/engine.py).

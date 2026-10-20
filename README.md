# TL;DR

**FootballFoundry is an AI-powered platform for turning live football data into stories people can understand.**

The repository contains an end-to-end implementation that generates synthetic football-realistic matches, streams every event in real time, analyzes the evolving game with specialized AI agents, verifies the resulting insights against match data, and transforms them into personalized experiences for fans, analysts and broadcasters.

FootballFoundry goes beyond reporting *what happened*. It explains **why it matters**: momentum shifts, tactical changes, player performances, big chances and the story developing behind the score.

Viewers experience this through a live web application with animated match playback, AI-generated insights, tactical visualizations, key-moment replays, multilingual personalization, season analytics and a conversational AI match assistant.

**One repository, one complete pipeline:**

`Synthetic football → Real-time streaming → Multi-agent analysis → Fact checking → Storytelling → Personalization → Live fan experience`

All competition, club, player and match data is entirely synthetic.

# How to use FootballFoundry

A note about the first load: The first request may take a little longer while FootballFoundry connects to Cosmos DB, Event Hubs, and the AI model. If the application feels slow at first, please give it a couple of minutes to warm up. Thanks for your patience! ⚽❤️

## 1. Start a match stream

1. Open the **Control Room** at [FootballFoundry Control Room](https://ff.testharder.net/control).
2. Enter the control key provided by the site administrator and select **Unlock**.
3. Choose what you want to stream:
   - **Fixture** – play a match from the simulated season.
   - **Matchday** – play all fixtures from a selected matchday.
   - **One-off** – create a matchup between any two clubs.
4. Select the playback speed and, optionally, a scenario.
5. Select **Start**.

The stream is sent through **Azure Event Hubs**, where it feeds the live application and the connected analytics and AI pipeline.

Use the **Streams** section to:
- Monitor the match progress.
- Open the match in the live viewer.
- Stop a running stream.

### 2. Watch the match live

Open [FootballFoundry Live](https://ff.testharder.net/).

Select a live match to:

- Follow the score and match clock in real time.
- Watch the match unfold on the animated pitch.
- Explore possession, momentum, team statistics, and the event feed.
- Pause, replay, or scrub through earlier moments.
- View AI-generated insights explaining important moments and tactical patterns.
- Use **Ask the analyst** to ask questions about the match.
- Explore personalized insights for different audiences, languages, clubs, and players.

### 3. Explore the season

Open the **[Season](https://ff.testharder.net/season)** page from the application to explore the wider simulated league.

From there you can view:

- League standings and form.
- Player and team statistics.
- Top performers.
- Player profiles and match-by-match performance.
- Fixtures and season progress.

A good place to start is to launch a fixture from the **Control Room**, select **Watch**, and then explore the AI insights and **Ask the analyst** while the match is running.

# Run it locally

FootballFoundry can run completely locally without Azure. In offline mode, it uses deterministic template insights instead of the Foundry-hosted AI workflow.

1. Install dependencies

From the repository root:
```powershell
uv sync
uv run footballfoundry world

cd packages\web
npm install
npm run build
cd ..\..
```
Optionally generate the season data in advance:

```powershell
uv run footballfoundry book
```

2. Start FootballFoundry

```powershell
uv run footballfoundry-agents serve --offline
```

3.Open:

- **App**: http://127.0.0.1:8100
- **Control Room**: http://127.0.0.1:8100/control
- **Season**: http://127.0.0.1:8100/season

In the Control Room, choose a fixture, matchday, or one-off match, select a playback speed, and press Start.

For a quick demo, run M0015 at 30×, select Watch, and explore the live match, AI-style insights, visualizations, replay controls, Ask the analyst, and the Season experience.

"""The FootballFoundry MCP server.

Run locally against the generator's output:
    footballfoundry season                       # writes ./data
    footballfoundry-mcp --data data              # http://127.0.0.1:8001/mcp

Or with the Fabric Eventhouse and the agents' event archive in Cosmos DB (Azure), in a fallback chain:
    footballfoundry-mcp --kusto-uri https://<eventhouse>.kusto.fabric.microsoft.com --cosmos-endpoint https://...

Every match tool takes `as_of`, an event seq, and never returns anything after it.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from footballfoundry_mcp import views
from footballfoundry_mcp.backends import (SEASON_QUERIES, ArchiveBackend, FallbackBackend, FileBackend, KustoBackend,
                                          MatchData)

INSTRUCTIONS = """Football match data for the FootballFoundry synthetic league.
Ids: clubs T01-T20, players P0101-P2026, fixtures M0001-M0380 (one-off matches X0001...).
Always pass as_of = the seq of the moment you are analysing: tools return nothing after it.
Coordinates: x 0-100 from the acting team's own goal line to the opponent's; y 0-100 across; the box starts at
x=83."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


def create_server(data: MatchData, *, agents_url: str | None = None, host: str = "127.0.0.1",
                  port: int = 8001) -> FastMCP:
    # Stateless JSON responses: any replica can answer any request, so it scales out behind Container Apps.
    mcp = FastMCP("footballfoundry", instructions=INSTRUCTIONS, host=host, port=port, stateless_http=True,
                  json_response=True)

    async def events(match_id: str, as_of: int) -> tuple[list, str | None]:
        """Events plus the name of the backend that supplied them (a fallback chain says which one answered)."""
        if hasattr(data, "events_with_source"):
            return await data.events_with_source(match_id, as_of)
        return await data.events(match_id, as_of), None

    def sourced(result: dict[str, Any], source: str | None) -> dict[str, Any]:
        return result | {"source": source} if source else result

    @mcp.tool(annotations=READ_ONLY)
    async def get_match_state(match_id: str, as_of: int) -> dict[str, Any]:
        """Score, clock, possession, team stats, attacking threat over the last 10 minutes, goals, cards and
        substitutions for a match, as of event seq `as_of`."""
        evs, source = await events(match_id, as_of)
        return sourced(views.match_state(match_id, evs, data.league), source)

    @mcp.tool(annotations=READ_ONLY)
    async def get_events(match_id: str, as_of: int, from_seq: int | None = None, possession_id: int | None = None,
                         types: list[str] | None = None, team_id: str | None = None, player_id: str | None = None,
                         limit: int = 50) -> list[dict[str, Any]]:
        """Events of a match up to `as_of`, optionally filtered: from_seq, one possession (possession_id), event
        types (e.g. ["shot", "pass"]), a team, or a player (as actor or receiver). Returns the latest `limit`
        matches, oldest first."""
        evs, _ = await events(match_id, as_of)
        return views.filter_events(evs, from_seq=from_seq, possession_id=possession_id, types=types,
                                   team_id=team_id, player_id=player_id, limit=limit)

    @mcp.tool(annotations=READ_ONLY)
    async def get_player_stats(match_id: str, player_id: str, as_of: int) -> dict[str, Any]:
        """One player's numbers in this match up to `as_of`: passes, accuracy, shots, xG, goals, tackles, touches,
        passes received, cards, and whether they came on or went off."""
        evs, source = await events(match_id, as_of)
        return sourced(views.player_view(evs, player_id, data.league), source)

    @mcp.tool(annotations=READ_ONLY)
    async def get_team_stats(match_id: str, team_id: str, as_of: int) -> dict[str, Any]:
        """A team's numbers in this match up to `as_of`."""
        evs, source = await events(match_id, as_of)
        return sourced({"team_id": team_id, "as_of_seq": evs[-1].seq if evs else 0,
                        "stats": views.round_stats(views.team_stats(evs, team_id))}, source)

    @mcp.tool(annotations=READ_ONLY)
    async def lookup(query: str) -> list[dict[str, Any]]:
        """Find clubs or players by id or (part of a) name: club colours, stadium, manager, formation and style
        dials; player position, foot, shirt number and attributes."""
        return views.lookup(data.league, query)

    @mcp.tool(annotations=READ_ONLY)
    async def season_query(name: str, params: dict[str, Any] | None = None,
                           before_match_id: str | None = None) -> list[dict[str, Any]]:
        """Run a named, read-only season query. Pass before_match_id (the match you are analysing) to use only
        matchdays before it. Queries:
        - standings: league table
        - top_scorers: leading scorers; params {top}
        - team_form: a club's latest results; params {club_id, last}
        - player_season: appearances, goals, shots, xG; params {player_id}
        - head_to_head: earlier meetings this season; params {club_a, club_b}"""
        if name not in SEASON_QUERIES:
            raise ValueError(f"unknown query {name!r}; choose one of {sorted(SEASON_QUERIES)}")
        md = await data.matchday(before_match_id) if before_match_id else None
        return await data.season(name, params or {}, md)

    @mcp.tool(annotations=READ_ONLY)
    async def get_insights(match_id: str, as_of: int) -> list[dict[str, Any]]:
        """Insights the analyst agents have already published for this match up to `as_of`: headline, text,
        why it matters, key numbers and the event ids behind each."""
        if not agents_url:
            return []
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{agents_url.rstrip('/')}/api/matches/{match_id}/insights", params={"as_of": as_of})
            r.raise_for_status()
            keep = ("insight_id", "kind", "seq", "clock", "headline", "text", "why_it_matters", "stats", "event_ids",
                    "verified")
            return [{k: i[k] for k in keep} for i in r.json()]

    return mcp


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="footballfoundry-mcp", description=__doc__.splitlines()[0])
    p.add_argument("--data", default=os.environ.get("FOOTBALLFOUNDRY_DATA", "data"),
                   help="generator output folder for the file backend [FOOTBALLFOUNDRY_DATA]")
    p.add_argument("--live", default=os.environ.get("FOOTBALLFOUNDRY_LIVE"),
                   help="a live JSONL file from `footballfoundry stream --sink file` [FOOTBALLFOUNDRY_LIVE]")
    p.add_argument("--league", default=os.environ.get("FOOTBALLFOUNDRY_LEAGUE"),
                   help="league.json (default: <data>/league.json) [FOOTBALLFOUNDRY_LEAGUE]")
    p.add_argument("--kusto-uri", default=os.environ.get("KUSTO_URI"),
                   help="Eventhouse query URI: first choice for season queries [KUSTO_URI]")
    p.add_argument("--cosmos-endpoint", default=os.environ.get("COSMOS_ENDPOINT"),
                   help="the agents' event archive in Cosmos DB: first choice for live-match tools [COSMOS_ENDPOINT]")
    p.add_argument("--cosmos-db", default=os.environ.get("COSMOS_DATABASE", "footballfoundry"),
                   help="[COSMOS_DATABASE]")
    p.add_argument("--archive", default=os.environ.get("FF_ARCHIVE_DIR"),
                   help="the agents' event archive as files (local runs: <agents --store-dir>/archive) "
                        "[FF_ARCHIVE_DIR]")
    p.add_argument("--kusto-db", default=os.environ.get("KUSTO_DATABASE", "footballfoundry"), help="[KUSTO_DATABASE]")
    p.add_argument("--agents-url", default=os.environ.get("AGENTS_URL"),
                   help="footballfoundry-agents API, for get_insights [AGENTS_URL]")
    p.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="[HOST]")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8001")), help="[PORT]")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_server(build_backend(args), agents_url=args.agents_url, host=args.host, port=args.port) \
        .run(transport=args.transport)


def build_backend(args) -> MatchData:
    """Every configured source, in a fallback chain.

    Live-match tools: the agents' archive (current as events arrive), then the Eventhouse, then season files.
    Season queries: the Eventhouse (built for analytics), then the archive's match results, then season files.
    """
    files = FileBackend(args.data, live=args.live, league=args.league)
    league_path = args.league or os.path.join(args.data, "league.json")
    archive = kusto = None
    if args.cosmos_endpoint:
        from azure.identity.aio import DefaultAzureCredential as AsyncCredential

        from footballfoundry_mcp.archive import CosmosArchive

        archive = ArchiveBackend(CosmosArchive(args.cosmos_endpoint, args.cosmos_db, AsyncCredential()),
                                 files.fixtures, files.league)
    elif args.archive and Path(args.archive).exists():
        from footballfoundry_mcp.archive import FileArchive

        archive = ArchiveBackend(FileArchive(args.archive), files.fixtures, files.league)
    if args.kusto_uri:
        from azure.identity import DefaultAzureCredential

        kusto = KustoBackend(args.kusto_uri, args.kusto_db, league_path, DefaultAzureCredential())
    live = [(n, b) for n, b in (("archive", archive), ("eventhouse", kusto), ("files", files)) if b is not None]
    season = [(n, b) for n, b in (("eventhouse", kusto), ("archive", archive), ("files", files)) if b is not None]
    logging.getLogger(__name__).info("live tools: %s; season queries: %s", " -> ".join(n for n, _ in live),
                                     " -> ".join(n for n, _ in season))
    return FallbackBackend(live, season)


if __name__ == "__main__":
    main()

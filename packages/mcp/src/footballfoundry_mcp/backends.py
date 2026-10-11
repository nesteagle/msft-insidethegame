"""Where the MCP tools read from.

    FileBackend      the generator's output folder (`footballfoundry season`), plus an optional live JSONL file
                     (`footballfoundry stream --sink file`), for local development
    KustoBackend     the Fabric Eventhouse fed by Eventstream (tables and functions in fabric/eventhouse/)
    ArchiveBackend   the agents service's event archive (Cosmos DB in Azure, files locally): current as events
                     arrive, and the fallback when the Eventhouse is unavailable or still ingesting
    FallbackBackend  tries backends in order, with a timeout each: live-match tools and season queries can use
                     different orders

All return events up to `as_of` only, and season queries only over matchdays before the match being analysed,
so a replayed season can't leak results from the future.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol

from footballfoundry_schema import Event, League

log = logging.getLogger(__name__)

SEASON_QUERIES = {
    "standings": "League table. params: none",
    "top_scorers": "Leading scorers. params: top (default 10)",
    "team_form": "A club's latest results. params: club_id, last (default 5)",
    "player_season": "A player's season totals: appearances, goals, shots, xG. params: player_id",
    "head_to_head": "Earlier meetings this season. params: club_a, club_b",
}


class MatchData(Protocol):
    league: League | None

    async def events(self, match_id: str, as_of: int) -> list[Event]: ...

    async def matchday(self, match_id: str) -> int | None: ...

    async def season(self, name: str, params: dict[str, Any], before_matchday: int | None) -> list[dict[str, Any]]: ...


def _load_league(path: Path) -> League | None:
    return League.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


# -- files ---------------------------------------------------------------------------------------------------


class FileBackend:
    def __init__(self, data_dir: str | Path, live: str | Path | None = None, league: str | Path | None = None) -> None:
        self.dir = Path(data_dir)
        self.live = Path(live) if live else None
        self.league = _load_league(Path(league) if league else self.dir / "league.json")
        fixtures = self.dir / "fixtures.json"
        self.fixtures = {f["match_id"]: f for f in json.loads(fixtures.read_text(encoding="utf-8"))} \
            if fixtures.exists() else {}
        self._cache: dict[Path, tuple[float, list[Event]]] = {}
        self._index: dict[str, Any] | None = None

    def _read(self, path: Path) -> list[Event]:
        mtime = path.stat().st_mtime
        cached = self._cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        with path.open(encoding="utf-8") as f:
            events = [Event.model_validate_json(line) for line in f if line.strip()]
        self._cache[path] = (mtime, events)
        return events

    async def events(self, match_id: str, as_of: int) -> list[Event]:
        found: list[Event] = []
        if self.live and self.live.exists():
            found = [e for e in await asyncio.to_thread(self._read, self.live) if e.match_id == match_id]
            # The live file may hold several runs of the same match; keep the latest run only.
            starts = [i for i, e in enumerate(found) if e.seq == 1]
            found = found[starts[-1]:] if starts else found
        path = self.dir / "events" / f"{match_id}.jsonl"
        if not found and path.exists():
            found = await asyncio.to_thread(self._read, path)
        return [e for e in found if e.seq <= as_of]

    async def matchday(self, match_id: str) -> int | None:
        f = self.fixtures.get(match_id)
        return f["matchday"] if f else None

    def _build_index(self) -> dict[str, Any]:
        """One pass over the season's files: results per match, and shots and appearances per player."""
        results, shots, apps = [], defaultdict(lambda: defaultdict(float)), defaultdict(int)
        for path in sorted((self.dir / "matches").glob("*.json")):
            info = json.loads(path.read_text(encoding="utf-8"))
            md = info["matchday"]
            results.append({"match_id": info["match_id"], "matchday": md, "home": info["home_club_id"],
                            "away": info["away_club_id"], "home_score": info["home_score"],
                            "away_score": info["away_score"]})
            on: set[str] = {e["player_id"] for e in info["lineups"] if e["starter"]}
            ev_path = self.dir / "events" / f"{info['match_id']}.jsonl"
            if ev_path.exists():
                with ev_path.open(encoding="utf-8") as f:
                    for line in f:
                        if '"type":"shot"' in line:
                            e = json.loads(line)
                            s = shots[(e["player_id"], md)]
                            s["shots"] += 1
                            s["xg"] += e.get("xg") or 0.0
                            s["goals"] += e.get("outcome") == "goal"
                        elif '"type":"substitution"' in line:
                            on.add(json.loads(line)["related_player_id"])
            for pid in on:
                apps[(pid, md)] += 1
        return {"results": results, "shots": shots, "apps": apps}

    async def season(self, name: str, params: dict[str, Any], before_matchday: int | None) -> list[dict[str, Any]]:
        if self._index is None:
            self._index = await asyncio.to_thread(self._build_index)
        return season_from_index(self._index, self.league, name, params, before_matchday)


def season_from_index(index: dict[str, Any], league: League | None, name: str, params: dict[str, Any],
                      before_matchday: int | None) -> list[dict[str, Any]]:
    """Named season queries over an index of results, and shots and appearances per (player, matchday)."""
    cut = before_matchday if before_matchday is not None else 10_000
    results = [r for r in index["results"] if r["matchday"] < cut]
    names = {p.player_id: (p.name, p.club_id) for p in league.players} if league else {}

    if name == "standings":
        return standings(results, league)
    if name == "top_scorers":
        totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for (pid, md), sh in index["shots"].items():
            if md < cut:
                for k, v in sh.items():
                    totals[pid][k] += v
        rows = [{"player_id": pid, "name": names.get(pid, (None,))[0], "club_id": names.get(pid, (None, None))[1],
                 "goals": int(sh["goals"]), "shots": int(sh["shots"]), "xg": round(sh["xg"], 2)}
                for pid, sh in totals.items() if sh["goals"]]
        rows.sort(key=lambda r: (-r["goals"], -r["xg"]))
        return rows[: int(params.get("top", 10))]
    if name == "team_form":
        club = params["club_id"]
        games = sorted((r for r in results if club in (r["home"], r["away"])), key=lambda r: r["matchday"])
        return [_result_row(r, club) for r in games[-int(params.get("last", 5)):]]
    if name == "player_season":
        pid = params["player_id"]
        tot: dict[str, float] = defaultdict(float)
        for (p_, md), v in index["shots"].items():
            if p_ == pid and md < cut:
                for k, x in v.items():
                    tot[k] += x
        appearances = sum(n for (p_, md), n in index["apps"].items() if p_ == pid and md < cut)
        return [{"player_id": pid, "name": names.get(pid, (None,))[0], "appearances": appearances,
                 "goals": int(tot["goals"]), "shots": int(tot["shots"]), "xg": round(tot["xg"], 2)}]
    if name == "head_to_head":
        a, b = params["club_a"], params["club_b"]
        return [r for r in results if {r["home"], r["away"]} == {a, b}]
    raise ValueError(f"unknown query {name!r}; choose one of {sorted(SEASON_QUERIES)}")


def _result_row(r: dict[str, Any], club: str) -> dict[str, Any]:
    home = r["home"] == club
    gf, ga = (r["home_score"], r["away_score"]) if home else (r["away_score"], r["home_score"])
    return {"match_id": r["match_id"], "matchday": r["matchday"], "opponent": r["away"] if home else r["home"],
            "venue": "home" if home else "away", "goals_for": gf, "goals_against": ga,
            "result": "W" if gf > ga else "D" if gf == ga else "L"}


def standings(results: list[dict[str, Any]], league: League | None) -> list[dict[str, Any]]:
    clubs = [c.club_id for c in league.clubs] if league else sorted({r["home"] for r in results})
    table = {c: {"club_id": c, "played": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0, "goals_against": 0,
                 "points": 0} for c in clubs}
    for r in results:
        for club in (r["home"], r["away"]):
            row, res = table[club], _result_row(r, club)
            row["played"] += 1
            row["goals_for"] += res["goals_for"]
            row["goals_against"] += res["goals_against"]
            row[{"W": "won", "D": "drawn", "L": "lost"}[res["result"]]] += 1
            row["points"] += {"W": 3, "D": 1, "L": 0}[res["result"]]
    rows = sorted(table.values(), key=lambda t: (-t["points"], t["goals_against"] - t["goals_for"], -t["goals_for"]))
    for i, row in enumerate(rows, 1):
        row["position"] = i
    return rows


# -- Eventhouse ----------------------------------------------------------------------------------------------


class KustoBackend:
    """Queries the Eventhouse (KQL database) with the server's managed identity. Functions FF_* are defined in
    fabric/eventhouse/functions.kql; the tables in fabric/eventhouse/tables.kql."""

    def __init__(self, cluster_uri: str, database: str, league: str | Path | None, credential) -> None:
        from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

        self.client = KustoClient(KustoConnectionStringBuilder.with_azure_token_credential(cluster_uri, credential))
        self.database = database
        self.league = _load_league(Path(league)) if league else None

    def _run(self, kql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        from azure.kusto.data import ClientRequestProperties

        crp = ClientRequestProperties()
        decl = []
        for k, v in params.items():
            kind = "long" if isinstance(v, int) else "string"
            decl.append(f"{k}:{kind}")
            crp.set_parameter(k, v)
        text = (f"declare query_parameters({', '.join(decl)});\n" if decl else "") + kql
        table = self.client.execute(self.database, text, crp).primary_results[0]
        cols = [c.column_name for c in table.columns]
        return [dict(zip(cols, row)) for row in table.raw_rows]

    async def _query(self, kql: str, **params: Any) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._run, kql, params)

    async def events(self, match_id: str, as_of: int) -> list[Event]:
        rows = await self._query("FF_MatchEvents(m, s)", m=match_id, s=as_of)
        unique = {r["event_id"]: r for r in rows}  # Event Hubs delivers at least once
        return sorted((Event.model_validate(r) for r in unique.values()), key=lambda e: e.seq)

    async def matchday(self, match_id: str) -> int | None:
        rows = await self._query("Fixtures | where match_id == m | project matchday | take 1", m=match_id)
        return int(rows[0]["matchday"]) if rows else None

    async def season(self, name: str, params: dict[str, Any], before_matchday: int | None) -> list[dict[str, Any]]:
        md = before_matchday if before_matchday is not None else 10_000
        if name == "standings":
            return await self._query("FF_Standings(md)", md=md)
        if name == "top_scorers":
            return await self._query("FF_TopScorers(md, n)", md=md, n=int(params.get("top", 10)))
        if name == "team_form":
            return await self._query("FF_TeamForm(club, md, n)", club=params["club_id"], md=md,
                                     n=int(params.get("last", 5)))
        if name == "player_season":
            return await self._query("FF_PlayerSeason(player, md)", player=params["player_id"], md=md)
        if name == "head_to_head":
            return await self._query("FF_HeadToHead(a, b, md)", a=params["club_a"], b=params["club_b"], md=md)
        raise ValueError(f"unknown query {name!r}; choose one of {sorted(SEASON_QUERIES)}")


# -- the agents' event archive -------------------------------------------------------------------------------


class ArchiveBackend:
    """Reads the archive the agents service writes (footballfoundry_mcp.archive). Matchdays come from the fixture
    list, so one-off matches (X...) count for live tools but not for season queries."""

    def __init__(self, archive, fixtures: dict[str, dict] | None, league: League | None, *,
                 cache_s: float = 30) -> None:
        self.archive = archive
        self.fixtures = fixtures or {}
        self.league = league
        self.cache_s = cache_s
        self._index: tuple[float, dict[str, Any]] | None = None

    async def events(self, match_id: str, as_of: int) -> list[Event]:
        return await self.archive.events(match_id, as_of)

    async def matchday(self, match_id: str) -> int | None:
        f = self.fixtures.get(match_id)
        return f["matchday"] if f else None

    async def _build_index(self) -> dict[str, Any]:
        results, shots, apps = [], defaultdict(lambda: defaultdict(float)), defaultdict(int)
        for doc in await self.archive.results():
            md = await self.matchday(doc["match_id"])
            if md is None:
                continue
            results.append({"match_id": doc["match_id"], "matchday": md, "home": doc["home"], "away": doc["away"],
                            "home_score": doc["home_score"], "away_score": doc["away_score"]})
            for pid, v in doc.get("players", {}).items():
                sh = shots[(pid, md)]
                for k in ("shots", "goals", "xg"):
                    sh[k] += v.get(k, 0)
                apps[(pid, md)] += 1
        return {"results": results, "shots": shots, "apps": apps}

    async def season(self, name: str, params: dict[str, Any], before_matchday: int | None) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._index is None or now - self._index[0] > self.cache_s:
            self._index = (now, await self._build_index())
        return season_from_index(self._index[1], self.league, name, params, before_matchday)


# -- fallback chain --------------------------------------------------------------------------------------------


class FallbackBackend:
    """Tries backends in order. A backend that fails, times out or has nothing for the request is skipped."""

    def __init__(self, live: list[tuple[str, MatchData]], season: list[tuple[str, MatchData]],
                 *, timeout_s: float = 5.0) -> None:
        self.live = live
        self.season_order = season
        self.timeout_s = timeout_s
        everyone = [b for _, b in live + season]
        self.league = next((b.league for b in everyone if b.league is not None), None)

    async def _try(self, chain: list[tuple[str, MatchData]], call, empty):
        for name, backend in chain:
            try:
                result = await asyncio.wait_for(call(backend), self.timeout_s)
            except Exception as exc:  # noqa: BLE001 - any backend failure moves on to the next one
                log.warning("%s backend failed (%s: %s); trying the next one", name, type(exc).__name__, exc)
                continue
            if result:
                return result, name
        return empty, None

    async def events_with_source(self, match_id: str, as_of: int) -> tuple[list[Event], str | None]:
        return await self._try(self.live, lambda b: b.events(match_id, as_of), [])

    async def events(self, match_id: str, as_of: int) -> list[Event]:
        return (await self.events_with_source(match_id, as_of))[0]

    async def matchday(self, match_id: str) -> int | None:
        return (await self._try(self.live + self.season_order, lambda b: b.matchday(match_id), None))[0]

    async def season(self, name: str, params: dict[str, Any], before_matchday: int | None) -> list[dict[str, Any]]:
        return (await self._try(self.season_order, lambda b: b.season(name, params, before_matchday), []))[0]

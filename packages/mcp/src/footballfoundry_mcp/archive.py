"""The operational event archive: every event and a result per finished match, written by the agents service.

The agents service consumes each event once (the `analyst` consumer group splits partitions across replicas)
and appends it here; at full time it adds a small result document. The MCP server reads it back:

    live-match tools   archive first: it's written as events arrive, so it's current even when the Fabric
                       Eventhouse is still ingesting (or unavailable)
    season queries     Eventhouse first, archive as the fallback (computed from the result documents)

Backends: memory (tests), file (local: <store-dir>/archive), Cosmos DB (Azure: containers `events` and
`matchResults`, both partitioned by /matchId).

A match streamed again from kick-off starts a new `run`; readers only return the latest run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from footballfoundry_schema import Event


class EventArchive(Protocol):
    async def append(self, match_id: str, run: int, events: list[Event]) -> None: ...
    async def put_result(self, doc: dict[str, Any]) -> None: ...
    async def events(self, match_id: str, as_of: int) -> list[Event]: ...
    async def results(self) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


def result_doc(match_id: str, run: int, home: str, away: str, home_score: int, away_score: int,
               players: dict[str, dict[str, float]]) -> dict[str, Any]:
    """What season queries need from a finished match: the score and each player's shots, goals and xG."""
    return {
        "match_id": match_id, "run": run, "home": home, "away": away,
        "home_score": home_score, "away_score": away_score,
        "players": {pid: {"shots": int(s.get("shots", 0)), "goals": int(s.get("goals", 0)),
                          "xg": round(float(s.get("xg", 0.0)), 3)} for pid, s in players.items()},
    }


# -- memory --------------------------------------------------------------------------------------------------


class MemoryArchive:
    def __init__(self) -> None:
        self.runs: dict[str, int] = {}
        self.data: dict[str, list[Event]] = {}
        self.res: dict[str, dict[str, Any]] = {}

    async def append(self, match_id: str, run: int, events: list[Event]) -> None:
        if self.runs.get(match_id) != run:
            self.runs[match_id], self.data[match_id] = run, []
        seen = {e.seq for e in self.data[match_id]}
        self.data[match_id] += [e for e in events if e.seq not in seen]

    async def put_result(self, doc: dict[str, Any]) -> None:
        self.res[doc["match_id"]] = doc

    async def events(self, match_id: str, as_of: int) -> list[Event]:
        return sorted((e for e in self.data.get(match_id, []) if e.seq <= as_of), key=lambda e: e.seq)

    async def results(self) -> list[dict[str, Any]]:
        return list(self.res.values())

    async def close(self) -> None:
        pass


# -- files ---------------------------------------------------------------------------------------------------


class FileArchive:
    """<root>/events/<match>.jsonl (+ .run with the current run) and <root>/results/<match>.json."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        (self.root / "events").mkdir(parents=True, exist_ok=True)
        (self.root / "results").mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _append(self, match_id: str, run: int, events: list[Event]) -> None:
        path, run_file = self.root / "events" / f"{match_id}.jsonl", self.root / "events" / f"{match_id}.run"
        new_run = not run_file.exists() or run_file.read_text().strip() != str(run)
        if new_run:
            run_file.write_text(str(run))
        with path.open("w" if new_run else "a", encoding="utf-8", newline="\n") as f:
            for e in events:
                f.write(e.model_dump_json() + "\n")

    async def append(self, match_id: str, run: int, events: list[Event]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._append, match_id, run, events)

    async def put_result(self, doc: dict[str, Any]) -> None:
        path = self.root / "results" / f"{doc['match_id']}.json"
        await asyncio.to_thread(path.write_text, json.dumps(doc), encoding="utf-8")

    def _read(self, match_id: str, as_of: int) -> list[Event]:
        path = self.root / "events" / f"{match_id}.jsonl"
        if not path.exists():
            return []
        unique: dict[int, Event] = {}
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    e = Event.model_validate_json(line)
                    if e.seq <= as_of:
                        unique[e.seq] = e
        return [unique[k] for k in sorted(unique)]

    async def events(self, match_id: str, as_of: int) -> list[Event]:
        return await asyncio.to_thread(self._read, match_id, as_of)

    async def results(self) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.root / "results").glob("*.json"))]
        return await asyncio.to_thread(read)

    async def close(self) -> None:
        pass


# -- Cosmos DB -----------------------------------------------------------------------------------------------


class CosmosArchive:
    """Containers `events` (one document per event, id = event_id) and `matchResults` (one per match).
    Created by the Bicep templates; with Entra ID data roles the app can't create containers itself."""

    def __init__(self, endpoint: str, database: str, credential, *, concurrency: int = 16) -> None:
        from azure.cosmos.aio import CosmosClient

        self.client = CosmosClient(endpoint, credential=credential)
        db = self.client.get_database_client(database)
        self.ev = db.get_container_client("events")
        self.res = db.get_container_client("matchResults")
        self._sem = asyncio.Semaphore(concurrency)

    async def append(self, match_id: str, run: int, events: list[Event]) -> None:
        async def put(e: Event) -> None:
            async with self._sem:
                await self.ev.upsert_item(e.model_dump(mode="json") | {"id": e.event_id, "matchId": match_id,
                                                                      "run": run})
        await asyncio.gather(*(put(e) for e in events))

    async def put_result(self, doc: dict[str, Any]) -> None:
        await self.res.upsert_item(doc | {"id": doc["match_id"], "matchId": doc["match_id"]})

    async def events(self, match_id: str, as_of: int) -> list[Event]:
        runs = [r async for r in self.ev.query_items("SELECT VALUE MAX(c.run) FROM c WHERE c.matchId = @m",
                                                     parameters=[{"name": "@m", "value": match_id}],
                                                     partition_key=match_id)]
        if not runs or runs[0] is None:
            return []
        query = "SELECT * FROM c WHERE c.matchId = @m AND c.run = @r AND c.seq <= @s ORDER BY c.seq"
        params = [{"name": "@m", "value": match_id}, {"name": "@r", "value": runs[0]},
                  {"name": "@s", "value": as_of}]
        return [Event.model_validate(d) async for d in self.ev.query_items(query, parameters=params,
                                                                            partition_key=match_id)]

    async def results(self) -> list[dict[str, Any]]:
        return [d async for d in self.res.query_items("SELECT * FROM c")]

    async def close(self) -> None:
        await self.client.close()

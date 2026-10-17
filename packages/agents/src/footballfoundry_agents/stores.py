"""Where insights, match state snapshots and viewer profiles live.

Three interchangeable backends:
    memory   tests and quick local runs; nothing survives a restart
    file     local development; JSON files under a directory
    cosmos   Azure Cosmos DB for NoSQL, signed in with Entra ID (containers: insights, matchState, profiles)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from footballfoundry_schema import Audience, Insight


class Profile(BaseModel):
    user_id: str
    audience: Audience = "casual"
    language: str = "en"
    favourite_club_ids: list[str] = Field(default_factory=list)
    favourite_player_ids: list[str] = Field(default_factory=list)
    focus_player_id: str | None = Field(default=None, description="Player-focus mode: follow this player all match")


class InsightStore(Protocol):
    async def put(self, insight: Insight) -> None: ...
    async def get(self, match_id: str, insight_id: str) -> Insight | None: ...
    async def list(self, match_id: str, as_of: int | None = None) -> list[Insight]: ...


class StateStore(Protocol):
    async def save(self, snapshot: dict[str, Any]) -> None: ...
    async def load(self, match_id: str) -> dict[str, Any] | None: ...
    async def summaries(self) -> list[dict[str, Any]]: ...


SUMMARY_FIELDS = ("match_id", "home_id", "away_id", "home_score", "away_score", "clock", "period", "last_seq",
                  "finished")


def _summary(snap: dict[str, Any]) -> dict[str, Any]:
    return {k: snap.get(k) for k in SUMMARY_FIELDS}


class ProfileStore(Protocol):
    async def get(self, user_id: str) -> Profile | None: ...
    async def put(self, profile: Profile) -> None: ...


@dataclass
class Stores:
    insights: InsightStore
    state: StateStore
    profiles: ProfileStore
    close: Any = None  # async callable, if the backend holds connections


def _visible(items: list[Insight], as_of: int | None) -> list[Insight]:
    """The latest run's insights up to `as_of`. A match streamed again (perhaps with a scenario, so a different
    match) overwrites the insights it repeats, but the earlier run's others stay stored: they must not show."""
    latest = max((i.run_id for i in items), default=0)
    return sorted((i for i in items if i.run_id == latest and (as_of is None or i.seq <= as_of)),
                  key=lambda i: (i.seq, i.insight_id))


# -- memory --------------------------------------------------------------------------------------------------


class MemoryInsights:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Insight]] = {}

    async def put(self, insight: Insight) -> None:
        self.data.setdefault(insight.match_id, {})[insight.insight_id] = insight

    async def get(self, match_id: str, insight_id: str) -> Insight | None:
        return self.data.get(match_id, {}).get(insight_id)

    async def list(self, match_id: str, as_of: int | None = None) -> list[Insight]:
        return _visible(list(self.data.get(match_id, {}).values()), as_of)


class MemoryState:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    async def save(self, snapshot: dict[str, Any]) -> None:
        self.data[snapshot["match_id"]] = snapshot

    async def load(self, match_id: str) -> dict[str, Any] | None:
        return self.data.get(match_id)

    async def summaries(self) -> list[dict[str, Any]]:
        return [_summary(s) for s in self.data.values()]


class MemoryProfiles:
    def __init__(self) -> None:
        self.data: dict[str, Profile] = {}

    async def get(self, user_id: str) -> Profile | None:
        return self.data.get(user_id)

    async def put(self, profile: Profile) -> None:
        self.data[profile.user_id] = profile


def memory_stores() -> Stores:
    return Stores(MemoryInsights(), MemoryState(), MemoryProfiles())


# -- file ----------------------------------------------------------------------------------------------------


class _JsonDir:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def read(self, key: str) -> Any:
        p = self.path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def write(self, key: str, data: Any) -> None:
        tmp = self.path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path(key))


class FileInsights(MemoryInsights):
    """One JSON file per match: {insight_id: insight}. Loaded lazily, written through."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.dir = _JsonDir(root)

    def _load(self, match_id: str) -> dict[str, Insight]:
        if match_id not in self.data:
            raw = self.dir.read(match_id) or {}
            self.data[match_id] = {k: Insight.model_validate(v) for k, v in raw.items()}
        return self.data[match_id]

    async def put(self, insight: Insight) -> None:
        async with self.dir.lock:
            items = self._load(insight.match_id)
            items[insight.insight_id] = insight
            self.dir.write(insight.match_id, {k: v.model_dump(mode="json") for k, v in items.items()})

    async def get(self, match_id: str, insight_id: str) -> Insight | None:
        return self._load(match_id).get(insight_id)

    async def list(self, match_id: str, as_of: int | None = None) -> list[Insight]:
        return _visible(list(self._load(match_id).values()), as_of)


class FileState:
    def __init__(self, root: Path) -> None:
        self.dir = _JsonDir(root)

    async def save(self, snapshot: dict[str, Any]) -> None:
        self.dir.write(snapshot["match_id"], snapshot)

    async def load(self, match_id: str) -> dict[str, Any] | None:
        return self.dir.read(match_id)

    async def summaries(self) -> list[dict[str, Any]]:
        return [_summary(self.dir.read(p.stem)) for p in sorted(self.dir.root.glob("*.json"))]


class FileProfiles:
    def __init__(self, root: Path) -> None:
        self.dir = _JsonDir(root)

    async def get(self, user_id: str) -> Profile | None:
        raw = self.dir.read(user_id)
        return Profile.model_validate(raw) if raw else None

    async def put(self, profile: Profile) -> None:
        self.dir.write(profile.user_id, profile.model_dump(mode="json"))


def file_stores(root: str | Path) -> Stores:
    root = Path(root)
    return Stores(FileInsights(root / "insights"), FileState(root / "state"), FileProfiles(root / "profiles"))


# -- Cosmos DB -----------------------------------------------------------------------------------------------


class CosmosInsights:
    def __init__(self, container) -> None:
        self.c = container

    async def put(self, insight: Insight) -> None:
        doc = insight.model_dump(mode="json") | {"id": insight.insight_id, "matchId": insight.match_id}
        await self.c.upsert_item(doc)

    async def get(self, match_id: str, insight_id: str) -> Insight | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return Insight.model_validate(await self.c.read_item(insight_id, partition_key=match_id))
        except CosmosResourceNotFoundError:
            return None

    async def list(self, match_id: str, as_of: int | None = None) -> list[Insight]:
        # Every insight of the match (a few dozen), not only those up to as_of: the latest run is picked from all.
        items = self.c.query_items("SELECT * FROM c WHERE c.matchId = @m",
                                   parameters=[{"name": "@m", "value": match_id}], partition_key=match_id)
        return _visible([Insight.model_validate(d) async for d in items], as_of)


class CosmosState:
    def __init__(self, container) -> None:
        self.c = container

    async def save(self, snapshot: dict[str, Any]) -> None:
        await self.c.upsert_item(snapshot | {"id": snapshot["match_id"], "matchId": snapshot["match_id"]})

    async def load(self, match_id: str) -> dict[str, Any] | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return await self.c.read_item(match_id, partition_key=match_id)
        except CosmosResourceNotFoundError:
            return None

    async def summaries(self) -> list[dict[str, Any]]:
        query = "SELECT " + ", ".join(f"c.{k}" for k in SUMMARY_FIELDS) + " FROM c"
        return [_summary(d) async for d in self.c.query_items(query)]


class CosmosProfiles:
    def __init__(self, container) -> None:
        self.c = container

    async def get(self, user_id: str) -> Profile | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return Profile.model_validate(await self.c.read_item(user_id, partition_key=user_id))
        except CosmosResourceNotFoundError:
            return None

    async def put(self, profile: Profile) -> None:
        await self.c.upsert_item(profile.model_dump(mode="json") | {"id": profile.user_id, "userId": profile.user_id})


def cosmos_stores(endpoint: str, database: str, credential) -> Stores:
    """Containers are created by the Bicep templates; with Entra ID data roles the app can't create them."""
    from azure.cosmos.aio import CosmosClient

    client = CosmosClient(endpoint, credential=credential)
    db = client.get_database_client(database)
    return Stores(
        CosmosInsights(db.get_container_client("insights")),
        CosmosState(db.get_container_client("matchState")),
        CosmosProfiles(db.get_container_client("profiles")),
        close=client.close,
    )

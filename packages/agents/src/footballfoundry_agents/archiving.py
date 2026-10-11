"""Writes every event, and a result per finished match, to the event archive the MCP server reads.

Event handling never waits for the archive: events are queued and flushed in batches in the background, so a slow
or unavailable Cosmos DB costs the archive a few writes (logged), never the live analysis.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from footballfoundry_mcp.archive import EventArchive, result_doc
from footballfoundry_schema import Event

from footballfoundry_agents.state import MatchState

log = logging.getLogger(__name__)

FLUSH_S = 0.5
BATCH = 200


class ArchiveWriter:
    def __init__(self, archive: EventArchive) -> None:
        self.archive = archive
        self._queue: asyncio.Queue[tuple[str, int, Event] | dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def add(self, state: MatchState, e: Event) -> None:
        self._start()
        self._queue.put_nowait((state.match_id, state.run_id, e))
        if e.type == "period_end" and e.period == 2 and e.team_id == state.away_id:
            self._queue.put_nowait(result_doc(state.match_id, state.run_id, state.home_id or "", state.away_id or "",
                                              state.home_score, state.away_score, state.players))

    def _start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="archive-writer")

    async def _run(self) -> None:
        while True:
            items = [await self._queue.get()]
            await asyncio.sleep(FLUSH_S)  # let a batch build up
            while not self._queue.empty() and len(items) < BATCH * 4:
                items.append(self._queue.get_nowait())
            await self._write(items)
            for _ in items:
                self._queue.task_done()

    async def _write(self, items: list) -> None:
        batches: dict[tuple[str, int], list[Event]] = defaultdict(list)
        results = []
        for item in items:
            if isinstance(item, dict):
                results.append(item)
            else:
                match_id, run, e = item
                batches[(match_id, run)].append(e)
        for (match_id, run), events in batches.items():
            for i in range(0, len(events), BATCH):
                try:
                    await self.archive.append(match_id, run, events[i:i + BATCH])
                except Exception:  # noqa: BLE001 - the archive is a copy; live analysis carries on
                    log.exception("archive: could not write %d events of %s", len(events[i:i + BATCH]), match_id)
        for doc in results:  # after the events, so a result never points at a match the archive lacks
            try:
                await self.archive.put_result(doc)
            except Exception:  # noqa: BLE001
                log.exception("archive: could not write the result of %s", doc["match_id"])

    async def flush(self) -> None:
        if self._task is not None:
            await self._queue.join()

    async def close(self) -> None:
        await self.flush()
        if self._task is not None:
            self._task.cancel()
        await self.archive.close()

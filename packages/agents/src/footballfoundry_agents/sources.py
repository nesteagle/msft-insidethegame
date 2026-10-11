"""Where events come from: a JSONL stream (stdin or a file) or Azure Event Hubs."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable, Protocol, TextIO

from footballfoundry_schema import Event

log = logging.getLogger(__name__)

Handle = Callable[[Event], Awaitable[None]]


class Source(Protocol):
    async def run(self, handle: Handle) -> None: ...


class JsonlSource:
    """Reads `footballfoundry stream` output: one Event JSON per line. Ends at end of input."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    @classmethod
    def open(cls, spec: str) -> JsonlSource:
        return cls(sys.stdin if spec in ("-", "stdin") else open(spec, encoding="utf-8"))

    async def run(self, handle: Handle) -> None:
        while True:
            line = await asyncio.to_thread(self.stream.readline)
            if not line:
                return
            if line.strip():
                await handle(Event.model_validate_json(line))


class FollowSource:
    """Tails the JSONL file `footballfoundry stream --sink file` writes, like `tail -f`, so the stream and the
    service can run in separate terminals and start in either order.

    Waits for the file to appear, reads what's already there (catch-up), then follows new lines. Restarting the
    stream truncates the file; that's noticed and the file is read again from the top. Never ends on its own.
    """

    def __init__(self, path: str | Path, poll_s: float = 0.2) -> None:
        self.path = Path(path)
        self.poll_s = poll_s

    async def run(self, handle: Handle) -> None:
        pos, partial, waiting_logged = 0, b"", False
        while True:
            try:
                size = self.path.stat().st_size
            except FileNotFoundError:
                if not waiting_logged:
                    log.info("waiting for %s (start: footballfoundry stream --sink file --out %s)", self.path,
                             self.path)
                    waiting_logged = True
                await asyncio.sleep(self.poll_s * 5)
                continue
            if size < pos:  # truncated: the stream was restarted
                log.info("%s was restarted, reading it from the top", self.path)
                pos, partial = 0, b""
            if size == pos:
                await asyncio.sleep(self.poll_s)
                continue
            with self.path.open("rb") as f:
                f.seek(pos)
                chunk = f.read(size - pos)
            pos += len(chunk)
            *lines, partial = (partial + chunk).split(b"\n")  # the last piece may be a half-written line
            for line in lines:
                if not line.strip():
                    continue
                try:
                    event = Event.model_validate_json(line)
                except ValueError:
                    log.warning("skipping a line that isn't an event: %.80r", line)
                    continue
                await handle(event)


class EventHubSource:
    """Consumes `match-events` with a consumer group, checkpointing to Blob Storage when configured.

    Events of one match share a partition (partition key = match_id), so they arrive in order. With a checkpoint
    store, several replicas share the partitions between them and a restart resumes where it left off.
    """

    def __init__(self, *, eventhub: str, consumer_group: str, credential, namespace: str | None = None,
                 connection_string: str | None = None, checkpoint_url: str | None = None,
                 checkpoint_container: str = "checkpoints", from_start: bool = False,
                 checkpoint_every: int = 50, checkpoint_s: float = 10.0) -> None:
        from azure.eventhub.aio import EventHubConsumerClient

        store = None
        if checkpoint_url:
            from azure.eventhub.extensions.checkpointstoreblobaio import BlobCheckpointStore

            store = BlobCheckpointStore(checkpoint_url, checkpoint_container, credential=credential)
        if connection_string:
            self.client = EventHubConsumerClient.from_connection_string(
                connection_string, consumer_group=consumer_group, eventhub_name=eventhub, checkpoint_store=store)
        else:
            fqns = namespace if "." in (namespace or "") else f"{namespace}.servicebus.windows.net"
            self.client = EventHubConsumerClient(fqns, eventhub, consumer_group, credential,
                                                 checkpoint_store=store)
        self.from_start = from_start
        self.checkpoint_every = checkpoint_every
        self.checkpoint_s = checkpoint_s
        self._since: dict[str, tuple[int, float]] = {}

    async def run(self, handle: Handle) -> None:
        async def on_event(ctx, data) -> None:
            if data is None:
                return
            try:
                await handle(Event.model_validate_json(data.body_as_str()))
            except Exception:  # noqa: BLE001 - one bad message must not stop the partition
                log.exception("failed to handle event on partition %s", ctx.partition_id)
            n, t = self._since.get(ctx.partition_id, (0, time.monotonic()))
            n += 1
            if n >= self.checkpoint_every or time.monotonic() - t >= self.checkpoint_s:
                await ctx.update_checkpoint(data)
                n, t = 0, time.monotonic()
            self._since[ctx.partition_id] = (n, t)

        async with self.client:
            await self.client.receive(on_event=on_event, starting_position="-1" if self.from_start else "@latest")

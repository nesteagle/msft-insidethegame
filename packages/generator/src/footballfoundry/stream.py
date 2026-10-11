"""Replay simulated matches as a live event stream.

Matches are simulated up front (about 0.2s each), then replayed on the match
clock: an event that happened at 23:14 is emitted 23m14s after kick-off at
--speed 1, or 2m19s at --speed 10. Several matches (a whole matchday) are
interleaved as if they kicked off together.

Sinks:
    JsonlSink      one JSON event per line to stdout or a file
    EventHubSink   Azure Event Hubs, partitioned by match_id so each match stays in order
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from typing import Callable, Iterable, Protocol, TextIO

from footballfoundry_schema import Event

HALF_TIME_S = 15 * 60
SEND_WINDOW_S = 0.05  # real seconds of events grouped into one send
FAST_CHUNK = 500  # events per send when --speed 0

Scheduled = tuple[float, Event]


def timeline(events: Iterable[Event], halftime_s: float = HALF_TIME_S) -> list[Scheduled]:
    """Seconds from kick-off for each event, including the half-time break."""
    events = list(events)
    p1_end = max((e.elapsed_s for e in events if e.period == 1), default=0.0)
    return [(e.elapsed_s if e.period == 1 else p1_end + halftime_s + e.elapsed_s, e) for e in events]


def merge(matches: Iterable[Iterable[Event]], halftime_s: float = HALF_TIME_S) -> list[Scheduled]:
    """Interleave several matches by match clock. The sort is stable, so each match keeps its own order."""
    items = [item for events in matches for item in timeline(events, halftime_s)]
    items.sort(key=lambda t: t[0])
    return items


class Sink(Protocol):
    def send(self, events: list[Event]) -> None: ...

    def close(self) -> None: ...


class JsonlSink:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def send(self, events: list[Event]) -> None:
        for e in events:
            self.stream.write(e.model_dump_json() + "\n")
        self.stream.flush()

    def close(self) -> None:
        if self.stream not in (sys.stdout, sys.stderr):
            self.stream.close()


def _producer_kwargs(namespace: str | None, connection_string: str | None) -> dict:
    if not (namespace or connection_string):
        raise SystemExit("Event Hubs needs --eventhub-namespace (or EVENTHUB_NAMESPACE) or EVENTHUB_CONNECTION_STRING")
    if connection_string:
        return {"conn_str": connection_string}
    from azure.identity import DefaultAzureCredential

    fqns = namespace if "." in namespace else f"{namespace}.servicebus.windows.net"
    return {"fully_qualified_namespace": fqns, "credential": DefaultAzureCredential()}


def _require_eventhub():
    try:
        import azure.eventhub  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Event Hubs support needs extra packages: pip install -e \".[azure]\"") from exc


class EventHubSink:
    """Sends each event as one JSON message. Partition key = match_id keeps a match in order."""

    def __init__(self, producer) -> None:
        self.producer = producer

    @classmethod
    def connect(cls, eventhub: str, namespace: str | None = None, connection_string: str | None = None) -> EventHubSink:
        _require_eventhub()
        from azure.eventhub import EventHubProducerClient

        kwargs = _producer_kwargs(namespace, connection_string)
        if "conn_str" in kwargs:
            producer = EventHubProducerClient.from_connection_string(kwargs["conn_str"], eventhub_name=eventhub)
        else:
            producer = EventHubProducerClient(eventhub_name=eventhub, **kwargs)
        return cls(producer)

    def send(self, events: list[Event]) -> None:
        from azure.eventhub import EventData

        by_match: dict[str, list[Event]] = defaultdict(list)
        for e in events:
            by_match[e.match_id].append(e)
        for match_id, match_events in by_match.items():
            batch = self.producer.create_batch(partition_key=match_id)
            for e in match_events:
                data = EventData(e.model_dump_json())
                data.content_type = "application/json"
                data.properties = {"match_id": e.match_id, "type": e.type}
                if e.stream_id:
                    data.properties["stream_id"] = e.stream_id
                try:
                    batch.add(data)
                except ValueError:  # batch full: send it and start another
                    self.producer.send_batch(batch)
                    batch = self.producer.create_batch(partition_key=match_id)
                    batch.add(data)
            self.producer.send_batch(batch)

    def close(self) -> None:
        self.producer.close()


def replay(
    items: list[Scheduled],
    sink: Sink,
    *,
    speed: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    on_send: Callable[[list[Event]], None] | None = None,
) -> int:
    """Emit scheduled events to `sink` at `speed` x real time (0 = as fast as possible). Returns events sent."""
    start = clock()
    sent = i = 0
    while i < len(items):
        if speed > 0:
            # Group events due within the same short real-time window into one send.
            window_end = items[i][0] + SEND_WINDOW_S * speed
            j = i + 1
            while j < len(items) and items[j][0] <= window_end:
                j += 1
            wait = start + items[i][0] / speed - clock()
            if wait > 0:
                sleep(wait)
        else:
            j = min(i + FAST_CHUNK, len(items))
        batch = [e for _, e in items[i:j]]
        sink.send(batch)
        if on_send:
            on_send(batch)
        sent += len(batch)
        i = j
    return sent


def listen(
    eventhub: str,
    on_event: Callable[[Event], None],
    *,
    namespace: str | None = None,
    connection_string: str | None = None,
    consumer_group: str = "$Default",
    from_start: bool = False,
) -> None:
    """Receive footballfoundry events from Event Hubs until interrupted (Ctrl+C)."""
    _require_eventhub()
    from azure.eventhub import EventHubConsumerClient

    kwargs = _producer_kwargs(namespace, connection_string)
    if "conn_str" in kwargs:
        client = EventHubConsumerClient.from_connection_string(
            kwargs["conn_str"], consumer_group=consumer_group, eventhub_name=eventhub)
    else:
        client = EventHubConsumerClient(consumer_group=consumer_group, eventhub_name=eventhub, **kwargs)

    def _handle(partition_context, event) -> None:
        if event is not None:
            on_event(Event.model_validate_json(event.body_as_str()))

    with client:
        client.receive(on_event=_handle, starting_position="-1" if from_start else "@latest")

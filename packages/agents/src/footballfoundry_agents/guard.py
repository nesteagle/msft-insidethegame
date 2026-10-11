"""First stream wins: keep two people streaming the same match id from corrupting it.

Every `footballfoundry stream` run stamps its events with a `stream_id`. A match belongs to the first stream that
sends it events. While that stream is live, events for the match from any other stream are dropped (and logged
once). When the owner finishes the match, or goes quiet (60 s, or 20 minutes at half-time, since a real-time stream
pauses for the break), the next stream to send events takes the match over.

Events without a stream_id (older streams, files written by `footballfoundry season`) are always accepted.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from footballfoundry_schema import Event

log = logging.getLogger(__name__)

IDLE_S = 60
HALF_TIME_IDLE_S = 20 * 60


@dataclass
class _Owner:
    stream_id: str
    last_seen: float
    at_half_time: bool = False
    finished: bool = False
    rejected: set[str] = field(default_factory=set)


class StreamGuard:
    def __init__(self, clock=time.monotonic) -> None:
        self.owners: dict[str, _Owner] = {}
        self.clock = clock

    def allow(self, e: Event) -> bool:
        if not e.stream_id:
            return True
        now = self.clock()
        owner = self.owners.get(e.match_id)
        if owner is None or owner.stream_id == e.stream_id or self._released(owner, now):
            if owner is not None and owner.stream_id != e.stream_id:
                log.info("%s: stream %s takes over from %s", e.match_id, e.stream_id, owner.stream_id)
                owner = None
            if owner is None:
                owner = self.owners[e.match_id] = _Owner(e.stream_id, now)
            owner.last_seen = now
            owner.at_half_time = e.type == "period_end" and e.period == 1
            owner.finished = owner.finished or (e.type == "period_end" and e.period == 2)
            return True
        if e.stream_id not in owner.rejected:
            owner.rejected.add(e.stream_id)
            log.warning("%s is already being streamed (stream %s); ignoring stream %s. Use another --match-id.",
                        e.match_id, owner.stream_id, e.stream_id)
        return False

    @staticmethod
    def _released(owner: _Owner, now: float) -> bool:
        idle = now - owner.last_seen
        return owner.finished or idle > (HALF_TIME_IDLE_S if owner.at_half_time else IDLE_S)

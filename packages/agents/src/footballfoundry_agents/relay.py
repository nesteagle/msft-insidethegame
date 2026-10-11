"""Every event of every match, for the web app and the chat tools.

The web app must show exactly the stream the agents analyse, so this service relays it. Each match's events are
kept in order with their time on the match clock (half-time included), and viewers replay from any point at any
speed, then follow live: a DVR over the match.

In Azure every replica fills its log from its own `viewer` consumer group read from the start of retention, so
any replica can serve any match. With a JSONL source the same events feed both the analysts and this log.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from footballfoundry_schema import Event

HALF_TIME_S = 15 * 60  # the same break `footballfoundry stream` uses


class MatchLog:
    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self.run = 0  # bumps when the match is streamed again from kick-off
        self.events: list[Event] = []
        self.times: list[float] = []
        self.p1_end: float | None = None
        self.finished = False
        self.updated = time.monotonic()  # when the last event arrived, to tell live matches from stalled ones
        self._changed = asyncio.Event()

    def add(self, e: Event) -> bool:
        if e.seq == 1 and self.events:
            run, changed = self.run, self._changed
            self.__init__(self.match_id)  # type: ignore[misc]
            self.run, self._changed = run + 1, changed  # waiting viewers wake up and see the new run
        if self.events and e.seq <= self.events[-1].seq:
            return False  # duplicate delivery
        if e.period == 1:
            t = e.elapsed_s
        else:
            t = (self.p1_end if self.p1_end is not None else 45 * 60) + HALF_TIME_S + e.elapsed_s
        if e.type == "period_end" and e.period == 1:
            self.p1_end = e.elapsed_s
        if e.type == "period_end" and e.period == 2:
            self.finished = True
        self.events.append(e)
        self.times.append(round(t, 2))
        self.updated = time.monotonic()
        self._changed.set()
        self._changed = asyncio.Event()
        return True

    async def wait(self, count: int, run: int, timeout: float) -> bool:
        """Wait until there are more than `count` events or a new run starts. False on timeout."""
        if len(self.events) > count or self.run != run:
            return True
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def up_to(self, seq: int | None) -> list[Event]:
        return list(self.events) if seq is None else [e for e in self.events if e.seq <= seq]

    def team_sheets(self) -> dict[str, Any]:
        """Formation, XI and bench per club, from each team's first kick-off event."""
        return {e.team_id: e.qualifiers for e in self.events[:4]
                if e.type == "period_start" and e.period == 1 and e.qualifiers.get("lineup")}

    def summary(self) -> dict[str, Any]:
        last = self.events[-1] if self.events else None
        starts = [e.team_id for e in self.events[:4] if e.type == "period_start" and e.period == 1]
        return {
            "match_id": self.match_id,
            "home_id": starts[0] if starts else None,
            "away_id": starts[1] if len(starts) > 1 else None,
            "home_score": last.home_score if last else 0,
            "away_score": last.away_score if last else 0,
            "clock": last.clock if last else "0'",
            "period": last.period if last else 0,
            "last_seq": last.seq if last else 0,
            "live_t": self.times[-1] if self.times else 0.0,
            "finished": self.finished,
            "idle_s": round(idle := time.monotonic() - self.updated),
            # Still receiving events. Half-time is quiet for up to 15 minutes at real-time speed.
            "live": not self.finished and bool(last) and idle < (
                20 * 60 if last.type == "period_end" and last.period == 1 else 60),
        }


class EventLog:
    def __init__(self) -> None:
        self.matches: dict[str, MatchLog] = {}

    def get(self, match_id: str) -> MatchLog:
        if match_id not in self.matches:
            self.matches[match_id] = MatchLog(match_id)
        return self.matches[match_id]

    def add(self, e: Event) -> bool:
        return self.get(e.match_id).add(e)

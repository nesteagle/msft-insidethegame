"""The analyst service: events in, insights out.

    event ─► MatchState.apply ─► TriggerEngine.check ─► Brief (frozen facts) ─► per-match queue
                                                                                   │
              viewers (SSE) ◄─ publish ◄─ InsightStore ◄─ writer (agents or templates)

Event handling never waits for a model: briefs go onto a per-match queue and a worker per match writes them in
order. If the agents fall behind, waiting filler (summaries) is dropped so the overlay stays close to live.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from footballfoundry_schema import Event, Insight, InsightVariant

from footballfoundry_agents.archiving import ArchiveWriter
from footballfoundry_agents.briefs import Brief, build
from footballfoundry_agents.guard import StreamGuard
from footballfoundry_agents.lore import Lore
from footballfoundry_agents.prematch import covered
from footballfoundry_agents.reference import Reference
from footballfoundry_agents.relay import EventLog
from footballfoundry_agents.state import MatchState
from footballfoundry_agents.stores import Stores
from footballfoundry_agents.templates import template_insight, template_variant
from footballfoundry_agents.triggers import TriggerEngine

log = logging.getLogger(__name__)


class Writer(Protocol):
    async def write(self, brief: Brief) -> Insight: ...

    async def personalize(self, insight: Insight, audience: str, language: str, club_id: str | None,
                          player_id: str | None) -> InsightVariant: ...


class TemplateWriter:
    """Offline writer: no LLM, no Azure."""

    def __init__(self, ref: Reference) -> None:
        self.ref = ref

    async def write(self, brief: Brief) -> Insight:
        return template_insight(brief, self.ref)

    async def personalize(self, insight: Insight, audience: str, language: str, club_id: str | None,
                          player_id: str | None) -> InsightVariant:
        return template_variant(insight, audience, language, club_id, player_id, self.ref)


class AnalystService:
    def __init__(self, stores: Stores, writer: Writer, ref: Reference, *, triggers: TriggerEngine | None = None,
                 snapshot_every: int = 100, archive=None, translator=None, lore: Lore | None = None) -> None:
        self.stores = stores
        self.writer = writer
        self.ref = ref
        self.triggers = triggers or TriggerEngine()
        self.snapshot_every = snapshot_every
        self.states: dict[str, MatchState] = {}
        self.log = EventLog()  # every event, for the web app and the chat tools
        # First stream wins: a second stream of a live match is ignored. One guard per path, because in Azure the
        # relay and the analysts read different consumer groups (same order per partition, so same decisions).
        self._relay_guard, self._analysis_guard = StreamGuard(), StreamGuard()
        # Every analysed event, copied to the archive the MCP server reads (Cosmos DB in Azure).
        self.archive = ArchiveWriter(archive) if archive is not None else None
        # Azure Translator: English variant -> viewer's language. Without it the personaliser translates.
        self.translator = translator
        # "Did you know?" facts for casual fans, retold by the storyteller agent when Foundry is configured.
        self.lore = lore or Lore(ref)
        self.season = None  # the season book (agents/season.py), for the kick-off preview; set by the CLI
        self._loading: dict[str, asyncio.Lock] = {}
        self._queues: dict[str, asyncio.Queue[Brief]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Insight]]] = {}
        self._variants_in_flight: dict[tuple[str, str], asyncio.Task] = {}

    # -- ingest ----------------------------------------------------------------------------------------------

    async def ingest(self, e: Event) -> None:
        """One source feeding both the relay log and the analysts (stdin, files, tests)."""
        self.record(e)
        await self.handle(e)

    def record(self, e: Event) -> None:
        if self._relay_guard.allow(e):
            self.log.add(e)

    async def handle(self, e: Event) -> None:
        if not self._analysis_guard.allow(e):
            return  # another stream owns this match
        state = await self._state(e.match_id)
        if not state.apply(e):
            return  # duplicate or redelivered event
        if self.archive is not None:
            self.archive.add(state, e)
        moments = self.triggers.check(state, e)
        for m in moments:
            self._queue(e.match_id).put_nowait(build(state, m, self.ref, lore=self.lore, season=self.season))
        if moments or e.seq % self.snapshot_every == 0 or e.type == "period_end":
            await self.stores.state.save(state.snapshot())

    async def _state(self, match_id: str) -> MatchState:
        if match_id in self.states:
            return self.states[match_id]
        async with self._loading.setdefault(match_id, asyncio.Lock()):
            if match_id not in self.states:
                snap = await self.stores.state.load(match_id)
                self.states[match_id] = MatchState.restore(snap) if snap else MatchState(match_id)
                if snap:
                    log.info("%s: resumed from snapshot at seq %s", match_id, snap["last_seq"])
        return self.states[match_id]

    # -- writing ---------------------------------------------------------------------------------------------

    def _queue(self, match_id: str) -> asyncio.Queue[Brief]:
        if match_id not in self._queues:
            self._queues[match_id] = asyncio.Queue()
            self._workers[match_id] = asyncio.create_task(self._work(match_id), name=f"writer-{match_id}")
        return self._queues[match_id]

    async def _work(self, match_id: str) -> None:
        q = self._queues[match_id]
        while True:
            batch = [await q.get()]
            while not q.empty():
                batch.append(q.get_nowait())
            # Fallen behind: keep everything notable, but only the latest filler and only if it's alone.
            keep = [b for b in batch if b.priority < 3] or batch[-1:]
            for brief in keep:
                try:
                    earlier = await self.stores.insights.list(match_id, as_of=brief.seq)
                    brief.previous = [i.headline for i in earlier if i.insight_id != brief.insight_id][-10:]
                    if brief.kind == "full_time":
                        brief.text += "\n\nTIMELINE OF EARLIER INSIGHTS:\n" + "\n".join(
                            f"  {i.clock} {i.kind}: {i.headline} - {i.why_it_matters}" for i in earlier)
                    # The fun fact (and the storyteller's retelling of it) is found while the analysts write,
                    # so it never delays the insight.
                    used = {i.trivia.trivia_id for i in earlier if i.trivia}
                    used |= covered(brief, self.lore)  # what the insight already tells isn't a "did you know?"
                    trivia = asyncio.create_task(self.lore.pick(brief, self._teams(match_id), used))
                    try:
                        insight = await self.writer.write(brief)
                    except BaseException:
                        trivia.cancel()
                        raise
                    try:
                        insight.trivia = await trivia
                    except Exception:  # noqa: BLE001 - a fun fact is never worth losing the insight over
                        log.exception("%s: no trivia for %s", match_id, brief.insight_id)
                    await self.publish(insight)
                except Exception:  # noqa: BLE001 - keep the worker alive for the rest of the match
                    log.exception("%s: could not write %s", match_id, brief.insight_id)
            for _ in batch:
                q.task_done()

    async def publish(self, insight: Insight) -> None:
        await self.stores.insights.put(insight)
        log.info("%s %s %s: %s", insight.match_id, insight.clock, insight.kind, insight.headline)
        for sub in list(self._subscribers.get(insight.match_id, ())):
            sub.put_nowait(insight)

    async def drain(self) -> None:
        """Wait until every queued moment has been written (end of a file replay, tests)."""
        for q in list(self._queues.values()):
            await q.join()
        if self.archive is not None:
            await self.archive.flush()

    async def close(self) -> None:
        for state in self.states.values():
            await self.stores.state.save(state.snapshot())
        for task in self._workers.values():
            task.cancel()
        if self.archive is not None:
            await self.archive.close()

    # -- viewers ---------------------------------------------------------------------------------------------

    def subscribe(self, match_id: str) -> asyncio.Queue[Insight]:
        q: asyncio.Queue[Insight] = asyncio.Queue()
        self._subscribers.setdefault(match_id, set()).add(q)
        return q

    def unsubscribe(self, match_id: str, q: asyncio.Queue[Insight]) -> None:
        self._subscribers.get(match_id, set()).discard(q)

    async def variant(self, insight: Insight, audience: str, language: str, club_id: str | None = None,
                      player_id: str | None = None) -> InsightVariant:
        """The insight as one viewer should see it. Cached on the insight, shared by every viewer with the same
        settings, and computed once even when many viewers ask at the same moment."""
        if player_id and player_id not in insight.player_ids:
            player_id = None  # focus only matters for insights the player is part of
        if club_id and club_id not in self._teams(insight.match_id):
            club_id = None  # a favourite club only reframes matches it plays in
        key = Insight.variant_key(audience, language, club_id, player_id)
        if key in insight.variants:
            return insight.variants[key]
        if audience == "broadcast" and language == "en" and not club_id and not player_id:
            return InsightVariant(audience=audience, language=language, headline=insight.headline, text=insight.text)
        task = self._variants_in_flight.get((insight.insight_id, key))
        if task is None:
            task = asyncio.create_task(self._make_variant(insight, key, audience, language, club_id, player_id))
            self._variants_in_flight[(insight.insight_id, key)] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._variants_in_flight.pop((insight.insight_id, key), None)

    async def _make_variant(self, insight: Insight, key: str, audience: str, language: str, club_id: str | None,
                            player_id: str | None) -> InsightVariant:
        v = await self._translated(insight, audience, language, club_id, player_id) \
            if self.translator is not None and not language.lower().startswith("en") else None
        if v is None:
            try:
                v = await self.writer.personalize(insight, audience, language, club_id, player_id)
                if not v.language.lower().startswith("en"):
                    v.translated_by = "agent"
            except Exception:  # noqa: BLE001
                log.exception("%s: personalisation failed, using template", insight.insight_id)
                v = template_variant(insight, audience, language, club_id, player_id, self.ref)
        insight.variants[key] = v
        # Merge into the latest stored copy: other variants of this insight may have been saved meanwhile.
        latest = await self.stores.insights.get(insight.match_id, insight.insight_id) or insight
        latest.variants[key] = v
        await self.stores.insights.put(latest)
        return v

    async def _translated(self, insight: Insight, audience: str, language: str, club_id: str | None,
                          player_id: str | None) -> InsightVariant | None:
        """Personalise in English (fact-checkable), then translate with Azure Translator. None if that fails, so
        the personaliser can write in the viewer's language itself."""
        english = await self.variant(insight, audience, "en", club_id, player_id)
        try:
            headline, text = await self.translator.translate([english.headline, english.text], language,
                                                             protect=self._names(insight), audience=audience)
        except Exception:  # noqa: BLE001 - the agent can still translate
            log.exception("%s: Azure Translator failed for %s; the personaliser will translate",
                          insight.insight_id, language)
            return None
        return InsightVariant(audience=audience, language=language, focus_club_id=club_id,
                              focus_player_id=player_id, headline=headline[:80], text=text,
                              translated_by="translator")

    def _names(self, insight: Insight) -> list[str]:
        """Names Translator must leave alone: the two clubs and everyone in their squads."""
        clubs = {c for c in (*self._teams(insight.match_id), insight.team_id) if c}
        names = [n for c in clubs if (club := self.ref.club(c)) for n in (club.name, club.short_name)]
        league = self.ref.league
        if league:
            names += [n for p in league.players if p.club_id in clubs for n in (p.name, p.short_name)]
        names += [self.ref.player_name(p) for p in insight.player_ids]
        return names

    def _teams(self, match_id: str) -> tuple[str | None, str | None]:
        s = self.states.get(match_id)
        return (s.home_id, s.away_id) if s else (None, None)

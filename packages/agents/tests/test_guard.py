import asyncio

from footballfoundry_agents.guard import HALF_TIME_IDLE_S, IDLE_S, StreamGuard
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.stores import memory_stores


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def stamped(events, stream_id):
    return [e.model_copy(update={"stream_id": stream_id}) for e in events]


def test_first_stream_keeps_a_live_match(match):
    clock = Clock()
    guard = StreamGuard(clock)
    a, b = stamped(match.events, "a"), stamped(match.events, "b")
    assert all(guard.allow(e) for e in a[:500])
    assert not any(guard.allow(e) for e in b[:50])  # a second tester on the same match
    assert guard.allow(a[500])


def test_a_finished_or_quiet_stream_hands_the_match_over(match):
    clock = Clock()
    guard = StreamGuard(clock)
    a, b, c = (stamped(match.events, s) for s in "abc")
    for e in a:
        guard.allow(e)
    assert guard.allow(b[0])  # a finished at full time
    clock.now += IDLE_S + 1
    assert guard.allow(c[0])  # b went quiet


def test_half_time_is_not_quiet(match):
    clock = Clock()
    guard = StreamGuard(clock)
    a, b = stamped(match.events, "a"), stamped(match.events, "b")
    half_time = next(i for i, e in enumerate(a) if e.type == "period_end" and e.period == 1)
    for e in a[:half_time + 2]:
        guard.allow(e)
    clock.now += IDLE_S * 5  # a real-time stream pauses for the break
    assert not guard.allow(b[0])
    clock.now += HALF_TIME_IDLE_S
    assert guard.allow(b[0])


def test_unstamped_events_are_always_accepted(match):
    guard = StreamGuard()
    assert all(guard.allow(e) for e in match.events[:10])


def test_two_testers_on_one_match_leave_it_intact(match, ref):
    """Interleave a second stream of the same match: the match plays out exactly as the first stream sent it."""
    a, b = stamped(match.events, "a"), stamped(match.events, "b")
    mixed = a[:600] + [x for pair in zip(a[600:], b) for x in pair] + b[len(a) - 600:]

    async def run(events):
        service = AnalystService(memory_stores(), TemplateWriter(ref), ref)
        for e in events:
            await service.ingest(e)
        await service.drain()
        state = service.states["M0001"]
        log = service.log.get("M0001")
        insights = [i.insight_id for i in await service.stores.insights.list("M0001")]
        return state.snapshot()["teams"], [e.seq for e in log.events], insights

    assert asyncio.run(run(mixed)) == asyncio.run(run(a))

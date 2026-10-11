import io
import json

import pytest

from footballfoundry import stream
from footballfoundry.cli import main
from footballfoundry_schema import Event


class ListSink:
    def __init__(self):
        self.batches = []

    def send(self, events):
        self.batches.append(list(events))

    def close(self):
        pass


class FakeClock:
    """Deterministic time: sleep() just advances the clock."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_timeline_includes_half_time(results):
    events = results[0].events
    items = stream.timeline(events, halftime_s=900)
    offsets = [t for t, _ in items]
    assert offsets == sorted(offsets)
    p1_end = max(e.elapsed_s for e in events if e.period == 1)
    first_p2 = next(t for t, e in items if e.period == 2)
    assert first_p2 == pytest.approx(p1_end + 900)


def test_merge_keeps_each_match_in_order(results):
    items = stream.merge([r.events for r in results[:3]])
    assert len({e.match_id for _, e in items}) == 3
    for r in results[:3]:
        seqs = [e.seq for _, e in items if e.match_id == r.info.match_id]
        assert seqs == sorted(seqs) == list(range(1, len(r.events) + 1))


def test_replay_paces_by_match_clock(results):
    items = stream.timeline(results[0].events)
    clock, sink = FakeClock(), ListSink()
    sent = stream.replay(items, sink, speed=10, clock=clock, sleep=clock.sleep)
    assert sent == len(results[0].events)
    # finishes when the last event is due: match duration / speed
    assert clock.now == pytest.approx(items[-1][0] / 10, abs=0.01)
    # nothing is sent before it is due
    flat = [e for batch in sink.batches for e in batch]
    assert [e.seq for e in flat] == [e.seq for _, e in items]


def test_replay_max_speed_does_not_sleep(results):
    clock, sink = FakeClock(), ListSink()
    stream.replay(stream.timeline(results[0].events), sink, speed=0, clock=clock, sleep=clock.sleep)
    assert clock.now == 0
    assert all(len(b) <= stream.FAST_CHUNK for b in sink.batches)


def test_jsonl_sink_roundtrip(results):
    buf = io.StringIO()
    stream.JsonlSink(buf).send(results[0].events[:5])
    lines = buf.getvalue().splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [1, 2, 3, 4, 5]


class FakeBatch:
    def __init__(self, partition_key, capacity):
        self.partition_key, self.capacity, self.items = partition_key, capacity, []

    def add(self, data):
        if len(self.items) >= self.capacity:
            raise ValueError("full")
        self.items.append(data)


class FakeProducer:
    def __init__(self, capacity=3):
        self.capacity, self.sent, self.closed = capacity, [], False

    def create_batch(self, partition_key=None):
        return FakeBatch(partition_key, self.capacity)

    def send_batch(self, batch):
        self.sent.append(batch)

    def close(self):
        self.closed = True


def test_eventhub_sink_partitions_by_match_and_splits_full_batches(results):
    events = results[0].events[:4] + results[1].events[:2]
    producer = FakeProducer(capacity=3)
    sink = stream.EventHubSink(producer)
    sink.send(events)
    sink.close()
    assert [(b.partition_key, len(b.items)) for b in producer.sent] == [
        (results[0].info.match_id, 3), (results[0].info.match_id, 1), (results[1].info.match_id, 2)]
    first = producer.sent[0].items[0]
    assert json.loads(first.body_as_str())["seq"] == 1
    assert first.properties == {"match_id": results[0].info.match_id, "type": "period_start"}
    assert first.content_type == "application/json"
    assert producer.closed


def test_cli_stream_to_file(tmp_path, capsys):
    out = tmp_path / "live.jsonl"
    main(["stream", "--seed", "7", "--matchday", "1", "--speed", "0", "--sink", "file", "--out", str(out), "--quiet"])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len({json.loads(line)["match_id"] for line in lines}) == 10
    assert "done:" in capsys.readouterr().err


def _streamed(tmp_path, name, *args):
    out = tmp_path / name
    main(["stream", "--speed", "0", "--quiet", "--sink", "file", "--out", str(out), *args])
    return [Event.model_validate_json(line) for line in out.read_text(encoding="utf-8").splitlines()]


def test_each_stream_run_is_stamped(tmp_path):
    a = _streamed(tmp_path, "a.jsonl", "--match-id", "M0002")
    b = _streamed(tmp_path, "b.jsonl", "--match-id", "M0002")
    c = _streamed(tmp_path, "c.jsonl", "--match-id", "M0002", "--stream-id", "tester-1")
    assert len({e.stream_id for e in a}) == 1 and a[0].stream_id != b[0].stream_id
    assert {e.stream_id for e in c} == {"tester-1"}
    assert [e.model_dump(exclude={"stream_id"}) for e in a] == [e.model_dump(exclude={"stream_id"}) for e in b]


def test_one_off_matches_get_their_own_id(tmp_path):
    a = _streamed(tmp_path, "a.jsonl", "--home", "T01", "--away", "T02")
    b = _streamed(tmp_path, "b.jsonl", "--home", "T01", "--away", "T02")
    fixed = _streamed(tmp_path, "c.jsonl", "--home", "T01", "--away", "T02", "--match-id", "X0001")
    assert a[0].match_id.startswith("X") and a[0].match_id != b[0].match_id
    assert fixed[0].match_id == "X0001"

import json

import pytest

from footballfoundry import export
from footballfoundry_schema import Event, MatchInfo


def test_json_roundtrip(tmp_path, league, results):
    export.write_league(league, tmp_path)
    info_path, events_path = export.write_match(results[0], tmp_path)
    info = MatchInfo.model_validate_json(info_path.read_text(encoding="utf-8"))
    events = [Event.model_validate_json(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert info == results[0].info
    assert events == results[0].events
    assert len(json.loads((tmp_path / "league.json").read_text(encoding="utf-8"))["players"]) == 520


def test_parquet_tables(tmp_path, league, results):
    pq = pytest.importorskip("pyarrow.parquet")
    paths = {p.stem: p for p in export.write_parquet(league, results, tmp_path)}
    assert set(paths) == {"clubs", "players", "matches", "lineups", "events"}
    events = pq.read_table(paths["events"])
    assert events.num_rows == sum(len(r.events) for r in results)
    assert pq.read_table(paths["matches"]).column("seed").to_pylist() == [r.info.seed for r in results]
    assert pq.read_table(paths["players"]).num_rows == 520

"""Write generated data to disk as JSON/JSONL, and optionally Parquet for Fabric/OneLake.

Layout:
    <out>/league.json                 clubs + players
    <out>/fixtures.json
    <out>/matches/<match_id>.json     MatchInfo (lineups, score, seed, ...)
    <out>/events/<match_id>.jsonl     one Event per line, in order
    <out>/standings.json              (season runs)
    <out>/parquet/*.parquet           (with --parquet) flat tables
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from footballfoundry.engine import MatchResult
from footballfoundry_schema import Fixture, League


def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def write_league(league: League, out: Path) -> Path:
    return _write_json(out / "league.json", league.model_dump(mode="json"))


def write_fixtures(fixtures: list[Fixture], out: Path) -> Path:
    return _write_json(out / "fixtures.json", [f.model_dump(mode="json") for f in fixtures])


def write_match(result: MatchResult, out: Path) -> tuple[Path, Path]:
    info_path = _write_json(out / "matches" / f"{result.info.match_id}.json", result.info.model_dump(mode="json"))
    events_path = out / "events" / f"{result.info.match_id}.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("w", encoding="utf-8", newline="\n") as f:
        for e in result.events:
            f.write(e.model_dump_json() + "\n")
    return info_path, events_path


def write_standings(rows: list[dict], out: Path) -> Path:
    return _write_json(out / "standings.json", rows)


def write_book(book: dict, path: Path) -> Path:
    """The season book (book.py), compact: it's served to the web app as is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return path


def write_parquet(league: League, results: Iterable[MatchResult], out: Path) -> list[Path]:
    """Flat tables ready for a Fabric Lakehouse. Requires the `parquet` extra (pyarrow)."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Parquet export needs pyarrow: pip install 'footballfoundry[parquet]'") from exc

    results = list(results)
    out = out / "parquet"
    out.mkdir(parents=True, exist_ok=True)

    clubs = []
    for c in league.clubs:
        row = c.model_dump(mode="json", exclude={"style", "colors"})
        row.update({f"style_{k}": v for k, v in c.style.model_dump().items()})
        row["colors"] = ",".join(c.colors)
        clubs.append(row)
    players = []
    for p in league.players:
        row = p.model_dump(mode="json", exclude={"attributes"})
        row.update(p.attributes.model_dump())
        players.append(row)
    matches = [r.info.model_dump(mode="json", exclude={"lineups", "added_time"})
               | {"added_time_p1": r.info.added_time[0], "added_time_p2": r.info.added_time[1]} for r in results]
    lineups = [{"match_id": r.info.match_id, **e.model_dump(mode="json")} for r in results for e in r.info.lineups]
    events = []
    for r in results:
        for e in r.events:
            row = e.model_dump(mode="json")
            row["qualifiers"] = json.dumps(row["qualifiers"])  # dynamic column in KQL / JSON in Spark
            events.append(row)

    paths = []
    for name, rows in (("clubs", clubs), ("players", players), ("matches", matches), ("lineups", lineups), ("events", events)):
        path = out / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        paths.append(path)
    return paths

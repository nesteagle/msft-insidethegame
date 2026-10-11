"""Command line interface: `footballfoundry <command> --help` for details."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

from footballfoundry import book, calibration, export, stream
from footballfoundry.engine import simulate_match
from footballfoundry.scenario import Scenario, scenario_from_args, write_truth
from footballfoundry_schema import Event
from footballfoundry.season import play_fixture, simulate_season
from footballfoundry.stats import match_stats, standings
from footballfoundry.world import generate_fixtures, generate_league, match_seed


def _cmd_world(args: argparse.Namespace) -> None:
    league = generate_league(args.seed)
    print(export.write_league(league, args.out))
    print(export.write_fixtures(generate_fixtures(league), args.out))


def _cmd_match(args: argparse.Namespace) -> None:
    league = generate_league(args.seed)
    scenario = scenario_from_args(args)
    result = simulate_match(league, args.home, args.away, match_id=args.match_id,
                            seed=match_seed(league.seed, args.match_id), **scenario.engine_kwargs())
    export.write_league(league, args.out)
    info_path, events_path = export.write_match(result, args.out)
    if scenario:
        print(write_truth(result, scenario, args.out / "truth"))
    i = result.info
    print(f"{league.club(i.home_club_id).name} {i.home_score}-{i.away_score} {league.club(i.away_club_id).name}"
          f"  ({len(result.events)} events)")
    stats = match_stats(result)
    for key in ("shots", "shots_on_target", "xg", "possession", "passes", "pass_accuracy", "corners", "fouls"):
        print(f"  {key:<16}{stats['home'].get(key, 0):>8.2f}{stats['away'].get(key, 0):>8.2f}")
    print(info_path, events_path, sep="\n")


def _cmd_season(args: argparse.Namespace) -> None:
    league = generate_league(args.seed)
    started = time.perf_counter()
    results = simulate_season(league, limit=args.matches, workers=args.workers)
    export.write_league(league, args.out)
    export.write_fixtures(generate_fixtures(league), args.out)
    for r in results:
        export.write_match(r, args.out)
    table = standings(league, results)
    export.write_standings(table, args.out)
    if not args.matches:
        export.write_book(book.build(league, results), args.out / "season-book.json")
    if args.parquet:
        for path in export.write_parquet(league, results, args.out):
            print(path)
    print(f"{len(results)} matches in {time.perf_counter() - started:.1f}s -> {args.out}")
    for row in table[:5]:
        print(f"  {row['position']:>2}. {row['club']:<26}{row['points']:>4} pts  GD {row['goal_difference']:+d}")


def _cmd_book(args: argparse.Namespace) -> None:
    league = generate_league(args.seed)
    started = time.perf_counter()
    results = simulate_season(league, workers=args.workers)
    path = export.write_book(book.build(league, results), args.out)
    print(f"season book for {len(results)} matches in {time.perf_counter() - started:.1f}s -> {path}")


def _cmd_calibrate(args: argparse.Namespace) -> None:
    _, results = calibration.simulate_sample(args.matches, args.seed, workers=args.workers)
    summary = calibration.summarize(results)
    print(calibration.report(summary))
    if calibration.out_of_range(summary):
        sys.exit(1)


def _log(msg: str) -> None:
    """Status goes to stderr so stdout stays clean JSONL."""
    print(msg, file=sys.stderr, flush=True)


def _headline(e: Event) -> str | None:
    """One-line description of notable events, for console progress."""
    if e.type == "shot" and e.outcome == "goal":
        return f"GOAL  {e.clock:>6} {e.match_id} {e.team_id} {e.player_id}  ({e.home_score}-{e.away_score}, xG {e.xg})"
    if e.type == "card" and e.outcome != "yellow":
        return f"RED   {e.clock:>6} {e.match_id} {e.team_id} {e.player_id}"
    if e.type in ("period_start", "period_end") and e.team_id == e.possession_team_id:
        label = {"period_start": "KICK-OFF" if e.period == 1 else "2ND HALF",
                 "period_end": "HALF-TIME" if e.period == 1 else "FULL-TIME"}[e.type]
        return f"{label:<9} {e.match_id}  {e.home_score}-{e.away_score}"
    return None


def _pick_results(args: argparse.Namespace, league, default_matchday: int | None = None,
                  scenario: Scenario | None = None) -> list:
    """Simulate the matches selected by --home/--away, --matchday or --match-id, with any scenario applied."""
    kwargs = scenario.engine_kwargs() if scenario else {}
    if args.home or args.away:
        # A stream picks a fresh one-off id unless told otherwise, so two testers don't share X0001.
        match_id = args.match_id or (f"X{secrets.token_hex(2).upper()}" if getattr(args, "unique_ids", False)
                                     else "X0001")
        if not args.match_id and getattr(args, "unique_ids", False):
            _log(f"one-off match id {match_id} (reuse it with --match-id {match_id} to replay this match)")
        return [simulate_match(league, args.home or "T01", args.away or "T02", match_id=match_id,
                               seed=match_seed(league.seed, match_id), **kwargs)]
    fixtures = generate_fixtures(league)
    matchday = args.matchday or (None if args.match_id else default_matchday)
    if not matchday and not args.match_id and getattr(args, "unique_ids", False):
        _log("no --match-id given: streaming fixture M0001. If someone else streams M0001 at the same time, the "
             "first stream keeps it; pick another with --match-id.")
    if matchday:
        chosen = [f for f in fixtures if f.matchday == matchday]
    else:
        chosen = [f for f in fixtures if f.match_id == (args.match_id or "M0001")]
    if not chosen:
        raise SystemExit("no matching fixture (matchdays are 1-38, match ids M0001-M0380)")
    for f in chosen:
        _log(f"{f.match_id}  {league.club(f.home_club_id).name} v {league.club(f.away_club_id).name}")
    return [play_fixture(league, f, **kwargs) for f in chosen]


def _stream_matches(args: argparse.Namespace) -> list[list[Event]]:
    """The matches to stream, every event stamped with this run's stream id."""
    stream_id = args.stream_id or secrets.token_hex(4)
    _log(f"stream id {stream_id}")

    def stamp(events: list[Event]) -> list[Event]:
        return [e.model_copy(update={"stream_id": stream_id}) for e in events]

    if args.file:
        return [stamp([Event.model_validate_json(line) for line in Path(f).read_text(encoding="utf-8").splitlines()
                       if line]) for f in args.file]
    scenario = scenario_from_args(args)
    results = _pick_results(args, generate_league(args.seed), scenario=scenario)
    if scenario:
        for r in results:
            _log(f"scenario '{scenario.name}': ground truth in {write_truth(r, scenario, args.truth)}")
    return [stamp(r.events) for r in results]


def _cmd_serve(args: argparse.Namespace) -> None:
    from footballfoundry.serve import serve

    league = generate_league(args.seed)
    results = _pick_results(args, league, default_matchday=1, scenario=scenario_from_args(args))
    server = serve(league, results, host=args.host, port=args.port, speed=args.speed)
    url = f"http://{args.host}:{server.server_address[1]}/?speed={args.speed:g}"
    _log(f"viewer running at {url}  Ctrl+C to stop")
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("stopped")
    finally:
        server.server_close()


def _cmd_stream(args: argparse.Namespace) -> None:
    matches = _stream_matches(args)
    halftime_s = args.halftime * args.speed if args.halftime is not None else stream.HALF_TIME_S
    items = stream.merge(matches, halftime_s)

    if args.sink == "eventhub":
        sink = stream.EventHubSink.connect(
            args.eventhub_name,
            namespace=args.eventhub_namespace or os.environ.get("EVENTHUB_NAMESPACE"),
            connection_string=os.environ.get("EVENTHUB_CONNECTION_STRING"),
        )
        target = f"Event Hubs '{args.eventhub_name}'"
    elif args.sink == "file":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        sink = stream.JsonlSink(args.out.open("w", encoding="utf-8", newline="\n"))
        target = str(args.out)
    else:
        sink = stream.JsonlSink(sys.stdout)
        target = "stdout"

    duration = items[-1][0] / args.speed if args.speed > 0 and items else 0
    _log(f"streaming {len(items)} events from {len(matches)} match(es) to {target} at "
         f"{'max speed' if args.speed <= 0 else f'{args.speed:g}x'}"
         + (f" (~{duration / 60:.1f} min)" if duration else "") + "  Ctrl+C to stop")

    def progress(batch: list[Event]) -> None:
        for e in batch:
            if line := _headline(e):
                _log(line)

    try:
        sent = stream.replay(items, sink, speed=args.speed, on_send=None if args.quiet else progress)
        _log(f"done: {sent} events sent")
    except KeyboardInterrupt:
        _log("stopped")
    finally:
        sink.close()


def _cmd_find(args: argparse.Namespace) -> None:
    from footballfoundry import stories

    league = generate_league(args.seed)
    _log(f"simulating {args.matches or 380} fixtures (seed {args.seed})...")
    results = simulate_season(league, limit=args.matches, workers=args.workers)
    found = stories.find(results, league, args.story)
    if args.json:
        import json

        print(json.dumps([{"match_id": s.match_id, "matchday": s.matchday, "home": s.home, "away": s.away,
                           "score": list(s.score), "drama": s.drama, "stories": s.tags} for s in found[:args.limit]],
                         indent=2))
        return
    if not found:
        print("no match has all of: " + ", ".join(args.story or []))
        return
    print(f"{len(found)} match(es){' with ' + ' + '.join(args.story) if args.story else ''}, most dramatic first:\n")
    for s in found[:args.limit]:
        print(f"{s.match_id}  MD{s.matchday:<2}  {s.home} {s.score[0]}-{s.score[1]} {s.away}")
        for tag, detail in s.tags.items():
            print(f"    {tag:<15}{detail}")
    print(f"\nreplay one: footballfoundry stream --match-id {found[0].match_id} --speed 30")


def _cmd_listen(args: argparse.Namespace) -> None:
    def show(e: Event) -> None:
        if args.all:
            print(e.model_dump_json(), flush=True)
        elif line := _headline(e):
            print(line, flush=True)

    _log(f"listening on '{args.eventhub_name}' (consumer group {args.consumer_group})  Ctrl+C to stop")
    try:
        stream.listen(
            args.eventhub_name,
            show,
            namespace=args.eventhub_namespace or os.environ.get("EVENTHUB_NAMESPACE"),
            connection_string=os.environ.get("EVENTHUB_CONNECTION_STRING"),
            consumer_group=args.consumer_group,
            from_start=args.from_start,
        )
    except KeyboardInterrupt:
        _log("stopped")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="footballfoundry", description="Synthetic football match data generator")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, out: bool = True) -> None:
        p.add_argument("--seed", type=int, default=42, help="world seed (default 42)")
        if out:
            p.add_argument("--out", type=Path, default=Path("data"), help="output directory (default ./data)")

    p = sub.add_parser("world", help="generate the league, clubs, players and fixtures")
    common(p)
    p.set_defaults(func=_cmd_world)

    def scenario_args(p: argparse.ArgumentParser) -> None:
        g = p.add_argument_group("scenario (scripted tactical changes, with a ground-truth file)")
        g.add_argument("--scenario", type=Path, help="scenario JSON, e.g. scenarios/high-press.json")
        g.add_argument("--tactic", action="append", metavar="MIN:SIDE:DIAL=DELTA[,...]",
                       help="style change, e.g. 58:home:press_intensity=+0.35,defensive_line=+0.25 (not announced)")
        g.add_argument("--move", action="append", metavar="MIN:SIDE:PLAYER_OR_ROLE:ROLE",
                       help="move a player (id, or current role like RW) to a role, e.g. 61:home:RW:AM")
        g.add_argument("--formation", action="append", metavar="MIN:SIDE:FORMATION",
                       help="switch formation, e.g. 60:away:3-5-2")

    p = sub.add_parser("match", help="simulate a single match")
    common(p)
    p.add_argument("--home", default="T01")
    p.add_argument("--away", default="T02")
    p.add_argument("--match-id", default="X0001")
    scenario_args(p)
    p.set_defaults(func=_cmd_match)

    p = sub.add_parser("season", help="simulate the full fixture list")
    common(p)
    p.add_argument("--matches", type=int, default=None, help="only the first N fixtures")
    p.add_argument("--workers", type=int, default=0, help="processes (default: all cores)")
    p.add_argument("--parquet", action="store_true", help="also write Parquet tables")
    p.set_defaults(func=_cmd_season)

    p = sub.add_parser("book", help="write only the season book: results and player lines for the web app")
    common(p, out=False)
    p.add_argument("--out", type=Path, default=Path("data/season-book.json"), help="(default data/season-book.json)")
    p.add_argument("--workers", type=int, default=0, help="processes (default: all cores)")
    p.set_defaults(func=_cmd_book)

    p = sub.add_parser("calibrate", help="check realism against target ranges (exit 1 if any are out)")
    common(p, out=False)
    p.add_argument("--matches", type=int, default=380)
    p.add_argument("--workers", type=int, default=0)
    p.set_defaults(func=_cmd_calibrate)

    def eventhub_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--eventhub-namespace", help="namespace name or FQDN (default: $EVENTHUB_NAMESPACE); "
                                                    "or set $EVENTHUB_CONNECTION_STRING instead")
        p.add_argument("--eventhub-name", default=os.environ.get("EVENTHUB_NAME", "match-events"),
                       help="event hub name (default: $EVENTHUB_NAME or match-events)")

    p = sub.add_parser("stream", help="replay matches in real time to stdout, a file or Azure Event Hubs")
    p.add_argument("--seed", type=int, default=42, help="world seed (default 42)")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--matchday", type=int, help="stream all 10 fixtures of a matchday (1-38) together")
    which.add_argument("--file", action="append", help="replay an existing events .jsonl (repeatable)")
    p.add_argument("--match-id", help="fixture to stream (default M0001)")
    p.add_argument("--home", help="simulate a one-off match: home club id, e.g. T01")
    p.add_argument("--away", help="simulate a one-off match: away club id, e.g. T07")
    p.add_argument("--speed", type=float, default=1.0, help="x real time: 1 = live, 30 = a match in ~3 min, 0 = no delay")
    p.add_argument("--halftime", type=float, help="half-time break in real seconds (default: 15 match minutes)")
    p.add_argument("--sink", choices=["stdout", "file", "eventhub"], default="stdout")
    p.add_argument("--out", type=Path, default=Path("data/stream.jsonl"), help="output path for --sink file")
    p.add_argument("--quiet", action="store_true", help="no goal/card progress lines on stderr")
    p.add_argument("--truth", type=Path, default=Path("data/truth"), help="where scenario ground truth goes")
    p.add_argument("--stream-id", help="identify this run (default: random); a live match belongs to its first stream")
    p.set_defaults(unique_ids=True)
    eventhub_args(p)
    scenario_args(p)
    p.set_defaults(func=_cmd_stream)

    p = sub.add_parser("serve", help="open a live match viewer in the browser")
    p.add_argument("--seed", type=int, default=42, help="world seed (default 42)")
    p.add_argument("--matchday", type=int, help="matches to offer in the viewer (default: matchday 1)")
    p.add_argument("--match-id", help="serve a single fixture, e.g. M0042")
    p.add_argument("--home", help="serve a one-off match: home club id")
    p.add_argument("--away", help="serve a one-off match: away club id")
    p.add_argument("--speed", type=float, default=10.0, help="starting playback speed (default 10x)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--open", action="store_true", help="open the viewer in your browser")
    scenario_args(p)
    p.set_defaults(func=_cmd_serve)

    from footballfoundry.stories import STORIES

    p = sub.add_parser("find", help="find matches with a story to tell: comebacks, late winners, red cards...",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog="stories:\n" + "\n".join(f"  {k:<15}{v}" for k, v in STORIES.items()))
    p.add_argument("--seed", type=int, default=42, help="world seed (default 42)")
    p.add_argument("--story", action="append", choices=sorted(STORIES), help="required story; repeat for several")
    p.add_argument("--matches", type=int, default=None, help="only the first N fixtures (default: all 380)")
    p.add_argument("--limit", type=int, default=10, help="how many to show (default 10)")
    p.add_argument("--workers", type=int, default=0, help="processes (default: all cores)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=_cmd_find)

    p = sub.add_parser("listen", help="print events arriving on Azure Event Hubs (to verify a stream)")
    eventhub_args(p)
    p.add_argument("--consumer-group", default="$Default")
    p.add_argument("--from-start", action="store_true", help="read retained events instead of only new ones")
    p.add_argument("--all", action="store_true", help="print every event as JSON (default: goals, reds, periods)")
    p.set_defaults(func=_cmd_listen)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

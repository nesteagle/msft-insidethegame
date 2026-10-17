"""footballfoundry-agents: run the analyst service.

Local, no Azure (template insights), either piped:
    footballfoundry stream --speed 30 --quiet | footballfoundry-agents serve --offline
or in two terminals, started in any order:
    footballfoundry-agents serve --offline --follow data/stream.jsonl
    footballfoundry stream --speed 30 --sink file --out data/stream.jsonl

Then open http://127.0.0.1:8100 for the web app (build it first: cd packages/web && npm run build).

Or just start it, and stream from the control room at http://127.0.0.1:8100/control:
    footballfoundry-agents serve --offline

Local with Foundry agents and the MCP server:
    footballfoundry-mcp --data data &
    footballfoundry stream --speed 30 | footballfoundry-agents serve --mcp-url http://127.0.0.1:8001/mcp

In Azure (Container Apps) every setting comes from environment variables, see `--help`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from footballfoundry_agents.api import create_app
from footballfoundry_agents.chat import chat_endpoint
from footballfoundry_agents.reference import Reference
from footballfoundry_agents.season import Season
from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.stores import cosmos_stores, file_stores, memory_stores

log = logging.getLogger("footballfoundry_agents")


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="footballfoundry-agents", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="consume events, write insights, serve the API",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    s.add_argument("--source", default=_env("FF_SOURCE"),
                   help="'stdin', a JSONL file, 'eventhub', or 'none' (only the control room's streams). Default: "
                        "eventhub when EVENTHUB_NAMESPACE or EVENTHUB_CONNECTION_STRING is set, else stdin when "
                        "something is piped in, else none [FF_SOURCE]")
    s.add_argument("--follow", metavar="FILE",
                   help="follow a JSONL file as `footballfoundry stream --sink file` writes it (like tail -f), so "
                        "the stream can run in another terminal and start before or after this")
    s.add_argument("--store", choices=["memory", "file", "cosmos"], default=_env("FF_STORE"),
                   help="default: cosmos when COSMOS_ENDPOINT is set, else memory [FF_STORE]")
    s.add_argument("--store-dir", type=Path, default=Path(_env("FF_STORE_DIR", "data/agents")),
                   help="directory for --store file [FF_STORE_DIR]")
    s.add_argument("--league", default=_env("FOOTBALLFOUNDRY_LEAGUE", "data/league.json"),
                   help="league.json for club and player names [FOOTBALLFOUNDRY_LEAGUE]")
    s.add_argument("--offline", action="store_true", help="template insights only; no Foundry, no LLM")
    s.add_argument("--mcp-url", default=_env("MCP_URL"), help="footballfoundry-mcp endpoint for the agents [MCP_URL]")
    s.add_argument("--translator-endpoint", default=_env("TRANSLATOR_ENDPOINT"),
                   help="Azure Translator (the Foundry/AI Services account endpoint) to translate insights; "
                        "without it the personaliser agent translates [TRANSLATOR_ENDPOINT]")
    s.add_argument("--translator-llm", default=_env("TRANSLATOR_LLM_DEPLOYMENT"),
                   help="LLM deployment for Translator's casual-fan translations (informal tone); NMT otherwise "
                        "[TRANSLATOR_LLM_DEPLOYMENT]")
    s.add_argument("--consumer-group", default=_env("EVENTHUB_CONSUMER_GROUP", "analyst"),
                   help="[EVENTHUB_CONSUMER_GROUP]")
    s.add_argument("--from-start", action="store_true", help="Event Hubs: read retained events from the start")
    s.add_argument("--host", default=_env("HOST", "127.0.0.1"), help="[HOST]")
    s.add_argument("--port", type=int, default=int(_env("PORT", "8100")), help="[PORT]")
    s.add_argument("--cors-origin", action="append", default=(_env("CORS_ORIGINS") or "").split(",") if
                   _env("CORS_ORIGINS") else None, help="allowed browser origin; repeat for several [CORS_ORIGINS]")
    s.add_argument("--web-dir", default=_env("FF_WEB_DIR", str(Path(__file__).parents[3] / "web" / "dist")),
                   help="built web app to serve at / (packages/web: npm run build) [FF_WEB_DIR]")
    s.add_argument("--exit-when-done", action="store_true", help="stop when a stdin/file source ends")
    s.add_argument("--control-key", default=_env("CONTROL_KEY"),
                   help="turn on the control room (/control) on a non-local host; testers enter this key. On "
                        "localhost it's always on [CONTROL_KEY]")
    s.add_argument("--no-control", action="store_true", help="turn the control room off")
    s.add_argument("--season-book", default=_env("FF_SEASON_BOOK", "data/season-book.json"),
                   help="season book for the season pages (`footballfoundry book`); built and saved here on first "
                        "use if missing [FF_SEASON_BOOK]")
    s.add_argument("--scenarios", default=_env("FF_SCENARIOS_DIR", str(Path(__file__).parents[4] / "scenarios")),
                   help="scenario files for the control room [FF_SCENARIOS_DIR]")
    s.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _credential():
    from azure.identity.aio import DefaultAzureCredential

    return DefaultAzureCredential()  # az login locally; managed identity (AZURE_CLIENT_ID) in Azure


def _telemetry() -> None:
    if not os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        log.warning("APPLICATIONINSIGHTS_CONNECTION_STRING is set but azure-monitor-opentelemetry isn't installed")
        return
    configure_azure_monitor()  # Agent Framework emits OpenTelemetry spans for every agent and tool call


async def serve(args: argparse.Namespace) -> None:
    import uvicorn

    ref = Reference.load(args.league)
    if ref.league is None:
        log.warning("no league file at %s: insights will use ids instead of names (run `footballfoundry world`)",
                    args.league)
    credential = None

    def cred():
        nonlocal credential
        credential = credential or _credential()
        return credential

    store = args.store or ("cosmos" if _env("COSMOS_ENDPOINT") else "memory")
    # The event archive follows the store: Cosmos DB in Azure, files under --store-dir locally (where
    # `footballfoundry-mcp --archive <store-dir>/archive` reads it). Memory runs keep no archive.
    if store == "cosmos":
        from footballfoundry_mcp.archive import CosmosArchive

        stores = cosmos_stores(_env("COSMOS_ENDPOINT"), _env("COSMOS_DATABASE", "footballfoundry"), cred())
        archive = CosmosArchive(_env("COSMOS_ENDPOINT"), _env("COSMOS_DATABASE", "footballfoundry"), cred())
    elif store == "file":
        from footballfoundry_mcp.archive import FileArchive

        stores = file_stores(args.store_dir)
        archive = FileArchive(Path(args.store_dir) / "archive")
    else:
        stores = memory_stores()
        archive = None

    async with contextlib.AsyncExitStack() as stack:
        client = mcp = None
        lore = None  # facts from the league data; the storyteller agent retells them when Foundry is on
        if args.offline or not _env("FOUNDRY_PROJECT_ENDPOINT"):
            if not args.offline:
                log.warning("FOUNDRY_PROJECT_ENDPOINT not set: running offline with template insights")
            writer = TemplateWriter(ref)
        else:
            from agent_framework.foundry import FoundryChatClient

            from footballfoundry_agents.workflow import AgentWriter

            client = FoundryChatClient(project_endpoint=_env("FOUNDRY_PROJECT_ENDPOINT"),
                                       model=_env("FOUNDRY_MODEL"), credential=cred())
            if args.mcp_url:
                from agent_framework import MCPStreamableHTTPTool

                mcp = await stack.enter_async_context(MCPStreamableHTTPTool(
                    name="footballfoundry", url=args.mcp_url, approval_mode="never_require",
                    description="Match events, live stats, player and club data, season queries"))
            writer = AgentWriter(client, ref, tools=mcp)
            from footballfoundry_agents.lore import Lore, Storyteller

            lore = Lore(ref, Storyteller(client))
            log.info("agents: Foundry model %s, MCP %s", _env("FOUNDRY_MODEL"), args.mcp_url or "off")

        translator = None
        if args.translator_endpoint:
            from footballfoundry_agents.translation import AzureTranslator

            translator = AzureTranslator(args.translator_endpoint, cred(), llm_deployment=args.translator_llm)
            stack.push_async_callback(translator.close)
            log.info("translation: Azure Translator (%s)", "NMT + LLM " + args.translator_llm
                     if args.translator_llm else "NMT")
        service = AnalystService(stores, writer, ref, archive=archive, translator=translator, lore=lore)
        web_dir = args.web_dir if args.web_dir and Path(args.web_dir, "index.html").exists() else None
        eventhub_configured = bool(_env("EVENTHUB_NAMESPACE") or _env("EVENTHUB_CONNECTION_STRING"))
        source_spec = args.source or ("eventhub" if eventhub_configured
                                      else "none" if sys.stdin.isatty() and not args.follow else "stdin")
        season = Season(path=args.season_book)
        season.book()  # load it now (or start building it in the background) rather than on first use
        control = _control(args, service, source_spec, eventhub_configured, season)
        app = create_app(service, cors_origins=args.cors_origin, chat=chat_endpoint(service, client, mcp),
                         web_dir=web_dir, control=control, season=season)
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))

        relay = None
        if source_spec == "eventhub":
            from footballfoundry_agents.sources import EventHubSource

            conn = _env("EVENTHUB_CONNECTION_STRING")
            hub = dict(eventhub=_env("EVENTHUB_NAME", "match-events"), credential=None if conn else cred(),
                       namespace=_env("EVENTHUB_NAMESPACE"), connection_string=conn)
            # Analysts: a shared consumer group with checkpoints, so replicas split the partitions.
            source = EventHubSource(**hub, consumer_group=args.consumer_group,
                                    checkpoint_url=_env("CHECKPOINT_BLOB_URL"),
                                    checkpoint_container=_env("CHECKPOINT_CONTAINER", "checkpoints"),
                                    from_start=args.from_start)
            # Relay for the web app: every replica reads every partition from the start of retention.
            relay = EventHubSource(**hub, consumer_group=_env("VIEWER_CONSUMER_GROUP", "viewer"), from_start=True)
            handle = service.handle
        elif args.follow:
            from footballfoundry_agents.sources import FollowSource

            source_spec = f"following {args.follow}"
            source = FollowSource(args.follow)
            handle = service.ingest
        elif source_spec == "none":
            source = handle = None
        else:
            from footballfoundry_agents.sources import JsonlSource

            source = JsonlSource.open(source_spec)
            handle = service.ingest
            if source.stream is sys.stdin and sys.stdin.isatty():
                log.warning("reading events from this terminal's input, but nothing is piped in. Either pipe a "
                            "stream in (footballfoundry stream --quiet | footballfoundry-agents serve ...) or run "
                            "the stream separately with --sink file and start this with --follow data/stream.jsonl")

        async def record(e) -> None:
            service.record(e)

        async def consume() -> None:
            if relay is not None:
                stack.push_async_callback(_cancel, asyncio.create_task(relay.run(record)))
            if source is None:
                return  # events come only from the control room
            await source.run(handle)
            await service.drain()
            log.info("source finished")
            if args.exit_when_done:
                server.should_exit = True

        log.info("http://%s:%d  (source: %s, store: %s, web: %s)", args.host, args.port, source_spec, store,
                 web_dir or "not built")
        if control is not None:
            log.info("control room: http://%s:%d/control (streams go to %s%s)", args.host, args.port,
                     control.target, ", key required" if control.key else "")
        consumer = asyncio.create_task(consume())
        try:
            await server.serve()
        finally:
            consumer.cancel()
            await service.close()
            if stores.close:
                await stores.close()
            if credential:
                await credential.close()


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _control(args: argparse.Namespace, service: AnalystService, source_spec: str, eventhub_configured: bool,
             season: Season):
    """The control room: always on locally; on a public host only with a key, so a link can't start streams."""
    from footballfoundry_agents.control import Control

    local = args.host in LOCAL_HOSTS
    if args.no_control or not (local or args.control_key):
        if not args.no_control:
            log.info("control room off (set CONTROL_KEY to turn it on for this host)")
        return None
    eventhub = None
    if eventhub_configured:
        eventhub = {"eventhub": _env("EVENTHUB_NAME", "match-events"), "namespace": _env("EVENTHUB_NAMESPACE"),
                    "connection_string": _env("EVENTHUB_CONNECTION_STRING")}
    # Event Hubs mode: the analysts and the relay read from Event Hubs, so control streams must go there too.
    target = "eventhub" if source_spec == "eventhub" else "local"
    return Control(service, target=target, eventhub=eventhub, scenarios_dir=args.scenarios,
                   data_dir=Path("data") if local else None, key=args.control_key, local_only=local,
                   season=season)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("azure", "httpx", "uamqp", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _telemetry()
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

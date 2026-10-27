# Documentation

Start with the [project overview](../README.md) for the end-to-end FootballFoundry pipeline and quick start.

## Guides

- [Design](./DESIGN.md) — architecture, agent workflow, event-to-screen flow, infrastructure, and design decisions.
- [Deploy and test](./TEST.md) — local testing, Azure deployment, smoke tests, Fabric setup, troubleshooting, and cleanup.
- [Microsoft Fabric](../fabric/README.md) — Microsoft Fabric eventstream and eventhouse.
   
## Component documentation

- [Agents service](../packages/agents/README.md) — run the match analyst, configure its API and chat, and learn about insights, visuals, and translation.
- [Web app](../packages/web/README.md) — use the live match viewer and season pages, and run the app locally.
- [Web app generator package](../packages/generator/README.md) — develop and build the web app.
- [MCP server](../packages/mcp/README.md) — run the Match Data Model Context Protocol server and configure its tools and backends.
- [Data schema](../packages/schema/README.md) — event, match, insight, and statistics data contracts.

## Operations and data

- [Infrastructure](../infra/README.md) — provision and deploy FootballFoundry's Azure resources with the Azure Developer CLI.
- [Scenarios](../scenarios/README.md) — create scripted match situations and use their ground truth to test the analyst agents.
- [Microsoft Fabric](../fabric/README.md) — configure the Eventstream, Eventhouse, tables, and functions for match data.

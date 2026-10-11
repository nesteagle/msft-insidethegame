# The agents service and the web app it serves. Build from the repository root:
#   docker build -f packages/agents/Dockerfile .
FROM node:24-slim AS web
WORKDIR /web
COPY packages/web/package.json packages/web/package-lock.json ./
RUN npm ci
COPY packages/web ./
RUN npm run build

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
# Club and player names for the prompts: generated once from the default seed, matching `footballfoundry stream`.
RUN uv run --frozen --no-dev --package footballfoundry footballfoundry world --out /data
# The season book for the season pages (table, stats, players): the full season, simulated once at build time.
RUN uv run --frozen --no-dev --package footballfoundry footballfoundry book --out /data/season-book.json
# Then the runtime environment: the agents and their dependencies (the generator too, for the control room).
RUN uv sync --frozen --no-dev --no-editable --package footballfoundry-agents --extra monitor

FROM python:3.12-slim
RUN useradd --create-home app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /data/league.json /data/season-book.json /data/
COPY scenarios /app/scenarios
COPY --from=web /web/dist /app/web
ENV PATH=/app/.venv/bin:$PATH \
    FOOTBALLFOUNDRY_LEAGUE=/data/league.json \
    FF_SEASON_BOOK=/data/season-book.json \
    FF_WEB_DIR=/app/web \
    FF_SCENARIOS_DIR=/app/scenarios \
    HOST=0.0.0.0 \
    PORT=8100 \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8100
CMD ["footballfoundry-agents", "serve"]

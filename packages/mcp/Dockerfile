# Build from the repository root: docker build -f packages/mcp/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
# The default-seed season, so the file backend works before the Eventhouse is set up. It's deterministic, so it
# matches what `footballfoundry stream` sends for fixtures M0001-M0380. With KUSTO_URI set, the Eventhouse is used.
RUN uv run --frozen --no-dev --package footballfoundry footballfoundry season --out /data
RUN uv sync --frozen --no-dev --no-editable --package footballfoundry-mcp --extra kusto --extra cosmos

FROM python:3.12-slim
RUN useradd --create-home app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /data /data
ENV PATH=/app/.venv/bin:$PATH \
    FOOTBALLFOUNDRY_DATA=/data \
    HOST=0.0.0.0 \
    PORT=8001 \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8001
CMD ["footballfoundry-mcp"]

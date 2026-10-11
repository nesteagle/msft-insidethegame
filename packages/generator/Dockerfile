# The match viewer (`footballfoundry serve`). Build from the repository root:
#   docker build -f packages/generator/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --no-editable --package footballfoundry

FROM python:3.12-slim
RUN useradd --create-home app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["footballfoundry", "serve", "--host", "0.0.0.0", "--port", "8000"]
